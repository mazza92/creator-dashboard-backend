"""
Opportunities Routes for Creator Dashboard
Brand casting calls / job board layer for PR opportunities
"""

from flask import Blueprint, request, jsonify, session
from flask_jwt_extended import jwt_required, get_jwt_identity
from functools import wraps
import psycopg2
from psycopg2.extras import RealDictCursor
import os
from datetime import datetime, timedelta, date
import re
import requests

opportunities_bp = Blueprint('opportunities', __name__, url_prefix='/api/opportunities')


def _infer_short_niche(text: str) -> str | None:
    t = (text or '').lower()
    if any(w in t for w in ('parent', 'mom', 'dad', 'kids', 'family')):
        return 'Parenting'
    if any(w in t for w in ('beauty', 'skincare', 'makeup')):
        return 'Beauty'
    if any(w in t for w in ('fitness', 'gym', 'workout')):
        return 'Fitness'
    if any(w in t for w in ('food', 'recipe', 'cook')):
        return 'Food'
    if any(w in t for w in ('fashion', 'outfit', 'style')):
        return 'Fashion'
    return None


def _short_pay_label(pr_value_usd, campaign_description: str) -> str | None:
    """Compact pay signal for cards — avoid dumping long compensation blurbs."""
    if pr_value_usd:
        return f"${pr_value_usd}"

    m_pay = re.search(r'Pay:\s*(.+)', campaign_description or '', re.I)
    raw = m_pay.group(1).strip().split('\n')[0] if m_pay else ''
    if not raw:
        return None

    m_dollar = re.search(r'\$[\d,]+(?:\s*[-–]\s*\$?[\d,]+)?(?:\s*/\s*\w+)?', raw)
    if m_dollar:
        return m_dollar.group(0)
    if re.search(r'\bgift(ed)?\b|\bfree\s*product\b|\bproduct\s*only\b|\bpr\s*package\b', raw, re.I):
        return 'Gifted product'
    if re.search(r'\bunpaid\b|\bno\s*pay\b', raw, re.I):
        return 'Unpaid'
    if re.search(r'\bperformance\b|\bbonus|\bvolume\b|\bper\s*video\b|\bvideos?\s*per\b', raw, re.I):
        return 'Paid · volume + bonuses'
    if re.search(r'\bpaid\b|\bugc\b|\brate\b|\bcompensation\b', raw, re.I):
        return 'Paid'
    if len(raw) <= 32:
        return raw
    return 'Paid opportunity'


_PLACEHOLDER_EMAILS = frozenset({
    'sourced@newcollab.co',
    'mahery@newcollab.co',
})


def _is_real_apply_email(email) -> bool:
    e = (email or '').strip().lower()
    return bool(e) and '@' in e and '.' in e.split('@')[-1] and e not in _PLACEHOLDER_EMAILS


def _notes_get(notes: str, key: str):
    m = re.search(rf'(?:^|\|\s*){re.escape(key)}=(\S+)', notes or '', re.I)
    if not m:
        return None
    return m.group(1).strip().rstrip('|')


def _notes_set(notes: str, key: str, value) -> str:
    """Set or remove a key=value token in additional_notes (pipe-delimited)."""
    notes = (notes or '').strip()
    pattern = rf'(?:^|\|\s*){re.escape(key)}=\S+'
    notes = re.sub(pattern, '', notes, flags=re.I)
    notes = re.sub(r'\s*\|\s*', ' | ', notes).strip(' |')
    if value is None or value == '':
        return notes
    token = f'{key}={value}'
    return f'{notes} | {token}' if notes else token


def _resolve_apply_path(opp) -> dict:
    """
    Decide how a creator applies:
      - url: open external application URL
      - email: open creator mail app to Brand Email (mailto only)
      - kit: classic media-kit application email
    """
    notes = opp.get('additional_notes') or ''
    is_sourced = '[scanner:' in notes

    source_platform = None
    m_src = re.search(r'\[scanner:([^:\]]+):', notes)
    if m_src:
        source_platform = m_src.group(1)

    brand_email = (opp.get('brand_email') or '').strip()
    notes_email = _notes_get(notes, 'email')
    apply_email = brand_email if _is_real_apply_email(brand_email) else None
    if not apply_email and _is_real_apply_email(notes_email):
        apply_email = notes_email.strip()

    # Real application link from scanner/admin notes (not a generic homepage)
    notes_apply_url = _notes_get(notes, 'apply_url')
    website = (opp.get('brand_website') or '').strip()

    def _as_http_url(value):
        if not value:
            return None
        v = str(value).strip()
        if v.lower().startswith('mailto:'):
            return v  # handled below
        if v.lower().startswith(('http://', 'https://')):
            return v
        return None

    external_apply_url = _as_http_url(notes_apply_url)
    website_url = _as_http_url(website)

    # mailto: stored as apply_url → email path
    for candidate in (external_apply_url, website_url):
        if candidate and str(candidate).lower().startswith('mailto:'):
            mail = candidate.split(':', 1)[1].split('?')[0].strip()
            if _is_real_apply_email(mail):
                apply_email = apply_email or mail
            if external_apply_url == candidate:
                external_apply_url = None
            if website_url == candidate:
                website_url = None

    explicit = (_notes_get(notes, 'apply_mode') or '').lower()
    if explicit not in ('url', 'email', 'kit', 'auto'):
        explicit = 'auto'

    # URL used when forcing url mode: prefer notes apply_url, else website
    url_for_mode = external_apply_url or website_url

    if explicit == 'email' and apply_email:
        mode = 'email'
    elif explicit == 'url' and url_for_mode:
        mode = 'url'
        external_apply_url = url_for_mode
    elif explicit == 'kit':
        mode = 'kit'
    elif is_sourced:
        # Auto: dedicated apply_url → URL; else real Brand Email → mailto;
        # brand website alone is not enough to prefer URL over email.
        if external_apply_url:
            mode = 'url'
        elif apply_email:
            mode = 'email'
        elif website_url:
            mode = 'url'
            external_apply_url = website_url
        else:
            mode = 'kit'
    else:
        mode = 'kit'

    return {
        'apply_mode': mode,
        'external_apply_url': external_apply_url if mode == 'url' else None,
        'apply_email': apply_email,
        'source_platform': source_platform,
        'is_sourced': is_sourced,
    }


