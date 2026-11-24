from unittest import mock

from services import autopilot_recap as ar


def test_trial_recap_mentions_weekends_and_tracker():
    text = ar.recap_text(5, 3, True, "Maya")
    assert text.startswith("You're all set, Maya.")
    assert "up to 5 pitches go out" in text
    assert "weekends included" in text
    assert "Tracker" in text


def test_pro_recap_uses_their_plan():
    text = ar.recap_text(2, 3, False)
    assert "**Every weekday:**" in text and "up to 3 pitches" in text
    assert "weekends" not in text


def test_post_setup_recap_closes_first_run_and_posts_once():
    with mock.patch('services.polly_autopilot.settings', return_value={
            'trial_started_at': object(), 'monthly_target': 400, 'send_days': [0, 1, 2, 3, 4, 5, 6]}), \
            mock.patch('services.polly_autopilot.daily_sends', return_value=5), \
            mock.patch('services.polly_autopilot.post_thread_note') as post, \
            mock.patch('services.creator_tracker.mark_milestone') as mark:
        msg = ar.post_setup_recap(object(), 9, 'Ana')
    mark.assert_called_once_with(mock.ANY, 9, 'first_run_done')
    assert post.call_args.kwargs['dedupe_key'] == ar.RECAP_KEY
    assert msg['task_chips'][0]['href'] == ar.TRACKER_HREF
    assert 'weekends included' in msg['content']
