"""
Unlock Output Validation Layer

The KILLER DEFENSE against generic AI output.

Validates that Gemini-generated unlock outputs are:
1. Brand-specific (not category-generic)
2. Not duplicated across brands for the same creator
3. Free of forbidden patterns

Two retries allowed; third failure falls back to curated template.
"""

import re
import json
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime, timedelta


# ============================================================================
# FORBIDDEN PATTERNS - Auto-fail if matched
# ============================================================================

FORBIDDEN_GENERIC_REASONS = [
    r'^haircare match$',
    r'^active audience$',
    r'^recent posting$',
    r'^good engagement$',
    r'^strong niche$',
    r'^great fit$',
    r'^niche match$',
    r'^niche alignment$',
    r'^perfect match$',
    r'^quality content$',
    r'^\w+ creator$',      # e.g. "beauty creator" alone
    r'^\w+ match$',        # e.g. "aesthetic match" alone
    r'^\w+ fit$',          # e.g. "category fit" alone
    r'^your \w+$',         # e.g. "your audience" alone
    r'^consistent \w+$',   # e.g. "consistent posting" alone
]

FORBIDDEN_GENERIC_QUICK_WINS = [
    r'post content this week',
    r'post something in your niche',
    r'add a reel',
    r'be more active',
    r'post more',
    r'improve your bio',
    r'add more content',
    r'create more posts',
    r'stay consistent',
    r'keep posting',
    r'engage with brands',
    r'tag more brands',
    r'^post \w+ content$',  # e.g. "post beauty content"
    r'^add \w+ to your',    # e.g. "add content to your feed"
]

# Generic action patterns for new coaching schema
# These are too vague and apply to ANY brand
FORBIDDEN_GENERIC_ACTIONS = [
    r'create \d+ posts? (this|next) week',  # "Create 3 posts this week"
    r'show products in use',
    r'tag brands in your content',
    r'post more consistently',
    r'add a collab email',  # Only if actually missing
    r'featuring \w+ products$',  # "featuring haircare products" without brand name
    r'tagging (the|relevant) brands?$',
    r'showing (you|yourself) using',
    r'^post \d+ (times?|videos?|reels?)',
]

# Phrases that indicate AI-generated generic content
# Note: "leverage" removed as it's too common and causes excessive failures
AI_TELL_PHRASES = [
    r'unlock\w* your potential',
    r'take your.*to the next level',
    r'position yourself',
    r'establish yourself',
    r'differentiate yourself',
    r'elevate your',
    r'maximize your',
    r'optimize your',
]


