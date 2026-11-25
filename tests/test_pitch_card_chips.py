"""The pitch card carries its own actions (Send email logs the send, Use autopilot), so the drafted-pitch message
has no 'I sent it' / 'More brands' chips and Polly never asks the creator to confirm a send (Nov 2026)."""


def test_drafted_pitch_has_no_confirm_chips():
    from services.polly import pitch_confirm_chips
    assert pitch_confirm_chips({"id": 5, "name": "HEVA"}) == []


def test_persona_points_at_send_email_not_i_sent_it():
    import services.polly_persona as persona
    src = open(persona.__file__).read()
    assert "**Copy email**" not in src
    assert "**Send email**" in src


def test_brain_ui_line_names_the_new_card():
    import services.polly_brain as brain
    src = open(brain.__file__).read()
    assert "Open email / Copy email / Copy pitch" not in src
    assert "Send email" in src
