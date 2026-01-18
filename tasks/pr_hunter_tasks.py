"""
Celery Tasks for PR Hunter Automation
Handles background processing of brand discovery and enrichment
"""

import sys
import os
from celery import Celery
from datetime import datetime

# Add parent directory to path for imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.pr_hunter import PRHunterService
from psycopg2.extras import RealDictCursor
import psycopg2

# Don't import from app - causes circular import
# Define get_db_connection here to avoid circular dependency
def get_db_connection():
    """Get database connection"""
    return psycopg2.connect(os.getenv('DATABASE_URL'), cursor_factory=RealDictCursor)


# Initialize Celery
import ssl

redis_url = os.getenv('REDIS_URL', 'redis://localhost:6379/0')

# Configure SSL for rediss:// URLs (Upstash)
broker_use_ssl = None
redis_backend_use_ssl = None

if redis_url.startswith('rediss://'):
    broker_use_ssl = {
        'ssl_cert_reqs': ssl.CERT_NONE
    }
    redis_backend_use_ssl = {
        'ssl_cert_reqs': ssl.CERT_NONE
    }

celery_app = Celery(
    'pr_hunter',
    broker=redis_url,
    backend=redis_url
)

celery_app.conf.update(
    task_serializer='json',
    accept_content=['json'],
    result_serializer='json',
    timezone='UTC',
    enable_utc=True,
    broker_use_ssl=broker_use_ssl,
    redis_backend_use_ssl=redis_backend_use_ssl,
)


@celery_app.task(name='pr_hunter.run_hunt', bind=True)
def run_pr_hunt(self, keyword: str, max_results: int = 50):
    """
    Main Celery task: Discover and enrich brands for a given keyword

    Args:
        keyword: Search keyword (e.g., "Clean Beauty", "K-Beauty")
        max_results: Maximum brands to discover

    Returns:
        Summary statistics of the hunt
    """
    service = PRHunterService()
    conn = None

    try:
        # Update task state
        self.update_state(state='PROGRESS', meta={'step': 'Discovering brands', 'progress': 0})

        # Step 1: Discovery
        print(f"Starting PR hunt for keyword: {keyword}")
        discovered_brands = service.search_google_for_brands(keyword, max_results)
        print(f"Discovered {len(discovered_brands)} brands")

        # Update progress
        self.update_state(
            state='PROGRESS',
            meta={'step': 'Enriching brands', 'progress': 20, 'discovered': len(discovered_brands)}
        )

        # Step 2: Filter out duplicates (check both live and staging)
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        unique_brands = []
        for brand in discovered_brands:
            domain = brand.get('domain')
            if not domain:
                continue

            # Check if already exists in live brands table
            cursor.execute('SELECT id FROM brands WHERE website LIKE %s', (f'%{domain}%',))
            if cursor.fetchone():
                print(f"Skipping {domain} - already in live brands")
                continue

            # Check if already in staging
            cursor.execute('SELECT id FROM brand_candidates WHERE domain = %s', (domain,))
            if cursor.fetchone():
                print(f"Skipping {domain} - already in candidates")
                continue

            unique_brands.append(brand)

        print(f"After deduplication: {len(unique_brands)} unique brands")

        # Step 3: Enrich each brand
        enriched_count = 0
        saved_count = 0
        rejected_count = 0

        for idx, brand in enumerate(unique_brands):
            try:
                # Update progress
                progress = 20 + int((idx / len(unique_brands)) * 70)
                self.update_state(
                    state='PROGRESS',
                    meta={
                        'step': f'Enriching {brand["brand_name"]}',
                        'progress': progress,
                        'enriched': enriched_count,
                        'saved': saved_count
                    }
                )

                # Enrich brand data
                enriched_brand = service.enrich_brand_data(brand)
                enriched_count += 1

                # Apply quality gate
                passes_quality, rejection_reason = service.quality_gate(enriched_brand)

                if passes_quality:
                    # Save to brand_candidates
                    save_candidate_to_db(cursor, enriched_brand, keyword)
                    saved_count += 1
                    print(f"✅ Saved: {enriched_brand['brand_name']}")
                else:
                    rejected_count += 1
                    print(f"❌ Rejected: {enriched_brand['brand_name']} - {rejection_reason}")

                # Commit after each brand to avoid losing progress
                conn.commit()

            except Exception as e:
                print(f"Error enriching {brand.get('brand_name', 'Unknown')}: {str(e)}")
                conn.rollback()
                continue

        # Final state
        result = {
            'keyword': keyword,
            'discovered': len(discovered_brands),
            'unique': len(unique_brands),
            'enriched': enriched_count,
            'saved': saved_count,
            'rejected': rejected_count,
            'completed_at': datetime.utcnow().isoformat()
        }

        print(f"PR Hunt completed: {result}")
        return result

    except Exception as e:
        print(f"PR Hunt failed: {str(e)}")
        raise

    finally:
        if conn:
            conn.close()


