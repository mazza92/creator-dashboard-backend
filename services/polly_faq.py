"""How Newcollab works, in answers Polly can give word for word.

One list feeds both the brain's prompt (FAQ_PROMPT) and the no-Gemini path
(faq_answer), so a real question never gets a canned menu instead of an answer.
"""

from __future__ import annotations

import re
from typing import List, Tuple

_FAQ: List[Tuple[str, str, str]] = [
    (
        "gifted_list",
        r"gifted list|applicant pool|in review|(am i|was i|i was|i'?m) (selected|picked|chosen)|did they (pick|choose|select) me",
        "**Applied** means you're in that brand's applicant pool for their open PR campaign. "
        "They haven't chosen yet. If they choose you, the status changes to **Picked** "
        "and you get an email. If they pick other creators, it shows **Not this time**.",
    ),
    (
        "brand_contact",
        r"reach out|contact me|email or (on )?(here|the app)|how will (they|i) (know|hear)|will they (email|message)",
        "If a brand picks you from a gifted list, Newcollab emails you and the status changes on "
        "**Timeline**. You don't need to email them. For pitches you send yourself, the brand replies "
        "to your own inbox.",
    ),
    (
        "timing",
        r"when (will|should|do|would) i hear|how long (does|will|until)|take months|hear back",
        "Most brands pick within 2 to 3 weeks of opening their roster, some take longer. While you "
        "wait, pitch one or two brands directly so you're not relying on one list.",
    ),
    (
        "how_added",
        r"how did .{1,40} add me|why (am i|was i) (on|added)|added me to",
        "You're on that list because you applied to it, in **Directory** or through me. Brands don't "
        "add creators themselves. It means you're in their applicant pool, not picked yet.",
    ),
    (
        "auto_apply",
        r"sent automatically|(need|have) to email the brand|(also|still) (need to )?email (them|the brand)|is my application sent",
        "Gifted list applications go to the brand automatically, with your kit. No email needed. "
        "Direct pitches are different: on the free plan you send them from your own email, then tap "
        "**I sent it**.",
    ),
    (
        "pro_automation",
        r"what (exactly )?is automated|copy each pitch|what does pro (do|automate|include)|what do i get (with|on) pro",
        "On Pro, **Autopilot** sends pitches from your own Gmail, about 3 every weekday, and follows "
        "up on day 4 and day 10 in the same thread. It needs your Gmail connected. Until then, "
        "pitches stay on the card for you to send yourself.",
    ),
    (
        "not_sent",
        r"(don'?t|dont|do not) think (it|this|that|they|these|u|you) (sent|went)|(did|didn'?t|didnt) (it|u|you) send|not sure (it|if it) (sent|went)|did u send",
        "Check your Sent folder first: if the email is there, it went out, so tap **I sent it**. "
        "If it isn't, open the pitch card, tap **Open email** (or **Copy email** and **Copy pitch**), "
        "send it from your inbox, then tap **I sent it**. On the free plan I never send for you.",
    ),
    (
        "replies",
        r"where (do|can|will) i see|see (if|whether) (they|the brand|brands?) repl|did they reply|how do i know if they repl",
        "Brands reply to your own email inbox, because the pitch goes out from your address. Tell me "
        "here when one replies and I'll update **Timeline** and write your answer.",
    ),
    (
        "remove_application",
        r"remove (an |my |the )?application|withdraw|(get|give) (my |the |a )?credits? back|refund (a |my |the )?credit|undo (an |my |the )?appl",
        "Applications can't be withdrawn once they're in, and that credit stays used. Pitch drafts "
        "are different: if one sits unsent for 7 days, the credit comes back automatically.",
    ),
    (
        "cancel",
        r"cancel (my |the )?(subscription|pro|plan|membership)|stop (paying|being charged|my subscription)|end (my )?(subscription|pro)",
        "Tap your profile picture, open **Account Settings**, then **Cancel subscription**. You keep "
        "Pro until the end of the period you've already paid for.",
    ),
    (
        "kit_seen",
        r"how do (the )?brands? see my kit|do (the )?brands? see my kit|see my (kit|profile|portfolio)",
        "Your kit link goes with every gifted list application and every pitch, so the brand opens "
        "**My Kit** from there. Keep it published and up to date.",
    ),
    (
        "credits",
        r"how many credits|what (is|are) (a )?credits?|credits? (reset|renew|come back)|when do (my )?credits",
        "Free accounts get 3 credits a month. A credit is used when I draft a pitch to a new brand or "
        "you apply to a gifted list. Follow-ups never use a credit. Pro removes the limit.",
    ),
]

_COMPILED = [(key, re.compile(rf"(?i)\b(?:{rx})"), answer) for key, rx, answer in _FAQ]

_STATUS_LINES = (
    "Timeline pitch statuses: Drafted (written, not sent) → Sent (waiting on the brand; follow-ups "
    "day 4 and 10) → Replied (interested / question / passed) → Shipping → Content due → Posted, or "
    "Dropped after 14 days with no reply. Open PR campaign statuses: Applied (in the applicant pool, "
    "brand hasn't chosen) → Picked → Done, or Not this time. A pitch is only Sent after they tap I "
    "sent it (or Autopilot sends it); it can only show a reply after it was sent. Brand cards also "
    "show how many creators pitched that brand in the last 30 days and when it last replied to anyone."
)

FAQ_PROMPT = (
    "HOW NEWCOLLAB WORKS (answer these questions directly from here, in your own words):\n"
    + "\n".join(f"- {answer.replace('**', '')}" for _, _, answer in _FAQ)
    + f"\n- {_STATUS_LINES}"
)


def faq_key(text: str) -> str:
    raw = (text or "").strip()
    if not raw:
        return ""
    for key, rx, _ in _COMPILED:
        if rx.search(raw):
            return key
    return ""


def faq_answer(text: str) -> str:
    key = faq_key(text)
    return next((answer for k, _, answer in _FAQ if k == key), "")
