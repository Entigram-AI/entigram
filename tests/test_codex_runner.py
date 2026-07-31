import unittest
from types import SimpleNamespace
from unittest.mock import patch

from entigram.cli_runner.runner import (
    execute_headless_model,
    launch_agent,
    list_ollama_models,
)


class TestCodexRunner(unittest.TestCase):
    def test_codex_uses_native_initial_prompt(self):
        prompt = "\x1b[200~Initialize from .etg/boot.json. Silent boot. Ready.\x1b[201~"

        with patch("platform.system", return_value="Darwin"):
            with patch("subprocess.run") as run:
                success, message = launch_agent(
                    ".",
                    "Codex",
                    yolo=True,
                    initial_prompt=prompt,
                    model="gpt-5",
                )

        self.assertTrue(success)
        self.assertEqual(message, "Launched Codex with native initial prompt.")
        run.assert_called_once()
        args = run.call_args.args[0]
        self.assertEqual(args[:2], ["osascript", "-e"])
        self.assertIn("codex --model gpt-5 --ask-for-approval never", args[2])
        self.assertIn("Initialize from .etg/boot.json. Silent boot. Ready.", args[2])
        self.assertNotIn("\x1b[200~", args[2])
        self.assertNotIn("\x1b[201~", args[2])
        self.assertNotIn("^[[200~", args[2])
        self.assertNotIn("^[[201~", args[2])
        self.assertNotIn("/usr/bin/expect", args[2])

    def test_ollama_uses_selected_launch_option_and_model(self):
        with patch("platform.system", return_value="Darwin"):
            with patch("subprocess.run") as run:
                success, message = launch_agent(
                    ".",
                    "Ollama",
                    model="llama3.2:latest",
                    ollama_launch_option="Codex",
                )

        self.assertTrue(success)
        self.assertEqual(message, "Launched and focused Terminal.")
        run.assert_called_once()
        args = run.call_args.args[0]
        self.assertEqual(args[:2], ["osascript", "-e"])
        self.assertIn("ollama launch codex --model llama3.2:latest", args[2])

    @patch.dict("os.environ", {"OLLAMA_HOST": "http://192.168.1.200:11434"})
    def test_ollama_launch_preserves_remote_host(self):
        with patch("platform.system", return_value="Darwin"):
            with patch("subprocess.run") as run:
                success, message = launch_agent(
                    ".",
                    "Ollama",
                    model="frob/glm-5.2:latest",
                    ollama_launch_option="Claude Code",
                )

        self.assertTrue(success)
        self.assertEqual(message, "Launched and focused Terminal.")
        args = run.call_args.args[0]
        self.assertIn("env OLLAMA_HOST=http://192.168.1.200:11434 ollama launch claude", args[2])
        self.assertIn("--model frob/glm-5.2:latest", args[2])

    def test_ollama_defaults_to_claude_and_qwen3(self):
        with patch("platform.system", return_value="Darwin"):
            with patch("subprocess.run") as run:
                success, message = launch_agent(".", "Ollama")

        self.assertTrue(success)
        self.assertEqual(message, "Launched and focused Terminal.")
        args = run.call_args.args[0]
        self.assertIn("ollama launch claude --model qwen3", args[2])

    def test_list_ollama_models_parses_local_models(self):
        output = """NAME              ID              SIZE      MODIFIED
qwen3:latest      abc123          5.2 GB    2 days ago
llama3.2:latest   def456          2.0 GB    1 week ago
"""
        with patch("subprocess.run", return_value=SimpleNamespace(stdout=output)):
            self.assertEqual(list_ollama_models(), ["qwen3:latest", "llama3.2:latest"])

    def test_headless_antigravity_keeps_permissions_enabled_by_default(self):
        with patch("subprocess.run", return_value=SimpleNamespace(stdout="ENTITY: Safe")) as run:
            output = execute_headless_model("model this", engine="Antigravity")

        self.assertEqual(output, "ENTITY: Safe")
        command = run.call_args.args[0]
        self.assertEqual(command, ["agy", "run"])
        self.assertNotIn("--dangerously-skip-permissions", command)

    def test_headless_runner_uses_selected_engine_in_read_only_mode(self):
        with patch("subprocess.run", return_value=SimpleNamespace(stdout="ENTITY: Safe")) as run:
            execute_headless_model("model this", engine="Codex", model="gpt-5")

        command = run.call_args.args[0]
        self.assertEqual(command[:4], ["codex", "exec", "--sandbox", "read-only"])
        self.assertIn("gpt-5", command)


if __name__ == "__main__":
    unittest.main()
