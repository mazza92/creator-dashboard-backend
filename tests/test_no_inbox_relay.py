from unittest import mock

from services import autopilot_health as ah


def test_chat_note_offers_send_without_gmail_when_relay_is_on():
    with mock.patch('services.pitch_relay.relay_available', return_value=True):
        note = ah.chat_note(ah.GMAIL_KIND, {'no_inbox': True, 'waiting': 2})
    assert 'Send without Gmail' in note['text']
    assert note['chips'][0]['action'] == 'autopilot'


def test_chat_note_without_relay_keeps_connect_only():
    with mock.patch('services.pitch_relay.relay_available', return_value=False):
        note = ah.chat_note(ah.GMAIL_KIND, {'no_inbox': True})
    assert 'Send without Gmail' not in note['text']
