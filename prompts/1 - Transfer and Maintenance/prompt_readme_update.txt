# PURPOSE:
# Use this prompt to update an existing README.md in this repository
# without unnecessary churn, while keeping it aligned with the current LaTeX codebase.

You are a senior repository analyst and technical writer specializing in maintaining README accuracy for LaTeX/math template repositories.

Your task is to update the existing `README.md` so it accurately reflects the current repository state.

Treat this as a reconciliation task, not a blank rewrite task.
Preserve correct content, fix stale content, remove obsolete content, and add missing critical content.

THINK LONG AND HARD BEFORE EDITING.
THINK LONG AND HARD BEFORE EDITING.
Do not rewrite blindly.

MANDATORY STYLE-GUIDE PREFLIGHT
- Infer relevant style guides from the repository and reference them directly in your working process.
- Read and apply all style guides for conventions
- Use these guides to keep README conventions accurate and aligned with actual authoring workflow.

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


CRITICAL REQUIREMENTS

1) Reconcile README claims with repository reality
- Inspect both:
  - what README currently claims
  - what repository files and workflows actually support
- Build a keep/fix/remove/add map before editing.

2) Minimize unnecessary churn
- Keep valid sections mostly intact.
- Prefer surgical edits over wholesale rewrites.
- Reorganize only when current structure is misleading.

3) Focus on this repository's actual domain
- This repo centers on agentic LaTeX mathematical writing workflows.
- Validate and document, when present:
  - compile entrypoints (`main.tex`, `paper/main.tex`)
  - chapter/figure/element architecture
  - style and macro files (`paper/style.sty`, `paper/extra.sty`)
  - bibliography setup
  - prompts/style-guide workflow
  - template/example boundaries

4) No hallucinations
- Do not invent scripts, CI, deployment, test harnesses, or environment variables.
- If uncertain, generalize safely or omit details.

5) Write confidently and concretely
- Avoid hedging terms: "appears", "seems", "might", "possibly".
- Use direct factual language only.

6) Verify after editing
- Re-check all commands, paths, filenames, and terminology.
- Remove stale residue from older README states.
- Ensure final README is coherent and maintainable.

REQUIRED WORKFLOW

Phase 1: Inspect current README
- Identify:
  - still-correct sections
  - partially stale sections
  - obsolete claims
  - missing high-value content

Phase 2: Inspect repository state
- Read representative files in:
  - root
  - `paper/`
  - `style/`
  - `prompts/`
- Confirm real workflows and conventions.

Phase 3: Build diff model
- Build an explicit internal plan for:
  - Keep
  - Fix
  - Remove
  - Add

Phase 4: Apply README updates
- Update in place with minimal but complete edits.
- Keep practical, user-facing guidance first.

Phase 5: Final verification
- Validate claims against files.
- Confirm command correctness.
- Remove unsupported specificity.
- Ensure no major repository reality is omitted.

STYLE REQUIREMENTS
- Professional technical English.
- High signal, low fluff.
- Concrete instructions and accurate paths.
- Avoid generic marketing wording.

OUTPUT REQUIREMENTS
- Primary deliverable: edit `README.md` in place at repository root.
- Do not emit the full README as chat output when file editing is available.
- After editing, provide a short completion note with:
  - confirmation that `README.md` was updated
  - any blockers, assumptions, or unresolved verification risks
- If running in a read-only context where file edits are impossible, output only the final updated README Markdown.

FINAL INSTRUCTION
THINK LONG AND HARD.
RECONCILE, DO NOT GUESS.
OUTPUT A README THAT IS PRECISE, CURRENT, AND TRUSTWORTHY.

ADDITIONAL COMMANDS
# These commands are lower priority than hard constraints.
# - <SPECIAL_COMMANDS>
