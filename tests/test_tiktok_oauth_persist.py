import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class TestSettingsReturn(unittest.TestCase):
    def test_settings_source_and_path(self):
        from social_verification_routes import _is_settings_return
        self.assertTrue(_is_settings_return('', 'settings'))
        self.assertTrue(_is_settings_return(
            'http://localhost:3000/creator/dashboard/settings', ''
        ))
        self.assertFalse(_is_settings_return('https://app.newcollab.co/onboarding', ''))


class TestTikTokHandle(unittest.TestCase):
    def test_prefers_username(self):
        from social_verification_routes import _tiktok_handle_from_user_info

        self.assertEqual(
            _tiktok_handle_from_user_info(
                {"username": "cdcparis", "display_name": "CDC Paris"}
            ),
            "cdcparis",
        )

    def test_falls_back_to_deep_link_not_display_name(self):
        from social_verification_routes import _tiktok_handle_from_user_info

        self.assertEqual(
            _tiktok_handle_from_user_info(
                {
                    "display_name": "CDC Paris",
                    "profile_deep_link": "https://www.tiktok.com/@cdcparis",
                }
            ),
            "cdcparis",
        )

    def test_empty_when_no_real_handle(self):
        from social_verification_routes import _tiktok_handle_from_user_info

        self.assertEqual(
            _tiktok_handle_from_user_info({"display_name": "CDC Paris"}),
            "",
        )


class TestTikTokVideoMapping(unittest.TestCase):
    def test_maps_stat_fields(self):
        from social_verification_routes import _fetch_tiktok_videos

        class _Resp:
            content = b"{}"

            def json(self):
                return {
                    "error": {"code": "ok"},
                    "data": {
                        "videos": [
                            {
                                "id": "123",
                                "title": "hi",
                                "cover_image_url": "https://x",
                                "share_url": "https://tiktok.com/@x/video/123",
                                "like_count": 40,
                                "comment_count": 2,
                                "share_count": 1,
                                "view_count": 900,
                            }
                        ]
                    },
                }

        with patch("services.tiktok_login_kit.requests.post", return_value=_Resp()):
            videos = _fetch_tiktok_videos("token")
        self.assertEqual(len(videos), 1)
        self.assertEqual(videos[0]["likes"], 40)
        self.assertEqual(videos[0]["comments"], 2)
        self.assertEqual(videos[0]["views"], 900)
