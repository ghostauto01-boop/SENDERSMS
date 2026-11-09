#!/usr/bin/env python3
"""Trusted external coding-worker coordinator. It is NOT a coding model or a sandbox."""
import argparse, datetime, fcntl, hashlib, json, os, subprocess, sys, tempfile
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
HOME = ROOT / 'wordpress/automation'
BRANCH = 'arena/d761f7c5-sendersms'


def atomic(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix='.state-')
    try:
        with os.fdopen(fd, 'w') as f:
            json.dump(data, f, indent=2); f.write('\n'); f.flush(); os.fsync(f.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name): os.unlink(name)


def git(*args):
    return subprocess.check_output(['git', *args], cwd=ROOT, text=True).strip()


def protected_snapshot():
    # Includes untracked and ignored originals; never read environment/credential files.
    result = {}
    for directory in ('backend', 'frontend'):
        for p in sorted((ROOT / directory).rglob('*')):
            if any(x in ('node_modules', '__pycache__', '.venv', 'dist', '.cache') for x in p.parts): continue
            if p.name.startswith('.env') and p.name != '.env.example': continue
            if p.is_symlink(): value = 'symlink:' + os.readlink(p)
            elif p.is_file(): value = hashlib.sha256(p.read_bytes()).hexdigest()
            else: continue
            result[str(p.relative_to(ROOT))] = value
    return result


def evidence_ok(path, revision, task, required):
    try: data = json.loads(path.read_text())
    except (OSError, ValueError): return False
    if not isinstance(data, dict): return False
    if data.get('revision') != revision or data.get('task') != task: return False
    return all(data.get(key) is True for key in required)


def run_command(argv, log, timeout):
    # No shell expansion. An agent is trusted code with host privileges, not sandboxed here.
    with log.open('wb') as out:
        p = subprocess.Popen(argv, cwd=ROOT, stdout=out, stderr=subprocess.STDOUT, start_new_session=True)
        try: return p.wait(timeout=timeout)
        except (subprocess.TimeoutExpired, KeyboardInterrupt):
            import signal
            os.killpg(p.pid, signal.SIGTERM)
            try: p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(p.pid, signal.SIGKILL); p.wait()
            raise RuntimeError('Command interrupted or timed out; inspect log before resuming')


