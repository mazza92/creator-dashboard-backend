"""Transactional waitlist emails — joined, approved, rejected.

Approved has its own template (approved_email.html): the brands Polly lined up and one button into Polly.

Uses the same welcome_email.html shell as resume-onboarding so branding stays consistent.
Lifecycle product emails stay off until the creator is approved; these status emails
are exempt from that gate because the waitlisted creator is the audience.
"""
from html import escape
from urllib.parse import urlencode

WAITLIST_APP_URL = 'https://app.newcollab.co'
FOR_YOU_URL = f'{WAITLIST_APP_URL}/creator/dashboard/for-you'
FREE_PITCHES = 3
PRO_DAILY_PITCHES = 20
_MONO_COLORS = ('#1f2937', '#b4436c', '#2563eb', '#7c3aed', '#0f766e', '#9a5b00')


def _initials(name):
    words = [w for w in ''.join(c if c.isalnum() else ' ' for c in str(name or '')).split() if w]
    return ((words[0][0] + words[1][0]) if len(words) > 1 else (words[0][:2] if words else '?')).upper()


LOGO_BOX = (96, 36)      # the tile's logo area, in CSS px
LOGO_MIN = (48, 24)      # smaller than this on both sides blurs or vanishes: show initials instead


def image_size(head):
    """(width, height) from the first bytes of a PNG, GIF, JPEG or WebP; None when unknown."""
    import struct
    try:
        if head[:8] == b'\x89PNG\r\n\x1a\n':
            return struct.unpack('>II', head[16:24])
        if head[:6] in (b'GIF87a', b'GIF89a'):
            return struct.unpack('<HH', head[6:10])
        if head[:4] == b'RIFF' and head[8:12] == b'WEBP':
            kind = head[12:16]
            if kind == b'VP8X':
                return (1 + int.from_bytes(head[24:27], 'little'), 1 + int.from_bytes(head[27:30], 'little'))
            if kind == b'VP8 ':
                w, h = struct.unpack('<HH', head[26:30])
                return (w & 0x3fff, h & 0x3fff)
            if kind == b'VP8L':
                b = int.from_bytes(head[21:25], 'little')
                return ((b & 0x3fff) + 1, ((b >> 14) & 0x3fff) + 1)
        if head[:2] == b'\xff\xd8':
            i = 2
            while i + 9 < len(head):
                if head[i] != 0xFF:
                    i += 1
                    continue
                marker = head[i + 1]
                if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                    i += 2
                    continue
                length = struct.unpack('>H', head[i + 2:i + 4])[0]
                if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                    h, w = struct.unpack('>HH', head[i + 5:i + 9])
                    return (w, h)
                i += 2 + length
    except Exception:
        return None
    return None


def logo_fit(size):
    """Display size inside LOGO_BOX that keeps the aspect ratio and never scales a logo up (upscaling is
    what made small logos blurry). None when the logo is too small to read: initials then."""
    if not size or not size[0] or not size[1]:
        return None
    w, h = size
    if w < LOGO_MIN[0] and h < LOGO_MIN[1]:
        return None
    scale = min(LOGO_BOX[0] / w, LOGO_BOX[1] / h, 1.0)
    dw, dh = max(1, round(w * scale)), max(1, round(h * scale))
    if dw < 28 and dh < 16:
        return None
    return dw, dh


def email_logo(url, timeout=3):
    """A brand logo an inbox will show cleanly: {'url', 'width', 'height'} or None (initials instead).
    Email clients can't fall back on a broken image, so it's checked at send time: https, a 200, a raster
    image (Gmail and Outlook don't render SVG), and big enough to stay sharp at its display size."""
    url = str(url or '').strip()
    if not url.startswith('https://') or '.svg' in url.lower().split('?')[0]:
        return None
    try:
        import requests
        resp = requests.get(url, timeout=timeout, stream=True, allow_redirects=True,
                            headers={'User-Agent': 'Mozilla/5.0 (compatible; NewcollabMailer/1.0)'})
        try:
            ctype = (resp.headers.get('Content-Type') or '').split(';')[0].strip().lower()
            if resp.status_code != 200 or not ctype.startswith('image/') or 'svg' in ctype or 'icon' in ctype:
                return None
            head = b''
            for chunk in resp.iter_content(8192):
                head += chunk
                if len(head) >= 65536 or image_size(head):
                    break
        finally:
            resp.close()
        fit = logo_fit(image_size(head))
        if not fit:
            return None
        return {'url': url, 'width': fit[0], 'height': fit[1]}
    except Exception:
        return None


def approved_pick_url(pick):
    """The approval email's button. With a pick it opens Polly on that brand: a creator still on the first-run
    welcome sees the welcome with that brand leading it (the frontend never posts a pitch over the welcome);
    a creator past it gets "Pitch <brand> for me"."""
    if not pick:
        return FOR_YOU_URL
    return f"{FOR_YOU_URL}?" + urlencode({
        'chip': 'pitch_brand', 'brand_id': str(pick['brand_id']), 'brand_name': pick.get('brand_name') or '',
        'utm_source': 'email', 'utm_medium': 'transactional', 'utm_campaign': 'approved',
    })


