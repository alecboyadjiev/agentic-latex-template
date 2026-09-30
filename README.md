# Agentic LaTeX Template

## What This Repository Is

This repository provides a structured LaTeX writing template for theorem-driven mathematical documents. It combines a reusable chapter skeleton, custom theorem/proof styling, modular TikZ figure components, and an integrated TODO system for drafting workflows.

## Core Features

- A complete article scaffold with standing notation and assumptions, local lemmas/propositions/corollaries, a main theorem section, and appendix + notation glossary patterns
- A single compile entrypoint at [`paper/main.tex`](paper/main.tex), keeping all manuscript sources and build outputs inside `paper/`
- [`paper/style.sty`](paper/style.sty): theorem environments, `cleveref` setup, hyperlink styling, abstract formatting
- [`paper/extra.sty`](paper/extra.sty): TikZ helpers, math utility commands, TODO/list-of-todos system
- Reusable figure architecture via wrappers in `paper/figures/examples/` and drawing primitives in `paper/elements/examples/`
- Bibliography setup with `biblatex` (`backend=bibtex`) using [`paper/refs.bib`](paper/refs.bib)
- Optional local-reference PDF workflow via `file` fields in [`paper/refs.bib`](paper/refs.bib) and files in [`paper/references/`](paper/references/)
- Risk-scoped JSON agents in [`agents/`](agents/) executed through [`agent.py`](agent.py)
- Standalone analysis output in the repository-root [`reports/`](reports/)
- A single LaTeX-writing authority at [`style/latex_style_guide.txt`](style/latex_style_guide.txt)

## Repository Layout

```text
.
|-- README.md
|-- agent.py                 # Risk-scoped agent runner and artifact handler
|-- pdf_cache.py             # Incremental searchable-text cache for PDFs
|-- agents/                  # JSON command-agent definitions
|-- reports/                 # Handler-written Markdown analysis, grouped by agent
|-- paper/
|   |-- main.tex              # Sole compile entrypoint
|   |-- style.sty
|   |-- extra.sty
|   |-- refs.bib
|   |-- references/
|   |   `-- README.md         # Guidance for local reference PDFs
|   |-- chapters/
|   |   `-- examples/
|   |       |-- core_template.tex
|   |       `-- appendix.tex
|   |-- figures/
|   |   `-- examples/         # Figure wrappers used by chapters
|   `-- elements/
|       `-- examples/         # Reusable TikZ drawing building blocks
`-- style/
    `-- latex_style_guide.txt
```

`src/`, `experiments/`, and `merge/` are active working directories (tracked with `.gitkeep` until you add project content). `merge/` is the staging area for automated LaTeX manuscript transfer.

## Requirements

- A LaTeX distribution with `pdflatex`, `bibtex`, and `latexmk`
- MiKTeX or TeX Live full installs are recommended
- Python 3.10+ and the Codex CLI are required for `agent.py`
- `pypdf` is required for `pdf_cache.py` (`python -m pip install pypdf`)
- No Node or Cargo dependency setup is required

## Build / Compile

Build from `paper/`:

```powershell
cd paper
latexmk -pdf -interaction=nonstopmode -file-line-error main.tex
```

Output: `paper/main.pdf`.

### PDF text cache

Update the searchable-text cache for every PDF anywhere in the repository:

```powershell
python pdf_cache.py
```

The generated `.pdf-cache/` directory is ignored by Git. Normal runs use file size and nanosecond modification/change timestamps to avoid reading or hashing unchanged PDFs. A changed file alone is hashed and re-extracted; unchanged and duplicate content reuses its existing content-addressed cache object. Use `python pdf_cache.py --verify` for a full hash check or `--force` to re-extract everything.

### Troubleshooting stale build state

If you hit auxiliary-file or bibliography-state errors, clean and rebuild from `paper/`:

```powershell
cd paper
latexmk -C main.tex
latexmk -pdf -interaction=nonstopmode -file-line-error main.tex
```

## Authoring Workflow

