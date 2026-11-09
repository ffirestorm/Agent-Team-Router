---
name: agent-team-router
description: Configure project-local Codex agent teams, choose a resource-conscious or read-only profile, explain model allocation for a task, and diagnose or roll back ATR configuration using the installed atr CLI.
---

Use the real `atr` CLI for configuration and planning. ATR V0.1 creates static native
Codex role definitions; it does not execute tasks, intercept calls, or enforce model
selection at runtime. Model tiers are heuristics; do not promise a Plus quota saving.

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

Only report success after the CLI succeeds. Do not edit global Codex config or auth.
Do not bypass conflicts, strict validation, or modified-file rollback protection.
Explain that bundled/manual catalog recognition and static validation do not prove
account access, model execution, or effective permissions under administrator policy.
In a code-review team keep the main agent read-only as well as child roles.
