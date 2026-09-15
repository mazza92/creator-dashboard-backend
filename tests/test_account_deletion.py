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
                {"confirmation": "delete", "email": "A@B.COM"},
                "a@b.com",
            )
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
