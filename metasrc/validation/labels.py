from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

from metasrc.errors import AgentError


PAPER_NAME_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*")
TEX_LABEL_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9:._/-]*")
TEX_LABEL_COMMAND_PATTERN = re.compile(r"\\label\s*\{\s*([^{}\s]+)\s*\}")


@dataclass(frozen=True)
class ResolvedPaper:
    name: str
    path: Path
    main_tex: Path
    relative_path: str

    def as_metadata(self) -> dict[str, str]:
        return {
            "name": self.name,
            "path": self.relative_path,
            "main_tex": f"{self.relative_path}/main.tex",
        }


@dataclass(frozen=True)
class LabelLocation:
    label: str
    path: Path
    relative_path: str
    line: int
    paper: ResolvedPaper

    def display(self) -> str:
        return f"{self.relative_path}:{self.line}"


def _inside(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def discover_papers(workspace_root: Path) -> list[ResolvedPaper]:
    workspace = workspace_root.resolve(strict=True)
    papers_root = workspace / "papers"
    if not papers_root.is_dir() or papers_root.is_symlink():
        raise AgentError(f"Cannot discover papers: missing directory {papers_root}")
    resolved_root = papers_root.resolve(strict=True)
    papers: list[ResolvedPaper] = []
    for child in papers_root.iterdir():
        if child.is_symlink() or not child.is_dir():
            continue
        if PAPER_NAME_PATTERN.fullmatch(child.name) is None:
            continue
        try:
            canonical = child.resolve(strict=True)
            canonical.relative_to(resolved_root)
        except (OSError, ValueError):
            continue
        main_tex = canonical / "main.tex"
        if main_tex.is_symlink() or not main_tex.is_file():
            continue
        papers.append(
            ResolvedPaper(
                name=child.name,
                path=canonical,
                main_tex=main_tex.resolve(strict=True),
                relative_path=canonical.relative_to(workspace).as_posix(),
            )
        )
    return sorted(papers, key=lambda p: (p.name.casefold(), str(p.path)))


def resolve_paper(name: str, workspace_root: Path) -> ResolvedPaper:
    if not isinstance(name, str) or PAPER_NAME_PATTERN.fullmatch(name) is None:
        raise AgentError(
            f"Invalid paper name {name!r}. Use letters, digits, underscores, or "
            "hyphens, beginning with a letter or digit."
        )
    papers = discover_papers(workspace_root)
    matches = [paper for paper in papers if paper.name.casefold() == name.casefold()]
    if len(matches) > 1:
        raise AgentError(
            f"Paper name {name!r} is ambiguous: "
            + ", ".join(paper.relative_path for paper in matches)
        )
    if not matches:
        expected = workspace_root.resolve() / "papers" / name / "main.tex"
        raise AgentError(f"Paper {name!r} was not found; expected {expected}")
    return matches[0]


def strip_tex_comments(text: str) -> str:
    output: list[str] = []
    for line in text.splitlines(keepends=True):
        comment_at: int | None = None
        for index, character in enumerate(line):
            if character != "%":
                continue
            backslashes = 0
            cursor = index - 1
            while cursor >= 0 and line[cursor] == "\\":
                backslashes += 1
                cursor -= 1
            if backslashes % 2 == 0:
                comment_at = index
                break
        if comment_at is None:
            output.append(line)
        elif line.endswith(("\n", "\r")):
            output.append(line[:comment_at] + "\n")
        else:
            output.append(line[:comment_at])
    return "".join(output)


def build_label_index(
    workspace_root: Path,
    papers: list[ResolvedPaper] | None = None,
) -> dict[str, list[LabelLocation]]:
    workspace = workspace_root.resolve(strict=True)
    papers = papers if papers is not None else discover_papers(workspace)
    index: dict[str, list[LabelLocation]] = {}
    for paper in papers:
        for path in sorted(
            paper.path.rglob("*.tex"), key=lambda item: item.as_posix().casefold()
        ):
            if path.is_symlink() or not path.is_file():
                continue
            text = strip_tex_comments(path.read_text(encoding="utf-8"))
            for match in TEX_LABEL_COMMAND_PATTERN.finditer(text):
                label = match.group(1)
                location = LabelLocation(
                    label=label,
                    path=path.resolve(strict=True),
                    relative_path=path.resolve().relative_to(workspace).as_posix(),
                    line=text.count("\n", 0, match.start()) + 1,
                    paper=paper,
                )
                index.setdefault(label, []).append(location)
    for locations in index.values():
        locations.sort(key=lambda item: (item.relative_path.casefold(), item.line))
    return index


def resolve_label_target(
    label: str, paper: ResolvedPaper, workspace_root: Path
) -> LabelLocation:
    if TEX_LABEL_PATTERN.fullmatch(label) is None:
        raise AgentError(f"Invalid TeX label key: {label!r}")
    papers = discover_papers(workspace_root)
    index = build_label_index(workspace_root, papers)
    duplicates = {key: value for key, value in index.items() if len(value) > 1}
    if duplicates:
        lines = ["Duplicate TeX labels exist across papers:"]
        for key in sorted(duplicates, key=lambda value: (value.casefold(), value)):
            lines.append(f"  {key}:")
            lines.extend(f"    {loc.display()}" for loc in duplicates[key])
        raise AgentError("\n".join(lines))
    locations = index.get(label, [])
    if not locations:
        searched = ", ".join(item.relative_path for item in papers) or "(none)"
        raise AgentError(
            f"TeX label {label!r} was not found. Searched every discovered paper: "
            f"{searched}"
        )
    location = locations[0]
    if location.paper.path != paper.path:
        raise AgentError(
            f"TeX label {label!r} is not in selected paper {paper.relative_path}; "
            f"it is at {location.display()}."
        )
    return location

