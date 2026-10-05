from __future__ import annotations

from pathlib import Path
import subprocess
import tempfile
import unittest
import uuid

from metasrc.handlers.worktrees import create_agent_worktree


def git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=root, text=True, encoding="utf-8",
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True,
    )
    return result.stdout


class WorktreeSnapshotTests(unittest.TestCase):
    def test_dirty_full_state_is_mirrored_without_source_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            source = parent / "repo"
            source.mkdir()
            git(source, "init")
            git(source, "config", "user.email", "test@example.invalid")
            git(source, "config", "user.name", "Test")
            (source / ".gitignore").write_text("ignored.bin\n", encoding="utf-8")
            (source / "tracked.txt").write_text("base\n", encoding="utf-8")
            (source / "deleted.txt").write_text("delete\n", encoding="utf-8")
            git(source, "add", ".")
            git(source, "commit", "-m", "base")
            (source / "tracked.txt").write_text("changed\n", encoding="utf-8")
            (source / "deleted.txt").unlink()
            (source / "untracked.txt").write_text("local\n", encoding="utf-8")
            (source / "ignored.bin").write_bytes(b"\x00\x01\xff")
            git(source, "add", "tracked.txt")
            before_status = git(source, "status", "--porcelain=v1", "--untracked-files=all")
            before_stashes = git(source, "stash", "list")
            worktree, branch = create_agent_worktree("sample", source, str(uuid.uuid4()))
            try:
                self.assertEqual((worktree / "tracked.txt").read_text(encoding="utf-8"), "changed\n")
                self.assertFalse((worktree / "deleted.txt").exists())
                self.assertEqual((worktree / "untracked.txt").read_text(encoding="utf-8"), "local\n")
                self.assertEqual((worktree / "ignored.bin").read_bytes(), b"\x00\x01\xff")
                self.assertEqual(git(source, "status", "--porcelain=v1", "--untracked-files=all"), before_status)
                self.assertEqual(git(source, "stash", "list"), before_stashes)
            finally:
                git(source, "worktree", "remove", "--force", str(worktree))
                git(source, "branch", "-D", branch)


if __name__ == "__main__":
    unittest.main()
