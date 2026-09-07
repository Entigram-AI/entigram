import json
import tempfile
import unittest
from pathlib import Path

from entigram.governance.task_envelope import (
    create_envelope,
    get_pending_envelopes,
    get_accepted_envelopes,
    get_error_envelopes,
    accept_envelope,
    authorize_execution,
    TaskEnvelopeError,
)

from entigram.cli_runner.etg_cli import get_hydration_vector

class TestTaskEnvelope(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_create_and_retrieve_envelope(self):
        env = create_envelope(
            self.workspace,
            intent="Update authentication logic",
            proposed_entities=["AuthService", "UserSession"],
            invariants=["UserSession MUST be ephemeral"],
            affected_paths=["src/auth.py", "tests/test_auth.py"],
            validation_commands=["pytest tests/test_auth.py"],
            uncertainty_unknowns=["Rate limit thresholds are unclear"],
            agent_id="test_agent"
        )
        
        self.assertIn("envelope_id", env)
        self.assertEqual(env["status"], "proposed")
        self.assertEqual(env["intent"], "Update authentication logic")
        
        pending = get_pending_envelopes(self.workspace)
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["envelope_id"], env["envelope_id"])
        
        accepted = get_accepted_envelopes(self.workspace)
        self.assertEqual(len(accepted), 0)

    def test_accept_and_authorize_envelope(self):
        env = create_envelope(
            self.workspace,
            intent="Update authentication logic",
            proposed_entities=[],
            invariants=[],
            affected_paths=[],
            validation_commands=[],
            uncertainty_unknowns=[],
            agent_id="test_agent"
        )
        
        # Proposed envelope should NOT authorize execution
        auth_result = authorize_execution(self.workspace, env["envelope_id"])
        self.assertFalse(auth_result["authorized"])
        self.assertIn("not 'accepted'", auth_result["reason"])
        
        accepted_env = accept_envelope(self.workspace, env["envelope_id"], "admin")
        self.assertEqual(accepted_env["status"], "accepted")
        self.assertEqual(accepted_env["accepted_by"], "admin")
        
        # Accepted envelope SHOULD authorize execution
        auth_result = authorize_execution(self.workspace, env["envelope_id"])
        self.assertTrue(auth_result["authorized"])
        self.assertEqual(auth_result["envelope"]["envelope_id"], env["envelope_id"])

    def test_invalid_envelope_id(self):
        with self.assertRaises(TaskEnvelopeError):
            accept_envelope(self.workspace, "invalid-id/../../../etc/passwd", "admin")
            
        with self.assertRaises(TaskEnvelopeError):
            authorize_execution(self.workspace, "invalid id")

    def test_malformed_envelope(self):
        # Create a malformed envelope directly
        env_dir = self.workspace / ".etg" / "task_envelopes"
        env_dir.mkdir(parents=True, exist_ok=True)
        malformed_path = env_dir / "task-envelope-1234.json"
        malformed_path.write_text("{ malformed json")
        
        # Should not crash hydration functions, but produce an error envelope
        errors = get_error_envelopes(self.workspace)
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]["status"], "error")
        self.assertIn("Malformed", errors[0]["error"])
        
        pending = get_pending_envelopes(self.workspace)
        self.assertEqual(len(pending), 0)

    def test_hydration_vector_integration(self):
        # Setup fake workspace
        etg_dir = self.workspace / ".etg"
        etg_dir.mkdir(parents=True, exist_ok=True)
        (etg_dir / "entigram.yaml").write_text("")

        env = create_envelope(
            self.workspace,
            intent="Integration test",
            proposed_entities=[],
            invariants=[],
            affected_paths=[],
            validation_commands=[],
            uncertainty_unknowns=[],
            agent_id="test_agent"
        )
        
        # Also create a malformed file
        malformed_path = self.workspace / ".etg" / "task_envelopes" / "task-envelope-bad.json"
        malformed_path.write_text("Not JSON")
        
        vector_str = get_hydration_vector(self.workspace, compact=True)
        
        # Simple extraction from the string
        self.assertIn('"pending":[{"affected_paths":[]', vector_str)
        self.assertIn(env["envelope_id"], vector_str)
        self.assertIn("task-envelope-bad", vector_str)
        self.assertIn("Malformed or unreadable", vector_str)
        
    def test_no_directory_created_on_read(self):
        # Reading should not create .etg/task_envelopes if it doesn't exist
        get_pending_envelopes(self.workspace)
        env_dir = self.workspace / ".etg" / "task_envelopes"
        self.assertFalse(env_dir.exists())
        
        # authorize_execution should also not create it
        auth_result = authorize_execution(self.workspace, "task-envelope-not-exist")
        self.assertFalse(auth_result["authorized"])
        self.assertFalse(env_dir.exists())

if __name__ == '__main__':
    unittest.main()
