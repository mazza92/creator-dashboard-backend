import json
import unittest
from unittest.mock import patch

from flask import Flask, jsonify

from services.polly import SayStreamer, _partial_json_string


class PartialJsonStringTests(unittest.TestCase):
    def test_decodes_escapes_and_stops_at_incomplete(self):
        self.assertEqual(_partial_json_string('Hi \\"you\\"\\n', 0), ('Hi "you"\n', False))
        self.assertEqual(_partial_json_string('ab\\', 0), ("ab", False))
        self.assertEqual(_partial_json_string('ab\\u00', 0), ("ab", False))
        self.assertEqual(_partial_json_string('caf\\u00e9" rest', 0), ("café", True))
        self.assertEqual(_partial_json_string('\\ud83d\\ude00!', 0), ("😀!", False))


class SayStreamerTests(unittest.TestCase):
    def _run(self, chunks):
        events = []
        streamer = SayStreamer(events.append)
        text = ""
        for chunk in chunks:
            text += chunk
            streamer.feed(text)
        return events

    def test_streams_say_for_chat_intent(self):
        events = self._run(['{"intent":"ch', 'at","say":"Hey ', 'there\\n', '**Ava**"', ',"brand_id":null}'])
        self.assertEqual(events[0], {"type": "intent", "intent": "chat"})
        said = "".join(e["text"] for e in events if e["type"] == "delta")
        self.assertEqual(said, "Hey there\n**Ava**")

    def test_tool_intent_does_not_stream_say(self):
        events = self._run(['{"intent":"suggest_brands","say":"Here are three"}'])
        self.assertEqual(events, [{"type": "intent", "intent": "suggest_brands"}])


class GeminiStreamTests(unittest.TestCase):
    def test_generate_json_streams_when_sink_set(self):
        import services.polly as polly

        chunks = ['{"intent":"chat",', '"say":"Real talk: ', 'pitch Glow Co."}']
        lines = [
            "data: " + json.dumps({"candidates": [{"content": {"parts": [{"text": c}]}}]})
            for c in chunks
        ] + ["data: " + json.dumps({"candidates": [{"finishReason": "STOP"}], "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 5}})]

        class FakeResp:
            status_code = 200

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def iter_lines(self, decode_unicode=True):
                return iter(lines)

        events = []
        polly.set_stream_sink(events.append)
        try:
            with patch.object(polly, "get_gemini_key", return_value="k"), \
                 patch.object(polly, "_model_candidates", return_value=["gemini-2.5-flash"]), \
                 patch.object(polly.requests, "post", return_value=FakeResp()) as post:
                out = polly._gemini_generate_json("sys", "user")
        finally:
            polly.clear_stream_sink()
        self.assertEqual(out["say"], "Real talk: pitch Glow Co.")
        self.assertIn("streamGenerateContent", post.call_args[0][0])
        said = "".join(e["text"] for e in events if e["type"] == "delta")
        self.assertEqual(said, "Real talk: pitch Glow Co.")


class ChatStreamEndpointTests(unittest.TestCase):
    def test_emits_deltas_then_done_payload(self):
        from routes import polly as polly_routes

        app = Flask(__name__)
        app.register_blueprint(polly_routes.polly_bp)

        def fake_chat():
            from services.polly import stream_sink
            sink = stream_sink()
            sink({"type": "intent", "intent": "chat"})
            sink({"type": "delta", "text": "Hi"})
            return jsonify({"success": True, "message": "Hi Ava"}), 200

        with patch.object(polly_routes, "chat", fake_chat):
            client = app.test_client()
            resp = client.post("/api/polly/chat/stream", json={"messages": []})
            body = b"".join(resp.response).decode()
        self.assertEqual(resp.mimetype, "text/event-stream")
        events = [json.loads(line[5:]) for line in body.splitlines() if line.startswith("data:")]
        self.assertEqual([e["type"] for e in events], ["intent", "delta", "done"])
        self.assertEqual(events[-1]["status"], 200)
        self.assertEqual(events[-1]["data"]["message"], "Hi Ava")


if __name__ == "__main__":
    unittest.main()
