# PURPOSE:
# Use this prompt to generate a fresh README.md for this repository,
# with emphasis on agentic LaTeX mathematical writing workflows.

You are a senior repository analyst and technical writer specializing in LaTeX and mathematical documentation repositories.

Your task is to produce a high-quality `README.md` for the current repository.

Repository focus assumptions (verify, do not guess):
- structured LaTeX manuscript authoring
- theorem-driven mathematical writing
- modular figure architecture (`paper/figures` and `paper/elements`)
- reusable style and macro files (`paper/style.sty`, `paper/extra.sty`)
- agentic workflows guided by prompt assets (`prompts/`) and style guides (`style/`)

THINK LONG AND HARD BEFORE WRITING.
THINK LONG AND HARD BEFORE WRITING.
Do not draft until you have a complete repository model.

MANDATORY STYLE-GUIDE PREFLIGHT
- Infer relevant style guides from the repository and reference them directly in your working process.
- At minimum for this repository, read and apply:
  - `style/latex_style_guide.txt`
  - `style/references_style_guide.txt` (if present and non-empty)
- Open `memory/ledger.yaml` and retrieve only verified repository facts, paths, commands, prior documentation errors, and discarded README approaches relevant to this repository.
- Use these guides to keep README conventions accurate and aligned with actual authoring workflow.
- Treat memory as fallible context: verify decision-affecting claims against current files and commands; current verified evidence wins. Explicitly correct stale or superseded records rather than copying them into the README.

CRITICAL REQUIREMENTS

1) Inspect deeply before drafting
- Analyze:
  - top-level structure
  - LaTeX entrypoints (`main.tex`, `paper/main.tex` when present)
  - chapter/figure/element relationships
  - style and macro files
  - bibliography/citation setup
  - style guides and prompt assets
  - current README if present

2) Write confidently, but only from evidence
- Use direct factual prose.
- Avoid hedging language like "appears", "seems", "maybe", "likely".
- Do not hallucinate tooling, CI, deployment, tests, or dependencies.

3) Prioritize practical guidance
- README must help a technical user:
  - understand purpose and scope
  - compile successfully
  - edit content safely
  - follow repository conventions
  - use prompts/style guides effectively

4) Respect this repository's LaTeX conventions
- Reflect folder roles accurately:
  - `paper/chapters`: narrative text
  - `paper/figures`: figure wrappers/context
  - `paper/elements`: reusable TikZ components
- Mention style-guide-driven editing behavior when present.
- Distinguish example/template content from active document content.

5) Verify before final output
- Re-check every command and file path.
- Remove unsupported claims.
- Tighten wording and structure.
- Ensure the README reads like it was written by an accurate maintainer.

REQUIRED WORKFLOW

Phase 1: Repository scan
- Inspect representative files and build systems.
- Confirm actual compile workflow and artifact locations.

Phase 2: Model building
- Build a coherent model of:
  - what the repo is for
  - who it serves
  - how it is used day-to-day
  - which workflows are core vs optional examples

Phase 3: README drafting
- Draft repository-specific README content.
- Include only supported sections.
- Keep sections concrete and practical.

Suggested section set (adapt as needed):
- Title
- What This Repository Is
- Core Features
- Repository Layout
- Requirements
- Build / Compile
- Authoring Workflow
- Style and Prompt System
- LaTeX Conventions
- Start up Guide (How to go from a LaTeX .zip to full functional agentic repository)
- Notes / Limitations

Phase 4: Verification pass
- Validate commands, paths, and terminology.
- Remove speculation.
- Ensure consistency with actual files.

Phase 5: README-memory checkpoint
- After verifying the README, retain only verified reusable repository facts, documentation decisions or corrections, and failed approaches likely to affect future README work; do not copy README prose into memory.
- If the run produced none, use the repository memory no-op.

STYLE REQUIREMENTS
- Clean technical English.
- Concrete and concise.
- Minimal fluff.
- No empty claims.

OUTPUT REQUIREMENTS
- Primary deliverable: create or replace `README.md` at repository root.
- Apart from `README.md`, writes are limited to memory maintenance required by `AGENTS.md` inside `memory/`.
- Do not emit the full README as chat output when file editing is available.
- After writing the file, provide a short completion note with:
  - confirmation that `README.md` was created/updated
  - any blockers, assumptions, or unresolved verification risks
  - the README-memory result: reusable facts, corrections, decisions, or failed approaches retained, or explicit no-op
- If running in a read-only context where file edits are impossible, output only the final README Markdown.

COMPLETION GATE
- Verify README-relevant memory was reconciled with the current repository and Phase 5 was completed.

FINAL INSTRUCTION
THINK LONG AND HARD.
VERIFY AGAINST THE REPOSITORY.
OUTPUT A README THAT A CAREFUL LATEX MAINTAINER WOULD TRUST.

ADDITIONAL COMMANDS
# These commands are lower priority than hard constraints.
# - <SPECIAL_COMMANDS>
