"""How Pro is described, in one place for Polly, emails and recaps (the frontend twin is src/config/proOffer.js).

Pro sells daily outreach we control: new pitches from the creator's Gmail every weekday, follow-ups, tracking.
It never promises a reply or a yes: that depends on the creator and the brand.
"""
from services.polly_autopilot import DEFAULT_FOLLOWUPS, DEFAULT_MONTHLY_TARGET, WEEKDAYS, sends_per_day

PRICE = "$19/mo"
DAILY_PITCHES = sends_per_day(DEFAULT_MONTHLY_TARGET, list(WEEKDAYS))
MONTHLY_PITCHES = DEFAULT_MONTHLY_TARGET

HEADLINE = "New brands pitched for you, every day."
PROMISE = (f"Every weekday I pick {DAILY_PITCHES} brands that fit you, write each pitch for that brand, send it "
           f"from your Gmail and follow up on day 4 and day 10. You see who opened your kit.")
SHORT = f"{DAILY_PITCHES} brands pitched for you every weekday, from your Gmail"
# For Polly's instructions: what she may and may not say about Pro.
RULE = (f"Pro = {PRICE}: Autopilot sends {DAILY_PITCHES} new pitches every weekday from the creator's own Gmail "
        f"(about {MONTHLY_PITCHES} a month by default; they can pick 30, 60, 100 or 150), with up to "
        f"{DEFAULT_FOLLOWUPS} follow-ups (day 4 and day 10) in the same thread, and tracks who opened their kit. "
        "Never promise that a brand will reply, say yes or send a deal: that depends on the creator and the brand. "
        "Describe what Pro does, not an outcome.")