def approved_picks(user_id=None, creator_id=None, limit=3):
    """The first brands the Polly welcome will show this creator (same pitch plan, same order), plus their first
    name and niche. Plans the week first when nothing is planned yet. Never raises: {} on any failure, and the
    email then goes out without the picks card."""
    if not user_id and not creator_id:
        return {}
    conn = None
    try:
        from psycopg2.extras import RealDictCursor
        from pr_crm_routes import get_db_connection
        from services.creator_tracker import first_run_payload, planned_rows
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""SELECT c.*, u.first_name FROM creators c JOIN users u ON u.id = c.user_id
                       WHERE """ + ('c.id = %s' if creator_id else 'c.user_id = %s') + " LIMIT 1",
                    (creator_id or user_id,))
        creator = cur.fetchone()
        if not creator:
            return {}
        cid = int(creator['id'])
        if not planned_rows(conn, cid):
            from routes.creator_tracker import _plan
            _plan(conn, cid, dict(creator))
        payload = first_run_payload(conn, cid) or {}
        conn.commit()
        top = (payload.get('brands') or [])[:limit]
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=max(1, len(top))) as pool:  # checked side by side: ~3s worst case
            logos = list(pool.map(lambda b: email_logo(b.get('logo')), top))
        brands = [{
            'brand_id': int(b['brand_id']), 'brand_name': b.get('brand_name') or 'Brand', 'logo': logos[i],
            'initials': _initials(b.get('brand_name')), 'color': _MONO_COLORS[i % len(_MONO_COLORS)],
        } for i, b in enumerate(top)]
        niches = [str(n).replace('_', ' ').lower() for n in (payload.get('niches') or [])][:2]
        return {'first_name': (creator.get('first_name') or '').strip(), 'brands': brands,
                'niche': ' and '.join(niches)}
    except Exception as err:
        print(f'[waitlist] approved picks skipped user={user_id} creator={creator_id}: {err}')
        return {}
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def waitlist_email_context(kind, name, reason=None, as_pro=False, picks=None, extra=None):
    """Pure template context. Safe to unit-test without SMTP."""
    safe_name = escape((name or 'there').strip() or 'there')
    if kind == 'joined':
        return {
            'subject': "You're on the Newcollab waitlist",
            'preheader': 'Most approvals happen within 24 hours. We will email you the moment you are in.',
            'action_url': f'{WAITLIST_APP_URL}/creator/waitlist',
            'action_text': 'Check your waitlist',
            'message': (
                f'<p>Hi {safe_name},</p>'
                f'<p>Your creator profile is in the review queue. We look at every application by hand '
                f'so brands only hear from real, active creators.</p>'
                f'<p>Most approvals happen within 24 hours. We will email you the moment you are in, '
                f'and in the meantime show you the brands already lined up for your niche.</p>'
                f'<p>Pro members skip the line and get in today.</p>'
            ),
        }
    if kind == 'approved':
        picks = (picks or {}).get('brands') or []
        niche = escape((extra or {}).get('niche') or '')
        return {
            'template': 'approved_email.html',
            'subject': "You're in \U0001F389 Your first brands are waiting",
            'preheader': (f'Polly already picked brands for your {niche} content. Send your first pitch today.'
                          if niche else 'Polly already picked your first brands. Send your first pitch today.'),
            'action_url': approved_pick_url(picks[0] if picks else None),
            'action_text': 'Send my first pitch \u2192',
            'first_name': safe_name,
            'as_pro': bool(as_pro),
            'badge': 'Pro unlocked' if as_pro else 'Profile approved',
            'picks': picks,
            'picks_note': f'Matched to your {niche} content.' if niche else 'Matched to your content.',
            'fine_print': (f'Takes about 2 minutes \u00b7 Up to {PRO_DAILY_PITCHES} pitches a day with Pro' if as_pro
                           else f'Takes about 2 minutes \u00b7 {FREE_PITCHES} free pitches every month'),
            # Plain-text fallback for any client or test that reads `message`.
            'message': (
                f'<p>Hi {safe_name},</p>'
                f"<p>{'Pro unlocked instant access. ' if as_pro else ''}Your creator profile is approved. "
                f"Brands pay creators like you for UGC. Let's land your first one.</p>"
            ),
        }
    if kind == 'rejected':
        safe_reason = escape((reason or '').strip()) or 'Your profile does not meet our current quality bar.'
        return {
            'subject': 'Update on your Newcollab application',
            'preheader': 'We could not approve your profile this time.',
            'action_url': 'mailto:team@newcollab.co?subject=Creator%20application',
            'action_text': 'Email the team',
            'message': (
                f'<p>Hi {safe_name},</p>'
                f'<p>Thank you for applying. After reviewing your profile, we are unable to approve your creator account right now.</p>'
                f'<p><strong>Reason:</strong> {safe_reason}</p>'
                f'<p>This keeps matches high-quality for brands. Keep posting, and reach out if you have questions.</p>'
            ),
        }
    raise ValueError(f'Unknown waitlist email kind: {kind}')


def send_waitlist_email(kind, email, name, user_id=None, reason=None, as_pro=False, creator_id=None):
    """Send a waitlist transactional email. Returns True on success."""
    if not email:
        return False
    picks = approved_picks(user_id=user_id, creator_id=creator_id) if kind == 'approved' else {}
    if picks.get('first_name'):
        name = picks['first_name']
    ctx = waitlist_email_context(kind, name, reason=reason, as_pro=as_pro, picks=picks, extra=picks)
    try:
        from email_cron_routes import send_template_email
        success, err = send_template_email(
            to_email=email,
            template_name=ctx.get('template') or 'welcome_email.html',
            subject=ctx['subject'],
            context={**{k: v for k, v in ctx.items() if k != 'template'}, 'user_id': user_id},
            allow_waitlisted=True,
            tags=[
                {'name': 'category', 'value': 'waitlist'},
                {'name': 'template', 'value': f'waitlist_{kind}'},
            ],
        )
        if not success:
            print(f'Waitlist {kind} email failed for {email}: {err}')
        return bool(success)
    except Exception as e:
        print(f'Waitlist {kind} email error for {email}: {e}')
        return False
