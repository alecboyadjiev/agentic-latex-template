from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from metasrc.errors import AgentError
from metasrc.providers.base import ModelRequest
from metasrc.providers.codex_cli import CodexCliProvider


class ProviderPermissionTests(unittest.TestCase):
    def test_rejects_workspace_and_overlapping_roots(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            data = root / "agent-data"
            nested = data / "scratch"
            run = root / ".agent-runs" / "pending"
            nested.mkdir(parents=True)
            request = ModelRequest("x", "gpt-5.6-sol", "high", root, data,
                                   (data, nested), None, run, False, False)
            with self.assertRaisesRegex(AgentError, "overlap"):
                CodexCliProvider().validate(request)
            request = ModelRequest("x", "gpt-5.6-sol", "high", root, root,
                                   (root,), None, run, False, False)
            with self.assertRaisesRegex(AgentError, "Repository root"):
                CodexCliProvider().validate(request)


if __name__ == "__main__":
    unittest.main()

