import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class TestOpenCodeDocs(unittest.TestCase):
    def test_opencode_doc_recommends_mcp_boundary(self):
        doc = (ROOT / "docs" / "opencode.md").read_text()

        self.assertIn("recommended general-purpose coding agent surface", doc)
        self.assertIn("etg\", \"serve\", \"--dir\", \".", doc)
        self.assertIn("OpenCode coding agent", doc)
        self.assertIn("Entigram local MCP governance", doc)
        self.assertIn("Advanced Proxy Path", doc)
        self.assertIn("experimental compatibility tools", doc)
        self.assertIn("Cloudflare Workers AI provider", doc)

    def test_opencode_example_config_keeps_entigram_enabled(self):
        config = (ROOT / ".opencode.example.jsonc").read_text()

        self.assertIn('"$schema": "https://opencode.ai/config.json"', config)
        self.assertIn('"entigram"', config)
        self.assertIn('"type": "local"', config)
        self.assertIn('"command": ["etg", "serve", "--dir", "."]', config)
        self.assertIn('"enabled": true', config)

    def test_opencode_example_keeps_cloudflare_mcp_opt_in(self):
        config = (ROOT / ".opencode.example.jsonc").read_text()

        self.assertIn('"cloudflare"', config)
        self.assertIn('"https://mcp.cloudflare.com/mcp"', config)
        self.assertIn('"https://docs.mcp.cloudflare.com/mcp"', config)
        self.assertGreaterEqual(config.count('"enabled": false'), 5)

    def test_readme_links_opencode_docs(self):
        readme = (ROOT / "README.md").read_text()

        self.assertIn("docs/opencode.md", readme)

    def test_cli_help_marks_proxy_commands_experimental(self):
        cli = (ROOT / "entigram" / "cli_runner" / "etg_cli.py").read_text()

        self.assertIn("Experimental: run an Ollama-compatible proxy", cli)
        self.assertIn("Experimental: start a dynamic Cloudflare Ollama proxy", cli)


if __name__ == "__main__":
    unittest.main()

