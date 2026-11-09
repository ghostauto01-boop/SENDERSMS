# Resumable migration coordinator — infrastructure, NOT a completed app

`backlog.json` records the remaining end-to-end work in execution order. `migration_runner.py` delegates each task to an **installed, trusted external coding worker**, waits for it, independently executes the offline regression suite and proceeds without chat prompts. State and per-attempt logs are retained in this directory (ignored by Git). Keep this directory on persistent storage. Only one coordinator may own a checkout at a time.

**Current environment: blocked. No coding-agent executable is installed/configured. Nothing is running in the background.** This coordinator is not an LLM, does not contain an API key, and cannot implement code on its own. Installing this file does not finish the migration. A persistent execution host and a supported coding worker must actually exist before unattended implementation can run. Arena/GitHub authentication is not automatically a subscription or authentication for a separate coding service. Never put credentials into chat, a command argument, this backlog or Git.

## Worker interface

Configure `MIGRATION_WORKER_ARGV` as a JSON array of executable and arguments for an existing worker. One argument must contain `{prompt}`; the coordinator replaces it with a task-file path. The executable must read that file, implement the task, and exit. There is deliberately no guessed vendor CLI flag or implicit shell expansion. A wrapper may adapt a real installed worker to this interface. Use the worker's supported local authentication, not repository-stored keys.

From the repository root:

```sh
python3 wordpress/tools/migration_runner.py --check
python3 wordpress/tools/migration_runner.py --max-attempts 3 --timeout 14400
```

An unconfigured worker returns exit 2 with a structured blocker. A configured check does **not** launch implementation. The timeout applies separately to each worker/check invocation, not the entire backlog. Attempts are bounded per invocation; previous logs and cumulative counts are retained for recovery. A restart resumes unfinished tasks and does not silently retry forever. Review failures before restarting.

The worker is instructed to modify only `wordpress/`, never commit/push/change branches, read secrets, contact production services or send messages. Original backend/frontend content and branch/HEAD invariants are checked after commands. On violation the runner stops **without** resetting or deleting user changes. This is detection, not containment: an external coding agent has its host account's privileges and must be run in a dedicated isolated checkout with no production secrets/network access. Do not install an untrusted executable.

Logs can contain worker output; protect them as private operator data. Do not upload logs blindly. Each worker receives a new task prompt and must emit the specified completion manifest tied to the migration tree digest. Missing/stale manifests and failed regression commands prevent advancement. Existing partial completion attestations are not source-contract certification.

## Release boundary

Offline tests plus worker self-attestation cannot prove native installation, HTTP, MySQL, browser, concurrent-worker or recovery safety. The coordinator deliberately **stops for independent native/release review** even if the worker claims these passed. It never automatically labels the plugin production-ready, pushes code, sends messages or packages a release. This is a substantive limit, not an implemented one-go completion guarantee.

The proposed `wordpress/tools/native-wordpress-workflow.yml` remains a separate native smoke-test configuration, not full native certification. No workflow has been installed by this coordinator, and no native results have been invented.
