import unittest
from unittest.mock import MagicMock, patch

from services.account_deletion import (
    CONFIRMATION_PHRASE,
    hash_email,
    purge_creator_account,
    validate_deletion_request,
)


class ValidateDeletionRequestTests(unittest.TestCase):
    def test_requires_delete_phrase(self):
        self.assertIsNotNone(
            validate_deletion_request({"confirmation": "yes", "email": "a@b.com"}, "a@b.com")
        )

    def test_requires_matching_email(self):
        self.assertIsNotNone(
            validate_deletion_request(
                {"confirmation": CONFIRMATION_PHRASE, "email": "other@b.com"},
                "a@b.com",
            )
        )

    def test_accepts_case_insensitive_email_and_phrase(self):
        self.assertIsNone(
            validate_deletion_request(
                {"confirmation": "delete", "email": "A@B.COM", "reason": "not_useful"},
                "a@b.com",
            )
        )

    def test_requires_a_reason(self):
        self.assertIsNotNone(
            validate_deletion_request({"confirmation": "DELETE", "email": "a@b.com"}, "a@b.com")
        )
        self.assertIsNotNone(
            validate_deletion_request({"confirmation": "DELETE", "email": "a@b.com", "reason": "nope"}, "a@b.com")
        )

    def test_hash_email_is_stable_and_lowercase(self):
        self.assertEqual(hash_email("A@B.com"), hash_email("a@b.com"))
        self.assertNotEqual(hash_email("a@b.com"), "a@b.com")


class PurgeCreatorAccountTests(unittest.TestCase):
    def test_deletes_related_rows_then_creator_and_user(self):
        cursor = MagicMock()
        cursor.fetchall.side_effect = [
            [{"table_name": "media_kits", "column_name": "creator_id"}],
            [{"table_name": "pr_email_reminders", "column_name": "user_id"}],
            [],
            [{"column_name": "first_name"}, {"column_name": "username"}],
            [],
            [{"column_name": "first_name"}],
        ]
        cursor.fetchone.return_value = {"column_name": "email"}
        cursor.rowcount = 1

        with patch("services.account_deletion._try_anonymize_email") as anonymize:
            purge_creator_account(cursor, 11, 22, "a@b.com")
            anonymize.assert_called()

        sql_blob = " ".join(str(call.args[0]) for call in cursor.execute.call_args_list)
        self.assertIn("DELETE FROM creators", sql_blob)
        self.assertIn("DELETE FROM users", sql_blob)
        self.assertIn("account_deletion_log", sql_blob.lower())


if __name__ == "__main__":
    unittest.main()


class DeletionGateTests(unittest.TestCase):
    def _cursor(self, creator_row, cancelled=False):
        cursor = MagicMock()
        cursor.fetchone.side_effect = [creator_row, ({"x": 1} if cancelled else None)]
        return cursor

    def test_free_creator_can_delete_with_free_reasons(self):
        from services.account_deletion import deletion_gate
        gate = deletion_gate(self._cursor({"subscription_tier": "free", "subscription_status": None,
                                           "stripe_subscription_id": None}), 5)
        self.assertEqual(gate["plan"], "free")
        self.assertFalse(gate["must_cancel_first"])
        self.assertIn("not_approved", [r["id"] for r in gate["reasons"]])

    def test_paying_pro_must_cancel_first(self):
        from services.account_deletion import deletion_gate
        gate = deletion_gate(self._cursor({"subscription_tier": "pro", "subscription_status": "active",
                                           "stripe_subscription_id": "sub_1", "subscription_started_at": None,
                                           "subscription_ends_at": None}), 5)
        self.assertEqual(gate["plan"], "pro")
        self.assertTrue(gate["must_cancel_first"])

    def test_pro_who_went_through_cancel_flow_can_delete(self):
        from services.account_deletion import deletion_gate
        gate = deletion_gate(self._cursor({"subscription_tier": "pro", "subscription_status": "active",
                                           "stripe_subscription_id": "sub_1", "subscription_started_at": None,
                                           "subscription_ends_at": None}, cancelled=True), 5)
        self.assertFalse(gate["must_cancel_first"])
        self.assertTrue(gate["cancel_scheduled"])

    def test_locked_past_due_creator_is_not_blocked(self):
        from services.account_deletion import deletion_gate
        gate = deletion_gate(self._cursor({"subscription_tier": "free", "subscription_status": "past_due",
                                           "stripe_subscription_id": "sub_1"}), 5)
        self.assertFalse(gate["must_cancel_first"])
