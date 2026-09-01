"""
Aggregate creator onboarding survey answers for the founder dashboard.

Stored on creators.onboarding_survey (JSONB):
  segment, intent[], pain[], intent_other, pain_other, skipped, completed_at
"""
from collections import Counter

SEGMENT_LABELS = {
    'just_starting': "Just starting — never worked with a brand",
    'early_stage': "1–3 gifted PR boxes",
    'growing': "5+ gifted collabs, wants paid work",
    'established': "Does paid UGC, wants more brands",
    'is_brand': "Not a creator — is a brand",
}

INTENT_LABELS = {
    'gifted_pr': 'Free PR boxes / gifted product',
    'paid_ugc': 'Paid UGC deals ($50–500)',
    'retainer': 'Monthly retainers',
    'simple_portfolio': 'Simple portfolio tool',
    'discovery': 'Get discovered by bigger brands',
    'sell_organic': 'Sell organic posts as ads',
    'learn': 'Learn what brands want',
    'other': 'Other',
}

PAIN_LABELS = {
    'no_replies': 'Brands never reply to pitches',
    'writing_pitches': "Don't know what to say in a pitch",
    'finding_brands': "Don't know which brands to reach out to",
    'no_portfolio': "Don't have a portfolio yet",
    'pricing': "Don't know how to price work",
    'content_ideas': 'No content ideas',
    'low_views': "Videos don't get many views",
    'other': 'Other',
}

INTENT_ACTIONS = {
    'gifted_pr': 'Lead with gifted PR and a clear request-a-box path. Paid retainers are not why they signed up.',
    'paid_ugc': 'They came for paid $50–500 UGC. Put those offers and a simple rate range on For You, not behind Pro.',
    'retainer': 'Monthly retainers are the ask — do not build that until one-off paid UGC actually converts.',
    'simple_portfolio': 'They want a kit/portfolio. Make “build a nice page” happen in session one, before pitching.',
    'discovery': 'They want to get found. Public kits and brand-facing profiles matter more than more pitch UI.',
    'sell_organic': 'They want to license existing posts. Usage-rights / Spark Ads is the product, not more outreach.',
    'learn': 'They are learning, not buying yet. Teach with pitch examples; do not hard-sell Pro on day one.',
    'other': 'Read the free-text “other” answers — they are telling you a job the chips missed.',
}

PAIN_ACTIONS = {
    'no_replies': 'The gap is reply rate, not more brands to spam. Default to a stronger pitch + 3-day follow-up.',
    'writing_pitches': 'Pitch writing is the job. Make AI pitch the default after every unlock, not an extra click.',
    'finding_brands': 'Matching is the job. For You + niches have to feel obvious in the first minute.',
    'no_portfolio': 'No proof = weak pitch. Get a 3-piece kit built before the first unlock.',
    'pricing': 'Add a plain UGC rate guide ($50–500). Guessing price is blocking outreach.',
    'content_ideas': 'They need hooks per brand, not another CRM. Put content angles on the brand page.',
    'low_views': 'Views are a creator-growth problem you do not solve yet. Do not pivot the whole product to virality.',
    'other': 'Read the free-text pain answers before adding another feature.',
}


def empty_survey_insights(error=None):
    payload = {
        'completed': 0,
        'skipped': 0,
        'signups_30d': 0,
        'completed_30d': 0,
        'skipped_30d': 0,
        'completion_rate_30d': 0,
        'skip_rate_30d': 0,
        'segments': [],
        'intents': [],
        'pains': [],
        'others': [],
        'insights': {
            'headline': 'No survey answers yet.',
            'who': '',
            'want': '',
            'pain': '',
            'action': 'New signups who finish onboarding will show up here.',
            'caution': None,
        },
    }
    if error:
        payload['error'] = error
    return payload


def _pct(count, total):
    return round(count / total * 100) if total else 0


def _ranked(counter, labels, respondents):
    rows = []
    for value, count in counter.most_common():
        if not value:
            continue
        rows.append({
            'value': value,
            'label': labels.get(value, value),
            'count': count,
            'pct': _pct(count, respondents),
        })
    return rows


def _top(rows):
    return rows[0] if rows else None


def build_insights(segments, intents, pains, completed, skipped, signups_30d,
                   completed_30d, skip_rate_30d):
    if completed < 1:
        return empty_survey_insights()['insights']

    top_seg = _top(segments)
    top_intent = _top(intents)
    top_pain = _top(pains)
    second_intent = intents[1] if len(intents) > 1 else None
    second_pain = pains[1] if len(pains) > 1 else None

    who = (
        f"{top_seg['pct']}% are “{top_seg['label']}”."
        if top_seg else 'Not enough segment answers yet.'
    )
    want = (
        f"{top_intent['pct']}% want {top_intent['label']}"
        + (f"; next is {second_intent['label']} ({second_intent['pct']}%)."
           if second_intent else '.')
        if top_intent else 'No intent answers yet.'
    )
    pain = (
        f"{top_pain['pct']}% say {top_pain['label']}"
        + (f"; next is {second_pain['label']} ({second_pain['pct']}%)."
           if second_pain else '.')
        if top_pain else 'No pain answers yet.'
    )

    if completed < 8:
        headline = (
            f"Only {completed} completed survey"
            f"{'s' if completed != 1 else ''} — directional, not a mandate."
        )
    elif top_seg and top_intent and top_pain:
        headline = (
            f"{top_seg['label'].split('—')[0].strip()} creators, "
            f"mainly here for {top_intent['label']}. "
            f"Biggest pain: {top_pain['label']}."
        )
    else:
        headline = f"{completed} creators completed the survey."

    actions = []
    if top_intent:
        actions.append(INTENT_ACTIONS.get(top_intent['value'], ''))
    if top_pain and (not top_intent or top_pain['value'] != top_intent['value']):
        actions.append(PAIN_ACTIONS.get(top_pain['value'], ''))
    brand_share = next((s['pct'] for s in segments if s['value'] == 'is_brand'), 0)
    if brand_share >= 15:
        actions.append(
            f"{brand_share}% said they are a brand. Split brand vs creator onboarding "
            "or you will keep polluting creator insights."
        )
    action = ' '.join(a for a in actions if a) or 'Keep collecting answers before pivoting.'

    caution = None
    if completed < 8:
        caution = 'Sample is small — use this to form a hypothesis, not to rebuild the product.'
    elif skip_rate_30d >= 40:
        caution = (
            f"{skip_rate_30d}% of new signups skipped the survey. "
            "Answers are biased toward people willing to talk."
        )
    elif signups_30d and completed_30d / signups_30d < 0.25:
        caution = (
            f"Only {completed_30d} of {signups_30d} signups in the last 30 days finished it. "
            "Most new users never told you what they want."
        )

    return {
        'headline': headline,
        'who': who,
        'want': want,
        'pain': pain,
        'action': action,
        'caution': caution,
    }


