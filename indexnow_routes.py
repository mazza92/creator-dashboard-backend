"""
IndexNow Backend Endpoint - Blueprint Version
Handles IndexNow submissions from the frontend
"""

import requests
import json
from flask import Blueprint, request, jsonify

# Create blueprint
indexnow_bp = Blueprint('indexnow', __name__, url_prefix='/api/indexnow')

# IndexNow configuration
INDEXNOW_KEY = '5b821f1380424d116b8da378e4ca2f143a13f7236d7dd3db58d09cb3e0aeb736'
INDEXNOW_KEY_LOCATION = 'https://newcollab.co/5b821f1380424d116b8da378e4ca2f143a13f7236d7dd3db58d09cb3e0aeb736.txt'
INDEXNOW_API_URL = 'https://api.indexnow.org/indexnow'

def submit_to_indexnow(urls):
    """
    Submit URLs to IndexNow API
    """
    try:
        payload = {
            "host": "newcollab.co",
            "key": INDEXNOW_KEY,
            "keyLocation": INDEXNOW_KEY_LOCATION,
            "urlList": urls if isinstance(urls, list) else [urls]
        }

        response = requests.post(
            INDEXNOW_API_URL,
            headers={'Content-Type': 'application/json; charset=utf-8'},
            json=payload,
            timeout=10
        )

        if response.status_code == 200:
            print(f"✅ IndexNow: Successfully submitted {len(urls) if isinstance(urls, list) else 1} URLs")
            return True
        else:
            print(f"❌ IndexNow: Failed to submit URLs. Status: {response.status_code}")
            return False

    except Exception as e:
        print(f"❌ IndexNow: Error submitting URLs: {str(e)}")
        return False

@indexnow_bp.route('/submit', methods=['POST'])
def indexnow_submit():
    """
    Endpoint to submit URLs to IndexNow
    """
    try:
        data = request.get_json()

        if not data or 'urls' not in data:
            return jsonify({'error': 'URLs are required'}), 400

        urls = data['urls']
        if not isinstance(urls, list):
            urls = [urls]

        # Validate URLs
        for url in urls:
            if not url.startswith('https://newcollab.co/'):
                return jsonify({'error': f'Invalid URL: {url}. Must be from newcollab.co domain'}), 400

        # Submit to IndexNow
        success = submit_to_indexnow(urls)

        if success:
            return jsonify({
                'success': True,
                'message': f'Successfully submitted {len(urls)} URLs to IndexNow',
                'urls': urls
            })
        else:
            return jsonify({
                'success': False,
                'message': 'Failed to submit URLs to IndexNow'
            }), 500

    except Exception as e:
        return jsonify({'error': str(e)}), 500

@indexnow_bp.route('/submit-creator', methods=['POST'])
def indexnow_submit_creator():
    """
    Endpoint to submit creator profile to IndexNow
    """
    try:
        data = request.get_json()

        if not data or 'username' not in data:
            return jsonify({'error': 'Username is required'}), 400

        username = data['username']
        creator_url = f'https://newcollab.co/c/{username}'

        success = submit_to_indexnow([creator_url])

        if success:
            return jsonify({
                'success': True,
                'message': f'Successfully submitted creator profile: {creator_url}',
                'url': creator_url
            })
        else:
            return jsonify({
                'success': False,
                'message': 'Failed to submit creator profile to IndexNow'
            }), 500

    except Exception as e:
        return jsonify({'error': str(e)}), 500

@indexnow_bp.route('/submit-key-pages', methods=['POST'])
def indexnow_submit_key_pages():
    """
    Endpoint to submit key pages to IndexNow
    """
    try:
        key_pages = [
            'https://newcollab.co/',
            'https://newcollab.co/blog',
            'https://newcollab.co/register/creator',
            'https://newcollab.co/register/brand',
            'https://newcollab.co/about',
            'https://newcollab.co/contact'
        ]

        success = submit_to_indexnow(key_pages)

        if success:
            return jsonify({
                'success': True,
                'message': f'Successfully submitted {len(key_pages)} key pages to IndexNow',
                'urls': key_pages
            })
        else:
            return jsonify({
                'success': False,
                'message': 'Failed to submit key pages to IndexNow'
            }), 500

    except Exception as e:
        return jsonify({'error': str(e)}), 500
