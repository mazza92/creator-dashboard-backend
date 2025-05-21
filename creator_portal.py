from flask import Blueprint, jsonify, request, session, redirect, url_for
from oauth_config import meta  # Import the Meta OAuth object from oauth_config.py


# Define the blueprint for the creator portal
creator_portal = Blueprint('creator_portal', __name__)


# Route for Meta login
@creator_portal.route('/meta-login')
def meta_login():
    redirect_uri = url_for('creator_portal.meta_callback', _external=True)
    return meta.authorize_redirect(redirect_uri)

# Callback after login
@creator_portal.route('/callback')
def meta_callback():
    token = meta.authorize_access_token()
    user_info = meta.get('https://graph.facebook.com/me?fields=id,name,email').json()
    session['creator_profile'] = user_info
    return redirect(url_for('creator_portal.dashboard'))

# Dashboard route (protected)
@creator_portal.route('/dashboard')
def dashboard():
    if 'creator_profile' not in session:
        return redirect(url_for('creator_portal.meta_login'))
    creator = session['creator_profile']
    return jsonify({
        'message': f"Welcome {creator['name']} to the Creator Portal!",
        'profile': creator
    })


