"""Weekly Paid UGC round-up email: every live paid brief on the board, newest first."""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from html import escape
from typing import Any, Dict, List, Optional

from services.paid_gigs_board import _age_days, _pay_usd, board_cards, greeting_brand, public_card

def _public_url(env_name: str, default: str) -> str:
    """Broadcasts go to real inboxes, so a local dev URL in .env must never leak into links."""
    url = (os.getenv(env_name) or '').rstrip('/')
    if not url.startswith('https://') or 'localhost' in url or '127.0.0.1' in url:
        return default
    return url


FRONTEND_URL = _public_url('FRONTEND_URL', 'https://newcollab.co')
BACKEND_URL = _public_url('BACKEND_URL', 'https://api.newcollab.co')
BOARD_PATH = '/creator/dashboard/paid-gigs'
ROUNDUP_SLUG = 'paid_ugc_roundup'
ROUNDUP_LIMIT = 6

_LIVE_SQL = '''
    SELECT
        id, brand_name, brand_category, brand_logo_url, brand_website,
        product_name, campaign_description,
        pr_value_usd, creator_count_range, shipping_regions, follower_ranges,
        content_types, creator_niches, additional_notes,
        spots_total, spots_filled, closes_at,
        created_at, published_at, listing_brief
    FROM opportunities
    WHERE status = 'live'
      AND (closes_at IS NULL OR closes_at > NOW())
      AND (
            additional_notes ILIKE '%%[scanner:%%'
            OR (
                pr_brand_id IS NULL
                AND COALESCE(spots_filled, 0) < COALESCE(NULLIF(spots_total, 0), 1)
            )
          )
    ORDER BY created_at DESC
'''


def live_board_cards(cursor) -> List[Dict[str, Any]]:
    """Every open paid brief, newest first, as the board shows it to a creator with no niche."""
    from opportunities_routes import _opportunity_card

    cursor.execute(_LIVE_SQL)
    rows = [_opportunity_card(opp, set(), set())[0] for opp in cursor.fetchall()]
    return board_cards(rows, 0)


