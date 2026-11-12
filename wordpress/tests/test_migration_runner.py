"""Coordinator primitives only; no model, provider, native WordPress or unattended completion proof."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import contextlib
import io

spec = importlib.util.spec_from_file_location('migration_runner', Path(__file__).resolve().parents[1] / 'tools/migration_runner.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

class RunnerTests(unittest.TestCase):
    def test_atomic_state_replacement(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'nested/state.json'
            m.atomic(p, {'completed': []})
            m.atomic(p, {'completed': ['one']})
            self.assertEqual(json.loads(p.read_text()), {'completed': ['one']})
            self.assertEqual([x.name for x in p.parent.iterdir()], ['state.json'])

    def test_evidence_rejects_stale_wrong_task_and_truthy_strings(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'evidence.json'
            self.assertFalse(m.evidence_ok(p, 'rev', 'task', ['complete']))
            p.write_text('invalid')
            self.assertFalse(m.evidence_ok(p, 'rev', 'task', ['complete']))
            for data in ({'revision':'old','task':'task','complete':True},
                         {'revision':'rev','task':'other','complete':True},
                         {'revision':'rev','task':'task','complete':'true'},
                         {'revision':'rev','task':'task','complete':True}):
                p.write_text(json.dumps(data))
                self.assertEqual(m.evidence_ok(p,'rev','task',['complete']), data == {'revision':'rev','task':'task','complete':True})
            self.assertFalse(m.evidence_ok(p,'rev','task',['complete','native_install']))

    def test_commands_propagate_failure_and_capture_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'command.log'
            self.assertEqual(m.run_command([sys.executable,'-c','print("test only"); raise SystemExit(7)'],p,5),7)
            self.assertIn('test only',p.read_text())

    def test_timeout_stops_child(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(RuntimeError):
                m.run_command([sys.executable,'-c','import time; time.sleep(30)'],Path(tmp)/'timeout.log',1)

    def test_protocol_advances_tasks_then_stops_for_independent_native_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            home=Path(tmp)
            tasks=[{'id':'transports','goal':'test only'}, {'id':'native-qualification','goal':'test only'}]
            (home/'backlog.json').write_text(json.dumps({'branch':m.BRANCH,'tasks':tasks}))
            invocations=[]
            def command(argv, log, timeout):
                invocations.append(log.name)
                if log.name=='worker.log':
                    task=json.loads((home/'state.json').read_text())['task']
                    data=dict(revision='fresh',task=task,complete=True)
                    data.update({k:True for k in ['native_install','native_http','native_mysql','browser','concurrency','recovery','security','all_contracts_certified']})
                    (log.parent/'completion.json').write_text(json.dumps(data))
                return 0
            with patch.object(m,'HOME',home), patch.object(m,'git',side_effect=lambda *a:m.BRANCH if a[0]=='branch' else 'head'), patch.object(m,'protected_snapshot',return_value={}), patch.object(m,'revision_digest',return_value='fresh'), patch.object(m,'run_command',side_effect=command), patch.object(sys,'argv',['runner','--worker',json.dumps([sys.executable,'{prompt}'])]), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(m.main(),2)
            state=json.loads((home/'state.json').read_text())
            self.assertEqual(state['completed'],['transports'])
            self.assertEqual(state['status'],'blocked')
            self.assertIn('Independent native',state['last_error'])
            self.assertEqual(invocations,['worker.log','checks.log','worker.log','checks.log'])

    def test_missing_evidence_retries_but_never_advances(self):
        with tempfile.TemporaryDirectory() as tmp:
            home=Path(tmp)
            (home/'backlog.json').write_text(json.dumps({'branch':m.BRANCH,'tasks':[{'id':'transports','goal':'test only'}]}))
            with patch.object(m,'HOME',home), patch.object(m,'git',side_effect=lambda *a:m.BRANCH if a[0]=='branch' else 'head'), patch.object(m,'protected_snapshot',return_value={}), patch.object(m,'revision_digest',return_value='fresh'), patch.object(m,'run_command',return_value=0) as run, patch.object(sys,'argv',['runner','--worker',json.dumps([sys.executable,'{prompt}']),'--max-attempts','2']), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(m.main(),2)
                self.assertEqual(run.call_count,2)
            state=json.loads((home/'state.json').read_text())
            self.assertEqual(state['completed'],[])
            self.assertEqual(state['attempts']['transports'],2)
            self.assertEqual(state['status'],'blocked')

    def test_backlog_order_and_fixed_branch(self):
        data=json.loads((m.HOME/'backlog.json').read_text())
        self.assertEqual(data['branch'],m.BRANCH)
        ids=[x['id'] for x in data['tasks']]
        self.assertEqual(len(ids),len(set(ids)))
        self.assertEqual(ids[0],'transports')
        self.assertEqual(ids[-2:],['native-qualification','release'])

if __name__ == '__main__': unittest.main()