def analyze_surveys(completed_surveys, skipped_count, signups_30d,
                    completed_30d, skipped_30d, others):
    completed = len(completed_surveys)
    segments = _ranked(
        Counter(s.get('segment') for s in completed_surveys if s.get('segment')),
        SEGMENT_LABELS,
        completed,
    )
    intent_counter = Counter()
    pain_counter = Counter()
    for s in completed_surveys:
        intent_counter.update(v for v in (s.get('intent') or []) if v)
        pain_counter.update(v for v in (s.get('pain') or []) if v)
    intents = _ranked(intent_counter, INTENT_LABELS, completed)
    pains = _ranked(pain_counter, PAIN_LABELS, completed)

    skip_rate_30d = _pct(skipped_30d, signups_30d)
    insights = build_insights(
        segments, intents, pains, completed, skipped_count,
        signups_30d, completed_30d, skip_rate_30d,
    )

    return {
        'completed': completed,
        'skipped': skipped_count,
        'signups_30d': signups_30d,
        'completed_30d': completed_30d,
        'skipped_30d': skipped_30d,
        'completion_rate_30d': _pct(completed_30d, signups_30d),
        'skip_rate_30d': skip_rate_30d,
        'segments': segments,
        'intents': intents,
        'pains': pains,
        'others': others,
        'insights': insights,
    }


def fetch_onboarding_survey_insights(cursor):
    """Query creators.onboarding_survey and return founder-ready aggregates."""
    cursor.execute("""
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'creators'
          AND column_name = 'onboarding_survey'
        LIMIT 1
    """)
    if not cursor.fetchone():
        return empty_survey_insights()

    cursor.execute("""
        SELECT
            COUNT(*) AS signups_30d,
            COUNT(*) FILTER (
                WHERE onboarding_survey ? 'completed_at'
                  AND COALESCE(onboarding_survey->>'skipped', 'false') NOT IN ('true', 'True', '1')
            ) AS completed_30d,
            COUNT(*) FILTER (
                WHERE COALESCE(onboarding_survey->>'skipped', 'false') IN ('true', 'True', '1')
            ) AS skipped_30d
        FROM creators
        WHERE created_at >= NOW() - INTERVAL '30 days'
    """)
    funnel = cursor.fetchone() or {}
    signups_30d = int(funnel.get('signups_30d') or 0)
    completed_30d = int(funnel.get('completed_30d') or 0)
    skipped_30d = int(funnel.get('skipped_30d') or 0)

    cursor.execute("""
        SELECT
            COUNT(*) FILTER (
                WHERE COALESCE(onboarding_survey->>'skipped', 'false') IN ('true', 'True', '1')
            ) AS skipped
        FROM creators
        WHERE onboarding_survey IS NOT NULL
          AND onboarding_survey != '{}'::jsonb
    """)
    skipped_count = int((cursor.fetchone() or {}).get('skipped') or 0)

    cursor.execute("""
        SELECT onboarding_survey, created_at
        FROM creators
        WHERE onboarding_survey ? 'completed_at'
          AND COALESCE(onboarding_survey->>'skipped', 'false') NOT IN ('true', 'True', '1')
        ORDER BY created_at DESC
    """)
    completed_surveys = []
    for row in cursor.fetchall() or []:
        raw = row.get('onboarding_survey') or {}
        if isinstance(raw, str):
            import json
            try:
                raw = json.loads(raw)
            except Exception:
                continue
        if isinstance(raw, dict):
            completed_surveys.append(raw)

    cursor.execute("""
        SELECT
            onboarding_survey->>'intent_other' AS intent_other,
            onboarding_survey->>'pain_other' AS pain_other,
            created_at
        FROM creators
        WHERE (
                NULLIF(TRIM(onboarding_survey->>'intent_other'), '') IS NOT NULL
             OR NULLIF(TRIM(onboarding_survey->>'pain_other'), '') IS NOT NULL
            )
        ORDER BY created_at DESC
        LIMIT 20
    """)
    others = []
    for row in cursor.fetchall() or []:
        others.append({
            'intent_other': (row.get('intent_other') or '').strip(),
            'pain_other': (row.get('pain_other') or '').strip(),
            'created_at': row['created_at'].isoformat() if row.get('created_at') else None,
        })

    return analyze_surveys(
        completed_surveys,
        skipped_count,
        signups_30d,
        completed_30d,
        skipped_30d,
        others,
    )
