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
- Report output workspace at [`paper/reports/`](paper/reports/) for proof/conjecture task prompts
- AI-oriented authoring guide in [`style/latex_style_guide.txt`](style/latex_style_guide.txt)
- Prompt assets for repository workflows in [`prompts/`](prompts/)
- Repository state ledgers at [`state/current-state.txt`](state/current-state.txt) and [`state/changelog.txt`](state/changelog.txt) for snapshot-style repository audits

## Repository Layout

```text
.
|-- README.md
|-- paper/
|   |-- main.tex              # Sole compile entrypoint
|   |-- style.sty
|   |-- extra.sty
|   |-- refs.bib
|   |-- references/
|   |   `-- README.md         # Guidance for local reference PDFs
|   |-- reports/
|   |   `-- .gitkeep
|   |-- chapters/
|   |   `-- examples/
|   |       |-- core_template.tex
|   |       `-- appendix.tex
|   |-- figures/
|   |   `-- examples/         # Figure wrappers used by chapters
|   `-- elements/
|       `-- examples/         # Reusable TikZ drawing building blocks
|-- prompts/
|   |-- 0 - Meta/
|   |-- 1 - Transfer and Maintenence/
|   |-- 2 - Paper Tasks/
|   `-- 3 - Mathematical Tasks/
|-- state/
|   |-- current-state.txt
|   `-- changelog.txt
`-- style/
    `-- latex_style_guide.txt
```

`src/`, `experiments/`, and `merge/` are active working directories (tracked with `.gitkeep` until you add project content). `merge/` is the staging area for automated LaTeX manuscript transfer.

## Requirements

- A LaTeX distribution with `pdflatex`, `bibtex`, and `latexmk`
- MiKTeX or TeX Live full installs are recommended
- No Python/Node/Cargo dependency setup is required for document compilation

## Build / Compile

Build from `paper/`:

```powershell
cd paper
latexmk -pdf -interaction=nonstopmode -file-line-error main.tex
```

Output: `paper/main.pdf`.

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

## Style and Prompt System

This repository is designed to be used as an agentic workspace.

- Prompt assets are grouped by workflow family under [`prompts/`](prompts/):
  - [`prompts/0 - Meta/`](prompts/0%20-%20Meta/)
  - [`prompts/1 - Transfer and Maintenence/`](prompts/1%20-%20Transfer%20and%20Maintenence/)
  - [`prompts/2 - Paper Tasks/`](prompts/2%20-%20Paper%20Tasks/)
  - [`prompts/3 - Mathematical Tasks/`](prompts/3%20-%20Mathematical%20Tasks/)
- Use [`style/latex_style_guide.txt`](style/latex_style_guide.txt) as the primary conventions reference for AI and human edits inside `paper/`.
- Apply the layering rule from the style guide during manuscript refactors: `chapters -> figures -> elements`.
- Use [`state/current-state.txt`](state/current-state.txt) and [`state/changelog.txt`](state/changelog.txt) when maintaining repository state snapshots.
- Style files are user-modifiable and intended to evolve with your authoring preferences.

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
- Label prefixes used by the template and style guide: `sec:`, `ssec:`, `sssec:`, `eq:`, `def:`, `rem:`, `lem:`, `prop:`, `cor:`, `thm:`, `ex:`, `app:`.

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
2. Run [`prompts/1 - Transfer and Maintenence/prompt_merge_tex.txt`](prompts/1%20-%20Transfer%20and%20Maintenence/prompt_merge_tex.txt) with your AI agent. No extra inputs are required.
3. After the merge run reports success, validate the transfer result in `paper/` manually (content placement and compile status).
4. Run [`prompts/2 - Paper Tasks/prompt_refine_tex_directory.txt`](prompts/2%20-%20Paper%20Tasks/prompt_refine_tex_directory.txt) to enforce repository-safe manuscript organization under `paper/`.
5. Run [`prompts/2 - Paper Tasks/prompt_detail_level_evaluator.txt`](prompts/2%20-%20Paper%20Tasks/prompt_detail_level_evaluator.txt) to generate or update `style/detail_level_style_guide.txt` for audience-calibrated exposition depth.
6. Manually copy source code into `src/` and experiment assets into `experiments/` when the paper depends on them.
7. Run [`prompts/1 - Transfer and Maintenence/prompt_readme_update.txt`](prompts/1%20-%20Transfer%20and%20Maintenence/prompt_readme_update.txt) and [`prompts/1 - Transfer and Maintenence/prompt_state_update.txt`](prompts/1%20-%20Transfer%20and%20Maintenence/prompt_state_update.txt) to refresh repository documentation and state ledgers.

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