class UnlockValidator:
    """Validates unlock output for brand-specificity and quality."""

    def __init__(self, db_conn=None):
        self.db_conn = db_conn

    def validate(self, output: Dict, creator_id: str, brand: Dict) -> List[str]:
        """
        Validate unlock output against all rules.
        Supports both legacy schema (reasons_you_fit) and new coaching schema (missing_proof).

        Args:
            output: Gemini-generated output
            creator_id: Creator UUID
            brand: Brand dict with 'name' and 'id'

        Returns:
            List of validation issues (empty = valid)
        """
        issues = []

        brand_name = brand.get('name') or brand.get('brand_name', '')

        # Detect schema type
        is_new_schema = 'missing_proof' in output or 'next_move' in output

        if is_new_schema:
            # ========================================
            # NEW SCHEMA: Coaching-style validation
            # ========================================

            # 1. Required fields check
            if 'verdict' not in output:
                issues.append('missing_verdict')
            elif 'status' not in output.get('verdict', {}):
                issues.append('missing_verdict_status')

            if 'missing_proof' not in output:
                issues.append('missing_proof_field')

            if 'next_move' not in output:
                issues.append('missing_next_move')
            elif 'action' not in output.get('next_move', {}):
                issues.append('missing_next_move_action')

            # 2. Validate status is valid
            valid_statuses = ['ready', 'almost', 'not_yet', 'poor_fit']
            status = output.get('verdict', {}).get('status', '')
            if status and status not in valid_statuses:
                issues.append(f'invalid_status: {status}')

            # 3. Brand-specificity check for coaching schema
            brand_name_lower = brand_name.lower()
            observation = output.get('missing_proof', {}).get('observation', '').lower()
            action = output.get('next_move', {}).get('action', '').lower()
            coach_note = output.get('coach_note', '').lower()
            all_coaching_text = f"{observation} {action} {coach_note}"

            # Check if brand name is mentioned anywhere
            has_brand_reference = brand_name_lower in all_coaching_text if brand_name_lower else True

            # Check for generic action patterns
            for pattern in FORBIDDEN_GENERIC_ACTIONS:
                if re.search(pattern, action, re.IGNORECASE):
                    issues.append(f'generic_action: {action[:50]}')
                    break

            # Only flag missing brand reference if action is also generic
            # (Allow generic observation if action is brand-specific)
            if not has_brand_reference and len(issues) > 0:
                issues.append('no_brand_reference_in_coaching')

        else:
            # ========================================
            # LEGACY SCHEMA: reasons_you_fit validation
            # ========================================
            brand_name_lower = brand_name.lower()

            # 1. Brand reference check
            reasons_text = ''
            for r in output.get('reasons_you_fit', []):
                reasons_text += f" {r.get('chip_text', '')} {r.get('detail', '')}"
            reasons_text = reasons_text.lower()

            if brand_name_lower and brand_name_lower not in reasons_text:
                issues.append('no_brand_reference_in_reasons')

            # 2. Forbidden generic reason patterns
            for reason in output.get('reasons_you_fit', []):
                chip = reason.get('chip_text', '').lower().strip()
                for pattern in FORBIDDEN_GENERIC_REASONS:
                    if re.match(pattern, chip, re.IGNORECASE):
                        issues.append(f'generic_reason: {chip}')
                        break

            # 3. Forbidden generic quick win patterns
            quick_win = output.get('quick_win', {})
            qw_action = quick_win.get('action_title', '').lower()
            for pattern in FORBIDDEN_GENERIC_QUICK_WINS:
                if re.search(pattern, qw_action, re.IGNORECASE):
                    issues.append(f'generic_quick_win: {qw_action}')
                    break

            # 4. Cross-brand duplication check
            if self.db_conn:
                duplicates = self._check_cross_brand_duplicates(
                    creator_id, output, brand.get('id')
                )
                issues.extend(duplicates)

        # ========================================
        # COMMON: Em-dash / en-dash check
        # ========================================
        all_text = json.dumps(output)
        if '\u2014' in all_text or '\u2013' in all_text:
            issues.append('em_dash_present')

        # ========================================
        # 6. AI tell-phrase check
        # ========================================
        for pattern in AI_TELL_PHRASES:
            if re.search(pattern, all_text, re.IGNORECASE):
                issues.append(f'ai_tell_phrase: {pattern}')
                break

        # ========================================
        # 7. Reason count check (LEGACY SCHEMA ONLY)
        # New coaching schema uses missing_proof/next_move instead
        # ========================================
        if not is_new_schema:
            reasons = output.get('reasons_you_fit', [])
            if len(reasons) != 3:
                issues.append(f'wrong_reason_count: {len(reasons)} (expected 3)')

            # ========================================
            # 8. Minimum detail length check (LEGACY SCHEMA ONLY)
            # ========================================
            for i, reason in enumerate(reasons):
                detail = reason.get('detail', '')
                if len(detail) < 20:
                    issues.append(f'reason_{i}_detail_too_short')

        return issues

    def _check_cross_brand_duplicates(self, creator_id: str, output: Dict,
                                       current_brand_id: str) -> List[str]:
        """
        Check if reasons/quick_wins are duplicated across recent unlocks.

        This is the KILLER DEFENSE - if a creator sees the same chips
        for different brands, trust is broken.

        Args:
            creator_id: Creator UUID
            output: Current unlock output
            current_brand_id: Current brand UUID

        Returns:
            List of duplication issues
        """
        issues = []

        # Skip cross-brand check if ai_analysis column doesn't exist yet
        # This allows gradual rollout of the feature
        try:
            from psycopg2.extras import RealDictCursor
            cursor = self.db_conn.cursor(cursor_factory=RealDictCursor)

            # Fetch last 5 unlocks for this creator (excluding current brand)
            cursor.execute('''
                SELECT pp.brand_id, pp.ai_analysis
                FROM pr_packages pp
                WHERE pp.creator_id = %s
                AND pp.brand_id != %s
                AND pp.ai_analysis IS NOT NULL
                AND pp.created_at > %s
                ORDER BY pp.created_at DESC
                LIMIT 5
            ''', (creator_id, current_brand_id, datetime.now() - timedelta(days=30)))

            recent_unlocks = cursor.fetchall()
        except Exception as e:
            # Column doesn't exist yet - skip cross-brand duplication check
            print(f"[Validator] Skipping cross-brand check: {e}")
            try:
                self.db_conn.rollback()
            except:
                pass
            return issues

        if not recent_unlocks:
            return issues

        # Collect all recent reason chips and quick wins
        all_recent_reasons = []
        all_recent_quick_wins = []

        for unlock in recent_unlocks:
            analysis = unlock.get('ai_analysis')
            if isinstance(analysis, str):
                try:
                    analysis = json.loads(analysis)
                except:
                    continue

            if not analysis:
                continue

            for reason in analysis.get('reasons_you_fit', []):
                chip = reason.get('chip_text', '').lower().strip()
                if chip:
                    all_recent_reasons.append(chip)

            qw = analysis.get('quick_win', {}).get('action_title', '').lower().strip()
            if qw:
                all_recent_quick_wins.append(qw)

        # Check current output against recent
        current_reasons = output.get('reasons_you_fit', [])
        for reason in current_reasons:
            chip = reason.get('chip_text', '').lower().strip()
            # Flag if chip appears 2+ times in recent unlocks
            if all_recent_reasons.count(chip) >= 2:
                issues.append(f'cross_brand_duplicate_reason: {chip}')

        current_qw = output.get('quick_win', {}).get('action_title', '').lower().strip()
        if current_qw in all_recent_quick_wins:
            issues.append('cross_brand_duplicate_quick_win')

        return issues

    def log_validation(self, creator_id: str, brand_id: str, attempt: int,
                       issues: List[str], used_fallback: bool = False) -> None:
        """
        Log validation attempt to database.

        Args:
            creator_id: Creator UUID
            brand_id: Brand UUID
            attempt: Attempt number (1-3)
            issues: List of validation issues
            used_fallback: Whether fallback was used
        """
        if not self.db_conn:
            return

        cursor = self.db_conn.cursor()

        try:
            # Get user_id from creator_id
            cursor.execute('SELECT user_id FROM creators WHERE id = %s', (creator_id,))
            result = cursor.fetchone()
            user_id = result[0] if result else None

            cursor.execute('''
                INSERT INTO unlock_validation_log
                (user_id, brand_id, attempt_number, validation_issues, used_fallback)
                VALUES (%s, %s, %s, %s, %s)
            ''', (user_id, brand_id, attempt, issues, used_fallback))

            self.db_conn.commit()
        except Exception as e:
            print(f"Failed to log validation: {e}")
            self.db_conn.rollback()


