import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from datetime import datetime, timezone

from services.gemini_usage import gemini_spend, priced_from_response
from services.polly_llm_cost import (
    estimate_usd,
    rollup_usage,
    usage_from_anthropic,
    usage_from_gemini,
)


class PollyLlmCostTests(unittest.TestCase):
    def test_gemini_flash_1m_tokens(self):
        self.assertAlmostEqual(estimate_usd("gemini", "gemini-2.5-flash", 1_000_000, 0), 0.30)
        self.assertAlmostEqual(estimate_usd("gemini", "gemini-2.5-flash", 0, 1_000_000), 2.50)

    def test_haiku_rates(self):
        self.assertAlmostEqual(estimate_usd("anthropic", "claude-haiku-4-5", 1_000_000, 0), 1.00)
        self.assertAlmostEqual(estimate_usd("anthropic", "claude-haiku-4-5", 0, 1_000_000), 5.00)

    def test_usage_from_gemini_counts_thinking_as_output(self):
        row = usage_from_gemini(
            {
                "usageMetadata": {
                    "promptTokenCount": 1200,
                    "candidatesTokenCount": 80,
                    "thoughtsTokenCount": 20,
                    "cachedContentTokenCount": 200,
                }
            },
            "gemini-2.5-flash",
        )
        self.assertEqual(row["input_tokens"], 1000)
        self.assertEqual(row["output_tokens"], 100)
        self.assertEqual(row["cached_tokens"], 200)
        self.assertGreater(row["usd"], 0)

    def test_usage_from_anthropic(self):
        row = usage_from_anthropic(
            {"usage": {"input_tokens": 500, "output_tokens": 40}},
            "claude-haiku-4-5",
        )
        self.assertEqual(row["input_tokens"], 500)
        self.assertEqual(row["output_tokens"], 40)
        self.assertGreater(row["usd"], 0)

    def test_rollup_sums_calls(self):
        out = rollup_usage([
            {"usd": 0.001, "input_tokens": 100, "output_tokens": 10, "provider": "gemini", "model": "gemini-2.5-flash"},
            {"usd": 0.002, "input_tokens": 50, "output_tokens": 5, "provider": "gemini", "model": "gemini-2.5-flash"},
        ])
        self.assertEqual(out["calls"], 2)
        self.assertEqual(out["input_tokens"], 150)
        self.assertAlmostEqual(out["usd"], 0.003)


class _SpendCursor:
    def __init__(self, exists, rows):
        self.exists = exists
        self.rows = rows
        self._one = None

    def execute(self, sql, params=None):
        if "information_schema.tables" in sql:
            self._one = {"ok": 1} if self.exists else None
        else:
            self._one = None

    def fetchone(self):
        return self._one

    def fetchall(self):
        return self.rows


class GeminiUsageTests(unittest.TestCase):
    def test_prices_a_response_and_an_sdk_object(self):
        priced = priced_from_response("gemini-2.5-flash", {
            "usageMetadata": {"promptTokenCount": 1000, "candidatesTokenCount": 100},
        })
        self.assertEqual(priced["input_tokens"], 1000)
        self.assertEqual(priced["output_tokens"], 100)
        self.assertGreater(priced["usd"], 0)

        class Meta:
            prompt_token_count = 500
            candidates_token_count = 50
            thoughts_token_count = 0
            cached_content_token_count = 0

        class Response:
            usage_metadata = Meta()

        sdk = priced_from_response("gemini-2.5-flash", Response())
        self.assertEqual(sdk["input_tokens"], 500)
        self.assertEqual(priced_from_response("gemini-2.5-flash", {}), {})

    def test_spend_sums_logged_calls(self):
        out = gemini_spend(
            _SpendCursor(True, [{
                "source": "pitches", "calls": 4, "usd": 1.25,
                "input_tokens": 100, "output_tokens": 20,
            }]),
            datetime(2026, 9, 14, tzinfo=timezone.utc),
        )
        self.assertEqual(out["calls"], 4)
        self.assertAlmostEqual(out["usd"], 1.25)
        self.assertEqual(out["sources"][0]["source"], "pitches")
        missing = gemini_spend(_SpendCursor(False, []), datetime(2026, 9, 14, tzinfo=timezone.utc))
        self.assertEqual(missing["calls"], 0)


if __name__ == "__main__":
    unittest.main()
