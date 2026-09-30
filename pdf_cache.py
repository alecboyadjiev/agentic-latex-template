#!/usr/bin/env python3
"""Maintain an incremental, content-addressed text cache for repository PDFs.

Run from anywhere with:
    python pdf_cache.py

The default root is the directory containing this script. Unchanged PDFs are
identified from their path, size, and nanosecond filesystem timestamps, so the
normal up-to-date run does not read or hash PDF contents. Use --verify to hash
every PDF when a full content check is desired.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import logging
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any


CACHE_SCHEMA_VERSION = 1
EXTRACTION_FORMAT_VERSION = 1
DEFAULT_CACHE_NAME = ".pdf-cache"
MANIFEST_NAME = "manifest.json"
TEXT_NAME = "document.txt"
METADATA_NAME = "metadata.json"


class CacheError(Exception):
    pass


def parse_args() -> argparse.Namespace:
    script_root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(
        description="Incrementally cache searchable text from every PDF below a root."
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=script_root,
        help="directory to scan (default: directory containing this script)",
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        help="cache location (default: <root>/.pdf-cache)",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="hash every PDF instead of trusting unchanged filesystem metadata",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="re-extract every PDF even when its cached content is valid",
    )
    parser.add_argument(
        "--no-prune",
        action="store_true",
        help="retain cache objects no longer referenced by a repository PDF",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="show PDF-parser warnings",
    )
    return parser.parse_args()


def pypdf_version() -> str:
    try:
        return importlib.metadata.version("pypdf")
    except importlib.metadata.PackageNotFoundError as exc:
        raise CacheError(
            "Missing dependency 'pypdf'. Install it with: "
            f"{sys.executable} -m pip install pypdf"
        ) from exc


def read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def atomic_write_json(path: Path, value: Any) -> None:
    atomic_write_text(
        path,
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )


def is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def scan_pdfs(root: Path, cache_dir: Path) -> tuple[list[tuple[Path, str, os.stat_result]], int]:
    found: list[tuple[Path, str, os.stat_result]] = []
    skipped_symlinks = 0
    resolved_cache = cache_dir.resolve()

    for directory, dirnames, filenames in os.walk(root, followlinks=False):
        current = Path(directory)
        retained: list[str] = []
        for dirname in dirnames:
            candidate = current / dirname
            if candidate.is_symlink():
                skipped_symlinks += 1
            elif dirname == ".git" or candidate.resolve() == resolved_cache:
                continue
            else:
                retained.append(dirname)
        dirnames[:] = sorted(retained, key=lambda value: (value.casefold(), value))

        for filename in filenames:
            if not filename.casefold().endswith(".pdf"):
                continue
            path = current / filename
            if path.is_symlink():
                skipped_symlinks += 1
                continue
            try:
                stat = path.stat()
            except OSError as exc:
                raise CacheError(f"Cannot inspect PDF {path}: {exc}") from exc
            if path.is_file():
                found.append((path, path.relative_to(root).as_posix(), stat))

    found.sort(key=lambda item: (item[1].casefold(), item[1]))
    return found, skipped_symlinks


def file_signature(stat: os.stat_result) -> dict[str, int]:
    return {
        "size_bytes": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "ctime_ns": stat.st_ctime_ns,
    }


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def object_relative_path(digest: str) -> Path:
    return Path("sha256") / digest[:2] / digest


def cache_artifacts_exist(cache_dir: Path, entry: dict[str, Any]) -> bool:
    relative = entry.get("cache_object")
    digest = entry.get("sha256")
    status = entry.get("status")
    if (
        not isinstance(relative, str)
        or not isinstance(digest, str)
        or len(digest) != 64
        or relative != object_relative_path(digest).as_posix()
        or status not in {"ok", "error"}
    ):
        return False
    object_dir = cache_dir / relative
    if not (object_dir / METADATA_NAME).is_file():
        return False
    return status == "error" or (object_dir / TEXT_NAME).is_file()


def cached_object_metadata(
    cache_dir: Path,
    digest: str,
    extractor_version: str,
) -> dict[str, Any] | None:
    object_dir = cache_dir / object_relative_path(digest)
    metadata = read_json(object_dir / METADATA_NAME)
    if metadata is None:
        return None
    expected = {
        "cache_schema_version": CACHE_SCHEMA_VERSION,
        "extraction_format_version": EXTRACTION_FORMAT_VERSION,
        "extractor": "pypdf",
        "extractor_version": extractor_version,
        "sha256": digest,
    }
    if any(metadata.get(key) != value for key, value in expected.items()):
        return None
    status = metadata.get("status")
    if status == "ok" and not (object_dir / TEXT_NAME).is_file():
        return None
    return metadata if status in {"ok", "error"} else None


def normalize_text(value: str | None) -> str:
    if not value:
        return ""
    value = value.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
    return "\n".join(line.rstrip() for line in value.split("\n")).strip()


def render_document(page_texts: list[str]) -> str:
    page_count = len(page_texts)
    sections = []
    for index, page_text in enumerate(page_texts, start=1):
        marker = f"===== PAGE {index:04d} OF {page_count:04d} ====="
        sections.append(f"{marker}\n{page_text}" if page_text else marker)
    return "\n\n".join(sections) + ("\n" if sections else "")


def extract_pdf(
    pdf_path: Path,
    relative_path: str,
    digest: str,
    cache_dir: Path,
    extractor_version: str,
) -> dict[str, Any]:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise CacheError(
            "Missing dependency 'pypdf'. Install it with: "
            f"{sys.executable} -m pip install pypdf"
        ) from exc

    object_dir = cache_dir / object_relative_path(digest)
    object_dir.mkdir(parents=True, exist_ok=True)
    common = {
        "cache_schema_version": CACHE_SCHEMA_VERSION,
        "extraction_format_version": EXTRACTION_FORMAT_VERSION,
        "extractor": "pypdf",
        "extractor_version": extractor_version,
        "sha256": digest,
    }
    try:
        reader = PdfReader(str(pdf_path), strict=False)
        if reader.is_encrypted and reader.decrypt("") == 0:
            raise ValueError("PDF is encrypted and cannot be opened")
        page_texts = [normalize_text(page.extract_text()) for page in reader.pages]
        metadata = {
            **common,
            "status": "ok",
            "page_count": len(page_texts),
            "page_character_counts": [len(text) for text in page_texts],
        }
        atomic_write_text(object_dir / TEXT_NAME, render_document(page_texts))
    except Exception as exc:
        metadata = {
            **common,
            "status": "error",
            "page_count": None,
            "error": f"{type(exc).__name__}: {exc}",
            "source_at_failure": relative_path,
        }
        (object_dir / TEXT_NAME).unlink(missing_ok=True)
    atomic_write_json(object_dir / METADATA_NAME, metadata)
    return metadata


def manifest_is_compatible(manifest: dict[str, Any] | None, version: str) -> bool:
    if manifest is None:
        return False
    return (
        manifest.get("cache_schema_version") == CACHE_SCHEMA_VERSION
        and manifest.get("extraction_format_version") == EXTRACTION_FORMAT_VERSION
        and manifest.get("extractor") == {"name": "pypdf", "version": version}
        and isinstance(manifest.get("documents"), dict)
    )


def prune_objects(cache_dir: Path, referenced: set[str]) -> int:
    objects_root = cache_dir / "sha256"
    if not objects_root.is_dir():
        return 0
    removed = 0
    for prefix_dir in objects_root.iterdir():
        if not prefix_dir.is_dir() or prefix_dir.is_symlink():
            continue
        for object_dir in prefix_dir.iterdir():
            if object_dir.is_dir() and not object_dir.is_symlink():
                if object_dir.name not in referenced:
                    shutil.rmtree(object_dir)
                    removed += 1
        if not any(prefix_dir.iterdir()):
            prefix_dir.rmdir()
    return removed


def cache_pdfs(
    root: Path,
    cache_dir: Path,
    verify: bool,
    force: bool,
    prune: bool,
) -> int:
    extractor_version = pypdf_version()
    previous_manifest = read_json(cache_dir / MANIFEST_NAME)
    compatible = manifest_is_compatible(previous_manifest, extractor_version)
    previous_documents = previous_manifest["documents"] if compatible else {}
    pdfs, skipped_symlinks = scan_pdfs(root, cache_dir)

    documents: dict[str, dict[str, Any]] = {}
    metadata_by_digest: dict[str, dict[str, Any]] = {}
    referenced: set[str] = set()
    extracted = hashed = fast_reused = content_reused = 0

    for pdf_path, relative_path, stat in pdfs:
        signature = file_signature(stat)
        previous = previous_documents.get(relative_path)
        unchanged = (
            not force
            and not verify
            and isinstance(previous, dict)
            and all(previous.get(key) == value for key, value in signature.items())
            and cache_artifacts_exist(cache_dir, previous)
        )
        if unchanged:
            documents[relative_path] = previous
            referenced.add(previous["sha256"])
            fast_reused += 1
            continue

        digest = sha256_file(pdf_path)
        hashed += 1
        referenced.add(digest)
        metadata = metadata_by_digest.get(digest)
        if metadata is None and not force:
            metadata = cached_object_metadata(cache_dir, digest, extractor_version)
        if metadata is None:
            metadata = extract_pdf(
                pdf_path, relative_path, digest, cache_dir, extractor_version
            )
            extracted += 1
        else:
            content_reused += 1
        metadata_by_digest[digest] = metadata

        entry: dict[str, Any] = {
            **signature,
            "sha256": digest,
            "cache_object": object_relative_path(digest).as_posix(),
            "status": metadata["status"],
            "page_count": metadata.get("page_count"),
        }
        if metadata["status"] == "error":
            entry["error"] = metadata.get("error", "unknown extraction error")
        documents[relative_path] = entry

    removed = prune_objects(cache_dir, referenced) if prune else 0
    manifest = {
        "cache_schema_version": CACHE_SCHEMA_VERSION,
        "extraction_format_version": EXTRACTION_FORMAT_VERSION,
        "extractor": {"name": "pypdf", "version": extractor_version},
        "root": ".",
        "documents": documents,
    }
    changed = manifest != previous_manifest
    if changed:
        atomic_write_json(cache_dir / MANIFEST_NAME, manifest)

    errors = sum(entry["status"] == "error" for entry in documents.values())
    if not changed and hashed == 0 and removed == 0:
        print(f"PDF cache is up to date ({len(documents)} file(s)).")
    else:
        print(f"PDFs found: {len(documents)}")
        print(f"Fast unchanged reuse: {fast_reused}")
        print(f"Files hashed: {hashed}")
        print(f"Content-cache reuse after hashing: {content_reused}")
        print(f"Extracted or refreshed: {extracted}")
        print(f"Pruned stale objects: {removed}")
        print(f"Skipped symlinks: {skipped_symlinks}")
        print(f"Extraction errors: {errors}")
        print(f"Manifest: {cache_dir / MANIFEST_NAME}")
    return 1 if errors else 0


def main() -> int:
    args = parse_args()
    if not args.verbose:
        logging.getLogger("pypdf").setLevel(logging.ERROR)
    root = args.root.resolve()
    if not root.is_dir():
        raise CacheError(f"Root is not a directory: {root}")
    cache_dir = (
        args.cache_dir.resolve()
        if args.cache_dir is not None
        else (root / DEFAULT_CACHE_NAME).resolve()
    )
    if cache_dir == root:
        raise CacheError("Cache directory cannot be the scan root")
    if is_within(cache_dir, root) and cache_dir.name == ".git":
        raise CacheError("Refusing to use .git as the cache directory")
    return cache_pdfs(
        root,
        cache_dir,
        verify=args.verify,
        force=args.force,
        prune=not args.no_prune,
    )


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except CacheError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2)