def _ago(days: Optional[float]) -> str:
    if days is None:
        return ''
    if days < 1:
        return 'Today'
    if days < 2:
        return 'Yesterday'
    if days < 7:
        return f'{int(days)}d ago'
    weeks = int(days // 7)
    return f'{weeks}w ago'


_EMOJI_RE = re.compile('[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F]')
_DANGLING_RE = re.compile(r'[\s&+,:;/\-\u2013\u2014|]+$')


def _title(headline: str, brand: str) -> str:
    """Scanned headlines arrive cut mid-phrase, as 'Creator', or as the brand name again."""
    text = _EMOJI_RE.sub('', headline or '')
    text = re.sub(r'\s+', ' ', text).strip()
    text = re.sub(r'(?i)^(now hiring|hiring|urgent)\b[\s:!\-\u2013\u2014]*', '', text)
    if text.count('(') > text.count(')'):
        text = text[: text.rfind('(')]
    text = _DANGLING_RE.sub('', text).strip()
    if text.isupper() and len(text) > 4:
        text = text.title()
    if brand and (not text or text.lower() == brand.lower() or len(text.split()) < 2):
        return f'{brand} UGC campaign'
    return text or 'Paid UGC brief'


def roundup_item(card: Dict[str, Any], now: Optional[datetime] = None) -> Dict[str, Any]:
    pub = public_card(card, now=now)
    brand = greeting_brand(card)
    logo = str(pub.get('logo') or '')
    title = _title(str(pub.get('headline') or card.get('product_name') or ''), brand)
    return {
        'id': pub.get('id'),
        'brand': brand,
        'initial': (brand or title or '?')[:1].upper(),
        'logo': logo if logo.startswith('https://') else '',
        'title': title,
        'pay': str(pub.get('pay_label') or '').strip(),
        'deliverable': str(pub.get('deliverable') or '').strip(),
        'location': str(pub.get('location') or '').strip(),
        'is_new': bool(pub.get('is_new')),
        'ago': _ago(_age_days(card, now)),
    }


TOP_PAY_COUNT = 3


def roundup_context(cards: List[Dict[str, Any]], limit: int = ROUNDUP_LIMIT, now: Optional[datetime] = None) -> Dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    week = [c for c in cards if (_age_days(c, now) or 99) <= 7]
    pays = [p for p in (_pay_usd(c) for c in cards) if p > 0]
    newest = cards[:limit]
    shown = {c.get('id') for c in newest}
    top_paying = sorted(
        (c for c in week if c.get('id') not in shown and _pay_usd(c) > 0),
        key=lambda c: -_pay_usd(c),
    )[:TOP_PAY_COUNT]
    return {
        'items': [roundup_item(c, now) for c in newest],
        'top_items': [roundup_item(c, now) for c in top_paying],
        'total': len(cards),
        'new_this_week': len(week),
        'top_pay': int(max(pays)) if pays else None,
        'more': max(len(cards) - limit - len(top_paying), 0),
    }


def roundup_subject(ctx: Dict[str, Any]) -> str:
    new = ctx.get('new_this_week') or 0
    top = ctx.get('top_pay')
    lead = f"{new} new paid UGC offers this week" if new else f"{ctx.get('total') or 0} paid UGC offers open now"
    return f"{lead} (up to ${top:,})" if top else lead


def board_url(utm_content: str = 'cta', upgrade: bool = False) -> str:
    url = (
        f"{FRONTEND_URL}{BOARD_PATH}?utm_source=email&utm_medium=polly_alert"
        f"&utm_campaign={ROUNDUP_SLUG}&utm_content={utm_content}"
    )
    return f"{url}&upgrade=pro" if upgrade else url


_SECTION = 'font-size:12px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;color:#6B7280;'


def _offer_line(item: Dict[str, Any], href: str) -> str:
    """Same row as Polly's 48h UGC roundup: bold title, one grey meta line, orange action."""
    title = escape(item['title'])
    new = '<span style="color:#C2410C;font-weight:700;">New</span> &middot; ' if item.get('is_new') else ''
    pay = f'<strong style="color:#111827;">{escape(item["pay"])}</strong>' if item.get('pay') else ''
    bits = [pay] + [escape(b) for b in (
        item.get('brand') or 'Brand confidential', item.get('deliverable'), item.get('location'), item.get('ago'),
    ) if b]
    meta = ' &middot; '.join(b for b in bits if b)
    link = escape(href, quote=True)
    return (
        f'<a href="{link}" style="color:#111827;font-weight:700;text-decoration:none;">{title}</a>'
        f'<br><span style="font-size:13px;color:#6B7280;">{new}{meta}</span>'
        f'<br><a href="{link}" style="font-size:13px;font-weight:600;color:#C2410C;">See offer &rarr;</a>'
    )


def roundup_email(first_name: str, ctx: Dict[str, Any], is_pro: bool, unsubscribe_url: str = '') -> Dict[str, str]:
    from services.polly_alerts import render_email

    subject = roundup_subject(ctx)
    item_href = board_url('offer')
    stats = [f"{ctx.get('total') or 0} open right now"]
    if ctx.get('top_pay'):
        stats.append(f"top offer ${ctx['top_pay']:,}")
    lines = [
        "Every brand paying for UGC, in one place, so you don't have to hunt across every board. "
        "Here's your radar for the week: newest first, then the best paying.",
        f'<span style="font-size:13px;color:#6B7280;">{escape(" · ".join(stats))}</span>',
        f'<span style="{_SECTION}">Newest</span>',
    ]
    lines += [_offer_line(item, item_href) for item in ctx.get('items') or []]
    if ctx.get('top_items'):
        lines.append(f'<span style="{_SECTION}">Top paying this week</span>')
        lines += [_offer_line(item, item_href) for item in ctx['top_items']]
    lines.append(escape('Tip: apply to new briefs in their first 48 hours, while there are fewer applicants.'))
    if not is_pro:
        lines.append(escape('Free accounts open the top 3 offers. Pro opens every brief and I write the application for you.'))
    see_all = f"See all {ctx.get('total') or 0} offers" if ctx.get('more') else 'Open the Paid UGC board'
    buttons = [{'label': see_all, 'href': board_url('cta')}]
    if not is_pro:
        buttons.append({'label': 'Go Pro', 'href': board_url('pro', upgrade=True)})
    headline = (
        f"Your paid UGC radar: {ctx['new_this_week']} new offers this week" if ctx.get('new_this_week')
        else 'Your paid UGC radar'
    )
    return {'subject': subject, 'html': render_email(first_name, headline, lines, buttons, unsubscribe_url)}
