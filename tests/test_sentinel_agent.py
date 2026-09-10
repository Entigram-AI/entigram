import unittest

from entigram.sentinel_agent import AGENT_NAME, agent_card, handle_request


class SentinelAgentTests(unittest.TestCase):
    def test_agent_card_is_a2a_discoverable(self):
        card = agent_card("http://endpoint:9010/")
        self.assertEqual(card["name"], AGENT_NAME)
        self.assertEqual(card["url"], "http://endpoint:9010")
        self.assertIn("text/plain", card["defaultInputModes"])

    def test_message_send_completes_without_side_effects(self):
        status, response = handle_request({
            "jsonrpc": "2.0",
            "id": "request-1",
            "method": "message/send",
            "params": {"message": {"role": "user", "parts": [{"kind": "text", "text": "hello"}]}},
        })
        self.assertEqual(status, 200)
        self.assertEqual(response["result"]["status"]["state"], "completed")
        text = response["result"]["status"]["message"]["parts"][0]["text"]
        self.assertIn("side-effect free", text)

    def test_unknown_method_is_rejected(self):
        status, response = handle_request({"jsonrpc": "2.0", "id": "1", "method": "tasks/get"})
        self.assertEqual(status, 400)
        self.assertEqual(response["error"]["code"], -32601)