def revision_digest():
    # Evidence is tied to the complete tracked/untracked migration input, not a stale HEAD.
    h = hashlib.sha256()
    for directory in ('wordpress/sender-suite', 'wordpress/tools', 'wordpress/tests'):
        for p in sorted((ROOT / directory).rglob('*')):
            if not p.is_file() or '__pycache__' in p.parts: continue
            if p.name.endswith(('.log', '-php74.json', '-php83.json')) or p.name == 'checks-summary.json': continue
            h.update(str(p.relative_to(ROOT)).encode()); h.update(b'\0'); h.update(p.read_bytes())
    return h.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--worker', help='JSON argv array for an installed trusted coding agent; {prompt} is replaced with the prompt file path')
    parser.add_argument('--check', action='store_true', help='Check configuration only; do not invoke a worker')
    parser.add_argument('--max-attempts', type=int, default=3)
    parser.add_argument('--timeout', type=int, default=14400)
    args = parser.parse_args()
    if args.max_attempts < 1 or args.timeout < 1: parser.error('Positive attempts/timeout required')
    HOME.mkdir(parents=True, exist_ok=True)
    with (HOME / 'run.lock').open('w') as lock:
        try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: raise RuntimeError('Another migration runner owns this checkout')
        backlog = json.loads((HOME / 'backlog.json').read_text())
        if git('branch', '--show-current') != BRANCH or backlog['branch'] != BRANCH:
            raise RuntimeError('Wrong branch; refusing to switch or create branches')
        worker = json.loads(args.worker or os.environ.get('MIGRATION_WORKER_ARGV', 'null'))
        if not isinstance(worker, list) or not worker or not all(isinstance(x, str) for x in worker):
            print(json.dumps({'ok': False, 'status': 'blocked', 'reason': 'No trusted coding worker configured. Coordinator alone cannot implement code.', 'backlog': str(HOME / 'backlog.json')})); return 2
        import shutil
        if not shutil.which(worker[0]): raise RuntimeError('Configured coding worker executable is not installed')
        if not any('{prompt}' in x for x in worker): raise RuntimeError('Worker must accept the {prompt} file argument')
        if args.check:
            print(json.dumps({'ok': True, 'status': 'configured_not_started', 'worker': worker[0], 'tasks': len(backlog['tasks'])})); return 0
        state_path = HOME / 'state.json'
        state = json.loads(state_path.read_text()) if state_path.exists() else {'version': 1, 'completed': [], 'attempts': {}}
        if not isinstance(state, dict) or state.get('version') != 1 or not isinstance(state.get('completed'), list) or not isinstance(state.get('attempts'), dict):
            raise RuntimeError('Malformed state; inspect it before resuming')
        original = protected_snapshot()
        baseline_head = git('rev-parse', 'HEAD')
        def invariant():
            if git('branch', '--show-current') != BRANCH or git('rev-parse', 'HEAD') != baseline_head:
                raise RuntimeError('Worker changed branch/HEAD. Stopping without resetting user work.')
            if protected_snapshot() != original:
                raise RuntimeError('Worker changed original application. Stopping without destructive rollback.')
        for task in backlog['tasks']:
            if task['id'] in state['completed']: continue
            for attempt in range(args.max_attempts):
                invariant()
                run_id = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ') + '-' + task['id']
                folder = HOME / 'runs' / run_id; folder.mkdir(parents=True)
                report = folder / 'completion.json'
                prompt = folder / 'task.txt'
                prompt.write_text('Implement the entire task, not a plan. Read wordpress/docs/COMPLETION_CHECKLIST.md and source contracts.\n'
                    'Only modify wordpress/. Original backend/frontend must stay untouched. Never read secrets, contact production services or send real messages.\n'
                    'Never commit, push, switch branches or alter runner/backlog/state. Do not claim certification from mocks.\n'
                    'Continue implementing and testing until this whole task passes. Preserve user changes.\n'
                    + json.dumps(task) + '\nPrevious attempt logs are in wordpress/automation/runs/.\n'
                    'When substantively complete write JSON to ' + str(report) + ' with task=' + task['id']
                    + ', complete=true and revision equal to migration_runner.revision_digest() (import from wordpress/tools). '
                    'The coordinator independently runs offline checks. For native-qualification/release include native_install, native_http, '
                    'native_mysql, browser, concurrency, recovery, security and all_contracts_certified=true only when proven by fresh actual test results.\n')
                state.update(status='running', task=task['id'], run=run_id)
                state['attempts'][task['id']] = state['attempts'].get(task['id'], 0) + 1
                atomic(state_path, state)
                try:
                    code = run_command([x.replace('{prompt}', str(prompt)) for x in worker], folder / 'worker.log', args.timeout)
                    invariant()
                except (RuntimeError, OSError) as exc:
                    state.update(status='blocked', last_error=str(exc)); atomic(state_path, state); raise
                if code != 0:
                    state['last_error'] = 'Worker exit ' + str(code); atomic(state_path, state); continue
                required = ['complete']
                if task['id'] in ('native-qualification', 'release'):
                    required += ['native_install', 'native_http', 'native_mysql', 'browser', 'concurrency', 'recovery', 'security', 'all_contracts_certified']
                if not evidence_ok(report, revision_digest(), task['id'], required):
                    state['last_error'] = 'Missing, stale or incomplete task evidence'; atomic(state_path, state); continue
                try:
                    check_code = run_command([sys.executable, 'wordpress/tools/run_checks.py'], folder / 'checks.log', args.timeout)
                    invariant()
                except (RuntimeError, OSError) as exc:
                    state.update(status='blocked', last_error=str(exc)); atomic(state_path, state); raise
                if check_code != 0:
                    state['last_error'] = 'Independent regression checks failed'; atomic(state_path, state); continue
                # Worker attestations are not independent release certification. Never auto-package on them.
                if task['id'] in ('native-qualification', 'release'):
                    state.update(status='blocked', last_error='Independent native/browser/release review required; worker self-attestation cannot certify release')
                    atomic(state_path, state); print(json.dumps(state)); return 2
                state['completed'].append(task['id']); state.pop('last_error', None); atomic(state_path, state)
                break
            else:
                state.update(status='blocked', last_error=state.get('last_error', 'Attempts exhausted'))
                atomic(state_path, state); print(json.dumps(state)); return 2
        state['status'] = 'awaiting_independent_release_review'; atomic(state_path, state); print(json.dumps(state)); return 2


if __name__ == '__main__':
    try: sys.exit(main())
    except (RuntimeError, ValueError, OSError, subprocess.SubprocessError) as exc:
        print(json.dumps({'ok': False, 'status': 'blocked', 'reason': str(exc)}), file=sys.stderr); sys.exit(2)
