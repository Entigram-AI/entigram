import tempfile
import unittest
from pathlib import Path

from entigram.workspace_links import allowed_children, decide, discover


class TestWorkspaceLinks(unittest.TestCase):
    def _child(self, parent: Path, name: str) -> Path:
        child = parent / name
        (child / ".etg").mkdir(parents=True)
        (child / ".etg" / "entigram.yaml").write_text("status: initialized\n", encoding="utf-8")
        return child

    def test_discovery_requires_explicit_parent_decision(self):
        with tempfile.TemporaryDirectory() as temp:
            parent = Path(temp)
            self._child(parent, "child-one")

            self.assertEqual(discover(parent), [{"path": "child-one", "name": "child-one", "decision": "pending"}])
            self.assertEqual(allowed_children(parent), [])

            result = decide(parent, "child-one", "allowed")
            self.assertEqual(result, {"path": "child-one", "decision": "allowed"})
            self.assertEqual(allowed_children(parent)[0]["path"], "child-one")

    def test_denied_child_remains_visible_but_never_allowed(self):
        with tempfile.TemporaryDirectory() as temp:
            parent = Path(temp)
            self._child(parent, "child-two")

            decide(parent, "child-two", "denied")
            self.assertEqual(discover(parent)[0]["decision"], "denied")
            self.assertEqual(allowed_children(parent), [])

    def test_rejects_child_outside_parent(self):
        with tempfile.TemporaryDirectory() as temp, tempfile.TemporaryDirectory() as external:
            parent = Path(temp)
            outside = self._child(Path(external), "other")
            with self.assertRaisesRegex(ValueError, "inside the parent"):
                decide(parent, outside, "allowed")


if __name__ == "__main__":
    unittest.main()
