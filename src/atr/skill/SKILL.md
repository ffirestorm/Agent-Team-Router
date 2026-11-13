---
name: agent-team-router
description: Configure project-local Codex teams, explain task difficulty and model allocation, diagnose or roll back configuration, guide lightweight Luna/Terra delegation, install optional native observation, and run explicitly requested bounded tasks with ATR.
---

Use the real `atr` CLI. Check `atr --version`: V0.1 only generates configuration;
V0.2 adds bounded single-task execution; V0.3 adds passive native hooks; V0.4 defaults recording OFF; V0.5 adds lightweight native delegation with narration independent of recording.
Native observation requires one-time installation and Codex hook review. ATR does not
intercept or switch arbitrary host calls. Model tiers are heuristics; do not promise quota savings.

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
- For specifically requested isolated/bounded ATR execution (legacy runtime), prepare a task JSON with
  description, allowed paths, baseline argv and independent JSON verifier argv.
  Inspect `atr run TASK.json --dry-run`. Only `--execute` invokes models. Add
  `--allow-escalation` only when automatic upgrades are in the requested scope.
  An ordinary native development task is not a request for this extra pipeline.
  Use the lightweight native flow below unless the user specifically requests isolated execution.
- Inspect `atr runs show ID` and `atr runs report`. Candidate changes stay in an
  isolated snapshot with a patch; review them before integrating. `runs cancel ID`
  requests cancellation; `runs recover ID` never automatically reexecutes a model.

For normal native development, use the development Profile and `atr apply` when
setup/update is authorized; it installs two writable roles: atr_simple_worker at
the fast mapping (default Luna/low) and atr_worker at balanced (default Terra/medium),
plus an optional read-only explorer. Main model remains the user's Codex choice.
Applicable AGENTS.md explicitly requests useful delegation: clear simple chunks to
simple_worker, normal implementation to worker; tiny/ambiguous/complex/risky work
stays main. Announce planned configured model/role/task, then confirm the actual
native spawn ID, relay actual progress and report the returned result. Do not claim
backend model proof, unrun tests, guaranteed correctness or subscription savings.
Use only necessary context and concise results; avoid duplicate work and concurrent
writes to the same files. Do not add a Reviewer, independent acceptance, automatic
test/repair/upgrade pipeline unless requested. Ordinary required checks still apply.
`atr delegate TASK` is an optional deterministic preview, not an execution tool;
`atr plan` separates native_delegation from the isolated executor's initial policy.
Narration works with recording OFF and without installed hooks. Do not ask the user
to enable recording. Native tools/updated role definitions require host support,
project trust and a new session after configuration changes.

For native conversation observation, install once with `atr native install`
(after `init`), previewable with `--dry-run`. It installs team configuration, this
Skill, managed AGENTS.md instructions and a standalone local hook collector. Explain
that recording defaults OFF, and only an explicit recording request permits `atr native on`
(default summary; `--level detailed` retains delegation metadata). `atr native off`
stops recording; never enable it just to diagnose or complete another task. Also explain
that the user must trust the project and review hooks in Codex `/hooks` or Settings >
Hooks. Do not bypass trust. Installing alone does not prove recording is active.
Use the session/turn/report path supplied by trusted SessionStart/UserPromptSubmit
context. Before authorized useful delegation, announce intended role/model/task,
then use native delegation tools. Follow the installed helper command to record a
short non-secret task label against the returned agent ID; this is internal tool
use, not a command the user must type. Read the current-turn report before the final
answer and summarize observed agents/models and separate independent test evidence.
First check `atr native status`. When recording is off, skip notes/reports and
state that it is off if relevant. Do not mistake historical events for current recording.
When enabled, derive a fresh report with the helper rather than relying on a cached
summary file. If context or events are missing, report incomplete observation. Notes declare
intent; only request hooks supply observed models. Do not fabricate a delegation,
model invocation, success or usage. Respect higher-priority delegation restrictions.
`atr native status`, `sessions`, and `report --session ID [--turn ID]` diagnose local
records. `atr native uninstall` removes recording hooks/instructions, keeping the
team, Skill and historical records. `atr rollback` can undo the installation.

Only report success after the CLI succeeds. Do not edit global Codex config or auth.
Do not bypass conflicts, strict validation, or modified-file rollback protection.
Explain that bundled/manual catalog recognition and static validation do not prove
account access, model execution, or effective permissions under administrator policy.
In a code-review team keep the main agent read-only as well as child roles.

V0.4 management: `atr native health` distinguishes off, absent/stale callbacks and
recent errors. `atr native report --latest [--turn latest] [--agent ID]` queries
without copying long IDs. `--current` requires CODEX_THREAD_ID; use the hook's explicit
session ID if supplied and never guess. `atr native cleanup --older-than-days N`,
`--keep-sessions N` or `--max-mib N` only previews; `--execute` deletes selected history
and requires recording off. Only execute deletion when the user's scope authorizes
that retention criterion; protect recent/unfinished sessions by default. A request
for a report alone is not permission to delete history.

For a natural-language execution request, internally use `atr task draft DESCRIPTION`
with explicit `--allow` paths, `--baseline-json` and `--verify-json` argv arrays.
Missing scope/verification creates a non-executable proposal. Never invent acceptance
or run the draft commands. The optional `--template pytest --acceptance-test PATH`
binds existing immutable tests outside model-write scope; use `--output` in a new
`.atr/tasks/NAME/task.json` directory to materialize the fixed verifier. Review the
contract and `atr run ... --dry-run` before execution within the user's scope.
`atr runs report --by-model` summarizes attempts for ongoing engineering validation;
model groups have unmatched task difficulty and do not establish capability ranking.
