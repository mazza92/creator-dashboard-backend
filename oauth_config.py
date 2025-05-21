from authlib.integrations.flask_client import OAuth

# Initialize the OAuth object
oauth = OAuth()

# Register Meta OAuth
meta = oauth.register(
    name='meta',
    client_id='519704940800185',  # Replace with your Meta client ID
    client_secret='d115573a00d303800f75b61df402b222',  # Replace with your Meta client secret
    authorize_url='https://www.facebook.com/v9.0/dialog/oauth',
    access_token_url='https://graph.facebook.com/v9.0/oauth/access_token',
    client_kwargs={'scope': 'email public_profile'},
    redirect_uri='http://localhost:5000/creator-portal/callback'
)
