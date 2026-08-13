import asyncio
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from entigram.injector import inject_entigram_manifest
from entigram.mcp_server import create_mcp_server
from entigram.usage import MCP_TOOL_DECLARATIONS


SCHEMA = """
ENTITY: User {
  id UUID PK
  name String
}
"""


class TestMCPServerContract(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        inject_entigram_manifest(self.test_dir, ["Entigram Schemas"], "Codex")
        Path(self.test_dir, "schema.lds").write_text(SCHEMA)
        self.server = create_mcp_server(self.test_dir)

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    def test_every_declared_tool_has_a_complete_contract(self):
        tools = {
            tool.name: tool
            for tool in asyncio.run(self.server.list_tools())
        }
        declarations = {tool["name"]: tool for tool in MCP_TOOL_DECLARATIONS}

        self.assertEqual(set(tools), set(declarations))
        for name, declaration in declarations.items():
            tool = tools[name]
            self.assertEqual(tool.input_schema["type"], "object")
            self.assertEqual(
                tool.annotations.model_dump(by_alias=True, exclude={"title"}),
                declaration["annotations"],
            )

    def test_every_declared_tool_dispatches_through_the_mcp_server(self):
        requests = {
            "etg_get_schemas": {},
            "etg_get_impact": {"file_path": "schema.lds"},
            "etg_get_workspace_context": {},
            "etg_get_capabilities": {},
            "etg_get_assessment_capabilities": {},
            "etg_assess": {
                "payload": json.dumps(
                    {
                        "adapter": "unavailable",
                        "subject_type": "sha256",
                        "subject": "abc123",
                    }
                )
            },
            "etg_propose_alignment": {
                "payload": json.dumps(
                    {
                        "source_domain": "CRM",
                        "target_domain": "CRM",
                        "source_concept": "User.name",
                        "target_concept": "User.name",
                        "rationale": "The canonical local user name.",
                    }
                )
            },
            "etg_log_conflict": {
                "payload": json.dumps(
                    {
                        "conflict_id": "UserName_001",
                        "entity_type": "User",
                        "agent_id": "Codex",
                        "proposed_states": {"Codex": {"name": "Ada"}},
                    }
                )
            },
        }

        for name, arguments in requests.items():
            with self.subTest(tool=name):
                result = asyncio.run(self.server.call_tool(name, arguments))
                self.assertFalse(result.is_error)
                self.assertEqual(len(result.content), 1)
                payload = json.loads(result.content[0].text)
                self.assertIn("ok", payload)
