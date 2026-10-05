# Handler architecture

`metasrc` implements the command-agent handler. Imports point from the CLI and orchestration layer toward leaf modules; providers and maintenance never import the CLI.

## Module ownership

| Module | Responsibility |
|---|---|
| `paths.py` | Canonical repository roots and workspace path constants |
| `definitions.py` | JSON loading, schema checks, prompt preamble and template validation |
| `validation/inputs.py` | Ordered/named parsing, scalar types, cross-field rules, typed target resolution |
| `validation/labels.py` | Paper discovery, TeX comment stripping, repository-wide label indexing |
| `handlers/outputs.py` | Destination containment, normalization, report reservation, atomic writes |
| `handlers/runs.py` | Atomic JSON helpers and historical one-shot run compatibility |
| `handlers/worktrees.py` | Registered worktree validation and full-state snapshot overlays |
| `providers/base.py` | Provider-neutral thread, turn, conversation, event, and settings types |
| `providers/codex_cli.py` | Shared Codex discovery, model validation, and permission validation |
| `providers/codex_app_server.py` | Persistent JSONL app-server lifecycle and method mapping |
| `sessions/registry.py` | Locked live registry, UUID identity, numeric-ID allocation, and recovery state |
| `sessions/supervisor.py` | Provider ownership, turn lifecycle, certification, cleanup, and loopback IPC |
| `sessions/client.py` | Authenticated client, supervisor bootstrap, and stale-control recovery |
| `sessions/console.py` | Transcript, menu, unclipped progress, responsive input, and slash-command rendering |
| `sessions/completion.py` | Completion schema, defensive parsing, validation, and idempotent commit helpers |
| `maintenance/base.py` | Maintenance result/task protocols and ordered default registry |
| `maintenance/pdf_cache.py` | Incremental content-addressed PDF extraction |
| `maintenance/runner.py` | Ordered execution and per-cache interprocess locking |
| `orchestration.py` | Validated launch preparation and one-shot test compatibility |
| `cli.py` | Arguments, help, warnings, session controls, persistent batches, and maintenance |

`errors.py` contains the shared user-facing handler exception.

The root entrypoint also accepts the internal `AGENT_WRAPPER_ARGS_FILE` handoff used by the Windows PowerShell `a` helper. The file is a UTF-8 JSON array of exact argument strings. This avoids Windows PowerShell 5.1 native-command quoting loss for multiline parameters containing quotes or Unicode; the wrapper owns creation and deletion of the temporary file.

## Persistent invocation sequence

1. Load and validate a definition.
2. Parse ordered or named values and validate model settings.
3. Resolve the explicit paper and/or mathematical target.
4. Resolve handler output, writable roots, and working directory.
5. Render the stored prompt and append typed handler-owned runtime context.
6. Construct and validate the provider-neutral request settings.
7. Stop here for `--validate-only`, without starting the supervisor or running maintenance.
8. Allocate the lowest free numeric ID plus an internal UUID under the registry lock.
9. Create or resume a durable provider thread; save the exact initial prompt in live state.
10. Run each registered maintenance task in order immediately before `turn/start`.
11. Keep interrupted, failed, `not completed`, malformed, and artifact-invalid turns incomplete and resumable.
12. For `completed`, validate and atomically commit the message, durably mark it committed, write a minimal receipt, delete the provider thread, remove live state, and release the ID.

A new interactive provider implements thread start/resume/read/delete, turn start/steer/interrupt, and an event stream using the types in `providers/base.py`. The supervisor owns the provider instance and serializes each session by internal UUID. The Codex adapter sends `approvalPolicy: never` and a `workspaceWrite` sandbox policy whose writable roots are exactly the validated roots. All agents receive `agent-data/`; only red agents also receive the selected paper. The working directory equals one approved root, so the repository itself is never model-writable.

The supervisor binds an ephemeral loopback port and requires the random token in `.agent-runs/control.json` on every request. Live registry state is under `.agent-runs/sessions/`; each session also appends its complete reasoning-summary stream to `reasoning-summary.txt` so consoles can resume without clipping or losing earlier progress after a supervisor restart. Completion receipts are under `.agent-runs/receipts/`. Control data, prompts, transcripts, reasoning summaries, and tokens are never copied into receipts. Certified and hard-deleted sessions disappear from the living-agent menu.

## Definitions and inputs

Definitions declare `id`, `risk`, `description`, `params`, `prompt`, and `output`; red definitions additionally declare `write_scope: papers/{paper}`. Prompts start with the canonical valid-input line and risk-specific agent-data permission line and contain the exact shared completion-contract instruction. Obsolete bare `Return ONLY` instructions are rejected. Handler-owned resolved context is appended at runtime and saved exactly in the live session's `prompt.txt`.

To add an input type, register its schema keys in `definitions.py`, implement scalar validation in `validation/inputs.py`, and add cross-field resolution to `validate_inputs`. Return typed context rather than unchecked paths. Tests belong in `tests/test_definitions.py` and `tests/test_inputs.py`; label and paper cases belong in `tests/test_labels.py`.

`paper_name` resolves a discovered direct child of `papers/`. `math_target` branches by case-insensitive `.md`/`.markdown` suffix. Markdown targets are whole readable files beneath `markdown/` and cannot take `paper`. Other values are labels, require `paper`, and trigger a global duplicate-label check before selected-paper matching.

The whole-note branch intentionally performs no Markdown parsing or indexing. A note with several objects or no obvious object is accepted but can yield an unreliable task result.

## Outputs, permissions, and maintenance

Markdown reports, README replacements, and JSON definitions are handler-owned. Red direct manuscript edits are model-owned within the resolved paper. `agent-data/` is available for scratch, ledger-governed memory, and explicitly defined state; it never authorizes a final artifact outside the definition contract.

Maintenance tasks return `unchanged`, `updated`, or `failed` with timestamps, duration, and details. Add tasks through the ordered registry in `maintenance/base.py`; providers remain unaware of them. A failure prevents invocation and is recorded in the run log and metadata. The PDF task always uses the active workspace and `<workspace>/.pdf-cache` with pruning enabled and verification/force disabled.

Expected user errors raise `AgentError`; maintenance failures include a structured failed result; provider failures cover invalid capabilities, nonzero exits, and missing final messages. Unit coverage is organized by definitions, inputs, labels, permissions, maintenance, outputs, and CLI behavior; fake providers should prove ordering without starting Codex.
