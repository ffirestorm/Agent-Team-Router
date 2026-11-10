---
name: agent-team-router
description: Configure project-local Codex teams, explain task difficulty and model allocation, diagnose or roll back configuration, and run explicitly requested bounded engineering tasks with ATR.
---

Use the real `atr` CLI. Check `atr --version`: V0.1 only generates configuration;
the V0.2 development CLI also supports bounded single-task execution. ATR does not
intercept arbitrary host calls. Model tiers are heuristics; do not promise quota savings.

Resolve the user's target project. Commands accept `--project /absolute/project`.
If ATR is unavailable, explain installation with `python -m pip install agent-team-router`
only if that package has actually been published; for a source checkout use
`python -m pip install /absolute/path/to/Agent-Team-Router` instead.

- Inspect presets with `atr profile list` and project health with `atr doctor`.
- For a new setup, run `atr init --profile economical` for limited resources or
  `atr init --profile code-review` for source understanding and call-chain tracing.
  Ask about actual model access when uncertain; `--models` restricts the catalog.
- For initialized projects, use `atr profile use PROFILE`.
- Use `atr plan "TASK"` to obtain model, effort, alternatives, and reasons. It only
  suggests allocation; the host decides whether and how to delegate.
- Inspect `atr apply --dry-run`. Apply with `atr apply` when configuration writes
  are within the user's requested scope. Report actual CLI output and failures.
- Install this skill locally with `atr skill install`. Use `atr rollback` to undo
  the latest application or skill installation. `atr recover` handles interrupted writes.
- For explicitly requested engineering execution in V0.2, prepare a task JSON with
  description, allowed paths, baseline argv and independent JSON verifier argv.
  Inspect `atr run TASK.json --dry-run`. Only `--execute` invokes models. Add
  `--allow-escalation` only when automatic upgrades are in the requested scope.
  A request for configuration or model advice alone is not an execution request.
- Inspect `atr runs show ID` and `atr runs report`. Candidate changes stay in an
  isolated snapshot with a patch; review them before integrating. `runs cancel ID`
  requests cancellation; `runs recover ID` never automatically reexecutes a model.

Only report success after the CLI succeeds. Do not edit global Codex config or auth.
Do not bypass conflicts, strict validation, or modified-file rollback protection.
Explain that bundled/manual catalog recognition and static validation do not prove
account access, model execution, or effective permissions under administrator policy.
In a code-review team keep the main agent read-only as well as child roles.
