import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class TestWorkspaceStandardDocs(unittest.TestCase):
    def test_workspace_standard_documents_portable_agent_flow(self):
        standard = (ROOT / "docs" / "workspace-standard.md").read_text()

        self.assertIn("hydrate", standard)
        self.assertIn("etg broker preflight --file <path>", standard)
        self.assertIn("etg broker impact --file <path>", standard)
        self.assertIn("etg broker handoff", standard)
        self.assertIn("etg broker status", standard)
        self.assertIn("Delivery status: current", standard)
        self.assertIn(".etg/entigram.yaml", standard)
        self.assertIn(".etg/state.db", standard)
        self.assertIn("schema_paths", standard)
        self.assertIn("governed_artifact_globs", standard)
        self.assertIn("git ls-files", standard)
        self.assertIn("etg broker export-audit --out entigram-audit.json", standard)
        self.assertIn("workspace_schema_version", standard)
        self.assertIn("Breaking changes", standard)

    def test_readme_points_to_standard_and_current_defaults(self):
        readme = (ROOT / "README.md").read_text()

        self.assertIn("docs/workspace-standard.md", readme)
        self.assertIn("docs/minimal-governed-workspace.md", readme)
        self.assertIn("pipx install entigram-ai", readme)
        self.assertIn("hydrate", readme)
        self.assertIn("etg broker preflight --file <path>", readme)
        self.assertIn("etg broker handoff", readme)
        self.assertIn("Delivery status: current", readme)
        self.assertNotIn("python3 -m entigram.cli_runner.etg_cli agent --engine Antigravity", readme)

    def test_mcp_docs_link_back_to_workspace_standard(self):
        mcp_docs = (ROOT / "docs" / "mcp-tools.md").read_text()

        self.assertIn("workspace-standard.md", mcp_docs)
        self.assertIn("error.code", mcp_docs)

    def test_workspace_standard_documents_usage_and_lifecycle(self):
        standard = (ROOT / "docs" / "workspace-standard.md").read_text()
        lifecycle = (ROOT / "docs" / "workspace-lifecycle.md").read_text()

        self.assertIn("lifecycle:", standard)
        self.assertIn("WORKSPACE_PAUSED", standard)
        self.assertIn("heuristic_chars_div_4_v1", standard)
        self.assertIn("Raw arguments", standard)
        self.assertIn("etg pause", lifecycle)
        self.assertIn("etg resume --force", lifecycle)
        self.assertIn("etg eject --dry-run", lifecycle)
        self.assertIn("0600", lifecycle)

    def test_agent_instruction_files_use_current_portable_flow(self):
        for relative_path in [
            "AGY.md",
            "GEMINI.md",
            "OLLAMA.md",
            "AGENT_INSTRUCTIONS.md",
        ]:
            content = (ROOT / relative_path).read_text()

            with self.subTest(path=relative_path):
                self.assertIn("hydrate", content)
                self.assertIn("etg broker preflight --file <path>", content)
                self.assertIn("etg broker impact --file <path>", content)
                self.assertIn("etg broker handoff", content)
                self.assertIn("Delivery status: current", content)
                self.assertNotIn("broker decide --id", content)
                self.assertNotIn("broker align --src_dom", content)
                self.assertNotIn("python3 -m entigram.cli_runner.etg_cli broker status", content)


if __name__ == "__main__":
    unittest.main()