1. Treat `paper/*/examples/` as reference templates. Create manuscript files in non-`examples/` paths such as `paper/chapters/`, `paper/figures/`, and `paper/elements/`.
   - Per [`style/latex_style_guide.txt`](style/latex_style_guide.txt), do not modify files under any `examples/` subfolder unless explicitly requested.
2. Copy from [`paper/chapters/examples/core_template.tex`](paper/chapters/examples/core_template.tex) and [`paper/chapters/examples/appendix.tex`](paper/chapters/examples/appendix.tex) into your active chapter files.
3. Update includes in [`paper/main.tex`](paper/main.tex) using `\include{chapters/<your-file>}`.
4. Add or modify figures using wrapper files in `paper/figures/` and reusable TikZ primitives in `paper/elements/`.
5. Update bibliography entries in [`paper/refs.bib`](paper/refs.bib), then rebuild with `latexmk`.

## Agent System

This repository is designed to be used as an agentic workspace.

Use `agent.py` for every agent workflow. It validates parameters, renders them into the selected JSON prompt, applies the declared risk boundary, and records execution logs under `.agent-runs/`.

- Green: the agent is read-only and returns a Markdown report written under `reports/<agent-id>/`.
- Yellow: the agent is read-only, but its returned artifact is written into the repository by the handler: root `README.md` or a JSON definition under `agents/`. A yellow warning appears and worktree creation is offered with a default of no.
- Red: the agent has genuine direct write access rooted at `paper/`. A red warning shows the writable location and requires `Y`; an isolated worktree is then recommended and selected by default. Without a worktree, red agents run synchronously in the current checkout. With a worktree they run asynchronously.

All parameters are mandatory unless their JSON specification declares a default. Repeat `--p` with bare values to fill parameters in their displayed order, or use `--p "id=value"` to name one explicitly; named and positional forms may be mixed. Run `python agent.py --help` for the complete prompt catalog, or `python agent.py --agent <id> --help` for one prompt's ordered parameter contract and copyable commands. `agent.py` checks definitions, defaults, runtime values, lengths, regexes, choices, and unknown fields before Codex starts. The `tex_label` type requires exactly one active matching label under `paper/`; `repo_file` requires a readable repository-relative file inside the active workspace and can restrict allowed suffixes. Prompts expose only the task-facing guarantee `All supplied inputs are valid; use them directly.` Green and yellow prompts immediately add `You are read-only; do not attempt writes.`

Each Markdown report is saved automatically as `reports/<agent-id>/DD-MM (N).md`. The daily sequence number is reserved atomically, so simultaneous processes cannot choose the same filename. A hyphen separates day and month because `/` is not valid in Windows filenames.

Examples:

```powershell
python agent.py --agent agentic-advisor
python agent.py --agent conjecture-evaluator --p "conj:template-global-upgrade"
python agent.py --agent proof-reviewer --p "thm:template-convergence"
python agent.py --agent proof-revisor --p "thm:template-convergence" --p "Replace the compactness assumption with sequential compactness."
python agent.py --agent proof-engine --p "thm:template-convergence"
python agent.py --agent prelim-finder
python agent.py --agent evaluate-references
python agent.py --agent todo-reviewer
python agent.py --agent readme-updater
python agent.py --agent merge
python agent.py --agent refine-tex-directory
```

Green Markdown reports and yellow file updates detach automatically unless `--foreground` is supplied. Calling another command immediately starts another independent instance. The printed `manager.log` path is a live, unbuffered activity log containing lifecycle phases, streamed Codex output, validation, writes, failures, and completion details. Worktree creation requires a clean checkout because the worktree starts from `HEAD`. Agent changes in a worktree are never committed automatically; inspect and commit them manually before merging the generated branch.

Use `--validate-only` to check an agent definition, its parameters, label resolution, and destination without running Codex.

To run report agents concurrently, put the jobs in a JSON file:

```json
[
  {
    "agent": "proof-reviewer",
    "params": {"id": "thm:template-convergence"}
  },
  {
    "agent": "proof-engine",
    "params": {"id": "thm:template-convergence"}
  },
  {
    "agent": "prelim-finder",
    "params": {}
  }
]
```

Then launch it:

```powershell
python agent.py --batch report-jobs.json
```

