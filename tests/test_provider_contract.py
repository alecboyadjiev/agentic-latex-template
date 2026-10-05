from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from metasrc.providers.base import ProviderSettings
from metasrc.providers.codex_app_server import CodexAppServerProvider


class ProviderContractTests(unittest.TestCase):
    def test_turn_policy_uses_exact_validated_roots(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            data = root / "agent-data"
            paper = root / "papers" / "sample"
            data.mkdir(parents=True)
            paper.mkdir(parents=True)
            settings = ProviderSettings(
                "gpt-5.6-sol", "high", root, paper, (data, paper),
            )
            params = CodexAppServerProvider._turn_params(settings)
            self.assertEqual(params["approvalPolicy"], "never")
            self.assertEqual(params["sandboxPolicy"]["type"], "workspaceWrite")
            self.assertEqual(
                params["sandboxPolicy"]["writableRoots"], [str(data), str(paper)]
            )
            self.assertNotIn(str(root), params["sandboxPolicy"]["writableRoots"])


if __name__ == "__main__":
    unittest.main()
