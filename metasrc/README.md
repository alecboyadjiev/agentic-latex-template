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
| `handlers/runs.py` | Run directories, exact prompts, metadata, and manager logs |
| `handlers/worktrees.py` | Registered Git worktree validation and creation |
| `providers/base.py` | Provider-neutral request, response, and protocol types |
| `providers/codex_cli.py` | Codex capabilities, flags, permission roots, and stream capture |
| `maintenance/base.py` | Maintenance result/task protocols and ordered default registry |
| `maintenance/pdf_cache.py` | Incremental content-addressed PDF extraction |
| `maintenance/runner.py` | Ordered execution and per-cache interprocess locking |
| `orchestration.py` | End-to-end stages without provider-specific subprocess flags |
| `cli.py` | Arguments, help, warnings, foreground/detached dispatch, batches, manual maintenance |

`errors.py` contains the shared user-facing handler exception.

## Invocation sequence

1. Load and validate a definition.
2. Parse ordered or named values and validate model settings.
3. Resolve the explicit paper and/or mathematical target.
4. Resolve handler output, writable roots, and working directory.
5. Render the stored prompt and append typed handler-owned runtime context.
6. Construct and validate the provider-neutral `ModelRequest`.
7. Stop here for `--validate-only`, without a run directory or maintenance.
8. Create run metadata and logs.
9. Run each registered maintenance task in order.
10. Record maintenance results and immediately call the provider.
11. Validate and atomically write handler-owned returned artifacts.
12. Finalize success or failure metadata.

A new provider implements `validate(ModelRequest)` and `invoke(ModelRequest) -> ModelResponse`. It owns model/capability checks and subprocess behavior. Writable roots must be existing, canonical, non-symlink, non-overlapping directories beneath the active workspace. All agents receive `agent-data/`; only red agents also receive the selected paper. The working directory equals one approved root so the repository itself is never writable.

## Definitions and inputs

Definitions declare `id`, `risk`, `description`, `params`, `prompt`, and `output`; red definitions additionally declare `write_scope: papers/{paper}`. Prompts start with the canonical valid-input line and risk-specific agent-data permission line. Handler-owned resolved context is appended at runtime and saved exactly in `prompt.txt`.

To add an input type, register its schema keys in `definitions.py`, implement scalar validation in `validation/inputs.py`, and add cross-field resolution to `validate_inputs`. Return typed context rather than unchecked paths. Tests belong in `tests/test_definitions.py` and `tests/test_inputs.py`; label and paper cases belong in `tests/test_labels.py`.

`paper_name` resolves a discovered direct child of `papers/`. `math_target` branches by case-insensitive `.md`/`.markdown` suffix. Markdown targets are whole readable files beneath `markdown/` and cannot take `paper`. Other values are labels, require `paper`, and trigger a global duplicate-label check before selected-paper matching.

The whole-note branch intentionally performs no Markdown parsing or indexing. A note with several objects or no obvious object is accepted but can yield an unreliable task result.

## Outputs, permissions, and maintenance

Markdown reports, README replacements, and JSON definitions are handler-owned. Red direct manuscript edits are model-owned within the resolved paper. `agent-data/` is available for scratch, ledger-governed memory, and explicitly defined state; it never authorizes a final artifact outside the definition contract.

Maintenance tasks return `unchanged`, `updated`, or `failed` with timestamps, duration, and details. Add tasks through the ordered registry in `maintenance/base.py`; providers remain unaware of them. A failure prevents invocation and is recorded in the run log and metadata. The PDF task always uses the active workspace and `<workspace>/.pdf-cache` with pruning enabled and verification/force disabled.

Expected user errors raise `AgentError`; maintenance failures include a structured failed result; provider failures cover invalid capabilities, nonzero exits, and missing final messages. Unit coverage is organized by definitions, inputs, labels, permissions, maintenance, outputs, and CLI behavior; fake providers should prove ordering without starting Codex.

