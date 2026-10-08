import json
import unittest
from unittest.mock import patch

from flask import Flask, jsonify

from services.polly_memory import repair_mojibake


class MojibakeRepairTests(unittest.TestCase):
    def test_em_dash_read_as_latin1_becomes_a_dash(self):
        broken = "Secretlab \u00e2\u0080\u0094 they're a fit."
        self.assertEqual(repair_mojibake(broken), "Secretlab \u2014 they're a fit.")

    def test_real_punctuation_is_left_alone(self):
        clean = "Secretlab \u2014 they're a fit. caf\u00e9"
        self.assertEqual(repair_mojibake(clean), clean)


class ChatStreamEndpointTests(unittest.TestCase):
    def test_emits_deltas_then_done_payload(self):
        from routes import polly as polly_routes

        app = Flask(__name__)
        app.register_blueprint(polly_routes.polly_bp)

        def fake_chat():
            from services.polly import stream_sink
            sink = stream_sink()
            sink({"type": "tool", "name": "search_directory"})
            sink({"type": "delta", "text": "Hi"})
            return jsonify({"success": True, "message": "Hi Ava"}), 200

        with patch.object(polly_routes, "chat", fake_chat):
            client = app.test_client()
            resp = client.post("/api/polly/chat/stream", json={"messages": []})
            body = b"".join(resp.response).decode()
        self.assertEqual(resp.mimetype, "text/event-stream")
        events = [json.loads(line[5:]) for line in body.splitlines() if line.startswith("data:")]
        self.assertEqual([e["type"] for e in events], ["tool", "delta", "done"])
        self.assertEqual(events[-1]["status"], 200)
        self.assertEqual(events[-1]["data"]["message"], "Hi Ava")


if __name__ == "__main__":
    unittest.main()