def save_candidate_to_db(cursor, brand: dict, keyword: str):
    """
    Save enriched brand candidate to database

    Args:
        cursor: Database cursor
        brand: Enriched brand data
        keyword: Search keyword used
    """
    query = '''
        INSERT INTO brand_candidates (
            brand_name, website_url, domain, instagram_handle, tiktok_handle,
            pr_manager_name, pr_manager_linkedin, pr_manager_title,
            contact_email, email_source, verification_score, verification_status, is_catch_all,
            application_url, application_method, form_platform,
            logo_url, description, status, discovery_source
        ) VALUES (
            %(brand_name)s, %(website_url)s, %(domain)s, %(instagram_handle)s, %(tiktok_handle)s,
            %(pr_manager_name)s, %(pr_manager_linkedin)s, %(pr_manager_title)s,
            %(contact_email)s, %(email_source)s, %(verification_score)s, %(verification_status)s, %(is_catch_all)s,
            %(application_url)s, %(application_method)s, %(form_platform)s,
            %(logo_url)s, %(description)s, 'PENDING', %(discovery_source)s
        )
        ON CONFLICT (domain) DO UPDATE SET
            updated_at = CURRENT_TIMESTAMP,
            verification_score = EXCLUDED.verification_score,
            verification_status = EXCLUDED.verification_status,
            application_url = EXCLUDED.application_url,
            application_method = EXCLUDED.application_method,
            form_platform = EXCLUDED.form_platform
        RETURNING id
    '''

    # Helper to safely truncate strings
    def truncate(value, max_len):
        if value and isinstance(value, str) and len(value) > max_len:
            return value[:max_len]
        return value

    params = {
        'brand_name': truncate(brand.get('brand_name'), 255),
        'website_url': truncate(brand.get('website_url'), 255),
        'domain': truncate(brand.get('domain'), 255),
        'instagram_handle': truncate(brand.get('instagram_handle'), 255),
        'tiktok_handle': truncate(brand.get('tiktok_handle'), 255),
        'pr_manager_name': truncate(brand.get('pr_manager_name'), 255),
        'pr_manager_linkedin': truncate(brand.get('pr_manager_linkedin'), 255),
        'pr_manager_title': truncate(brand.get('pr_manager_title'), 255),
        'contact_email': truncate(brand.get('contact_email'), 255),
        'email_source': truncate(brand.get('email_source', 'Hunter'), 50),
        'verification_score': brand.get('verification_score', 0),
        'verification_status': truncate(brand.get('verification_status', 'unknown'), 20),
        'is_catch_all': brand.get('is_catch_all', False),
        'application_url': truncate(brand.get('application_url'), 500),
        'application_method': truncate(brand.get('application_method', 'EMAIL_ONLY'), 50),
        'form_platform': truncate(brand.get('form_platform'), 100),
        'logo_url': truncate(brand.get('logo_url'), 500),
        'description': brand.get('description'),  # TEXT field, no limit
        'discovery_source': truncate(f"{keyword} - {brand.get('discovery_source', 'Unknown')}", 100)
    }

    cursor.execute(query, params)


@celery_app.task(name='pr_hunter.reverify_email')
def reverify_email(candidate_id: int):
    """
    Re-verify a specific candidate's email

    Args:
        candidate_id: ID of the brand candidate

    Returns:
        Updated verification status
    """
    service = PRHunterService()
    conn = None

    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Get candidate
        cursor.execute('SELECT * FROM brand_candidates WHERE id = %s', (candidate_id,))
        candidate = cursor.fetchone()

        if not candidate or not candidate.get('contact_email'):
            return {'error': 'Candidate not found or no email'}

        # Re-verify
        verification = service._verify_email_smtp(candidate['contact_email'])

        # Update database
        cursor.execute('''
            UPDATE brand_candidates
            SET verification_score = %s,
                verification_status = %s,
                is_catch_all = %s,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
        ''', (
            verification['score'],
            verification['status'],
            verification['is_catch_all'],
            candidate_id
        ))

        conn.commit()

        return {
            'candidate_id': candidate_id,
            'email': candidate['contact_email'],
            'verification': verification
        }

    except Exception as e:
        print(f"Re-verification failed: {str(e)}")
        if conn:
            conn.rollback()
        raise

    finally:
        if conn:
            conn.close()


# For running tasks manually during development
if __name__ == '__main__':
    # Test discovery
    result = run_pr_hunt.delay('K-Beauty', max_results=10)
    print(f"Task ID: {result.id}")
    print("Waiting for result...")
    print(result.get(timeout=300))  # 5 minute timeout