def _parse_niche(niche_value) -> Optional[str]:
    """Parse niche from various formats (JSON array, string, etc)."""
    if not niche_value:
        return None

    # If it's a list, take first element
    if isinstance(niche_value, list):
        return niche_value[0].lower() if niche_value else None

    # If it's a string that looks like JSON array
    if isinstance(niche_value, str):
        niche_str = niche_value.strip()
        if niche_str.startswith('['):
            try:
                parsed = json.loads(niche_str)
                if isinstance(parsed, list) and parsed:
                    return parsed[0].lower()
            except:
                pass
        # Plain string
        return niche_str.lower()

    return None


def get_curated_fallback(brand_id: str, creator_niche: Optional[str],
                         db_conn) -> Optional[Dict]:
    """
    Fetch curated fallback template for a brand.

    Args:
        brand_id: Brand UUID
        creator_niche: Optional creator niche for niche-specific fallback
        db_conn: Database connection

    Returns:
        Fallback template dict or None
    """
    try:
        from psycopg2.extras import RealDictCursor
        cursor = db_conn.cursor(cursor_factory=RealDictCursor)

        # Parse niche to handle JSON arrays like '["fitness"]'
        parsed_niche = _parse_niche(creator_niche)

        # Try niche-specific fallback first
        if parsed_niche:
            cursor.execute('''
                SELECT * FROM brand_curated_fallbacks
                WHERE brand_id = %s AND niche = %s
            ''', (brand_id, parsed_niche))
            result = cursor.fetchone()
            if result:
                return _parse_fallback(result)

        # Fall back to generic brand template
        cursor.execute('''
            SELECT * FROM brand_curated_fallbacks
            WHERE brand_id = %s AND niche IS NULL
            ORDER BY updated_at DESC
            LIMIT 1
        ''', (brand_id,))
        result = cursor.fetchone()

        if result:
            return _parse_fallback(result)

        return None
    except Exception as e:
        print(f"[Validator] Error fetching curated fallback: {e}")
        try:
            db_conn.rollback()
        except:
            pass
        return None


