# Agentic LaTeX Template

A multi-paper LaTeX workspace with risk-scoped command agents, whole-file Markdown research targets, durable agent memory, and incremental PDF text caching.

## Repository layout

```text
agent.py                 Thin command entrypoint
metasrc/                 Handler implementation and architecture documentation
agents/                  JSON command-agent definitions
agent-data/memory/       Durable memory registry and managed records
agent-data/scratch/      Git-ignored agent scratch area
papers/template/         Migrated example paper and independent build root
markdown/                User-authored Markdown research notes
reports/                 Handler-written green-agent reports
style/                   Repository-wide LaTeX style guidance
merge/                   Optional merge staging area
.pdf-cache/              Generated PDF text cache
```

A paper is a non-symlink direct child of `papers/`, has a name matching `[A-Za-z0-9][A-Za-z0-9_-]*`, and contains a regular `main.tex`. The handler never selects `template` implicitly.

## Build a paper

Requirements include a LaTeX distribution with `latexmk`, `biblatex`, BibTeX, TikZ/PGF, and the packages imported by `main.tex`.

```powershell
cd papers/template
latexmk -pdf -interaction=nonstopmode -file-line-error main.tex
```

Every paper builds independently from its own directory. Auxiliary files and bibliography state are not shared across papers. The migrated template keeps reusable chapter, figure, and element examples beneath its own directory.

Local reference PDFs belong in `papers/<paper>/references/` and remain ignored except for the directory README. Update each paper's `\setlocalreferenceuriprefix` when its absolute checkout location changes.

## Command agents

List all agents and their generated parameter help:

```powershell
python agent.py --help
python agent.py --agent proof-reviewer --help
```

Repeat `--p` with ordered bare values or named assignments. These forms can be mixed.

```powershell
python agent.py --agent merge --p "template" --validate-only
python agent.py --agent evaluate-references --p "paper=template" --validate-only
python agent.py --agent proof-reviewer --p "id=thm:template-convergence" --p "paper=template" --validate-only
python agent.py --agent proof-reviewer --p "id=markdown/ideas/convergence.md" --validate-only
```

Paper-wide agents `merge`, `refine-tex-directory`, `evaluate-references`, `prelim-finder`, and `todo-reviewer` require an explicit `paper`. The four mathematical target agents use `math_target`:

- A TeX-label target requires `paper`. Before accepting it, the handler indexes every TeX file in every discovered paper. Any duplicate label anywhere blocks label-target execution; a unique label must belong to the selected paper.
- A value ending in `.md` or `.markdown` resolves to a readable regular file beneath `markdown/` and rejects `paper`. The entire note is the target. Notes may contain multiple mathematical objects or no clearly identifiable target, so result reliability is intentionally limited; there is no generated index or object extraction.

## Risk and write boundaries

- Green agents return Markdown reports under `reports/<agent-id>/`.
- Yellow agents return a root README or JSON definition that the handler validates and writes.
- Red agents directly edit one explicitly resolved paper after confirmation.

Every model process may write beneath `agent-data/` and is read-only elsewhere. A red process receives one additional writable root: its selected paper. The repository root, other papers, `reports/`, root `README.md`, and `agents/` are never granted as model-writable roots. Handler-owned outputs are written only after return validation. The provider working directory is `agent-data/` for green/yellow agents and the resolved paper for red agents.

Every actual launch creates a persistent Codex chat and prints the lowest available numeric session ID. Green report and yellow launches continue in the background by default. Red non-worktree launches attach by default, and `--foreground` explicitly attaches any single launch. Closing an attached terminal does not stop the agent.

List or reopen living agents at any time:

```powershell
python agent.py --sessions
python agent.py --session 2
python agent.py --interrupt 2
python agent.py --delete 2
python agent.py --delete 2 --force
```

An attached console prints the full user-agent transcript and streams complete reasoning-summary updates into terminal scrollback without fixed-width clipping. The accumulated summary remains with the live session across supervisor restarts. Keyboard input is handled independently of the 500 ms status/spinner refresh, buffered characters are consumed immediately, and typed steering text remains visible while a turn executes. Ordinary text steers an executing turn or starts a new turn for an incomplete session. `/interrupt`, `/delete`, and `/exit` control the session. One-shot deletion asks for confirmation; non-interactive deletion requires `--force`.

Agents finish each turn with a structured `completed` or `not completed` envelope. Only a self-contained `completed` message that passes the existing output validator is committed. Incomplete, interrupted, failed, malformed, or artifact-invalid turns remain available for follow-up and do not create a final handler-owned artifact. Certified sessions delete their provider chat automatically and release their numeric ID; minimal receipts remain under `.agent-runs/receipts/`.

Report-only batches launch independent persistent sessions concurrently and record UUID-keyed results under `.agent-runs/batches/`. Red and yellow workflows may offer an isolated worktree. Worktree creation accepts a dirty source checkout: it creates a normal local-`HEAD` worktree and overlays the complete task-visible source state, including staged, unstaged, untracked, ignored, and deleted files. Root `.git` and `.agent-runs` are excluded. No source commit or stash is created, and the resulting worktree is preserved for review after completion or deletion.

## Maintenance and PDF cache

Every new provider turn runs registered maintenance after validation and immediately before `turn/start`; steering an active turn does not rerun maintenance. Help and `--validate-only` do not run maintenance or start the session supervisor. The initial ordered registry contains only `pdf-cache`.

Run maintenance manually:

```powershell
python agent.py --maintenance
```

For cache diagnostics, the migrated implementation also has a module entrypoint:

```powershell
python -m metasrc.maintenance.pdf_cache
```

The cache scans the active workspace, writes `.pdf-cache/manifest.json`, and stores content-addressed text and metadata under `.pdf-cache/sha256/`. Normal maintenance uses metadata-fast reuse with `verify=False`, `force=False`, and `prune=True`; a current cache is not rehashed, re-extracted, or rewritten. Invocations sharing a cache directory serialize maintenance and rerun the incremental check after acquiring the lock.

Agents must use cached text for source PDFs and may directly inspect a PDF only to verify a PDF they created or updated during the current task, as defined in `AGENTS.md`.

## Durable agent data

`agent-data/memory/ledger.yaml` is the canonical memory entrypoint. Agents must follow its reread, merge, correction-history, and no-op rules. Scratch files go under `agent-data/scratch/`; permission to write there does not broaden a task's final-output contract.

`markdown/` is the complete research-note database for this release. It contains user-authored notes only. There is no research JSON schema, generated index, watcher, local-model pipeline, segmentation, embedding store, or semantic matcher.

## Handler architecture

Root `agent.py` imports and invokes `metasrc.cli.main`. The modules beneath `metasrc/` separately own definitions, typed inputs and labels, persistent session state and IPC, the Codex app-server adapter, handler outputs/receipts/worktrees, orchestration, and generic maintenance. See [`metasrc/README.md`](metasrc/README.md) for extension contracts and transaction order.

## Verification

```powershell
python -B -m unittest discover -s tests -v
python -B agent.py --maintenance
python -B agent.py --sessions
python -B agent.py --agent merge --p "template" --validate-only
python -B agent.py --agent refine-tex-directory --p "paper=template" --validate-only
python -B agent.py --agent evaluate-references --p "paper=template" --validate-only
python -B agent.py --agent proof-reviewer --p "id=thm:template-convergence" --p "paper=template" --validate-only
```

The default suite uses a fake JSONL app server. To exercise the installed authenticated Codex app server with a disposable thread that is deleted by the test:

```powershell
$env:AGENT_SESSION_INTEGRATION = "1"
python -B -m unittest tests.test_live_session_integration -v
```
