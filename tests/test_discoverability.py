import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class TestDiscoverability(unittest.TestCase):
    def test_server_metadata_matches_package_and_repository(self):
        metadata = json.loads((ROOT / "server.json").read_text())

        self.assertEqual(metadata["name"], "io.github.entigram-ai/entigram")
        self.assertEqual(metadata["repository"]["url"], "https://github.com/Entigram-AI/entigram")
        self.assertEqual(metadata["packages"][0]["registryType"], "pypi")
        self.assertEqual(metadata["packages"][0]["identifier"], "entigram-ai")
        self.assertEqual(metadata["packages"][0]["transport"]["type"], "stdio")

        project = (ROOT / "pyproject.toml").read_text()
        version = re.search(r'^version = "([^"]+)"$', project, re.MULTILINE).group(1)
        self.assertEqual(metadata["version"], version)
        self.assertEqual(metadata["packages"][0]["version"], version)

    def test_repository_exposes_agent_skill_and_discovery_index(self):
        skill = (ROOT / "skills" / "entigram-workspace" / "SKILL.md").read_text()
        self.assertIn("name: entigram-workspace", skill)
        self.assertIn("description:", skill)
        self.assertIn("etg_get_capabilities", skill)
        self.assertIn("Delivery status: current", skill)

        discovery = (ROOT / "docs" / "discoverability.md").read_text()
        self.assertIn("server.json", discovery)
        self.assertIn("MCP Registry publication", discovery)
        self.assertIn("authenticated public endpoint", discovery)

    def test_readme_and_workspace_standard_link_discovery_contract(self):
        readme = (ROOT / "README.md").read_text()
        standard = (ROOT / "docs" / "workspace-standard.md").read_text()
        mcp_docs = (ROOT / "docs" / "mcp-tools.md").read_text()

        self.assertIn("docs/discoverability.md", readme)
        self.assertIn("skills/entigram-workspace/SKILL.md", readme)
        self.assertIn("etg_get_workspace_context", standard)
        self.assertIn("etg_get_capabilities", standard)
        self.assertIn("etg_get_workspace_context", mcp_docs)
        self.assertIn("Writes `.etg/state.db`", mcp_docs)


if __name__ == "__main__":
    unittest.main()