def _parse_fallback(fallback: Dict) -> Dict:
    """Parse fallback row into usable format with both legacy and coaching formats."""
    reasons = fallback.get('reasons', [])
    if isinstance(reasons, str):
        reasons = json.loads(reasons)

    quick_wins = fallback.get('quick_wins', [])
    if isinstance(quick_wins, str):
        quick_wins = json.loads(quick_wins)

    # Select first 3 reasons and first quick win
    selected_reasons = reasons[:3] if len(reasons) >= 3 else reasons
    selected_qw = quick_wins[0] if quick_wins else {
        'emoji': '📸',
        'action_title': 'Post brand-relevant content',
        'note': 'Content that aligns with the brand increases your visibility.',
        'gain_pill': '🟢 Better chance of reply'
    }

    # Build observation from reasons
    observation = 'I reviewed your recent content.'
    if selected_reasons:
        first_reason = selected_reasons[0]
        detail = first_reason.get('detail', '')
        if detail:
            observation = detail

    return {
        # Coaching format (new)
        'verdict': {
            'status': 'almost',
            'confidence': 'medium'
        },
        'missing_proof': {
            'observation': observation,
            'why_it_matters': 'Showing relevant content increases your chance of a reply.'
        },
        'next_move': {
            'action': selected_qw.get('action_title', 'Post brand-relevant content'),
            'reasoning': selected_qw.get('note', 'Content that matches the brand increases visibility.'),
            'then_what': 'Then pitch'
        },
        'coach_note': 'One strong post could make the difference.',
        # Legacy format (backwards compatibility)
        'reasons_you_fit': selected_reasons,
        'quick_win': selected_qw,
        'used_fallback': True
    }


def validate_and_retry(output: Dict, creator_id: str, brand: Dict,
                       attempt: int, db_conn) -> Tuple[bool, List[str]]:
    """
    Validate output and return whether it passes.

    Args:
        output: Gemini-generated output
        creator_id: Creator UUID
        brand: Brand dict
        attempt: Attempt number (1-3)
        db_conn: Database connection

    Returns:
        Tuple of (is_valid, issues)
    """
    validator = UnlockValidator(db_conn)
    issues = validator.validate(output, creator_id, brand)

    # Log the attempt
    validator.log_validation(
        creator_id,
        brand.get('id'),
        attempt,
        issues,
        used_fallback=False
    )

    return len(issues) == 0, issues