def admin_required(f):
    """
    Decorator to require admin authentication.
    Matches the pattern used in admin_brands.py and admin_reports.py.
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # Check X-Admin-Token header first
        admin_token = request.headers.get('X-Admin-Token')
        if admin_token == 'pr-hunter-admin-2026':
            return f(*args, **kwargs)

        # Check session-based auth as fallback
        user_id = session.get('user_id')
        if not user_id:
            return jsonify({'success': False, 'error': 'Authentication required'}), 401

        try:
            conn = get_db_connection()
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            cursor.execute('SELECT email FROM users WHERE id = %s', (user_id,))
            user = cursor.fetchone()
            conn.close()

            if not user or user.get('email', '').lower() != 'team@newcollab.co':
                return jsonify({'success': False, 'error': 'Admin access required'}), 403

        except Exception as e:
            return jsonify({'success': False, 'error': str(e)}), 500

        return f(*args, **kwargs)
    return decorated_function

def get_db_connection():
    """Get database connection"""
    return psycopg2.connect(
        host=os.getenv('DB_HOST'),
        port=os.getenv('DB_PORT', 5432),
        database=os.getenv('DB_NAME'),
        user=os.getenv('DB_USER'),
        password=os.getenv('DB_PASSWORD')
    )

def get_creator_id_from_session():
    """Get creator ID from session or JWT"""
    # Check if creator_id is directly in session
    creator_id = session.get('creator_id')
    if creator_id:
        return creator_id

    # Check if user_id is in session and lookup creator
    user_id = session.get('user_id')
    if user_id:
        try:
            conn = get_db_connection()
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            cursor.execute('SELECT id FROM creators WHERE user_id = %s', (user_id,))
            creator = cursor.fetchone()
            cursor.close()
            conn.close()
            if creator:
                return creator['id']
        except:
            pass

    # Try JWT as fallback
    try:
        jwt_user_id = get_jwt_identity()
        if jwt_user_id:
            conn = get_db_connection()
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            cursor.execute('SELECT id FROM creators WHERE user_id = %s', (jwt_user_id,))
            creator = cursor.fetchone()
            cursor.close()
            conn.close()
            if creator:
                return creator['id']
    except:
        pass
    return None

def send_email_notification(to_email, subject, body):
    """Send email via SendGrid"""
    try:
        sendgrid_api_key = os.getenv('SENDGRID_API_KEY')
        if not sendgrid_api_key:
            print(f"SendGrid API key not configured, skipping email to {to_email}")
            return False

        response = requests.post(
            'https://api.sendgrid.com/v3/mail/send',
            headers={
                'Authorization': f'Bearer {sendgrid_api_key}',
                'Content-Type': 'application/json'
            },
            json={
                'personalizations': [{'to': [{'email': to_email}]}],
                'from': {'email': 'team@newcollab.co', 'name': 'Newcollab'},
                'subject': subject,
                'content': [{'type': 'text/plain', 'value': body}]
            }
        )
        return response.status_code in [200, 201, 202]
    except Exception as e:
        print(f"Error sending email: {str(e)}")
        return False


# ============================================
# PUBLIC: Brand submits opportunity
# ============================================

@opportunities_bp.route('/public/submit', methods=['POST'])
def brand_submit():
    """Public endpoint for brands to submit opportunities"""
    try:
        data = request.get_json()

        # Validate required fields
        required = ['brand_name', 'brand_email', 'brand_website', 'product_name', 'campaign_description', 'spots_total']
        for field in required:
            if not data.get(field):
                return jsonify({'success': False, 'error': f'{field} is required'}), 400

        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        cursor.execute('''
            INSERT INTO opportunities (
                brand_name, brand_email, brand_website, brand_category,
                product_name, campaign_description, pr_value_usd,
                creator_count_range, shipping_regions, follower_ranges,
                content_types, creator_niches, additional_notes,
                application_deadline, spots_total, status
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'pending'
            ) RETURNING id
        ''', (
            data['brand_name'],
            data['brand_email'],
            data['brand_website'],
            data.get('brand_category'),
            data['product_name'],
            data['campaign_description'],
            data.get('pr_value_usd'),
            data.get('creator_count_range', '5-10'),
            data.get('shipping_regions', []),
            data.get('follower_ranges', []),
            data.get('content_types', []),
            data.get('creator_niches', []),
            data.get('additional_notes') or None,
            data.get('application_deadline') or None,
            int(data['spots_total'])
        ))

        result = cursor.fetchone()
        opp_id = result['id']

        conn.commit()
        cursor.close()
        conn.close()

        # Notify admin
        send_email_notification(
            'mahery@newcollab.co',
            f'New opportunity to review: {data["brand_name"]}',
            f'{data["brand_name"]} submitted an opportunity for {data["product_name"]}.\n\n'
            f'Review at: https://app.newcollab.co/admin/opportunities/{opp_id}'
        )

        # Confirm to brand
        send_email_notification(
            data['brand_email'],
            'Your Newcollab listing is under review',
            f'Hi {data["brand_name"]},\n\n'
            f'We received your opportunity listing for {data["product_name"]}. '
            f'We will review and publish it within 24 hours.\n\n'
            f'Best,\nNewcollab Team'
        )

        return jsonify({'success': True, 'id': opp_id}), 201

    except Exception as e:
        print(f"Error in brand_submit: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


# ============================================
# CREATOR: List live opportunities
# ============================================

@opportunities_bp.route('/list', methods=['GET'])
def list_opportunities():
    """Get list of live opportunities for creators"""
    creator_id = get_creator_id_from_session()
    if not creator_id:
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401

    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Get creator's niche and subscription tier
        cursor.execute('''
            SELECT niche, subscription_tier
            FROM creators
            WHERE id = %s
        ''', (creator_id,))
        creator = cursor.fetchone()

        if not creator:
            cursor.close()
            conn.close()
            return jsonify({'success': False, 'error': 'Creator not found'}), 404

        creator_niche = creator.get('niche') or ''
        is_pro = creator.get('subscription_tier') in ['pro', 'elite']

        # Get all live opportunities
        cursor.execute('''
            SELECT
                id, brand_name, brand_category, brand_logo_url, brand_website,
                product_name, campaign_description,
                pr_value_usd, creator_count_range, shipping_regions, follower_ranges,
                content_types, creator_niches, additional_notes,
                spots_total, spots_filled, closes_at,
                created_at
            FROM opportunities
            WHERE status = 'live'
              AND (closes_at IS NULL OR closes_at > NOW())
              AND spots_filled < spots_total
            ORDER BY created_at DESC
        ''')
        all_opps = cursor.fetchall()

        # Get which ones the creator already applied to
        cursor.execute('''
            SELECT opportunity_id FROM opportunity_applications
            WHERE creator_id = %s
        ''', (creator_id,))
        applied_ids = {row['opportunity_id'] for row in cursor.fetchall()}

        cursor.close()
        conn.close()

        # Split into matched and others
        matched = []
        others = []

        for opp in all_opps:
            opp_niches = opp.get('creator_niches') or []
            # Match if opportunity has no niche restriction, or creator's niche is in the list
            is_match = not opp_niches or (creator_niche and creator_niche.lower() in [n.lower() for n in opp_niches])

            spots_left = opp['spots_total'] - opp['spots_filled']
            days_left = None
            if opp['closes_at']:
                delta = (opp['closes_at'] - datetime.utcnow()).days
                days_left = max(0, delta)

            path = _resolve_apply_path(opp)
            is_sourced = path['is_sourced']
            apply_mode = path['apply_mode']
            external_apply_url = path['external_apply_url']
            apply_email = path['apply_email']
            source_platform = path['source_platform']

            desc = opp['campaign_description'] or ''
            if is_sourced:
                desc = re.sub(r'\n*Apply here:\s*\S+', '', desc, flags=re.I).strip()
                desc = re.sub(r'\n*Pay:\s*', '\n', desc).strip()

            niches = opp_niches if isinstance(opp_niches, list) else []
            # Card label: admin brand_category wins (editable in /admin/opportunities).
            # Fall back to first creator niche, then text inference.
            display_niche = None
            bc = (opp.get('brand_category') or '').strip()
            if bc and bc.lower() not in ('other', 'unknown', 'n/a', 'none'):
                display_niche = bc.replace('_', ' ').strip()
                if display_niche.islower() or '_' in bc:
                    display_niche = display_niche.title()
            elif niches:
                first = str(next((n for n in niches if n), '')).strip()
                if first:
                    display_niche = first.split(',')[0].split('(')[0].strip()[:28] or None

            pay_label = _short_pay_label(opp.get('pr_value_usd'), opp.get('campaign_description') or '')
            if not display_niche or len(display_niche) > 28:
                inferred = _infer_short_niche(
                    f"{opp.get('product_name') or ''} {opp.get('campaign_description') or ''}"
                )
                if inferred:
                    display_niche = inferred
                elif display_niche and len(display_niche) > 28:
                    display_niche = display_niche.split(',')[0].split('(')[0].strip()[:28]

            serialized = {
                'id': opp['id'],
                'brand_name': opp['brand_name'],
                'brand_category': opp['brand_category'],
                'display_niche': display_niche,
                'creator_niches': niches,
                'brand_logo_url': opp.get('brand_logo_url'),
                'product_name': opp['product_name'],
                'campaign_description': desc,
                'pr_value_usd': opp['pr_value_usd'],
                'pay_label': pay_label,
                'creator_count_range': opp['creator_count_range'],
                'shipping_regions': opp['shipping_regions'] or [],
                'follower_ranges': opp['follower_ranges'] or [],
                'content_types': opp['content_types'] or [],
                'spots_total': opp['spots_total'],
                'spots_left': spots_left,
                'days_left': days_left,
                'is_matched': is_match,
                'already_applied': opp['id'] in applied_ids,
                'external_apply_url': external_apply_url,
                'apply_email': apply_email,
                'source_platform': source_platform,
                'apply_mode': apply_mode,
                'is_sourced': is_sourced,
            }

            if is_match:
                matched.append(serialized)
            else:
                others.append(serialized)

        return jsonify({
            'success': True,
            'matched': matched,
            'others': others,
            'is_pro': is_pro
        })

    except Exception as e:
        print(f"Error in list_opportunities: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


# ============================================
# CREATOR: Apply to opportunity
# ============================================

@opportunities_bp.route('/<int:opp_id>/apply', methods=['POST'])
def apply_opportunity(opp_id):
    """Creator applies to an opportunity"""
    creator_id = get_creator_id_from_session()
    if not creator_id:
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401

    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Get opportunity
        cursor.execute('''
            SELECT id, brand_name, brand_email, brand_website, product_name, status,
                   spots_total, spots_filled, additional_notes
            FROM opportunities
            WHERE id = %s
        ''', (opp_id,))
        opp = cursor.fetchone()

        if not opp:
            cursor.close()
            conn.close()
            return jsonify({'success': False, 'error': 'Opportunity not found'}), 404

        if opp['status'] != 'live':
            cursor.close()
            conn.close()
            return jsonify({'success': False, 'error': 'This opportunity is no longer active'}), 400

        path = _resolve_apply_path(opp)
        is_sourced = path['is_sourced']
        apply_mode = path['apply_mode']
        external_apply_url = path['external_apply_url']
        apply_email = path['apply_email']

        if apply_mode == 'email' and not apply_email:
            cursor.close()
            conn.close()
            return jsonify({
                'success': False,
                'error': 'This opportunity has no brand email set for applications'
            }), 400

        if apply_mode == 'url' and not external_apply_url:
            cursor.close()
            conn.close()
            return jsonify({
                'success': False,
                'error': 'This opportunity has no application URL'
            }), 400

        # Spots capacity only for brand-submitted PR campaigns (not sourced gigs)
        if not is_sourced and opp['spots_filled'] >= opp['spots_total']:
            cursor.close()
            conn.close()
            return jsonify({'success': False, 'error': 'No spots remaining'}), 400

        # Check if already applied
        cursor.execute('''
            SELECT id FROM opportunity_applications
            WHERE opportunity_id = %s AND creator_id = %s
        ''', (opp_id, creator_id))
        existing = cursor.fetchone()

        if existing:
            cursor.close()
            conn.close()
            return jsonify({'success': False, 'error': 'You have already applied'}), 409

        # Check pitch/application limits (same pool as pitches) — free = 3/month
        cursor.execute('''
            SELECT subscription_tier, pitches_sent_this_week, last_pitch_reset
            FROM creators
            WHERE id = %s
        ''', (creator_id,))
        creator = cursor.fetchone()

        if not creator:
            cursor.close()
            conn.close()
            return jsonify({'success': False, 'error': 'Creator not found'}), 404

        tier = creator['subscription_tier'] or 'free'
        pitches_used = creator.get('pitches_sent_this_week') or 0
        last_reset = creator.get('last_pitch_reset')

        # Reset monthly count if needed
        today = date.today()
        month_start = today.replace(day=1)
        if last_reset is None or (isinstance(last_reset, date) and last_reset < month_start):
            pitches_used = 0
            cursor.execute('''
                UPDATE creators
                SET pitches_sent_this_week = 0, last_pitch_reset = %s
                WHERE id = %s
            ''', (month_start, creator_id))

        FREE_MONTHLY_LIMIT = 3
        is_pro = tier in ['pro', 'elite']

        if not is_pro and pitches_used >= FREE_MONTHLY_LIMIT:
            cursor.close()
            conn.close()
            return jsonify({
                'success': False,
                'error': 'limit_reached',
                'limit': FREE_MONTHLY_LIMIT,
                'used': pitches_used,
                'message': 'You have used all your free applications this month'
            }), 403

        # Create application
        cursor.execute('''
            INSERT INTO opportunity_applications (opportunity_id, creator_id)
            VALUES (%s, %s)
            RETURNING id
        ''', (opp_id, creator_id))

        # Increment spots filled only for brand PR campaigns
        if not is_sourced:
            cursor.execute('''
                UPDATE opportunities
                SET spots_filled = spots_filled + 1
                WHERE id = %s
            ''', (opp_id,))

        # Increment application/pitch count (uses same pool) — always deduct quota
        cursor.execute('''
            UPDATE creators
            SET pitches_sent_this_week = COALESCE(pitches_sent_this_week, 0) + 1,
                last_pitch_reset = COALESCE(last_pitch_reset, CURRENT_DATE)
            WHERE id = %s
        ''', (creator_id,))

        conn.commit()

        # Get creator details for email
        cursor.execute('''
            SELECT username, followers_count, niche, kit_slug
            FROM creators
            WHERE id = %s
        ''', (creator_id,))
        creator_info = cursor.fetchone()

        cursor.close()
        conn.close()

        # Classic kit applies: platform emails the brand. Email-mode applies only open
        # the creator's mail app (mailto) on the client — no SendGrid send.
        if apply_mode == 'kit' and creator_info:
            notify_to = None
            if apply_email and _is_real_apply_email(apply_email):
                notify_to = apply_email
            elif _is_real_apply_email(opp.get('brand_email')):
                notify_to = opp.get('brand_email')
            if notify_to:
                niche_str = creator_info['niche'] or 'Creator'
                slug = creator_info['kit_slug'] or creator_info['username']
                send_email_notification(
                    notify_to,
                    f'New application from {creator_info["username"]} for {opp["product_name"]}',
                    f'{creator_info["username"]} ({creator_info["followers_count"]} followers, {niche_str}) '
                    f'applied to your opportunity.\n\n'
                    f'View their media kit: https://app.newcollab.co/kit/{slug}\n\n'
                    f'Reply to this email to approve or decline.'
                )

        return jsonify({
            'success': True,
            'apply_mode': apply_mode,
            'external_apply_url': external_apply_url,
            'apply_email': apply_email,
            'used': pitches_used + 1,
            'limit': FREE_MONTHLY_LIMIT if not is_pro else None,
        }), 201

    except Exception as e:
        print(f"Error in apply_opportunity: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


# ============================================
# ADMIN: List opportunities
# ============================================

@opportunities_bp.route('/admin/list', methods=['GET'])
@admin_required
def admin_list():
    """Admin endpoint to list opportunities by status"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        status = request.args.get('status', 'pending')

        cursor.execute('''
            SELECT
                id, brand_name, brand_email, brand_website, brand_category,
                brand_logo_url, product_name, campaign_description, pr_value_usd,
                creator_count_range, shipping_regions, follower_ranges,
                content_types, creator_niches, additional_notes, application_deadline,
                spots_total, spots_filled, status, created_at, published_at, closes_at
            FROM opportunities
            WHERE status = %s
            ORDER BY created_at DESC
        ''', (status,))
        opps = cursor.fetchall()

        cursor.close()
        conn.close()

        # Convert datetime objects to strings
        opportunities = []
        for opp in opps:
            path = _resolve_apply_path(opp)
            opportunities.append({
                'id': opp['id'],
                'brand_name': opp['brand_name'],
                'brand_email': opp['brand_email'],
                'brand_website': opp['brand_website'],
                'brand_category': opp['brand_category'],
                'brand_logo_url': opp['brand_logo_url'],
                'product_name': opp['product_name'],
                'campaign_description': opp['campaign_description'],
                'pr_value_usd': opp['pr_value_usd'],
                'creator_count_range': opp['creator_count_range'],
                'shipping_regions': opp['shipping_regions'] or [],
                'follower_ranges': opp['follower_ranges'] or [],
                'content_types': opp['content_types'] or [],
                'creator_niches': opp['creator_niches'] or [],
                'additional_notes': opp['additional_notes'],
                'apply_mode': path['apply_mode'],
                'apply_via': _notes_get(opp.get('additional_notes') or '', 'apply_mode') or 'auto',
                'external_apply_url': path['external_apply_url'] or _notes_get(opp.get('additional_notes') or '', 'apply_url'),
                'apply_email': path['apply_email'],
                'is_sourced': path['is_sourced'],
                'source_platform': path['source_platform'],
                'application_deadline': opp['application_deadline'].isoformat() if opp['application_deadline'] else None,
                'spots_total': opp['spots_total'],
                'spots_filled': opp['spots_filled'],
                'status': opp['status'],
                'created_at': opp['created_at'].isoformat() if opp['created_at'] else None,
                'published_at': opp['published_at'].isoformat() if opp['published_at'] else None,
                'closes_at': opp['closes_at'].isoformat() if opp['closes_at'] else None
            })

        return jsonify({
            'success': True,
            'opportunities': opportunities
        })

    except Exception as e:
        print(f"Error in admin_list: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


# ============================================
# ADMIN: Update opportunity (logo, etc)
# ============================================

@opportunities_bp.route('/admin/<int:opp_id>/update', methods=['PATCH'])
@admin_required
def admin_update(opp_id):
    """Admin updates opportunity details (logo URL, etc)"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        data = request.get_json() or {}

        # Build update query dynamically
        updates = []
        values = []

        if 'brand_logo_url' in data:
            updates.append('brand_logo_url = %s')
            values.append(data['brand_logo_url'])

        if not updates:
            return jsonify({'success': False, 'error': 'No updates provided'}), 400

        values.append(opp_id)
        cursor.execute(f'''
            UPDATE opportunities
            SET {', '.join(updates)}
            WHERE id = %s
        ''', values)

        conn.commit()
        cursor.close()
        conn.close()

        return jsonify({'success': True})

    except Exception as e:
        print(f"Error in admin_update: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


# ============================================
# ADMIN: Publish opportunity
# ============================================

@opportunities_bp.route('/admin/<int:opp_id>/publish', methods=['PATCH'])
@admin_required
def admin_publish(opp_id):
    """Admin publishes an opportunity"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        data = request.get_json() or {}
        days_open = data.get('days_open', 14)

        # Get opportunity for email and application_deadline
        cursor.execute('SELECT brand_email, brand_name, product_name, application_deadline FROM opportunities WHERE id = %s', (opp_id,))
        opp = cursor.fetchone()

        if not opp:
            cursor.close()
            conn.close()
            return jsonify({'success': False, 'error': 'Opportunity not found'}), 404

        # Use brand's application_deadline if set, otherwise calculate from now
        if opp.get('application_deadline'):
            cursor.execute('''
                UPDATE opportunities
                SET status = 'live',
                    published_at = NOW(),
                    closes_at = %s
                WHERE id = %s
            ''', (opp['application_deadline'], opp_id))
        else:
            cursor.execute('''
                UPDATE opportunities
                SET status = 'live',
                    published_at = NOW(),
                    closes_at = NOW() + INTERVAL '%s days'
                WHERE id = %s
            ''', (days_open, opp_id))

        conn.commit()
        cursor.close()
        conn.close()

        # Notify brand
        send_email_notification(
            opp['brand_email'],
            f'Your Newcollab listing is live',
            f'Your opportunity for {opp["product_name"]} is now live on Newcollab.\n\n'
            f'Creators will start applying within the next 24 hours.\n\n'
            f'Best,\nNewcollab Team'
        )

        return jsonify({'success': True})

    except Exception as e:
        print(f"Error in admin_publish: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


# ============================================
# ADMIN: Reject opportunity
# ============================================

@opportunities_bp.route('/admin/<int:opp_id>/reject', methods=['PATCH'])
@admin_required
def admin_reject(opp_id):
    """Admin rejects an opportunity"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        data = request.get_json() or {}
        reason = data.get('reason', '')

        # Get opportunity for email
        cursor.execute('SELECT brand_email, brand_name, product_name FROM opportunities WHERE id = %s', (opp_id,))
        opp = cursor.fetchone()

        if not opp:
            cursor.close()
            conn.close()
            return jsonify({'success': False, 'error': 'Opportunity not found'}), 404

        cursor.execute('''
            UPDATE opportunities
            SET status = 'rejected'
            WHERE id = %s
        ''', (opp_id,))

        conn.commit()
        cursor.close()
        conn.close()

        # Notify brand
        reason_text = f'\n\nReason: {reason}' if reason else ''
        send_email_notification(
            opp['brand_email'],
            'Your Newcollab listing needs some changes',
            f'Thanks for submitting to Newcollab. We were not able to publish '
            f'your listing for {opp["product_name"]} at this time.'
            f'{reason_text}\n\n'
            f'Feel free to resubmit or email us at brands@newcollab.co.\n\n'
            f'Best,\nNewcollab Team'
        )

        return jsonify({'success': True})

    except Exception as e:
        print(f"Error in admin_reject: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


# ============================================
# ADMIN: Get opportunity applications
# ============================================

@opportunities_bp.route('/admin/<int:opp_id>/applications', methods=['GET'])
@admin_required
def admin_get_applications(opp_id):
    """Admin gets applications for an opportunity"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute('''
            SELECT
                oa.id, oa.applied_at, oa.status,
                c.id as creator_id, c.username, c.followers_count,
                c.niche, c.kit_slug, c.image_profile
            FROM opportunity_applications oa
            JOIN creators c ON oa.creator_id = c.id
            WHERE oa.opportunity_id = %s
            ORDER BY oa.applied_at DESC
        ''', (opp_id,))
        apps = cursor.fetchall()

        cursor.close()
        conn.close()

        applications = []
        for app in apps:
            applications.append({
                'id': app['id'],
                'applied_at': app['applied_at'].isoformat() if app['applied_at'] else None,
                'status': app['status'],
                'creator': {
                    'id': app['creator_id'],
                    'display_name': app['username'],
                    'follower_count': app['followers_count'],
                    'niches': [app['niche']] if app['niche'] else [],
                    'slug': app['kit_slug'] or app['username'],
                    'avatar_url': app['image_profile']
                }
            })

        return jsonify({
            'success': True,
            'applications': applications
        })

    except Exception as e:
        print(f"Error in admin_get_applications: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


# ============================================
# ADMIN: Ingest sourced gigs (scanner → review queue)
# ============================================

@opportunities_bp.route('/admin/ingest', methods=['POST'])
@admin_required
def admin_ingest():
    """
    Bulk-ingest sourced creator gigs into the Opportunities admin queue.

    Used by the brand-manager creator_gig_scanner after classification.
    Creates status='pending' rows for Mazza to approve/publish.
    Does NOT email brands (scraped listings often have no real brand inbox).

    Body: { "gigs": [ { title, buyer_name, apply_url, source_platform,
            source_post_id, deliverable_summary, category, compensation_*,
            niche_required, min_followers, geo_required, deadline, ... } ] }
    """
    try:
        data = request.get_json() or {}
        gigs = data.get('gigs') or []
        if not isinstance(gigs, list) or not gigs:
            return jsonify({'success': False, 'error': 'gigs[] required'}), 400

        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        created = []
        skipped = []
        errors = []

        for raw in gigs[:100]:
            try:
                title = (raw.get('title') or '').strip()
                buyer = (raw.get('buyer_name') or raw.get('brand_name') or 'Unknown brand').strip()
                apply_url = (raw.get('apply_url') or raw.get('source_url') or '').strip()
                source_platform = (raw.get('source_platform') or 'scanner').strip()
                source_post_id = (raw.get('source_post_id') or '').strip()
                deliverable = (raw.get('deliverable_summary') or raw.get('campaign_description') or '').strip()

                if not title or not apply_url:
                    errors.append({'title': title, 'error': 'title and apply_url required'})
                    continue

                # Dedup fingerprint stored in additional_notes
                fingerprint = f"[scanner:{source_platform}:{source_post_id or apply_url}]"
                cursor.execute(
                    "SELECT id FROM opportunities WHERE additional_notes LIKE %s LIMIT 1",
                    (f'%{fingerprint}%',)
                )
                existing = cursor.fetchone()
                if existing:
                    skipped.append({'id': existing['id'], 'fingerprint': fingerprint})
                    continue

                # Build creator-facing description with external apply link
                comp_bits = []
                if raw.get('compensation_display'):
                    comp_bits.append(str(raw['compensation_display']))
                elif raw.get('compensation_min_usd') or raw.get('compensation_max_usd'):
                    lo = raw.get('compensation_min_usd')
                    hi = raw.get('compensation_max_usd')
                    if lo and hi:
                        comp_bits.append(f"${lo}–${hi}")
                    elif lo:
                        comp_bits.append(f"from ${lo}")
                    elif hi:
                        comp_bits.append(f"up to ${hi}")
                if raw.get('compensation_type'):
                    comp_bits.append(str(raw['compensation_type']))

                desc_parts = [deliverable] if deliverable else [title]
                if comp_bits:
                    desc_parts.append("Pay: " + " · ".join(comp_bits))
                desc_parts.append(f"Apply here: {apply_url}")
                if raw.get('other_requirements'):
                    desc_parts.append(f"Requirements: {raw['other_requirements']}")
                campaign_description = "\n\n".join(p for p in desc_parts if p)

                notes_bits = [
                    fingerprint,
                    f"source={source_platform}",
                    f"apply_url={apply_url}",
                ]
                if raw.get('legitimacy_score') is not None:
                    notes_bits.append(f"score={raw['legitimacy_score']}")
                if raw.get('apply_email'):
                    notes_bits.append(f"email={raw['apply_email']}")
                additional_notes = " | ".join(notes_bits)

                niches = []
                if raw.get('niche_required'):
                    niches = [raw['niche_required']] if isinstance(raw['niche_required'], str) else list(raw['niche_required'])
                elif raw.get('creator_niches'):
                    niches = list(raw['creator_niches'])
                # Infer niche from title when classifier left it empty
                if not niches:
                    title_l = title.lower()
                    if any(w in title_l for w in ('parent', 'mom', 'dad', 'kids', 'family')):
                        niches = ['Parenting']
                    elif any(w in title_l for w in ('beauty', 'skincare', 'makeup')):
                        niches = ['Beauty']
                    elif any(w in title_l for w in ('fitness', 'gym', 'workout')):
                        niches = ['Fitness']
                    elif any(w in title_l for w in ('food', 'recipe', 'cook')):
                        niches = ['Food']
                niches = [str(n).strip().title() for n in niches if n]

                follower_ranges = []
                min_f = raw.get('min_followers')
                if min_f:
                    try:
                        n = int(min_f)
                        if n >= 50000:
                            follower_ranges = ['50K+']
                        elif n >= 10000:
                            follower_ranges = ['10K-50K']
                        else:
                            follower_ranges = ['1K-10K']
                    except (TypeError, ValueError):
                        follower_ranges = ['1K-10K']

                shipping = []
                if raw.get('geo_required'):
                    geo = str(raw['geo_required']).upper()
                    for code in ('US', 'UK', 'AU', 'CA'):
                        if code in geo:
                            shipping.append(code)
                if not shipping:
                    shipping = ['US']

                pr_value = raw.get('pr_value_usd') or raw.get('compensation_max_usd') or raw.get('compensation_min_usd')
                try:
                    pr_value = int(pr_value) if pr_value is not None else None
                except (TypeError, ValueError):
                    pr_value = None

                deadline = raw.get('deadline') or raw.get('application_deadline') or None

                # Placeholder email — scanner listings are not brand-submitted
                brand_email = (raw.get('brand_email') or raw.get('apply_email') or 'sourced@newcollab.co').strip()
                brand_website = apply_url[:500]

                cursor.execute('''
                    INSERT INTO opportunities (
                        brand_name, brand_email, brand_website, brand_category,
                        product_name, campaign_description, pr_value_usd,
                        creator_count_range, shipping_regions, follower_ranges,
                        content_types, creator_niches, additional_notes,
                        application_deadline, spots_total, status
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'pending'
                    ) RETURNING id
                ''', (
                    buyer[:255],
                    brand_email[:255],
                    brand_website,
                    # Prefer niche label over coarse category when we have one
                    (niches[0] if niches else (raw.get('category') or raw.get('brand_category') or None)),
                    title[:255],
                    campaign_description,
                    pr_value,
                    raw.get('creator_count_range', '5-10'),
                    shipping,
                    follower_ranges,
                    raw.get('content_types') or ['TikTok', 'Reel', 'UGC'],
                    niches,
                    additional_notes,
                    deadline,
                    int(raw.get('spots_total') or 10),
                ))
                row = cursor.fetchone()
                created.append({'id': row['id'], 'title': title, 'fingerprint': fingerprint})
            except Exception as item_err:
                errors.append({'title': raw.get('title'), 'error': str(item_err)})

        conn.commit()
        cursor.close()
        conn.close()

        if created:
            send_email_notification(
                'mahery@newcollab.co',
                f'{len(created)} sourced gigs ready to review',
                f'{len(created)} new listings from the gig scanner are in Admin → Opportunities.\n\n'
                f'Review: https://app.newcollab.co/admin/opportunities\n\n'
                f'Skipped duplicates: {len(skipped)}'
            )

        return jsonify({
            'success': True,
            'created': created,
            'skipped': skipped,
            'errors': errors,
            'created_count': len(created),
            'skipped_count': len(skipped),
        }), 201

    except Exception as e:
        print(f"Error in admin_ingest: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


# ============================================
# ADMIN: Close/Pause opportunity
# ============================================

@opportunities_bp.route('/admin/<int:opp_id>/close', methods=['PATCH'])
@admin_required
def admin_close(opp_id):
    """Admin closes or pauses an opportunity"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        data = request.get_json() or {}
        new_status = data.get('status', 'closed')  # 'closed' or 'paused'

        if new_status not in ['closed', 'paused']:
            return jsonify({'success': False, 'error': 'Invalid status'}), 400

        cursor.execute('''
            UPDATE opportunities
            SET status = %s
            WHERE id = %s
        ''', (new_status, opp_id))

        conn.commit()
        cursor.close()
        conn.close()

        return jsonify({'success': True})

    except Exception as e:
        print(f"Error in admin_close: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


# ============================================
# ADMIN: Reopen opportunity
# ============================================

@opportunities_bp.route('/admin/<int:opp_id>/reopen', methods=['PATCH'])
@admin_required
def admin_reopen(opp_id):
    """Admin reopens a closed/paused opportunity"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        data = request.get_json() or {}
        days_open = data.get('days_open', 14)

        cursor.execute('''
            UPDATE opportunities
            SET status = 'live',
                closes_at = NOW() + INTERVAL '%s days'
            WHERE id = %s
        ''', (days_open, opp_id))

        conn.commit()
        cursor.close()
        conn.close()

        return jsonify({'success': True})

    except Exception as e:
        print(f"Error in admin_reopen: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


# ============================================
# ADMIN: Edit opportunity (full update)
# ============================================

@opportunities_bp.route('/admin/<int:opp_id>/edit', methods=['PATCH'])
@admin_required
def admin_edit(opp_id):
    """Admin edits opportunity details"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        data = request.get_json() or {}

        cursor.execute(
            'SELECT additional_notes, brand_email FROM opportunities WHERE id = %s',
            (opp_id,)
        )
        existing = cursor.fetchone()
        if not existing:
            cursor.close()
            conn.close()
            return jsonify({'success': False, 'error': 'Opportunity not found'}), 404

        # Build update query dynamically for allowed fields
        allowed_fields = [
            'brand_name', 'brand_website', 'brand_email', 'brand_category',
            'brand_logo_url', 'product_name', 'campaign_description',
            'pr_value_usd', 'creator_count_range', 'shipping_regions',
            'follower_ranges', 'content_types', 'creator_niches',
            'additional_notes', 'spots_total'
        ]

        updates = []
        values = []
        int_fields = {'pr_value_usd', 'spots_total'}

        for field in allowed_fields:
            if field not in data:
                continue
            value = data[field]
            if field in int_fields:
                if value is None or value == '':
                    value = None
                else:
                    try:
                        value = int(value)
                    except (TypeError, ValueError):
                        return jsonify({
                            'success': False,
                            'error': f'{field} must be an integer or empty'
                        }), 400
                    if field == 'spots_total' and value is not None and value < 1:
                        value = 1
            updates.append(f'{field} = %s')
            values.append(value)

        # Apply path controls (stored in additional_notes)
        notes = data.get('additional_notes', existing.get('additional_notes') or '')
        notes_dirty = False

        if 'apply_url' in data:
            url = (data.get('apply_url') or '').strip()
            notes = _notes_set(notes, 'apply_url', url or None)
            notes_dirty = True
            if url and 'brand_website' not in data:
                updates.append('brand_website = %s')
                values.append(url[:500])

        if 'apply_via' in data or 'apply_mode' in data:
            via = (data.get('apply_via') or data.get('apply_mode') or 'auto').strip().lower()
            if via not in ('auto', 'url', 'email', 'kit'):
                cursor.close()
                conn.close()
                return jsonify({'success': False, 'error': 'apply_via must be auto, url, email, or kit'}), 400
            if via == 'email':
                email_check = (data.get('brand_email') if 'brand_email' in data else existing.get('brand_email')) or ''
                if not _is_real_apply_email(email_check):
                    cursor.close()
                    conn.close()
                    return jsonify({
                        'success': False,
                        'error': 'Set a real Brand Email before using Apply via Email'
                    }), 400
            notes = _notes_set(notes, 'apply_mode', None if via == 'auto' else via)
            notes_dirty = True

        if 'brand_email' in data and _is_real_apply_email(data.get('brand_email')):
            notes = _notes_set(notes, 'email', data.get('brand_email').strip())
            notes_dirty = True

        if notes_dirty:
            replaced = False
            for i, u in enumerate(updates):
                if u.startswith('additional_notes'):
                    values[i] = notes
                    replaced = True
                    break
            if not replaced:
                updates.append('additional_notes = %s')
                values.append(notes)

        if not updates:
            return jsonify({'success': False, 'error': 'No updates provided'}), 400

        values.append(opp_id)
        cursor.execute(f'''
            UPDATE opportunities
            SET {', '.join(updates)}
            WHERE id = %s
        ''', values)

        conn.commit()
        cursor.close()
        conn.close()

        return jsonify({'success': True})

    except Exception as e:
        print(f"Error in admin_edit: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


# ============================================
# ADMIN: Delete opportunity
# ============================================

@opportunities_bp.route('/admin/<int:opp_id>/delete', methods=['DELETE'])
@admin_required
def admin_delete(opp_id):
    """Admin deletes an opportunity"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Delete applications first (should cascade, but be explicit)
        cursor.execute('DELETE FROM opportunity_applications WHERE opportunity_id = %s', (opp_id,))

        # Delete opportunity
        cursor.execute('DELETE FROM opportunities WHERE id = %s', (opp_id,))

        conn.commit()
        cursor.close()
        conn.close()

        return jsonify({'success': True})

    except Exception as e:
        print(f"Error in admin_delete: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500