Batch mode accepts only Markdown-report agents, starts every listed job concurrently with no configured cap, and detaches immediately so the terminal remains available. Progress logs and the final `results.json` are written below `.agent-runs/batches/`; use `--foreground` to wait in the current terminal.

Create and revise JSON definitions through the meta-agents:

```powershell
python agent.py --agent prompt-engineer --p "my-reviewer" --p "Create a focused read-only reviewer that returns a Markdown report."
python agent.py --agent prompt-revisor --p "my-reviewer" --p "Reports miss concrete evidence anchors and repeat repository inventory."
```

`merge` and `refine-tex-directory` are the only direct-write agents. Their LaTeX instructions use [`style/latex_style_guide.txt`](style/latex_style_guide.txt), the repository's sole LaTeX-writing guide.

## LaTeX Conventions

By default, keep files inside any `examples/` subfolder unchanged and use them as templates.

### Draft Marker System

The template includes margin-note TODO and preliminary marker mechanisms with generated lists.

- Add TODOs with `\todo{Replace placeholder argument with domain-specific theorem.}` or `\todo[Short task caption]`
- Print collected TODOs with `\listoftodos`
- Add preliminary markers for external references with `\prelim{Confirm notation convention against source.}{\cite{rudin1976principles}}`
- Print collected preliminary markers with `\listofprelims`
- Hide draft markers globally with `\hidetodos` and `\hideprelims`; restore them with `\showtodos` and `\showprelims`
- For inline TODOs in sentence text, keep `\todo` directly attached to the previous word (no inserted space) to avoid output spacing artifacts.

Implementation lives in [`paper/extra.sty`](paper/extra.sty).

### Cross-Reference Conventions

- Use `\cref{...}` for equation/theorem-style references.
- Label prefixes used by the template and style guide: `sec:`, `ssec:`, `sssec:`, `eq:`, `def:`, `rem:`, `lem:`, `prop:`, `cor:`, `thm:`, `ex:`, `claim:`, `conj:`, `asm:`, `ctx:`, `app:`.

## Bibliography and References

- Citation package: `biblatex` with numeric style
- Backend: `bibtex`
- Bibliography file: [`paper/refs.bib`](paper/refs.bib)
- Optional local files: `file = {references/<filename>.pdf}` entries in `paper/refs.bib` with files under `paper/references/`
- Recommended default: add local copies when source access is restricted (for example paywalled or unstable links), and keep `paper/references/README.md` aligned with your policy to avoid unnecessary context bloat in agent workflows
- Cross-references use `cleveref` and custom theorem/equation label formatting from [`paper/style.sty`](paper/style.sty)

### Local PDF Link Behavior (Exact Reproduction Steps)

This is the full setup used in this repo to make bibliography `Local copy` links open in the VS Code LaTeX Workshop viewer (not Chrome).

1. Put local PDFs in `paper/references/`.
2. Add a BibTeX `file` field per entry in [`paper/refs.bib`](paper/refs.bib), for example:

```bibtex
@book{tao2011book,
  author = {Tao, Terence},
  title = {An Introduction to Measure Theory},
  year = {2011},
  publisher = {American Mathematical Society},
  file = {references/gsm-126-tao5-measure-book.pdf}
}
```

3. Confirm local-link macros exist in [`paper/extra.sty`](paper/extra.sty):
   - `\showlocalreferences`, `\hidelocalreferences`
   - `\setlocalreferenceuriprefix{...}`
   - bibliography finentry hook that prints `[Local copy]` from `file` field
4. In [`paper/main.tex`](paper/main.tex), enable links and set your absolute machine path prefix:

```tex
\showlocalreferences
\setlocalreferenceuriprefix{https://latex-workshop.local/open-reference?path=c:/Users/<YOUR_USER>/<PATH_TO_REPO>/paper/}
```

5. Patch local LaTeX Workshop extension code (machine-local, version-specific).
   - Extension used here: `james-yu.latex-workshop-10.14.1`
   - File A: `C:\Users\<YOU>\.vscode\extensions\james-yu.latex-workshop-10.14.1\out\src\preview\viewer.js`
   - Replace `case 'external_link'` so `https://latex-workshop.local` routes to local file open in viewer:

