"""Keep only UGC creator gigs: a brand paying a creator to film content.

Scraped boards (Craigslist, Freelancer, LinkedIn, Upwork) mix in acting, journalism,
editing, social media management, ambassador shifts and AI animation. Those are not
what a UGC creator is looking for, so they never go live.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, Optional, Tuple

# (reason, where, pattern). "title" rules only read the listing title: those words show up
# inside genuine UGC briefs ("street-interview style", "designer jewelry", "photos and videos").
_RULES: Tuple[Tuple[str, str, str], ...] = (
    ("journalism", "any", r"journalists?|reporters?|correspondents?|podcasters?|podcast hosts?|news anchors?|"
                          r"conduct an interview|online interview"),
    ("journalism", "title", r"interviews?|interviewers?|podcasts?"),
    ("acting", "any", r"actors?|actress(?:es)?|casting calls?|now casting|self[- ]?tapes?|theat(?:er|re)"),
    ("acting", "title", r"acting|casting|extras|performers?"),
    ("modeling", "any", r"promotional models?|models? (?:needed|wanted)|cover models?|fit models?|hand models?"),
    ("modeling", "title", r"models?|modell?ing"),
    ("voice", "any", r"voice (?:over )?talent|voice actors?|voice artists?|dubbing"),
    ("voice", "title", r"voice[- ]?overs?|narrators?|voiced"),
    ("ambassador", "any", r"brand ambassadors? (?:needed|wanted)|promoters?|product sampling|street team|"
                          r"event staff|booth (?:staff|representatives?)"),
    ("ambassador", "title", r"ambassadors?"),
    ("live_host", "any", r"live ?stream(?:ers?|ing)? hosts?|live hosts?|livestream(?:ing)?|tiktok live|dj|djs|hologram"),
    ("live_host", "title", r"streamers?|twitch|live shopping"),
    ("editing", "any", r"video editors?|editing services|edit (?:raw|their|our|the) footage|clippers?|post[- ]production"),
    ("editing", "title", r"edit|editing|edits|editors?"),
    ("smm", "any", r"social media (?:manager|management|specialist|strategist|assistant|coordinator|marketer)|"
                   r"community managers?|account managers?|manage (?:and|&) grow|grow (?:our|my|the|a) "
                   r"(?:\w+ )?(?:account|page|channel)|plan and run|marketing (?:managers?|assistants?|assistance|"
                   r"strategists?|specialists?)|branding strategists?|growth (?:hackers?|marketers?|managers?)|"
                   r"facebook growth|ads? managers?|media buyers?|social media assistance"),
    ("writing", "any", r"copywrit\w*|blog posts?|seo|articles?|ghostwrit\w*|newsletters?"),
    ("writing", "title", r"writ(?:e|er|ers|ing)|blog(?:gers?|ging)?|twitter|tweets?"),
    ("design_ai", "any", r"graphic design\w*|motion design\w*|3d|animat(?:ed|ion|ions|ors?)|vfx|cgi|"
                         r"ai[- ]generated|ai videos?|ai content|ai avatars?|realistic ai|higgsfield"),
    ("design_ai", "title", r"designers?|graphic|desain|design"),
    ("ai_title", "title", r"ai"),
    ("production_crew", "any", r"videograph(?:ers?|y)|filmmak(?:ers?|ing)|documentar(?:y|ies)|camera ?(?:man|operators?)|"
                               r"cinematograph\w*|av (?:event )?technicians?|drone pilots?"),
    ("production_crew", "title", r"photograph(?:ers?|y)"),
    ("sales", "any", r"sales[- ]driven|sales (?:reps?|challenge|associates?)|commission[- ]only|performance[- ]based|"
                     r"mlm|monetize your|recruitment of|onlyfans|ai training|datasets?|data collection"),
    ("sales", "title", r"affiliates?|commissions?|surveys?|lead[- ]gen"),
    ("employment", "any", r"full[- ]time|part[- ]time|internships?|interns?|w-?2|salary|benefits package|401k"),
    ("other_role", "any", r"tour guides?|lead (?:\w+ )?tours|bartenders?|waiters?|waitress|nannies|nanny|"
                          r"tutors?|virtual assistants?|customer (?:service|support) reps?|data entry|"
                          r"web developers?|cosplay\w*|mascots?"),
)
_COMPILED = tuple((name, where, re.compile(rf"(?i)\b(?:{pattern})\b")) for name, where, pattern in _RULES)

# A UGC title can say "UGC actor" or "part-time UGC creator" and still be the real thing.
_UGC_TITLE = re.compile(r"(?i)\bugc\b|user[- ]generated")
_UGC_TITLE_FORGIVES = frozenset({"acting", "modeling", "employment", "ai_title"})

_POSITIVE = re.compile(
    r"(?i)\b(?:ugc|user[- ]generated|content creators?|creators?|influencers?|tiktok|reels?|instagram|"
    r"youtube shorts|shorts|short[- ]form|videos?|testimonials?|unboxing|product reviews?|review videos?|"
    r"on[- ]camera|talking[- ]head|b-?roll|demo videos?|promo(?:tional)? videos?|selfie videos?|"
    r"vertical (?:video|9:16)|9:16|content)\b"
)


def _text(*parts: Any) -> str:
    out = []
    for part in parts:
        if isinstance(part, (list, tuple, set)):
            out.extend(str(p) for p in part if p)
        elif part:
            out.append(str(part))
    return " ".join(out)


def ugc_gig_verdict(title: Any, description: Any = "", extra: Optional[Iterable[Any]] = None) -> Tuple[bool, str]:
    """(is_ugc, reason). Reason is the rule name that rejected it, or 'ok'."""
    title_s = _text(title)
    body = _text(title, description, list(extra or []))
    ugc_title = bool(_UGC_TITLE.search(title_s))
    for name, where, pattern in _COMPILED:
        if ugc_title and name in _UGC_TITLE_FORGIVES:
            continue
        if pattern.search(title_s if where == "title" else body):
            return False, name
    if not _POSITIVE.search(body):
        return False, "no_ugc_signal"
    return True, "ok"


def is_ugc_gig(gig: Optional[Dict[str, Any]]) -> bool:
    """Works on scanner payloads, opportunity rows and Polly cards."""
    gig = gig or {}
    ok, _ = ugc_gig_verdict(
        gig.get("title") or gig.get("product_name"),
        gig.get("deliverable_summary") or gig.get("campaign_description") or gig.get("blurb"),
        gig.get("creator_niches") or ([gig["niche_required"]] if gig.get("niche_required") else None),
    )
    return ok