```js
case 'external_link': {
    const uri = vscode.Uri.parse(data.url);
    const openInVsCodeViewer = (filePath) => {
        const normalized = filePath.replace(/^\/+/, '').replace(/\//g, '\\');
        const targetUri = vscode.Uri.file(normalized);
        void vscode.commands.executeCommand('vscode.openWith', targetUri, 'latex-workshop-pdf-hook');
    };
    if (uri.scheme === 'https' && uri.authority.toLowerCase() === 'latex-workshop.local') {
        const query = new URLSearchParams(uri.query);
        const filePath = query.get('path');
        if (filePath) {
            openInVsCodeViewer(filePath);
            break;
        }
    }
    if (uri.scheme === 'vscode' && uri.authority === 'file') {
        const filePath = decodeURIComponent(uri.path.replace(/^\//, ''));
        openInVsCodeViewer(filePath);
    }
    else if (uri.scheme === 'file') {
        const localPath = decodeURIComponent(uri.fsPath || uri.path);
        openInVsCodeViewer(localPath);
    }
    else if (['http', 'https'].includes(uri.scheme)) {
        void vscode.env.openExternal(uri);
    }
    else {
        void vscode.window.showInputBox({
            prompt: 'For security reasons, please copy and visit this link manually.',
            value: data.url
        });
    }
    break;
}
```

6. Patch local LaTeX Workshop viewer click interception.
   - File B: `C:\Users\<YOU>\.vscode\extensions\james-yu.latex-workshop-10.14.1\out\viewer\components\gui.js`
   - Ensure external-link interception is active in all modes and uses `closest('a')`:

```js
document.addEventListener('click', (e) => {
    const rawTarget = e.target;
    const anchor = rawTarget instanceof Element ? rawTarget.closest('a') : null;
    if (anchor && !anchor.href.startsWith(window.location.href) && !anchor.href.startsWith('blob:')) {
        void send({ type: 'external_link', url: anchor.href });
        e.preventDefault();
        e.stopPropagation();
    }
}, true);
```

7. Reload VS Code and verify:
   - Run `Developer: Reload Window`
   - Close/reopen LaTeX Workshop PDF preview
   - Click a `Local copy` link in bibliography
   - Expected: PDF opens in VS Code viewer tab; no `latex-workshop.local` browser tab, no DNS error

8. Debug checks if it fails:
   - Confirm active extension version path under `C:\Users\<YOU>\.vscode\extensions\`.
   - Confirm [`paper/main.tex`](paper/main.tex) URI prefix points to your actual repo path.
   - Confirm clicked entry has a valid `file = {references/...pdf}` field and PDF exists.
   - Re-run `Developer: Reload Window` after every extension-file edit.

Important:
- These extension patches are not stored in this repo and will not transfer via `git push`.
- Extension updates can overwrite patched files; reapply patches after updates.

## Start-up Guide

To move from a plain LaTeX `.zip` project and source code / experiment results into this agentic template:

1. Unpack your source project into `merge/`.
2. Run `python agent.py --agent merge` and accept the red warning. Use the recommended worktree when the checkout is clean.
3. After the merge run reports success, validate the transfer result in `paper/` manually (content placement and compile status).
4. Run `python agent.py --agent refine-tex-directory` to enforce repository-safe manuscript organization under `paper/`.
5. Manually copy source code into `src/` and experiment assets into `experiments/` when the paper depends on them.
6. Run `python agent.py --agent readme-updater` to refresh repository documentation.
7. Use the JSON agents above for literature, conjecture, and proof analysis; their reports stay outside the manuscript in `reports/`.

## Cleaning Build Artifacts

Run clean from `paper/`, the sole build directory:

```powershell
cd paper
latexmk -C main.tex
```

## Notes / Limitations

- Generated LaTeX artifacts are partially covered by [`.gitignore`](.gitignore).
- No CI pipeline, container configuration, or deployment manifests are currently defined in this repository.
- No `LICENSE` file is currently present.
