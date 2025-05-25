import psycopg2
from psycopg2 import OperationalError, InterfaceError
from psycopg2.extras import RealDictCursor
import stripe
import paypalrestsdk
#from flask_socketio import SocketIO, emit
from flask import Flask, jsonify, request, session, make_response
from google.oauth2 import id_token
from google.auth.transport.requests import Request
from flask_session import Session
import requests
from flask_cors import CORS
from dotenv import load_dotenv
from werkzeug.utils import secure_filename
from urllib.parse import urlparse, urlunparse
import time
from datetime import timedelta, timezone
import datetime
import bcrypt
from supabase import create_client, Client
import os
import json
import uuid
import logging
import jwt
from flask_jwt_extended import JWTManager, jwt_required, get_jwt_identity
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from jinja2 import Environment, FileSystemLoader
from markupsafe import escape
import decimal
import redis
from redis.exceptions import ConnectionError


# Load environment variables
load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))

app = Flask(__name__)

# Initialize Flask app
logging.basicConfig(level=logging.INFO)
app = Flask(__name__)
app.logger.setLevel(logging.INFO)


app.secret_key = os.urandom(24)

# ✅ Flask Session Configuration
app.config['SECRET_KEY'] = os.getenv('FLASK_SECRET_KEY')
app.config['JWT_SECRET_KEY'] = os.getenv('JWT_SECRET_KEY')
app.config['SESSION_TYPE'] = 'redis'
app.config['SESSION_PERMANENT'] = True
app.config['SESSION_USE_SIGNER'] = False
app.config['SESSION_REDIS'] = redis.Redis.from_url(os.getenv('REDIS_URL'), ssl_cert_reqs=None, decode_responses=True)
app.config['SESSION_COOKIE_SECURE'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'None'
app.config['SESSION_COOKIE_HTTPONLY'] = True

try:
    redis_url = os.getenv('REDIS_URL')
    app.logger.info(f"🟢 REDIS_URL: {redis_url}")
    if not redis_url:
        raise ValueError("REDIS_URL not set in environment")
    app.config['SESSION_REDIS'] = redis.Redis.from_url(redis_url, ssl_cert_reqs=None)
    app.config['SESSION_REDIS'].ping()  # Test connection
    Session(app)
    jwt = JWTManager(app)

    app.logger.info("🟢 Session initialized with Redis")
except (ConnectionError, ValueError) as e:
    app.logger.error(f"🔥 Redis initialization error: {str(e)}")
    raise


# Initialize CORS
CORS(app, resources={
    r"/.*": {
        "origins": [
            "http://localhost:3000",
            "https://newcollab.co",
            "https://creator-dashboard-frontend.vercel.app"
        ],
        "methods": ["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        "allow_headers": ["Content-Type", "Authorization"],
        "supports_credentials": True,
        "expose_headers": ["Content-Type", "Authorization"],
        "max_age": 600
    }
})




@app.before_request
#def ensure_session():
#   session.modified = True  # ✅ Force session to persist

# Handle OPTIONS preflight requests
def handle_options():
    if request.method == 'OPTIONS':
        response = jsonify({"message": "Preflight request successful"})
        response.headers['Access-Control-Allow-Origin'] = request.headers.get('Origin', '*')
        response.headers['Access-Control-Allow-Methods'] = 'GET, POST, PUT, DELETE, OPTIONS'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization'
        response.headers['Access-Control-Allow-Credentials'] = 'true'
        app.logger.info(f"Handled OPTIONS preflight for {request.path} with methods: GET, POST, PUT, DELETE, OPTIONS")
        return response

@app.after_request
def add_cors_headers(response):
    origin = request.headers.get('Origin')
    allowed_origins = [
        'http://localhost:3000',
        'https://newcollab.co',
        'https://creator-dashboard-frontend.vercel.app'
    ]
    if origin in allowed_origins:
        response.headers['Access-Control-Allow-Origin'] = origin
        response.headers['Access-Control-Allow-Credentials'] = 'true'
    return response

def get_db_connection():
    try:
        conn = psycopg2.connect(os.getenv('DATABASE_URL'))
        app.logger.info("🟢 Database connection established")
        return conn
    except Exception as e:
        app.logger.error(f"🔥 Database connection error: {str(e)}")
        raise


#socketio = SocketIO(app, cors_allowed_origins="http://localhost:3000")


@app.route('/')
def home():
    return jsonify({'message': 'Flask backend is running!'})



# Stripe Configuration
stripe.api_key = os.getenv('STRIPE_API_KEY')

# PayPal Configuration
paypalrestsdk.configure({
    "mode": "sandbox",  # Change to "live" for production
    "client_id": "AdcSPBFMx1tyFYYM0w7OPguxT3BrrL8J2PoZiiSy58--ou77qv37Av2SCT3s2kiyUBN9WsRpjm4PaFvN",
    "client_secret": "EPrUnQjf8LBTKvYFw-kQn8WoOjDk_FAutJ1WLtsBxATFaXRn7kW69fGQf-IM6ACOrUT_XQoI7U1Gz24E",
})


# Initialize the scheduler
def auto_update_subscriptions():
    conn = get_db_connection()
    cursor = conn.cursor()

    # Renew active subscriptions monthly
    cursor.execute('''
        UPDATE brand_subscriptions
        SET start_date = start_date + INTERVAL '1 month',
            end_date = end_date + INTERVAL '1 month',
            status = CASE 
                WHEN end_date + INTERVAL '1 month' > NOW() THEN 'active'
                ELSE 'inactive'
            END
        WHERE status = 'active' AND end_date <= CURRENT_DATE
    ''')

    # Reset deliverable statuses for renewed subscriptions
    cursor.execute('''
        INSERT INTO subscription_deliverables (subscription_id, creator_id, type, platform, quantity, status, created_at, updated_at)
        SELECT bs.id, csp.creator_id, (d->>'type')::VARCHAR, (d->>'platform')::VARCHAR, (d->>'quantity')::INTEGER, 'Pending', NOW(), NOW()
        FROM brand_subscriptions bs
        JOIN creator_subscription_packages csp ON bs.package_id = csp.id
        CROSS JOIN jsonb_array_elements(csp.deliverables) d
        WHERE bs.status = 'active' AND bs.end_date <= CURRENT_DATE
        ON CONFLICT DO NOTHING
    ''')

    conn.commit()
    conn.close()


# Supabase credentials
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
SUPABASE_BUCKET = os.getenv("SUPABASE_BUCKET", "creators")

# Database credentials
DB_HOST = os.getenv("DB_HOST")
DB_USER = os.getenv("DB_USER")
DB_NAME = os.getenv("DB_NAME")
DB_PASSWORD = os.getenv("DB_PASSWORD")
DB_PORT = os.getenv("DB_PORT", "5432")

# Initialize Supabase client
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Allowed file extensions (updated to include video types)
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'pdf', 'txt', 'mp4', 'mov', 'webm', 'avi'}

# Utility function to validate file extensions
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def upload_file_to_supabase(file, bucket_name):
    """
    Uploads a file to Supabase and returns its public URL.
    """
    try:
        if file and allowed_file(file.filename):
            # Generate a secure filename
            unique_prefix = uuid.uuid4().hex
            filename = f"{unique_prefix}_{secure_filename(file.filename)}"
            print(f"Uploading file: {filename}, Bucket: {bucket_name}, Content-Type: {file.content_type}")

            # Read file content
            file_data = file.read()

            # Upload file to Supabase
            upload_response = supabase.storage.from_(bucket_name).upload(filename, file_data, {
                "content-type": file.content_type
            })

            # Print and inspect upload_response for debugging
            print(f"Upload response type: {type(upload_response)}")
            print(f"Upload response content: {upload_response}")

            # If the response is not iterable, adapt based on the structure
            if hasattr(upload_response, 'error') and upload_response.error:
                raise Exception(f"Supabase Upload Error: {upload_response.error}")

            # Fetch public URL
            public_url_response = supabase.storage.from_(bucket_name).get_public_url(filename)
            print(f"Uploaded file URL: {public_url_response}")
            return public_url_response

        raise ValueError("Invalid file or unsupported file type.")

    except Exception as e:
        print(f"Error uploading file to Supabase: {e}")
        raise

def normalize_instagram_url(url):
    try:
        parsed_url = urlparse(url)
        # Check if the URL is from Instagram and contains a reel
        if 'instagram.com' in parsed_url.netloc and '/reel/' in parsed_url.path:
            # Extract the part after '/reel/'
            parts = parsed_url.path.split('/reel/')
            if len(parts) > 1:
                reel_id = parts[1].split('/')[0]  # Get the first part after '/reel/'
                # Construct the normalized URL
                normalized_url = urlunparse((
                    parsed_url.scheme,  # 'https'
                    parsed_url.netloc,  # 'www.instagram.com'
                    f'/reel/{reel_id}/',  # Path: '/reel/{REEL_ID}/'
                    '', '', '',  # Clear query, params, and fragments
                ))
                return normalized_url
        return None  # Return None if the URL is invalid
    except Exception as e:
        print(f"Error normalizing URL: {e}")
        return None


# Google Sign-In endpoint (existing users only)
@app.route('/google-signup', methods=['POST'])
def google_signup():
    try:
        data = request.get_json()
        if not data or 'idToken' not in data:
            app.logger.error("Missing idToken in request")
            return jsonify({'error': 'Missing ID token'}), 400

        id_token_str = data.get('idToken')
        email = data.get('email')
        name = data.get('name')

        app.logger.debug(f"Received Google Sign-In request: email={email}, name={name}, idToken={id_token_str[:50]}...")

        # Verify Firebase ID token
        client_id = "auth-app-feed3"  # Firebase project ID
        app.logger.debug(f"Using client_id (project ID): {client_id}")
        try:
            idinfo = id_token.verify_firebase_token(id_token_str, Request(), client_id)
            app.logger.debug(f"Verified token: issuer={idinfo['iss']}, audience={idinfo['aud']}, email={idinfo['email']}, kid={idinfo.get('kid')}")
        except ValueError as e:
            app.logger.error(f"Token verification failed: {str(e)}")
            try:
                decoded = jwt.decode(id_token_str, options={"verify_signature": False})
                app.logger.debug(f"Token payload (unverified): audience={decoded.get('aud')}, issuer={decoded.get('iss')}, email={decoded.get('email')}, kid={decoded.get('kid')}, iat={decoded.get('iat')}, exp={decoded.get('exp')}")
            except Exception as decode_error:
                app.logger.error(f"Failed to decode token payload: {str(decode_error)}")
            return jsonify({'error': f'Invalid token: {str(e)}'}), 401

        if idinfo['iss'] != f"https://securetoken.google.com/{client_id}":
            app.logger.error(f"Invalid token issuer: {idinfo['iss']}, expected: https://securetoken.google.com/{client_id}")
            return jsonify({'error': 'Invalid token issuer'}), 401

        if idinfo['email'] != email:
            app.logger.error(f"Email mismatch: token_email={idinfo['email']}, provided_email={email}")
            return jsonify({'error': 'Email mismatch'}), 401

        # Connect to database
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Check if user exists
        cursor.execute('SELECT id, email, role FROM users WHERE email = %s', (email,))
        user = cursor.fetchone()

        if user:
            # Existing user: Fetch creator_id if role is creator
            user_id = user['id']
            role = user['role']
            creator_id = None
            if role == 'creator':
                cursor.execute('SELECT id FROM creators WHERE user_id = %s', (user_id,))
                creator = cursor.fetchone()
                if creator:
                    creator_id = creator['id']
                else:
                    app.logger.error(f"No creator record found for user_id: {user_id}")
                    conn.close()
                    return jsonify({'error': 'Creator profile not found. Please complete registration.'}), 404

            # Set session
            session['user_id'] = user_id
            session['user_role'] = role
            if creator_id:
                session['creator_id'] = creator_id
            app.logger.info(f"Logged in existing user: {user_id}, role: {role}, creator_id: {creator_id}")
            app.logger.debug(f"Session before response: {session}")
            conn.close()
            return jsonify({'user_id': user_id, 'user_role': role}), 200
        else:
            # New user: Reject and prompt registration
            app.logger.info(f"No account found for email: {email}. User must register.")
            conn.close()
            return jsonify({'error': 'No account found. Please register first.'}), 404

    except ValueError as e:
        app.logger.error(f"Token verification failed: {str(e)}")
        return jsonify({'error': 'Invalid token: ' + str(e)}), 401
    except psycopg2.Error as e:
        app.logger.error(f"Database error: {str(e)}")
        return jsonify({'error': 'Database error. Please try again.'}), 500
    except Exception as e:
        app.logger.error(f"Unexpected error in google_signup: {str(e)}")
        return jsonify({'error': 'An unexpected error occurred. Please try again.'}), 500
    
@app.route('/forgot-password', methods=['POST'])
def forgot_password():
    try:
        data = request.get_json()
        if not data or 'email' not in data:
            app.logger.error("Missing email in forgot password request")
            return jsonify({'error': 'Email is required'}), 400

        email = data['email']

        # Connect to database
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Check if user exists and has a password
        cursor.execute('SELECT id, email, password FROM users WHERE email = %s', (email,))
        user = cursor.fetchone()
        conn.close()

        if not user:
            app.logger.info(f"No account found for email: {email}")
            return jsonify({'error': 'Email not found'}), 404

        if user['password'] is None:
            app.logger.info(f"User {email} uses Google Sign-In; cannot reset password")
            return jsonify({'error': 'This account uses Google Sign-In. Please sign in with Google.'}), 400

        # Generate JWT token
        secret_key = os.getenv('JWT_SECRET_KEY', app.config['SECRET_KEY'])
        token = jwt.encode(
            {
                'user_id': user['id'],
                'email': email,
                'exp': datetime.datetime.utcnow() + datetime.timedelta(hours=1)
            },
            secret_key,
            algorithm='HS256'
        )

        # Send reset email
        smtp_server = os.getenv('SMTP_SERVER', 'smtp.gmail.com')
        smtp_port = int(os.getenv('SMTP_PORT', 587))
        smtp_username = os.getenv('SMTP_USERNAME')
        smtp_password = os.getenv('SMTP_PASSWORD')

        msg = MIMEMultipart()
        msg['From'] = smtp_username
        msg['To'] = email
        msg['Subject'] = "Password Reset Request"
        body = f"""
        Hello,

        You requested to reset your password. Click the link below to set a new password:
        http://localhost:3000/reset-password?token={token}

        This link will expire in 1 hour. If you did not request a password reset, please ignore this email.

        Best regards,
        Your App Team
        """
        msg.attach(MIMEText(body, 'plain'))

        try:
            server = smtplib.SMTP(smtp_server, smtp_port)
            server.starttls()
            server.login(smtp_username, smtp_password)
            server.sendmail(smtp_username, email, msg.as_string())
            server.quit()
            app.logger.info(f"Password reset email sent to: {email}")
        except Exception as e:
            app.logger.error(f"Failed to send reset email to {email}: {str(e)}")
            return jsonify({'error': 'Failed to send reset email. Please try again.'}), 500

        return jsonify({'message': 'Password reset email sent. Please check your inbox.'}), 200

    except psycopg2.Error as e:
        app.logger.error(f"Database error: {str(e)}")
        return jsonify({'error': 'Database error. Please try again.'}), 500
    except Exception as e:
        app.logger.error(f"Unexpected error in forgot_password: {str(e)}")
        return jsonify({'error': 'An unexpected error occurred. Please try again.'}), 500

# Reset Password endpoint
@app.route('/reset-password', methods=['POST'])
def reset_password():
    try:
        data = request.get_json()
        if not data or not all(key in data for key in ['token', 'new_password']):
            app.logger.error("Missing token or new_password in reset password request")
            return jsonify({'error': 'Token and new password are required'}), 400

        token = data['token']
        new_password = data['new_password']

        # Validate JWT token
        secret_key = os.getenv('JWT_SECRET_KEY', app.config['SECRET_KEY'])
        try:
            payload = jwt.decode(token, secret_key, algorithms=['HS256'])
            user_id = payload['user_id']
            email = payload['email']
        except jwt.ExpiredSignatureError:
            app.logger.error("Password reset token expired")
            return jsonify({'error': 'Password reset link has expired. Please request a new one.'}), 400
        except jwt.InvalidTokenError:
            app.logger.error("Invalid password reset token")
            return jsonify({'error': 'Invalid password reset link.'}), 400

        # Connect to database
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Verify user
        cursor.execute('SELECT id, email FROM users WHERE id = %s AND email = %s', (user_id, email))
        user = cursor.fetchone()

        if not user:
            app.logger.error(f"No user found for user_id: {user_id}, email: {email}")
            conn.close()
            return jsonify({'error': 'Invalid user.'}), 404

        # Hash new password
        hashed_password = bcrypt.hashpw(new_password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

        # Update password
        cursor.execute(
            'UPDATE users SET password = %s WHERE id = %s',
            (hashed_password, user_id)
        )
        conn.commit()
        conn.close()

        app.logger.info(f"Password reset successful for user_id: {user_id}, email: {email}")
        return jsonify({'message': 'Password reset successful. Please sign in.'}), 200

    except psycopg2.Error as e:
        app.logger.error(f"Database error: {str(e)}")
        return jsonify({'error': 'Database error. Please try again.'}), 500
    except Exception as e:
        app.logger.error(f"Unexpected error in reset_password: {str(e)}")
        return jsonify({'error': 'An unexpected error occurred. Please try again.'}), 500
    
    
@app.route('/login', methods=['POST', 'OPTIONS'])
def login():
    if request.method == 'OPTIONS':
        response = jsonify({})
        response.headers['Access-Control-Allow-Origin'] = request.headers.get('Origin', 'http://localhost:3000')
        response.headers['Access-Control-Allow-Methods'] = 'POST, OPTIONS'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization'
        response.headers['Access-Control-Allow-Credentials'] = 'true'
        return response, 200
    data = request.get_json()
    email = data.get('email')
    password = data.get('password')
    app.logger.info(f"🟢 Login Attempt: {data}")
    if not email or not password:
        app.logger.error("🔥 Missing email or password")
        response = jsonify({'error': 'Missing email or password'})
        response.headers['Access-Control-Allow-Origin'] = request.headers.get('Origin', 'http://localhost:3000')
        response.headers['Access-Control-Allow-Credentials'] = 'true'
        return response, 400
    conn = get_db_connection()
    if not conn:
        app.logger.error("🔥 Database connection failed")
        response = jsonify({'error': 'Database connection failed'})
        response.headers['Access-Control-Allow-Origin'] = request.headers.get('Origin', 'http://localhost:3000')
        response.headers['Access-Control-Allow-Credentials'] = 'true'
        return response, 500
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute("SELECT id, password, role FROM users WHERE email = %s", (email,))
        user = cursor.fetchone()
        app.logger.info(f"🟢 User Query Result: {user}")
        if not user or not bcrypt.checkpw(password.encode('utf-8'), user['password'].encode('utf-8')):
            app.logger.error("🔥 Invalid email or password")
            response = jsonify({'error': 'Invalid email or password'})
            response.headers['Access-Control-Allow-Origin'] = request.headers.get('Origin', 'http://localhost:3000')
            response.headers['Access-Control-Allow-Credentials'] = 'true'
            return response, 401
        user_id = user['id']
        user_role = user['role']
        app.logger.info(f"🟢 User Found: ID={user_id}, Role={user_role}")
        cursor.execute("SELECT id FROM creators WHERE user_id = %s", (user_id,))
        creator = cursor.fetchone()
        creator_id = creator['id'] if creator else None
        app.logger.info(f"🟢 Retrieved Creator ID: {creator_id}")
        cursor.execute("SELECT id FROM brands WHERE user_id = %s", (user_id,))
        brand = cursor.fetchone()
        brand_id = brand['id'] if brand else None
        session.clear()
        session['user_id'] = user_id
        session['user_role'] = user_role
        session['creator_id'] = creator_id
        session['brand_id'] = brand_id
        session.permanent = True
        app.logger.info(f"🟢 Session Set: {session}")
        login_response = {
            'message': 'Login successful',
            'user_id': user_id,
            'user_role': user_role,
            'creator_id': creator_id,
            'brand_id': brand_id,
            'redirect_url': 'http://localhost:3000/creator/dashboard-overview' if user_role == 'creator' else 'http://localhost:3000/brand/dashboard-overview'
        }
        response = make_response(jsonify(login_response))
        response.set_cookie('session', session.sid, samesite='None', secure=True, httponly=True, max_age=86400)
        response.headers['Access-Control-Allow-Origin'] = request.headers.get('Origin', 'http://localhost:3000')
        response.headers['Access-Control-Allow-Credentials'] = 'true'
        app.logger.info(f"🟢 Login Response Headers: {response.headers}")
        app.logger.info(f"🟢 Login successful for user_id: {user_id}, role: {user_role}")
        return response, 200
    except Exception as e:
        app.logger.error(f"🔥 Login Error: {str(e)}")
        response = jsonify({'error': str(e)})
        response.headers['Access-Control-Allow-Origin'] = request.headers.get('Origin', 'http://localhost:3000')
        response.headers['Access-Control-Allow-Credentials'] = 'true'
        return response, 500
    finally:
        cursor.close()
        conn.close()


@app.route('/logout', methods=['POST'])
def logout():
    try:
        session.clear()
        response = jsonify({"message": "Logged out successfully"})
        response.headers['Access-Control-Allow-Origin'] = 'http://localhost:3000'
        response.headers['Access-Control-Allow-Credentials'] = 'true'
        response.headers['Access-Control-Allow-Methods'] = 'POST, OPTIONS'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization'
        response.set_cookie('session', '', expires=0, httponly=True, samesite='None', secure=False)
        return response, 200
    except Exception as e:
        app.logger.error(f"🔥 Logout Error: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/logout', methods=['OPTIONS'])
def logout_options():
    response = jsonify({"message": "CORS preflight successful"})
    response.headers['Access-Control-Allow-Origin'] = 'http://localhost:3000'
    response.headers['Access-Control-Allow-Credentials'] = 'true'
    response.headers['Access-Control-Allow-Methods'] = 'POST, OPTIONS'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization'
    return response, 200


@app.route('/profile', methods=['GET'])
def get_profile():
    try:
        user_id = session.get('user_id')  
        user_role = session.get('user_role', 'creator')
        creator_id = session.get('creator_id')

        if not user_id:
            app.logger.warning("❌ Unauthorized access attempt: No user_id in session")
            return jsonify({"error": "Unauthorized"}), 403  
        
        app.logger.info(f"✅ Fetching profile for user_id={user_id}, role={user_role}")

        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        cursor.execute('SELECT email, phone, country FROM users WHERE id = %s', (user_id,))
        user_data = cursor.fetchone()

        if not user_data:
            return jsonify({"error": "User not found"}), 404

        profile_data = {**user_data, "user_role": user_role, "creator_id": creator_id}

        if user_role == 'creator':
            cursor.execute('SELECT * FROM creators WHERE user_id = %s', (user_id,))
            creator_data = cursor.fetchone() or {}
            profile_data.update(creator_data)
        else:  # brand
            cursor.execute('SELECT * FROM brands WHERE user_id = %s', (user_id,))
            brand_data = cursor.fetchone() or {}
            if 'logo' in brand_data:
                brand_data['image_profile'] = brand_data.pop('logo')  # Ensure logo is renamed
            profile_data.update(brand_data)

        conn.close()
        app.logger.info(f"🟢 Profile Data Sent: {profile_data}")
        return jsonify(profile_data), 200

    except Exception as e:
        app.logger.error(f"🔥 Error fetching profile: {str(e)}")
        return jsonify({"error": str(e)}), 500
    finally:
        if 'cursor' in locals():
            cursor.close()
        if 'conn' in locals():
            conn.close()
    
@app.route('/profile', methods=['GET'])
def get_user_profile():
    app.logger.info(f"🔍 Request headers: {request.headers}")
    app.logger.info(f"🔍 Cookies received: {request.cookies}")
    app.logger.info(f"🔍 Session contents: {session}")
    if 'user_id' not in session:
        app.logger.error(f"🔥 Authentication failed: Headers={request.headers}, Session={session}")
        response = jsonify({'error': 'User not authenticated'})
        response.headers['Access-Control-Allow-Origin'] = request.headers.get('Origin', 'http://localhost:3000')
        response.headers['Access-Control-Allow-Credentials'] = 'true'
        return response, 401
    conn = get_db_connection()
    if not conn:
        response = jsonify({'error': 'Database connection failed'})
        response.headers['Access-Control-Allow-Origin'] = request.headers.get('Origin', 'http://localhost:3000')
        response.headers['Access-Control-Allow-Credentials'] = 'true'
        return response, 500
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute("SELECT * FROM users WHERE id = %s", (session['user_id'],))
        user = cursor.fetchone()
        if not user:
            response = jsonify({'error': 'User not found'})
            response.headers['Access-Control-Allow-Origin'] = request.headers.get('Origin', 'http://localhost:3000')
            response.headers['Access-Control-Allow-Credentials'] = 'true'
            return response, 404
        user_data = dict(user)
        user_data.pop('password', None)
        cursor.execute("SELECT id, bio, followers_count FROM creators WHERE user_id = %s", (session['user_id'],))
        creator = cursor.fetchone()
        if creator:
            user_data['creator_id'] = creator['id']
            user_data['bio'] = creator['bio']
            user_data['followers_count'] = creator['followers_count']
        user_data['user_role'] = session.get('user_role', 'user')
        response = jsonify(user_data)
        response.headers['Access-Control-Allow-Origin'] = request.headers.get('Origin', 'http://localhost:3000')
        response.headers['Access-Control-Allow-Credentials'] = 'true'
        app.logger.info(f"🔍 Profile response headers: {response.headers}")
        return response, 200
    except Exception as e:
        app.logger.error(f"🔥 Profile fetch error: {str(e)}")
        response = jsonify({'error': str(e)})
        response.headers['Access-Control-Allow-Origin'] = request.headers.get('Origin', 'http://localhost:3000')
        response.headers['Access-Control-Allow-Credentials'] = 'true'
        return response, 500
    finally:
        cursor.close()
        conn.close()


# Profile Image Upload Endpoint
@app.route('/profile/update-image', methods=['POST'])
def update_profile_image():
    try:
        if 'image' not in request.files:
            return jsonify({'error': 'No image file part'}), 400

        file = request.files['image']
        if not file or not allowed_file(file.filename):
            return jsonify({'error': 'Invalid file type'}), 400

        # Upload to Supabase
        file_url = upload_file_to_supabase(file, SUPABASE_BUCKET)
        if not file_url:
            return jsonify({'error': 'Failed to upload image'}), 500

        user_id = session.get('user_id')
        user_role = session.get('user_role')

        if not user_id or not user_role:
            return jsonify({"error": "Unauthorized access"}), 403

        conn = get_db_connection()
        cursor = conn.cursor()

        if user_role == 'creator':
            cursor.execute("UPDATE creators SET image_profile = %s WHERE user_id = %s", (file_url, user_id))
        elif user_role == 'brand':
            cursor.execute("UPDATE brands SET logo = %s WHERE user_id = %s", (file_url, user_id))
        else:
            return jsonify({'error': 'Invalid user role'}), 400

        conn.commit()
        return jsonify({'message': 'Profile image updated successfully', 'file_url': file_url}), 200
    except Exception as e:
        app.logger.error(f"Error updating profile image: {e}")
        return jsonify({'error': str(e)}), 500
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

# File Upload Endpoint
@app.route('/upload-file', methods=['POST'])
def upload_file():
    try:
        if 'file' not in request.files:
            return jsonify({'error': 'No file provided'}), 400

        file = request.files['file']
        if not file or not allowed_file(file.filename):
            return jsonify({'error': 'Invalid file type'}), 400

        file_url = upload_file_to_supabase(file, SUPABASE_BUCKET)
        if not file_url:
            return jsonify({'error': 'Failed to upload file'}), 500

        return jsonify({'message': 'File uploaded successfully', 'file_url': file_url}), 200
    except Exception as e:
        print(f"Error uploading file: {e}")
        return jsonify({'error': str(e)}), 500

# Test Supabase Connection
@app.route('/test-supabase', methods=['GET'])
def test_supabase():
    try:
        buckets = supabase.storage.list_buckets()
        return jsonify({'buckets': buckets}), 200
    except Exception as e:
        print(f"Error testing Supabase connection: {e}")
        return jsonify({'error': str(e)}), 500



@app.route('/profile/update', methods=['PUT'])
def update_profile():
    try:
        user_id = session.get("user_id", 1)  # Assuming default user for dev
        user_role = session.get("user_role", "creator")

        conn = get_db_connection()
        cursor = conn.cursor()

        if 'image' in request.files:
            image = request.files['image']
            if image and allowed_file(image.filename):
                # Upload to S3 instead of saving locally
                file_url = upload_file_to_s3(image, S3_BUCKET)
                if file_url:
                    if user_role == 'creator':
                        cursor.execute("UPDATE creators SET image_profile = %s WHERE user_id = %s", (file_url, user_id))
                    elif user_role == 'brand':
                        cursor.execute("UPDATE brands SET logo = %s WHERE user_id = %s", (file_url, user_id))
                    else:
                        return jsonify({"error": "Invalid user role"}), 400
                else:
                    return jsonify({'error': 'Failed to upload to S3'}), 500

        if 'bio' in request.form:
            bio = request.form.get('bio')
            if user_role == 'creator':
                cursor.execute("UPDATE creators SET bio = %s WHERE user_id = %s", (bio, user_id))
            elif user_role == 'brand':
                cursor.execute("UPDATE brands SET description = %s WHERE user_id = %s", (bio, user_id))

        conn.commit()
        cursor.close()
        conn.close()

        return jsonify({"message": "Profile updated successfully"}), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500

    
@app.route('/creators', methods=['GET'])
def get_creators():
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        
        # Fetch creators
        cursor.execute('SELECT * FROM creators')
        creators = cursor.fetchall()
        
        conn.close()

        return jsonify(creators)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/creator-profile/<int:creator_id>', methods=['GET'])
def get_creator_profile(creator_id):
    if not creator_id:
        return jsonify({"error": "Creator ID is required"}), 400

    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        cursor.execute("SELECT * FROM creators WHERE id = %s", (creator_id,))
        profile = cursor.fetchone()

        if not profile:
            return jsonify({"error": "Creator not found"}), 404

        cursor.execute("SELECT * FROM packages WHERE creator_id = %s", (creator_id,))
        offers = cursor.fetchall() or []

        cursor.execute("""
            SELECT b.name AS brand_name, b.logo AS brand_logo
            FROM bookings bk
            JOIN brands b ON bk.brand_id = b.id
            WHERE bk.creator_id = %s
        """, (creator_id,))
        collaborations = cursor.fetchall() or []

        conn.close()

        return jsonify({
            "creator": profile,
            "offers": offers,
            "collaborations": collaborations
        }), 200

    except Exception as e:
        app.logger.error(f"Error fetching creator profile: {str(e)}")
        return jsonify({"error": str(e)}), 500


from jinja2 import Environment, FileSystemLoader
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from markupsafe import escape

def send_email(to_email, message, data=None, action_url=None, action_text=None, user_id=None):
    try:
        smtp_server = os.getenv('SMTP_SERVER', 'smtp.gmail.com')
        smtp_port = int(os.getenv('SMTP_PORT', 587))
        smtp_username = os.getenv('SMTP_USERNAME')
        smtp_password = os.getenv('SMTP_PASSWORD')

        env = Environment(loader=FileSystemLoader('templates'))
        html_template = env.get_template('email_template.html')
        text_template = env.get_template('email_template.txt')

        safe_message = escape(message)
        safe_data = {k: escape(str(v)) for k, v in (data or {}).items()}
        safe_action_url = action_url
        safe_action_text = escape(action_text) if action_text else None
        safe_user_id = escape(str(user_id)) if user_id else None

        subject = (safe_message[:75] + '...') if len(safe_message) > 78 else safe_message

        html_content = html_template.render(
            message=safe_message,
            data=safe_data,
            action_url=safe_action_url,
            action_text=safe_action_text,
            user_id=safe_user_id
        )
        text_content = text_template.render(
            message=safe_message,
            data=safe_data,
            action_url=safe_action_url,
            action_text=safe_action_text,
            user_id=safe_user_id
        )

        msg = MIMEMultipart('alternative')
        msg['From'] = smtp_username
        msg['To'] = to_email
        msg['Subject'] = subject
        msg.attach(MIMEText(text_content, 'plain'))
        msg.attach(MIMEText(html_content, 'html'))

        server = smtplib.SMTP(smtp_server, smtp_port)
        server.starttls()
        server.login(smtp_username, smtp_password)
        server.sendmail(smtp_username, to_email, msg.as_string())
        server.quit()
        app.logger.info(f"Email sent to {to_email} with subject: {subject}")
    except Exception as e:
        app.logger.error(f"🔥 Failed to send email to {to_email}: {str(e)}")
        raise

def create_notification(user_id, user_role, event_type, data=None, should_send_email=True, parent_conn=None):
    conn = parent_conn
    cursor = None
    try:
        template = NOTIFICATION_TEMPLATES.get(event_type, {}).get(user_role, {})
        if not template:
            app.logger.error(f"No template found for event_type={event_type}, user_role={user_role}")
            return

        message = template['message'](data) if 'message' in template else "Notification triggered."
        action_url = template.get('action_url', lambda d: None)(data) if 'action_url' in template else None
        action_text = template.get('action_text', lambda d: None)(data) if 'action_text' in template else None

        app.logger.debug(f"Creating notification: user_id={user_id}, user_role={user_role}, event_type={event_type}, message={message}, data={data}")
        if not conn:
            conn = get_db_connection()
            if not conn:
                app.logger.error("Failed to establish database connection for notification")
                return
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Check for recent similar notification to avoid duplicates
        cursor.execute(
            '''
            SELECT id
            FROM notifications
            WHERE user_id = %s AND user_role = %s AND event_type = %s
            AND created_at > NOW() - INTERVAL '5 minutes'
            LIMIT 1
            ''',
            (user_id, user_role, event_type)
        )
        recent_notification = cursor.fetchone()
        if recent_notification:
            app.logger.info(f"Skipping duplicate notification for user_id={user_id}, event_type={event_type}, recent_notification_id={recent_notification['id']}")
            return

        cursor.execute('SELECT email, role FROM users WHERE id = %s', (user_id,))
        user = cursor.fetchone()
        if not user:
            app.logger.error(f"No user found for user_id: {user_id}")
            return
        if user['role'] != user_role:
            app.logger.error(f"Role mismatch for user_id {user_id}: expected {user_role}, found {user['role']}")
            return

        app.logger.debug(f"User found: email={user['email']}, role={user['role']}")
        # Convert Decimal values to float for JSON serialization
        if data:
            serialized_data = {
                key: float(value) if isinstance(value, decimal.Decimal) else value
                for key, value in data.items()
            }
        else:
            serialized_data = None

        cursor.execute(
            '''
            INSERT INTO notifications (user_id, user_role, event_type, message, data, created_at)
            VALUES (%s, %s, %s, %s, %s, NOW())
            RETURNING id, user_id, user_role, event_type, message, is_read, created_at, data
            ''',
            (user_id, user_role, event_type, message, json.dumps(serialized_data) if serialized_data else None)
        )
        notification = cursor.fetchone()
        notification_id = notification['id']
        app.logger.info(f"Notification inserted: ID {notification_id}, user_id={user_id}, event_type={event_type}")

        # Commit only if using a new connection
        if not parent_conn:
            conn.commit()

        # Serialize notification for WebSocket emission
        serialized_notification = {
            'id': notification['id'],
            'user_id': notification['user_id'],
            'user_role': notification['user_role'],
            'event_type': notification['event_type'],
            'message': notification['message'],
            'is_read': notification['is_read'],
            'created_at': notification['created_at'].isoformat(),
            'data': serialized_data
        }

        # Emit WebSocket event
    #    app.logger.debug(f"Emitting WebSocket event: notification_{user_id}_{user_role}, data={serialized_notification}")
    #    socketio.emit(f'notification_{user_id}_{user_role}', serialized_notification)

    #    if should_send_email:
    #        for attempt in range(3):
    #            try:
    #               send_email(
    #                    to_email=user['email'],
    #                    message=message,
    #                    data=serialized_data,
    #                    action_url=action_url,
    #                    action_text=action_text,
    #                    user_id=user_id
    #                )
    #                app.logger.info(f"Email sent to {user['email']} for notification {notification_id}")
    #                break
    #            except Exception as e:
    #                app.logger.error(f"Email attempt {attempt + 1} failed for notification {notification_id}: {str(e)}")
    #                if attempt == 2:
    #                    app.logger.error(f"Failed to send email for notification {notification_id} after 3 attempts")

    except Exception as e:
        app.logger.error(f"Error creating notification for user_id {user_id}, event_type {event_type}: {str(e)}")
        if conn and not parent_conn:
            try:
                conn.rollback()
            except Exception as rollback_e:
                app.logger.error(f"Rollback failed: {str(rollback_e)}")
    finally:
        if cursor:
            try:
                cursor.close()
            except Exception as e:
                app.logger.error(f"Error closing cursor: {str(e)}")
        if conn and not parent_conn and not conn.closed:
            try:
                conn.close()
            except Exception as e:
                app.logger.error(f"Error closing connection: {str(e)}")

# In app.py (or wherever create_notification is defined)
NOTIFICATION_TEMPLATES = {
    'Content Submitted': {
        'creator': {
            'message': lambda data: f"📝 Content Submitted! Your content for '{data['product_name']}' has been submitted for review.",
            'action_url': lambda data: f"/bookings/{data['booking_id']}",
            'action_text': lambda data: "View Submission"
        },
        'brand': {
            'message': lambda data: f"📥 New Content Received! @{data['creator_username']} has submitted content for '{data['product_name']}'. Please review it.",
            'action_url': lambda data: f"/bookings/{data['booking_id']}",
            'action_text': lambda data: "Review Content"
        }
    },
    'Content Approved': {
        'creator': {
            'message': lambda data: f"✨ Content Approved! Your content for '{data['product_name']}' has been approved. Please publish the final link.",
            'action_url': lambda data: f"/bookings/{data['booking_id']}",
            'action_text': lambda data: "Publish Now"
        }
    },
    'Content Approval Confirmed': {
        'brand': {
            'message': lambda data: f"✅ Content Approved! You've approved @{data['creator_username']}'s content for '{data['product_name']}'. Awaiting final publication.",
            'action_url': lambda data: f"/bookings/{data['booking_id']}",
            'action_text': lambda data: "View Booking"
        }
    },
    'Revision Requested': {
        'creator': {
            'message': lambda data: f"🔄 Revision Requested! The brand has requested changes for '{data['product_name']}'. Feedback: {data['revision_notes']}",
            'action_url': lambda data: f"/bookings/{data['booking_id']}",
            'action_text': lambda data: "View Feedback"
        }
    },
    'Bid Submitted': {
        'creator': {
            'message': lambda data: f"💡 New Bid Received! {data['brand_name']} has submitted a bid of €{data['bid_amount']} for Draft #{data['draft_id']}. Pitch: {data['pitch']}",
            'action_url': lambda data: f"/drafts/{data['draft_id']}",
            'action_text': lambda data: "Review Bid"
        }
    },
    'NEW_BOOKING': {
        'creator': {
            'message': lambda data: f"🎉 New Booking Received! Create content for '{data['product_name']}' (Booking #{data['booking_id']}).",
            'action_url': lambda data: f"/bookings/{data['booking_id']}",
            'action_text': lambda data: "View Booking"
        },
        'brand': {
            'message': lambda data: f"✅ Your booking for '{data['product_name']}' (Booking #{data['booking_id']}) has been created. Awaiting creator's content.",
            'action_url': lambda data: f"/bookings/{data['booking_id']}",
            'action_text': lambda data: "View Booking"
        }
    },
    'BOOKING_STEP_UPDATE': {
        'creator': {
            'message': lambda data: f"📢 Booking Update! Your booking #{data['booking_id']} for '{data['product_name']}' is now '{data['status']}'.",
            'action_url': lambda data: f"/bookings/{data['booking_id']}",
            'action_text': lambda data: "Check Status"
        },
        'brand': {
            'message': lambda data: f"📢 Booking Update! Booking #{data['booking_id']} for '{data['product_name']}' is now '{data['status']}'.",
            'action_url': lambda data: f"/bookings/{data['booking_id']}",
            'action_text': lambda data: "Check Status"
        }
    },
    'CONTENT_SUBMITTED': {
        'creator': {
            'message': lambda data: f"👍 Content Submitted! Your content for '{data['product_name']}' (Booking #{data['booking_id']}) has been submitted. Awaiting brand review.",
            'action_url': lambda data: f"/bookings/{data['booking_id']}",
            'action_text': lambda data: "View Submission"
        },
        'brand': {
            'message': lambda data: f"🔔 New Content Submitted! @{data['creator_username']} has submitted content for '{data['product_name']}' (Booking #{data['booking_id']}). Please review.",
            'action_url': lambda data: f"/bookings/{data['booking_id']}",
            'action_text': lambda data: "Review Content"
        }
    },
    'CONTENT_APPROVED': {
        'creator': {
            'message': lambda data: f"🎉 Content Approved! Your content for '{data['product_name']}' (Booking #{data['booking_id']}) has been approved. Please publish it.",
            'action_url': lambda data: f"/bookings/{data['booking_id']}",
            'action_text': lambda data: "Publish Content"
        }
    },
    'CONTENT_APPROVED_CONFIRMATION': {
        'brand': {
            'message': lambda data: f"✅ Content Approved! You've approved content for '{data['product_name']}' by @{data['creator_username']} (Booking #{data['booking_id']}). Awaiting publication.",
            'action_url': lambda data: f"/bookings/{data['booking_id']}",
            'action_text': lambda data: "View Booking"
        }
    },
    'CONTENT_REVISION_REQUESTED': {
        'creator': {
            'message': lambda data: f"📝 Revision Requested! The brand has requested changes for '{data['product_name']}' (Booking #{data['booking_id']}). Feedback: {data['revision_notes']}",
            'action_url': lambda data: f"/bookings/{data['booking_id']}",
            'action_text': lambda data: "Revise Content"
        }
    },
    'BID_ACCEPTED': {
        'brand': {
            'message': lambda data: f"🎉 Bid Accepted! Your €{data['bid_amount']} bid for draft #{data['draft_id']} was accepted by @{data['creator_username']}.",
            'action_url': lambda data: f"/bookings/{data['booking_id']}",
            'action_text': lambda data: "View Booking"
        }
    },
    'PAYMENT_COMPLETED': {
        'creator': {
            'message': lambda data: f"💸 Payment Received! Payment of €{data['amount']} for booking #{data['booking_id']} has been received!",
            'action_url': lambda data: f"/bookings/{data['booking_id']}",
            'action_text': lambda data: "View Booking"
        },
        'brand': {
            'message': lambda data: f"✅ Payment Completed! Payment of €{data['amount']} for booking #{data['booking_id']} has been completed.",
            'action_url': lambda data: f"/bookings/{data['booking_id']}",
            'action_text': lambda data: "View Booking"
        }
    },
    'NEW_MESSAGE': {
        'creator': {
            'message': lambda data: f"📩 New Message! You have a new message from {data['sender_name']} about booking #{data['booking_id']}: {data['message_text']}",
            'action_url': lambda data: f"/bookings/{data['booking_id']}/messages",
            'action_text': lambda data: "Reply Now"
        },
        'brand': {
            'message': lambda data: f"📩 New Message! You have a new message from {data['sender_name']} about booking #{data['booking_id']}: {data['message_text']}",
            'action_url': lambda data: f"/bookings/{data['booking_id']}/messages",
            'action_text': lambda data: "Reply Now"
        }
    },
    'SUBSCRIPTION_INITIATED': {
        'brand': {
            'message': lambda data: f"✅ Subscription Initiated! You have initiated a subscription to '{data['package_name']}' by @{data['creator_username']} (Subscription #{data['subscription_id']}).",
            'action_url': lambda data: f"/subscriptions/{data['subscription_id']}",
            'action_text': lambda data: "View Subscription"
        },
        'creator': {
            'message': lambda data: f"🎉 Subscription Received! {data['brand_name']} has subscribed to your package '{data['package_name']}' (Subscription #{data['subscription_id']}).",
            'action_url': lambda data: f"/subscriptions/{data['subscription_id']}",
            'action_text': lambda data: "View Subscription"
        }
    },
    'DELIVERABLES_APPROVED': {
        'brand': {
            'message': lambda data: f"✅ Deliverables Approved! You have approved deliverables for '{data['package_name']}' by @{data['creator_username']} (Subscription #{data['subscription_id']}). Payment of €{data['amount']} released.",
            'action_url': lambda data: f"/subscriptions/{data['subscription_id']}",
            'action_text': lambda data: "View Subscription"
        },
        'creator': {
            'message': lambda data: f"💸 Payment Received! Payment of €{data['amount']} for '{data['package_name']}' (Subscription #{data['subscription_id']}) has been released by {data['brand_name']}.",
            'action_url': lambda data: f"/subscriptions/{data['subscription_id']}",
            'action_text': lambda data: "View Subscription"
        }
    },
    'NEW_CAMPAIGN_INVITE': {
        'creator': {
            'message': lambda data: f"📩 New Campaign Invite! You have received a new campaign invite for '{data['product_name']}' from {data['brand_name']}. Bid: €{data['bid_amount']}. Please review and accept or reject the invite.",
            'action_url': lambda data: f"/bookings/{data['booking_id']}",
            'action_text': lambda data: "Review Invite"
        },
        'brand': {
            'message': lambda data: f"📩 Campaign Invite Sent! Your campaign invite for '{data['product_name']}' has been sent to the creator. Awaiting response.",
            'action_url': lambda data: f"/bookings/{data['booking_id']}",
            'action_text': lambda data: "View Invite"
        }
    },
    'CAMPAIGN_INVITE_ACCEPTED': {
        'creator': {
            'message': lambda data: f"🎉 Campaign Invite Accepted! You have accepted the campaign invite from {data['brand_name']} for Booking #{data['booking_id']}.",
            'action_url': lambda data: f"/bookings/{data['booking_id']}",
            'action_text': lambda data: "View Booking"
        },
        'brand': {
            'message': lambda data: f"✅ Campaign Invite Accepted! Your campaign invite for '{data['product_name']}' has been accepted by the creator for Booking #{data['booking_id']}.",
            'action_url': lambda data: f"/bookings/{data['booking_id']}",
            'action_text': lambda data: "View Booking"
        }
    },
    'CAMPAIGN_INVITE_REJECTED': {
        'creator': {
            'message': lambda data: f"You have rejected the campaign invite from {data['brand_name']} for Booking #{data['booking_id']}.",
            'action_url': lambda data: f"https://your-platform.com/bookings/{data['booking_id']}",
            'action_text': lambda data: "View Booking"
        },
        'brand': {
            'message': lambda data: f"Your campaign invite for '{data['product_name']}' has been rejected by the creator for Booking #{data['booking_id']}.",
            'action_url': lambda data: f"https://your-platform.com/bookings/{data['booking_id']}",
            'action_text': lambda data: "View Booking"
        }
    }
}

    


# WebSocket connection event
# @socketio.on('connect')
# def handle_connect():
#     user_id = session.get('user_id')
#     user_role = session.get('user_role')
#     if user_id and user_role:
#         logger.info(f"WebSocket connected for user_id={user_id}, user_role={user_role}")
#     else:
#         logger.error("WebSocket connection rejected: No user_id or user_role in session")

# if __name__ == '__main__':
#     socketio.run(app, debug=True, port=5000)



@app.route('/notifications', methods=['GET'])
def get_notifications():
    try:
        user_id = request.args.get('user_id')
        user_role = request.args.get('user_role')
        
        if not user_id or not user_role:
            logger.error("Missing user_id or user_role in query parameters")
            return jsonify({'error': 'user_id and user_role are required'}), 400

        if user_role != session.get('user_role') or str(user_id) != str(session.get('user_id')):
            logger.error(f"Unauthorized access: user_id={user_id}, user_role={user_role}, session={dict(session)}")
            return jsonify({'error': 'Unauthorized'}), 403

        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute(
            '''
            SELECT id, user_id, user_role, event_type, message, is_read, created_at, data
            FROM notifications
            WHERE user_id = %s AND user_role = %s
            ORDER BY created_at DESC
            ''',
            (user_id, user_role)
        )
        notifications = cursor.fetchall()
        conn.close()
        return jsonify(notifications), 200
    except Exception as e:
        logger.error(f"Error fetching notifications: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/notifications/<int:notification_id>/read', methods=['PUT'])
def mark_notification_read(notification_id):
    try:
        user_id = session.get('user_id')
        user_role = session.get('user_role')
        
        if not user_id or not user_role:
            logger.error("No user_id or user_role in session")
            return jsonify({'error': 'Unauthorized'}), 403

        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute(
            '''
            SELECT user_id
            FROM notifications
            WHERE id = %s AND user_role = %s
            ''',
            (notification_id, user_role)
        )
        notification = cursor.fetchone()
        if not notification:
            conn.close()
            logger.error(f"Notification {notification_id} not found for user_role={user_role}")
            return jsonify({'error': 'Notification not found'}), 404

        if str(notification['user_id']) != str(user_id):
            conn.close()
            logger.error(f"Unauthorized: notification user_id={notification['user_id']} does not match session user_id={user_id}")
            return jsonify({'error': 'Unauthorized'}), 403

        cursor.execute(
            '''
            UPDATE notifications
            SET is_read = TRUE
            WHERE id = %s
            RETURNING id
            ''',
            (notification_id,)
        )
        result = cursor.fetchone()
        conn.commit()
        conn.close()

        if not result:
            logger.error(f"Failed to mark notification {notification_id} as read")
            return jsonify({'error': 'Failed to update notification'}), 500

        return jsonify({'message': 'Notification marked as read'}), 200
    except Exception as e:
        logger.error(f"Error marking notification read: {str(e)}")
        return jsonify({'error': str(e)}), 500


@app.route('/creators/me/stats', methods=['GET'])
def get_creator_stats():
    creator_id = session.get('creator_id')
    if not creator_id:
        app.logger.error("Unauthorized: No creator_id in session")
        return jsonify({"error": "Unauthorized"}), 403
    
    try:
        conn = get_db_connection()
        if not conn:
            app.logger.error("Database connection failed")
            return jsonify({'error': 'Database connection failed'}), 500
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Total Earnings: Sum of bid_amount minus platform_fee for Sponsor and Campaign Invite
        cursor.execute('''
            SELECT COALESCE(SUM(
                COALESCE(b.bid_amount, 0) - 
                COALESCE(b.platform_fee, 0)
            ), 0) AS total_earnings
            FROM bookings b
            WHERE b.creator_id = %s 
            AND b.type IN ('Sponsor', 'Campaign Invite')
            AND b.payment_status IN ('Completed', 'Paid', 'On Hold', 'Pending')
        ''', (creator_id,))
        total_earnings = cursor.fetchone()['total_earnings'] or 0.0

        # Active Campaigns: Count of active Sponsor and Campaign Invite bookings
        cursor.execute('''
            SELECT COUNT(*) AS active_campaigns
            FROM bookings b
            WHERE b.creator_id = %s 
            AND b.type IN ('Sponsor', 'Campaign Invite')
            AND b.payment_status IN ('On Hold', 'Pending')
        ''', (creator_id,))
        active_campaigns = cursor.fetchone()['active_campaigns'] or 0

        # Pending Actions: Count of bookings awaiting creator action
        cursor.execute('''
            SELECT COUNT(*) AS pending_actions
            FROM bookings b
            WHERE b.creator_id = %s 
            AND b.type IN ('Sponsor', 'Campaign Invite')
            AND b.content_status IN ('Confirmed', 'Pending', 'Revision Requested', 'Approved')
        ''', (creator_id,))
        pending_actions = cursor.fetchone()['pending_actions'] or 0

        stats = {
            "total_earnings": float(total_earnings),
            "active_campaigns": active_campaigns,
            "pending_actions": pending_actions
        }

        app.logger.info(f"🟢 Stats fetched for creator_id={creator_id}: {stats}")
        return jsonify(stats), 200
    except Exception as e:
        app.logger.error(f"Error fetching creator stats: {str(e)}")
        return jsonify({"error": str(e)}), 500
    finally:
        if 'conn' in locals() and not conn.closed:
            conn.close()


@app.route('/creator-recent-requests', methods=['GET'])
def get_creator_recent_requests():
    creator_id = 1  # Example: session.get('creator_id', 1)
    try:
        requests_data = query_to_fetch_recent_requests(creator_id)
        if requests_data:
            return jsonify(requests_data)
        else:
            return jsonify({'error': 'No recent requests found for this creator'}), 404
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/creator-submission-metrics', methods=['GET'])
def get_submission_metrics():
    creator_id = 1  # For testing, replace with actual logged-in creator's ID
    metrics_data = query_to_fetch_submission_metrics(creator_id)
    return jsonify(metrics_data)

@app.route('/collaboration-requests', methods=['GET'])
def collaboration_requests():
    conn = None
    cursor = None  # Initialize cursor to None

    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        
        # Fetch collaboration requests with unread message count for each request
        cursor.execute('''
            SELECT 
                cr.*, 
                (SELECT COUNT(*) 
                 FROM messages 
                 WHERE request_id = cr.id 
                 AND is_read = FALSE 
                 AND sender_type = 'creator') AS unread_count
            FROM collaboration_requests cr
        ''')
        requests = cursor.fetchall()
        
        # Return the fetched data
        return jsonify(requests), 200
        
    except Exception as e:
        app.logger.error(f"Error in collaboration_requests: {e}")
        return jsonify({'error': str(e)}), 500

    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

@app.route('/creators/<int:creator_id>/bookings', methods=['GET', 'OPTIONS'])
def get_creator_bookings(creator_id):
    if request.method == 'OPTIONS':
        response = jsonify({"message": "CORS preflight successful"})
        response.headers['Access-Control-Allow-Origin'] = 'http://localhost:3000'
        response.headers['Access-Control-Allow-Credentials'] = 'true'
        response.headers['Access-Control-Allow-Methods'] = 'GET, OPTIONS'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization'
        return response, 200

    try:
        session_creator_id = session.get('creator_id')
        if not session_creator_id or session_creator_id != creator_id:
            app.logger.error(f"Unauthorized: Creator {creator_id} not logged in")
            return jsonify({"error": "Unauthorized: Must be logged in as the creator"}), 403

        conn = get_db_connection()
        if not conn:
            app.logger.error("Database connection failed")
            return jsonify({'error': 'Database connection failed'}), 500
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        cursor.execute(
            '''
            SELECT 
                b.id, b.brand_id, b.product_name, b.product_link, b.brief, b.bid_amount,
                b.promotion_date, b.free_sample, b.status, b.type, b.created_at, b.updated_at,
                b.content_link, b.content_file_url, b.submission_notes, b.revision_notes,
                b.payment_status, br.name AS brand_name
            FROM bookings b
            LEFT JOIN brands br ON b.brand_id = br.id
            WHERE b.creator_id = %s
            ORDER BY b.created_at DESC
            ''',
            (creator_id,)
        )
        bookings = cursor.fetchall()

        conn.close()
        app.logger.info(f"Fetched {len(bookings)} bookings for creator {creator_id}")
        return jsonify(bookings), 200

    except Exception as e:
        app.logger.error(f"Error fetching bookings for creator {creator_id}: {str(e)}")
        return jsonify({"error": str(e)}), 500

@app.route('/creators/<int:creator_id>/campaign-invites', methods=['GET'])
def get_campaign_invites(creator_id):
    try:
        user_role = session.get('user_role')
        session_creator_id = session.get('creator_id')
        app.logger.debug(f"Session data: {dict(session)}")

        if not user_role or user_role != 'creator':
            app.logger.error(f"Unauthorized access to campaign invites: user_role={user_role}")
            return jsonify({"error": "Unauthorized: Must be logged in as a creator"}), 403

        if not session_creator_id or session_creator_id != creator_id:
            app.logger.error(f"Unauthorized: creator_id={creator_id} does not match session_creator_id={session_creator_id}")
            return jsonify({"error": "Unauthorized: Creator ID mismatch"}), 403

        status = request.args.get('status', 'Invited')
        valid_statuses = ['Invited', 'Accepted', 'Rejected']
        if status not in valid_statuses:
            app.logger.error(f"Invalid status: {status}. Valid options: {valid_statuses}")
            return jsonify({"error": f"Invalid status. Valid options: {', '.join(valid_statuses)}"}), 400

        conn = get_db_connection()
        if not conn:
            app.logger.error("Database connection failed")
            return jsonify({'error': 'Database connection failed'}), 500
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        query = '''
           SELECT 
                b.id, 
                b.brand_id, 
                b.product_name, 
                b.product_link, 
                b.brief, 
                b.bid_amount,
                b.promotion_date, 
                b.is_gifting, 
                b.status, 
                b.type, 
                b.created_at, 
                b.updated_at,
                b.platforms,  -- Add this line
                COALESCE(br.name, 'Unknown Brand') AS brand_name,
                br.logo AS brand_logo
            FROM bookings b
            LEFT JOIN brands br ON b.brand_id = br.id
            WHERE b.creator_id = %s 
            AND LOWER(b.type) = LOWER('Campaign Invite') 
            AND LOWER(b.status) = LOWER(%s)
            ORDER BY b.created_at DESC
        '''
        params = [creator_id, status]

        app.logger.debug(f"Executing campaign invites query: {query} with params: {params}")
        cursor.execute(query, params)
        invites = cursor.fetchall()
        app.logger.debug(f"Fetched invites: {len(invites)} items: {invites}")

        conn.close()
        app.logger.info(f"Campaign invites response: {len(invites)} items fetched for creator_id={creator_id}, status={status}")
        return jsonify(invites), 200

    except Exception as e:
        app.logger.error(f"Error fetching campaign invites for creator_id={creator_id}: {str(e)}")
        if 'conn' in locals() and not conn.closed:
            conn.close()
        return jsonify({"error": str(e)}), 500

import json

@app.route('/create-campaign-invite', methods=['POST'])
def create_campaign_invite():
    try:
        user_role = session.get('user_role')
        brand_id = session.get('brand_id')
        data = request.get_json()
        app.logger.debug(f"Received campaign invite data: {data}")

        if user_role != 'brand':
            app.logger.error(f"Unauthorized access: user_role={user_role}")
            return jsonify({"error": "Unauthorized: Must be a brand"}), 403

        if not brand_id:
            app.logger.error("No brand_id in session")
            return jsonify({"error": "Brand ID not found in session"}), 403

        required_fields = ['creator_id', 'product_name', 'product_link', 'brief', 'promotion_date', 'bid_amount', 'platforms']
        for field in required_fields:
            if field not in data or data[field] is None or (field == 'platforms' and not data[field]):
                app.logger.error(f"Missing or invalid required field: {field}")
                return jsonify({"error": f"Missing or invalid required field: {field}"}), 400

        conn = get_db_connection()
        if not conn:
            app.logger.error("Database connection failed")
            return jsonify({'error': 'Database connection failed'}), 500
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Validate creator_id
        cursor.execute('SELECT id FROM creators WHERE id = %s', (data['creator_id'],))
        creator = cursor.fetchone()
        if not creator:
            app.logger.error(f"Creator not found: creator_id={data['creator_id']}")
            return jsonify({"error": "Creator not found"}), 404

        # Validate platforms
        valid_platforms = [
            'Instagram', 'TikTok', 'YouTube', 'Facebook', 'Twitter', 
            'LinkedIn', 'Snapchat', 'Pinterest', 'Twitch'
        ]
        invalid_platforms = [p for p in data['platforms'] if p not in valid_platforms]
        if invalid_platforms:
            app.logger.error(f"Invalid platforms: {invalid_platforms}")
            return jsonify({"error": f"Invalid platforms: {invalid_platforms}"}), 400

        # Insert booking
        cursor.execute(
            '''
            INSERT INTO bookings (
                creator_id, brand_id, product_name, product_link, brief, promotion_date,
                is_gifting, bid_amount, type, status, content_status, payment_status,
                payment_method, created_at, updated_at, platforms
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW(), %s)
            RETURNING id, creator_id, brand_id, product_name, type, status, is_gifting, platforms
            ''',
            (
                data['creator_id'],
                brand_id,
                data['product_name'],
                data['product_link'],
                data['brief'],
                data['promotion_date'],
                data.get('is_gifting', False),
                data['bid_amount'],
                'Campaign Invite',
                'Invited',
                'Pending',
                'On Hold',
                'stripe',  # Default, adjust if needed
                json.dumps(data['platforms'])
            )
        )
        booking = cursor.fetchone()
        if not booking:
            app.logger.error("Failed to create booking")
            return jsonify({"error": "Failed to create booking"}), 500

        # Fetch creator and brand details for notifications
        cursor.execute('SELECT user_id FROM creators WHERE id = %s', (data['creator_id'],))
        creator = cursor.fetchone()
        cursor.execute('SELECT user_id, name FROM brands WHERE id = %s', (brand_id,))
        brand = cursor.fetchone()

        notification_data = {
            'booking_id': booking['id'],
            'product_name': booking['product_name'],
            'brand_name': brand['name'],
            'action': 'New campaign invite received'
        }
        create_notification(
            user_id=creator['user_id'],
            user_role='creator',
            event_type='NEW_CAMPAIGN_INVITE',
            data=notification_data,
            should_send_email=True,
            parent_conn=conn
        )
        create_notification(
            user_id=brand['user_id'],
            user_role='brand',
            event_type='CAMPAIGN_INVITE_SENT',
            data={**notification_data, 'action': 'Campaign invite sent to creator'},
            should_send_email=True,
            parent_conn=conn
        )

        conn.commit()
        app.logger.info(f"Campaign invite created: booking_id={booking['id']}, creator_id={data['creator_id']}, brand_id={brand_id}")
        return jsonify({
            "message": "Campaign invite created successfully",
            "booking_id": booking['id']
        }), 201

    except Exception as e:
        app.logger.error(f"Error creating campaign invite: {str(e)}")
        if 'conn' in locals():
            try:
                conn.rollback()
            except Exception as rollback_e:
                app.logger.error(f"Rollback failed: {str(rollback_e)}")
        if 'conn' in locals() and not conn.closed:
            conn.close()
        return jsonify({"error": str(e)}), 500

@app.route('/bookings/<int:booking_id>/accept', methods=['POST'])
def accept_booking(booking_id):
    app.logger.info(f"Starting accept_booking for booking_id: {booking_id}")
    try:
        user_role = session.get('user_role')
        creator_id = session.get('creator_id')
        app.logger.debug(f"Session data: {dict(session)}")

        if user_role != 'creator':
            app.logger.error(f"Unauthorized: user_role={user_role} cannot accept booking")
            return jsonify({"error": "Unauthorized: Must be a creator"}), 403

        conn = get_db_connection()
        if not conn:
            app.logger.error("Database connection failed")
            return jsonify({"error": "Database connection failed"}), 500
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        app.logger.debug(f"Querying booking {booking_id}")
        cursor.execute(
            '''
            SELECT id, creator_id, brand_id, status, type, product_name, content_status
            FROM bookings 
            WHERE id = %s
            ''',
            (booking_id,)
        )
        booking = cursor.fetchone()
        if not booking:
            app.logger.error(f"Booking {booking_id} not found")
            cursor.close()
            conn.close()
            return jsonify({"error": "Booking not found"}), 404
        if booking['creator_id'] != creator_id:
            app.logger.error(f"Creator {creator_id} not authorized for booking {booking_id}")
            cursor.close()
            conn.close()
            return jsonify({"error": "Unauthorized: Not your booking"}), 403
        if booking['status'] != 'Invited':
            app.logger.error(f"Booking {booking_id} status is {booking['status']}, cannot accept")
            cursor.close()
            conn.close()
            return jsonify({"error": f"Booking is already {booking['status']}"}), 400
        if booking['type'] != 'Campaign Invite':
            app.logger.error(f"Booking {booking_id} type is {booking['type']}, not a campaign invite")
            cursor.close()
            conn.close()
            return jsonify({"error": "Booking is not a campaign invite"}), 400

        app.logger.debug(f"Updating booking {booking_id} to Confirmed")
        cursor.execute(
            '''
            UPDATE bookings 
            SET status = %s, content_status = %s, type = %s, updated_at = NOW()
            WHERE id = %s
            RETURNING id, status, content_status, type, updated_at
            ''',
            ('Confirmed', 'Confirmed', 'Sponsor', booking_id)
        )
        updated_booking = cursor.fetchone()
        if not updated_booking:
            app.logger.error(f"Failed to update booking {booking_id}")
            cursor.close()
            conn.close()
            return jsonify({"error": "Failed to accept booking"}), 500

        app.logger.debug(f"Fetching creator and brand details for notifications")
        cursor.execute('SELECT user_id FROM creators WHERE id = %s', (creator_id,))
        creator = cursor.fetchone()
        cursor.execute('SELECT user_id, name FROM brands WHERE id = %s', (booking['brand_id'],))
        brand = cursor.fetchone()

        if creator and brand:
            app.logger.debug(f"Creating notifications for booking {booking_id}")
            notification_data = {
                'booking_id': booking_id,
                'product_name': booking['product_name'],
                'brand_name': brand['name'],
                'action': 'Booking confirmed, please proceed with content creation'
            }
            create_notification(
                user_id=creator['user_id'],
                user_role='creator',
                event_type='CAMPAIGN_INVITE_ACCEPTED',
                data=notification_data,
                should_send_email=True,
                parent_conn=conn
            )
            notification_data['action'] = 'Creator has accepted your campaign invite'
            create_notification(
                user_id=brand['user_id'],
                user_role='brand',
                event_type='CAMPAIGN_INVITE_ACCEPTED',
                data=notification_data,
                should_send_email=True,
                parent_conn=conn
            )

        conn.commit()
        app.logger.info(f"Booking {booking_id} accepted by creator {creator_id}, status='Confirmed', content_status='Confirmed', type='Sponsor'")
        cursor.close()
        conn.close()
        return jsonify({
            "message": "Booking accepted successfully",
            "booking_id": updated_booking['id'],
            "status": updated_booking['status'],
            "content_status": updated_booking['content_status'],
            "type": updated_booking['type'],
            "updated_at": updated_booking['updated_at'].isoformat()
        }), 200

    except Exception as e:
        app.logger.error(f"Error accepting booking {booking_id}: {str(e)}")
        if 'conn' in locals():
            try:
                conn.rollback()
            except Exception as rollback_e:
                app.logger.error(f"Rollback failed: {str(rollback_e)}")
        if 'conn' in locals() and not conn.closed:
            conn.close()
        return jsonify({"error": str(e)}), 500


@app.route('/bookings/<int:booking_id>/reject', methods=['POST', 'OPTIONS'])
def reject_campaign_invite(booking_id):
    if request.method == 'OPTIONS':
        response = jsonify({"message": "CORS preflight successful"})
        response.headers['Access-Control-Allow-Origin'] = 'http://localhost:3000'
        response.headers['Access-Control-Allow-Credentials'] = 'true'
        response.headers['Access-Control-Allow-Methods'] = 'POST, OPTIONS'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization'
        return response, 200

    try:
        creator_id = session.get('creator_id')
        if not creator_id:
            app.logger.error("Unauthorized: No creator_id in session")
            return jsonify({"error": "Unauthorized: Must be logged in as a creator"}), 403

        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Verify booking exists and belongs to the creator
        cursor.execute(
            'SELECT id, creator_id, brand_id, status, type FROM bookings WHERE id = %s AND creator_id = %s',
            (booking_id, creator_id)
        )
        booking = cursor.fetchone()
        if not booking:
            conn.close()
            app.logger.error(f"Booking {booking_id} not found or not owned by creator {creator_id}")
            return jsonify({"error": "Booking not found or unauthorized"}), 404

        if booking['type'] != 'Campaign Invite':
            conn.close()
            app.logger.error(f"Booking {booking_id} is not a campaign invite")
            return jsonify({"error": "Booking is not a campaign invite"}), 400

        if booking['status'] != 'Invited':
            conn.close()
            app.logger.error(f"Booking {booking_id} is not in Invited status: {booking['status']}")
            return jsonify({"error": f"Booking is not in Invited status (current: {booking['status']})"}), 400

        # Update booking status
        cursor.execute(
            '''
            UPDATE bookings
            SET status = %s, updated_at = NOW()
            WHERE id = %s
            RETURNING id, status
            ''',
            ('Rejected', booking_id)
        )
        updated_booking = cursor.fetchone()

        # Notify brand and creator
        cursor.execute('SELECT user_id, name FROM brands WHERE id = %s', (booking['brand_id'],))
        brand = cursor.fetchone()
        cursor.execute('SELECT user_id FROM creators WHERE id = %s', (creator_id,))
        creator = cursor.fetchone()

        if brand and creator:
            notification_data = {
                'booking_id': booking_id,
                'brand_name': brand['name']
            }
            create_notification(
                user_id=brand['user_id'],
                user_role='brand',
                event_type='CAMPAIGN_INVITE_REJECTED',
                data={**notification_data, 'action': 'Creator has rejected your campaign invite'},
                should_send_email=True
            )
            create_notification(
                user_id=creator['user_id'],
                user_role='creator',
                event_type='CAMPAIGN_INVITE_REJECTED',
                data={**notification_data, 'action': 'You have rejected the campaign invite'},
                should_send_email=True
            )

        conn.commit()
        app.logger.info(f"Campaign invite rejected: booking_id={booking_id}")
        return jsonify({
            "message": "Campaign invite rejected successfully",
            "booking_id": updated_booking['id'],
            "status": updated_booking['status']
        }), 200

    except Exception as e:
        app.logger.error(f"Error rejecting campaign invite: {str(e)}")
        if 'conn' in locals():
            conn.rollback()
        return jsonify({"error": str(e)}), 500

    finally:
        if 'cursor' in locals():
            cursor.close()
        if 'conn' in locals() and not conn.closed:
            conn.close()

@app.route('/submit-collaboration-request', methods=['POST'])
def submit_collaboration_request():
    data = request.json
    creator_id = data.get('creator_id')
    brand_id = data.get('brand_id')
    product_name = data.get('product_name')
    content_brief = data.get('content_brief')
    commercial_model = data.get('commercial_model')
    commission_percentage = data.get('commission_percentage')
    fixed_fee = data.get('fixed_fee')
    previous_collaborations = data.get('previous_collaborations')
    status = data.get('status', 'pending')

    # Attempt to fetch creator statistics from the database
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        
        # Fetch the creator’s statistics from the creators table
        cursor.execute('''
            SELECT followers_count, top_locations, primary_age_range, niche AS platform_niche
            FROM creators
            WHERE id = %s
        ''', (creator_id,))
        creator_info = cursor.fetchone()

        # Assign creator statistics if not provided in the request
        statistics = {
            'follower_count': data.get('follower_count') or creator_info.get('followers_count'),
            'top_locations': data.get('top_locations') or creator_info.get('top_locations'),
            'primary_age_range': data.get('primary_age_range') or creator_info.get('primary_age_range'),
            'platform_niche': data.get('platform_niche') or creator_info.get('platform_niche')
        }

        # Convert statistics to JSON string
        statistics_json = json.dumps(statistics)

        # Insert collaboration request data into the database
        cursor.execute('''
            INSERT INTO collaboration_requests 
            (creator_id, brand_id, product_name, content_brief, statistics, commercial_model, 
             commission_percentage, fixed_fee, previous_collaborations, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ''', (creator_id, brand_id, product_name, content_brief, statistics_json, commercial_model, 
              commission_percentage, fixed_fee, previous_collaborations, status))
        
        conn.commit()
        return jsonify({'message': 'Collaboration request submitted successfully!'}), 201

    except Exception as e:
        app.logger.error(f"Error submitting collaboration request: {e}")
        return jsonify({'error': str(e)}), 500

    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()



@app.route('/campaigns/<int:campaign_id>/applicants', methods=['GET'])
def get_campaign_applicants(campaign_id):
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Retrieve applicants for the specific campaign
        cursor.execute('''
            SELECT cr.creator_id, cr.content_brief, c.username, c.image_profile
            FROM collaboration_requests cr
            JOIN creators c ON cr.creator_id = c.id
            WHERE cr.brand_id = %s
        ''', (campaign_id,))
        applicants = cursor.fetchall()

        return jsonify(applicants), 200
    except Exception as e:
        app.logger.error(f"Error fetching applicants: {e}")
        return jsonify({'error': 'Failed to fetch applicants'}), 500
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

@app.route('/creator-submission-history', methods=['GET'])
def creator_submission_history():
    creator_id = 1  # Replace with dynamic creator ID from session or state
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        
        cursor.execute('''
            SELECT cr.id, cr.content_brief, cr.status, b.name AS brand_name,
            (SELECT COUNT(*)
            FROM messages 
            WHERE request_id = cr.id 
            AND is_read = FALSE 
            AND sender_type = 'brand') AS unread_count
            FROM collaboration_requests cr
            JOIN brands b ON cr.brand_id = b.id
            WHERE cr.creator_id = %s
            ORDER BY cr.created_at DESC
        ''', (creator_id,))
        
        submissions = cursor.fetchall()
        conn.close()
        return jsonify(submissions)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/spotlight-brands', methods=['GET'])
def get_spotlight_brands():
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        
        # Fetch spotlight brands including logo URLs
        cursor.execute('SELECT id, name, description, logo FROM brands WHERE spotlight = TRUE LIMIT 6')
        brands = cursor.fetchall()
        
        conn.close()
        
        return jsonify({'brands': brands})
    except Exception as e:
        print(f"Error fetching spotlight brands: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/brands', methods=['GET'])
def get_brands():
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute('SELECT * FROM brands')
        brands = cursor.fetchall()
        conn.close()

        return jsonify(brands)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/brands/<int:brand_id>', methods=['GET'])
def get_brand(brand_id):
    try:
        conn = get_db_connection()  # Your DB connection function
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute('SELECT * FROM brands WHERE id = %s', (brand_id,))
        brand = cursor.fetchone()
        conn.close()
        if not brand:
            app.logger.info(f"Brand with ID {brand_id} not found")
            return jsonify({'error': 'Brand not found'}), 404
        app.logger.info(f"Fetched brand: {brand}")
        return jsonify(brand), 200
    except Exception as e:
        app.logger.error(f"Error fetching brand {brand_id}: {str(e)}")
        return jsonify({'error': str(e)}), 500


@app.route('/brands/<int:brand_id>/social-posts', methods=['GET'])
def get_brand_social_posts(brand_id):
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute('SELECT * FROM social_posts WHERE brand_id = %s ORDER BY created_at DESC LIMIT 3', (brand_id,))
        posts = cursor.fetchall()
        conn.close()
        if not posts:
            app.logger.info(f"No social posts found for brand {brand_id}")
            return jsonify([]), 200
        app.logger.info(f"Fetched social posts for brand {brand_id}: {posts}")
        return jsonify(posts), 200
    except Exception as e:
        app.logger.error(f"Error fetching social posts for brand {brand_id}: {str(e)}")
        return jsonify({'error': str(e)}), 500


@app.route('/create-campaign', methods=['POST'])
def create_campaign():
    try:
        # Parse the JSON data from the request
        data = request.get_json()

        # Extract data for the new table structure
        brand_id = data.get("brand_id", 1)
        campaign_name = data.get("campaign_name")
        product_url = data.get("product_url")
        campaign_description = data.get("campaign_description")
        target_audience_location = data.get("target_audience_location", [])
        target_audience_age = data.get("target_audience_age", [])
        application_start_date = data.get("application_start_date")
        application_end_date = data.get("application_end_date")
        deliverables = data.get("deliverables", [])
        number_of_creators = data.get("number_of_creators")
        budget = data.get("budget")
        currency = data.get("currency")
        gift_free_products = data.get("gift_free_products")
        created_at = datetime.now()
        status = 'active'

        # Check for missing required fields and log them
        required_fields = {
            "campaign_name": campaign_name,
            "product_url": product_url,
            "campaign_description": campaign_description,
            "application_start_date": application_start_date,
            "application_end_date": application_end_date,
            "budget": budget
        }
        missing_fields = [field for field, value in required_fields.items() if value is None]
        
        if missing_fields:
            print(f"Missing required fields: {missing_fields}")
            return jsonify({"error": f"Missing required fields: {', '.join(missing_fields)}"}), 400

        # Connect to the database
        conn = get_db_connection()
        cursor = conn.cursor()

        # Insert into campaigns
        cursor.execute('''
            INSERT INTO campaigns (
                brand_id, campaign_name, product_url, campaign_description, 
                target_audience_location, target_audience_age, application_start_date, application_end_date, 
                deliverables, number_of_creators, budget, currency, gift_free_products, 
                created_at, status
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ''', (
            brand_id, campaign_name, product_url, campaign_description, 
            target_audience_location, target_audience_age, application_start_date, application_end_date, 
            deliverables, number_of_creators, budget, currency, gift_free_products, 
            created_at, status
        ))

        conn.commit()
        conn.close()

        return jsonify({"message": "Campaign created successfully!"}), 201

    except Exception as e:
        print(f"Error creating campaign: {str(e)}")
        return jsonify({"error": str(e)}), 500
        

@app.route('/campaigns', methods=['GET'])
def get_campaigns():
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Updated SQL query to join Campaigns and Brands for full data, without status filtering
        cursor.execute('''
            SELECT 
                       
                c.brand_id, 
                c.id, 
                c.campaign_name, 
                c.campaign_description,    -- Fetch campaign description
                c.deliverables AS visibility,   -- Fetch deliverables as visibility
                c.budget, 
                c.application_start_date, 
                c.application_end_date,
                c.status,                  -- Include status to track each campaign's current state
                b.name AS brand_name,      -- Fetch brand name from Brands table
                b.logo AS brand_logo       -- Fetch brand logo from Brands table
            FROM campaigns c
            LEFT JOIN brands b ON c.brand_id = b.id
            ORDER BY c.created_at DESC;
        ''')
        
        campaigns = cursor.fetchall()
        return jsonify(campaigns)
        
    except Exception as e:
        print("Error fetching campaigns:", e)
        return jsonify({"error": "Failed to fetch campaigns"}), 500
    finally:
        cursor.close()
        conn.close()



@app.route('/campaigns/<int:id>', methods=['GET'])
def get_campaign(id):
    try:
        # Connect to the database
        conn = get_db_connection()
        cursor = conn.cursor()

        # Updated query to select only current columns
        cursor.execute('''
            SELECT id, brand_id, campaign_name, product_url, budget, 
                   application_start_date, application_end_date, target_audience_location, 
                   target_audience_age, deliverables, number_of_creators, currency, 
                   gift_free_products, campaign_description, status, created_at
            FROM campaigns
            WHERE id = %s
        ''', (id,))

        # Fetch and check if campaign exists
        campaign = cursor.fetchone()
        if campaign is None:
            return jsonify({"error": "Campaign not found"}), 404

        # Construct a dictionary with fetched campaign data
        campaign_data = {
            "id": campaign[0],
            "brand_id": campaign[1],
            "campaign_name": campaign[2],
            "product_url": campaign[3],
            "budget": campaign[4],
            "application_start_date": campaign[5],
            "application_end_date": campaign[6],
            "target_audience_location": campaign[7],
            "target_audience_age": campaign[8],
            "deliverables": campaign[9],
            "number_of_creators": campaign[10],
            "currency": campaign[11],
            "gift_free_products": campaign[12],
            "campaign_description": campaign[13],
            "status": campaign[14],
            "created_at": campaign[15]
        }

        # Close the database connection
        conn.close()

        return jsonify(campaign_data)

    except Exception as e:
        app.logger.error(f"Error fetching campaign: {str(e)}")
        return jsonify({"error": "An error occurred while fetching the campaign"}), 500


@app.route('/campaigns/<int:id>', methods=['PUT'])
def update_campaign(id):
    try:
        # Parse the incoming JSON request
        data = request.get_json()

        # Only allow specific fields for update
        allowed_fields = ["product_url", "campaign_description", "application_start_date", "application_end_date"]
        
        # Check if all required editable fields are provided
        missing_fields = [field for field in allowed_fields if field not in data]
        if missing_fields:
            return jsonify({"error": f"Missing required fields: {missing_fields}"}), 400

        # Connect to the database
        conn = get_db_connection()
        cursor = conn.cursor()

        # Update only the allowed fields in the campaigns table
        cursor.execute('''
            UPDATE campaigns 
            SET product_url = %s,
                campaign_description = %s,
                application_start_date = %s,
                application_end_date = %s
            WHERE id = %s
        ''', (
            data["product_url"],
            data["campaign_description"],
            data["application_start_date"],
            data["application_end_date"],
            id
        ))

        # Commit changes and close the connection
        conn.commit()
        conn.close()

        # Return success response
        return jsonify({"message": "Campaign updated successfully!"}), 200

    except Exception as e:
        app.logger.error(f"Error updating campaign: {str(e)}")
        return jsonify({"error": str(e)}), 500


# Route to update campaign status (Turn off, Pause, Resume)
@app.route('/campaigns/<int:id>/status', methods=['PUT'])
def update_campaign_status(id):
    try:
        # Parse the incoming JSON request
        data = request.json
        
        # Extract the new status
        new_status = data.get('status')
        
        # Validate status
        if new_status not in ['active', 'paused', 'inactive']:
            return jsonify({"error": "Invalid status"}), 400

        # Connect to the database
        conn = get_db_connection()
        cursor = conn.cursor()

        # Update the campaign status in the database
        cursor.execute('''
            UPDATE campaigns 
            SET status = %s
            WHERE id = %s
        ''', (new_status, id))

        conn.commit()
        conn.close()

        # Return success response
        return jsonify({"message": "Campaign status updated successfully!"}), 200

    except Exception as e:
        print(f"Error updating campaign status: {str(e)}")
        return jsonify({"error": str(e)}), 500

@app.route('/active-campaigns', methods=['GET'])
def get_active_campaigns():
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        
        # Fetch active campaigns from the 'campaigns' table
        cursor.execute('SELECT * FROM campaigns WHERE status = %s', ('active',))
        active_campaigns = cursor.fetchall()
        
        conn.close()
        
        if not active_campaigns:
            return jsonify({'message': 'No active campaigns found.'}), 404
        
        return jsonify(active_campaigns), 200
    except Exception as e:
        app.logger.error(f"Error fetching active campaigns: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/apply-to-campaign', methods=['POST'])
def apply_to_campaign():
    data = request.json
    creator_id = data.get('creator_id')
    campaign_id = data.get('campaign_id')

    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Fetch campaign and brand data
        cursor.execute('SELECT * FROM campaigns WHERE id = %s', (campaign_id,))
        campaign_data = cursor.fetchone()

        if not campaign_data:
            return jsonify({'error': 'Campaign not found'}), 404

        # Insert application into campaign_applications table
        cursor.execute('''
            INSERT INTO campaign_applications (creator_id, campaign_id, brand_id)
            VALUES (%s, %s, %s)
        ''', (
            creator_id,
            campaign_id,
            campaign_data['brand_id']
        ))

        conn.commit()
        conn.close()

        return jsonify({'message': 'Successfully applied to the campaign!'}), 201
    except Exception as e:
        print(f"Error applying to campaign: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/campaign-applications', methods=['GET'])
def get_campaign_applications():
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Assuming you want to fetch applications for a specific brand
        brand_id = 1  # Replace with the actual brand's ID, possibly from the session
        
        # Query to fetch applications for the brand's campaigns
        cursor.execute('''
            SELECT ca.id, ca.creator_id, c.name as creator_name, c.followers_count, cp.campaign_name
            FROM campaign_applications ca
            JOIN creators c ON ca.creator_id = c.id
            JOIN campaigns cp ON ca.campaign_id = cp.id
            WHERE cp.brand_id = %s
        ''', (brand_id,))
        
        applications = cursor.fetchall()
        conn.close()

        return jsonify(applications), 200
    except Exception as e:
        print(f"Error fetching campaign applications: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/applications/<int:application_id>/accept', methods=['PUT'])
def accept_application(application_id):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # Update the application's status to 'accepted'
        cursor.execute('''
            UPDATE collaboration_requests
            SET status = 'accepted'
            WHERE id = %s
        ''', (application_id,))

        conn.commit()
        conn.close()

        # Optionally, you can send a notification to the creator here
        return jsonify({'message': 'Application accepted successfully!'}), 200

    except Exception as e:
        print(f"Error accepting application: {e}")
        return jsonify({'error': str(e)}), 500


# Route to decline a collaboration request with a reason
@app.route('/applications/<int:id>/decline', methods=['PUT'])
def decline_application(id):
    data = request.json
    reason = data.get('reason', '')
    
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # Check if the collaboration request exists
        cursor.execute("SELECT * FROM collaboration_requests WHERE id = %s", (id,))
        application = cursor.fetchone()

        if application:
            # Update the collaboration request status to 'declined' and add the reason
            cursor.execute('''
                UPDATE collaboration_requests
                SET status = %s, decline_reason = %s
                WHERE id = %s
            ''', ('declined', reason, id))
            conn.commit()

            return jsonify({"message": "Collaboration request declined successfully"}), 200
        else:
            return jsonify({"error": "Collaboration request not found"}), 404

    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/applications/<int:application_id>/inquire', methods=['PUT'])
def inquire_application(application_id):
    try:
        data = request.json
        inquiry_message = data.get('inquiry_message')
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Update the collaboration request with the inquiry message and set the status to 'Waiting for Creator Response'
        cursor.execute('''
            UPDATE collaboration_requests
            SET inquire_message = %s, status = %s
            WHERE id = %s
        ''', (inquiry_message, 'Waiting for Creator Response', application_id))
        
        conn.commit()
        conn.close()
        
        return jsonify({'message': 'Inquiry sent successfully!'}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500



# API for sending messages
@app.route('/messages', methods=['POST'])
def send_message():
    data = request.json
    request_id = data.get('request_id')
    sender_type = data.get('sender_type')  # 'brand' or 'creator'
    message = data.get('message')

    if not request_id or not sender_type or not message:
        return jsonify({'error': 'Missing required fields'}), 400

    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # Insert the message into the messages table with is_read = FALSE
        cursor.execute('''
            INSERT INTO messages (request_id, sender_type, message, created_at, is_read)
            VALUES (%s, %s, %s, NOW(), FALSE)
        ''', (request_id, sender_type, message))

        conn.commit()
        conn.close()

        return jsonify({'message': 'Message sent successfully'}), 201
    except Exception as e:
        return jsonify({'error': str(e)}), 500




# API for fetching messages for a specific request
@app.route('/messages/<int:request_id>', methods=['GET'])
def get_messages_for_request(request_id):
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

         # Fetch all messages for the given request_id
        cursor.execute('''
            SELECT * FROM messages WHERE request_id = %s ORDER BY created_at ASC
        ''', (request_id,))
        messages = cursor.fetchall()

        # Mark all unread messages for this request as read
        cursor.execute('''
            UPDATE messages
            SET is_read = TRUE
            WHERE request_id = %s AND is_read = FALSE
        ''', (request_id,))

        conn.commit()
        conn.close()

        return jsonify(messages), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/reply-inquiry/<int:id>', methods=['POST'])
def reply_inquiry(id):
    try:
        data = request.json
        reply_message = data.get('message')
        sender_type = data.get('sender_type')
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO messages (request_id, sender_type, message, created_at)
            VALUES (%s, %s, %s, NOW())
        ''', (id, sender_type, reply_message,))
        cursor.execute('''
            UPDATE collaboration_requests 
            SET inquire_message = %s, status = %s 
            WHERE id = %s
        ''', (reply_message, 'Waiting for Brand Response', id))
        conn.commit()
        conn.close()
        return jsonify({'message': 'Reply sent successfully'}), 200
    except Exception as e:
        app.logger.error(f"Error in reply_inquiry: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/messages/booking/<int:booking_id>', methods=['POST'])
def send_booking_message(booking_id):
    try:
        data = request.json
        message_text = data.get('message')
        sender_type = data.get('sender_type')  # 'brand' or 'creator'
        if not message_text or not sender_type:
            app.logger.error("Missing message or sender_type")
            return jsonify({'error': 'Message and sender_type are required'}), 400

        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        cursor.execute("SELECT id, creator_id, brand_id FROM bookings WHERE id = %s", (booking_id,))
        booking = cursor.fetchone()
        if not booking:
            app.logger.error(f"Booking ID {booking_id} not found")
            conn.close()
            return jsonify({'error': 'Booking not found'}), 404

        cursor.execute(
            '''
            INSERT INTO messages (booking_id, sender_type, message, created_at, is_read)
            VALUES (%s, %s, %s, NOW(), %s)
            RETURNING id
            ''',
            (booking_id, sender_type, message_text, False)
        )
        message_id = cursor.fetchone()['id']
        app.logger.info(f"Message inserted: ID {message_id} for booking {booking_id}")

        # Notify recipient via email
        recipient_id = booking['brand_id'] if sender_type == 'creator' else booking['creator_id']
        recipient_role = 'brand' if sender_type == 'creator' else 'creator'
        cursor.execute('SELECT username FROM creators WHERE id = %s', (booking['creator_id'],))
        creator = cursor.fetchone()
        cursor.execute('SELECT name FROM brands WHERE id = %s', (booking['brand_id'],))
        brand = cursor.fetchone()

        sender_name = creator['username'] if creator and sender_type == 'creator' else (brand['name'] if brand else 'Unknown Brand')
        app.logger.debug(f"Sender name: {sender_name}, sender_type: {sender_type}")

        if recipient_role == 'creator':
            cursor.execute('SELECT user_id FROM creators WHERE id = %s', (recipient_id,))
        else:
            cursor.execute('SELECT user_id FROM brands WHERE id = %s', (recipient_id,))
        recipient = cursor.fetchone()

        if not recipient or not recipient['user_id']:
            app.logger.error(f"No valid user_id found for recipient_id {recipient_id} (role: {recipient_role})")
        else:
            app.logger.debug(f"Recipient found: user_id={recipient['user_id']}, role={recipient_role}")
            try:
                create_notification(
                    user_id=recipient['user_id'],
                    user_role=recipient_role,
                    event_type='NEW_MESSAGE',
                    data={
                        'booking_id': booking_id,
                        'message_id': message_id,
                        'sender_name': sender_name,
                        'message_text': message_text
                    },
                    should_send_email=True
                )
                app.logger.info(f"Notification triggered for message {message_id}, user_id={recipient['user_id']}, role={recipient_role}")
            except Exception as e:
                app.logger.error(f"Failed to send notification for message {message_id} in booking {booking_id}: {str(e)}")
                # Continue execution despite notification failure

        conn.commit()
        app.logger.info(f"Message sent for booking {booking_id} by {sender_type}")
        cursor.close()
        conn.close()
        return jsonify({'message': 'Message sent successfully'}), 201
    except Exception as e:
        app.logger.error(f"Error sending message for booking {booking_id}: {str(e)}")
        if 'conn' in locals():
            conn.close()
        return jsonify({'error': str(e)}), 500

        
@app.route('/messages/booking/<int:booking_id>', methods=['GET'])
def get_booking_messages(booking_id):
    try:
        user_role = session.get('user_role')
        print(f"📌 Fetching messages for booking {booking_id} with role={user_role}")

        if not user_role:
            print("❌ No user_role in session")
            return jsonify({'error': 'Unauthorized: No user role in session'}), 403

        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Fetch messages
        cursor.execute('''
            SELECT * FROM messages WHERE booking_id = %s ORDER BY created_at ASC
        ''', (booking_id,))
        messages = cursor.fetchall()

        # Mark messages from the opposite role as read
        opposite_sender = 'creator' if user_role == 'brand' else 'brand'
        cursor.execute('''
            UPDATE messages
            SET is_read = TRUE
            WHERE booking_id = %s AND sender_type = %s AND is_read = FALSE
        ''', (booking_id, opposite_sender))

        conn.commit()
        print(f"📌 Fetched {len(messages)} messages for booking {booking_id}, marked {opposite_sender} messages as read")
        cursor.close()
        conn.close()
        return jsonify(messages), 200
    except Exception as e:
        print(f"🔥 Error fetching messages for booking {booking_id}: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/applications/<int:id>/update-status', methods=['PUT'])
def update_application_status(id):
    data = request.json
    new_status = data.get('status')
    
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Update the status of the application
        cursor.execute('''
            UPDATE collaboration_requests
            SET status = %s
            WHERE id = %s
        ''', (new_status, id))
        
        conn.commit()
        conn.close()

        return jsonify({'message': 'Status updated successfully!'}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500



    finally:
        cursor.close()
        conn.close()

@app.route('/register/brand', methods=['POST'])
def register_brand():
    try:
        app.logger.info("🟢 Processing Brand Registration...")
        app.logger.info("Form Data:", request.form.to_dict())
        app.logger.info("Files Received:", request.files)

        # Validate required fields
        required_fields = ['firstName', 'lastName', 'email', 'password', 'brandName', 'brandWebsite', 'brandDescription']
        missing_fields = [field for field in required_fields if not request.form.get(field)]
        if missing_fields:
            return jsonify({'error': f'Missing required fields: {", ".join(missing_fields)}'}), 400

        # Extract fields
        first_name = request.form.get('firstName')
        last_name = request.form.get('lastName')
        email = request.form.get('email')
        phone = request.form.get('phone')
        country = request.form.get('country')
        password = request.form.get('password')
        brand_name = request.form.get('brandName')
        brand_website = request.form.get('brandWebsite')
        brand_description = request.form.get('brandDescription')
        categories = request.form.get('categories')
        terms_accepted = request.form.get('termsAccepted') == 'true'
        role = request.form.get('role', 'brand')
        post_urls = json.loads(request.form.get('postUrls', '[]'))

        # Validate terms
        if not terms_accepted:
            return jsonify({'error': 'You must accept the terms and conditions'}), 400

        # Validate and upload logo
        if 'brandLogo' not in request.files:
            app.logger.error("No brand logo file provided")
            return jsonify({'error': 'No brand logo file provided'}), 400

        file = request.files['brandLogo']
        app.logger.info(f"brandLogo type: {type(file)}, value: {file}")
        if not file or not allowed_file(file.filename):
            app.logger.error("Invalid or missing brand logo file")
            return jsonify({'error': 'Invalid file format. Only PNG or JPEG allowed'}), 400

        logo_url = upload_file_to_supabase(file, SUPABASE_BUCKET)
        if not logo_url:
            app.logger.error("Failed to upload brand logo to Supabase")
            return jsonify({'error': 'Failed to upload brand logo'}), 500

        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Check if email already exists
        cursor.execute("SELECT id FROM users WHERE email = %s", (email,))
        existing_user = cursor.fetchone()
        if existing_user:
            conn.close()
            return jsonify({"error": "This email is already registered. Please use another email or log in."}), 400

        # Hash password securely
        hashed_password = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode()

        # Insert user
        cursor.execute(
            'INSERT INTO users (first_name, last_name, email, phone, country, password, role) VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id',
            (first_name, last_name, email, phone, country, hashed_password, role)
        )
        user_id = cursor.fetchone()['id']
        app.logger.info(f"Inserted user with ID: {user_id}")

        # Insert brand
        cursor.execute(
            'INSERT INTO brands (name, description, category, website, user_id, logo) VALUES (%s, %s, %s, %s, %s, %s) RETURNING id',
            (brand_name, brand_description, categories, brand_website, user_id, logo_url)
        )
        brand_id = cursor.fetchone()['id']
        app.logger.info(f"Inserted brand with ID: {brand_id}")

        # Insert social posts
        for post_url in post_urls:
            if post_url:
                cursor.execute(
                    'INSERT INTO social_posts (brand_id, post_url) VALUES (%s, %s)',
                    (brand_id, post_url)
                )

        conn.commit()
        conn.close()

        # Store user session
        session['user_id'] = user_id
        session['user_role'] = role
        session['brand_id'] = brand_id
        session.modified = True
        app.logger.info(f"🟢 Session Set: user_id={session.get('user_id')}, role={session.get('user_role')}, brand_id={session.get('brand_id')}")

        return jsonify({'redirect_url': '/success'}), 201
    except Exception as e:
        app.logger.error(f"Error registering brand: {str(e)}")
        return jsonify({'error': str(e)}), 500
        

@app.route('/register/creator', methods=['POST'])
def register_creator():
    try:
        app.logger.info("🟢 Processing Creator Registration...")
        app.logger.info("Form Data: %s", request.form.to_dict())
        app.logger.info("Files Received: %s", request.files)

        # Validate required fields
        required_fields = ['firstName', 'lastName', 'email', 'password', 'bio', 'username']
        missing_fields = [field for field in required_fields if not request.form.get(field)]
        if missing_fields:
            app.logger.error(f"Missing required fields: {missing_fields}")
            return jsonify({'error': f'Missing required fields: {", ".join(missing_fields)}'}), 400

        # Extract and validate fields
        first_name = request.form.get('firstName')
        last_name = request.form.get('lastName')
        email = request.form.get('email')
        phone = request.form.get('phone')
        country = request.form.get('country')
        password = request.form.get('password')
        bio = request.form.get('bio')
        username = request.form.get('username')
        primary_age_range = request.form.get('primaryAgeRange')

        # Connect to DB
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Check if email already exists
        cursor.execute("SELECT id FROM users WHERE email = %s", (email,))
        existing_user = cursor.fetchone()
        if existing_user:
            conn.close()
            app.logger.warning(f"Email already registered: {email}")
            return jsonify({"error": "This email is already registered. Please use another email or log in."}), 400

        # Parse JSON fields safely
        try:
            regions = json.loads(request.form.get('regions', '[]'))
            interests = json.loads(request.form.get('interests', '[]'))
            social_links = json.loads(request.form.get('socialLinks', '[]'))
            portfolio_links = json.loads(request.form.get('portfolioLinks', '[]'))
        except json.JSONDecodeError as e:
            conn.close()
            app.logger.error(f"JSON parsing error: {e}")
            return jsonify({'error': f'Invalid JSON format: {str(e)}'}), 400

        # Calculate engagement rate
        metrics = {
            "total_posts": int(request.form.get('totalPosts', 0)),
            "total_views": int(request.form.get('totalViews', 0)),
            "total_likes": int(request.form.get('totalLikes', 0)),
            "total_comments": int(request.form.get('totalComments', 0)),
            "total_shares": int(request.form.get('totalShares', 0)),
        }
        total_views = metrics["total_views"]
        engagement_rate = (
            round((metrics["total_likes"] + metrics["total_comments"] + metrics["total_shares"]) / total_views * 100, 2)
            if total_views > 0 else 0.0
        )

        # Cap engagement rate to prevent overflow
        if engagement_rate >= 1000:
            engagement_rate = 999.99
        app.logger.info(f"📊 Calculated Engagement Rate: {engagement_rate}")

        # Count total followers
        followers_count = sum(
            int(link.get('followersCount', 0)) for link in social_links if str(link.get('followersCount', '')).isdigit()
        )

        # Validate Terms
        if request.form.get('termsAccepted') != 'true':
            conn.close()
            app.logger.error("Terms and conditions not accepted")
            return jsonify({'error': 'You must accept the terms and conditions'}), 400

        # Upload Profile Picture
        if 'imageProfile' not in request.files:
            conn.close()
            app.logger.error("No profile picture file provided")
            return jsonify({'error': 'No profile picture file provided'}), 400

        file = request.files['imageProfile']
        if not allowed_file(file.filename):
            conn.close()
            app.logger.error(f"Invalid file format: {file.filename}")
            return jsonify({'error': 'Invalid file format. Only PNG, JPEG, or JPG allowed'}), 400

        profile_pic_url = upload_file_to_supabase(file, SUPABASE_BUCKET)
        if not profile_pic_url:
            conn.close()
            app.logger.error("Failed to upload profile picture to Supabase")
            return jsonify({'error': 'Failed to upload profile picture'}), 500

        # Hash password securely
        hashed_password = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode()

        # Insert user into `users` table
        cursor.execute(
            '''
            INSERT INTO users (first_name, last_name, email, phone, country, password, role)
            VALUES (%s, %s, %s, %s, %s, %s, 'creator')
            RETURNING id
            ''',
            (first_name, last_name, email, phone, country, hashed_password)
        )
        user_id = cursor.fetchone()['id']
        app.logger.info(f"Inserted user with ID: {user_id}")

        # Insert creator into `creators` table
        cursor.execute(
            '''
            INSERT INTO creators 
            (username, bio, followers_count, platforms, image_profile, social_links, user_id, niche, regions,
             primary_age_range, total_posts, total_views, total_likes, total_comments, total_shares, portfolio_links,
             engagement_rate)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            ''',
            (
                username, bio, followers_count, json.dumps([link['platform'] for link in social_links]),
                profile_pic_url, json.dumps(social_links), user_id, json.dumps(interests), json.dumps(regions),
                primary_age_range, metrics["total_posts"], metrics["total_views"], metrics["total_likes"],
                metrics["total_comments"], metrics["total_shares"], json.dumps(portfolio_links), engagement_rate
            )
        )
        creator_id = cursor.fetchone()['id']
        app.logger.info(f"Inserted creator with ID: {creator_id}")

        conn.commit()

        # Store user session
        session.clear()
        session['user_id'] = user_id
        session['user_role'] = 'creator'
        session['creator_id'] = creator_id
        session.permanent = True
        session.modified = True
        app.logger.info(f"🟢 Session Set: user_id={user_id}, role=creator, creator_id={creator_id}")

        return jsonify({
            'message': 'Registration successful',
            'redirect_url': 'http://localhost:3000/creator/dashboard/overview'
        }), 201

    except Exception as e:
        app.logger.error(f"🔥 Error registering creator: {str(e)}")
        if 'conn' in locals() and not conn.closed:
            conn.rollback()
        return jsonify({'error': str(e)}), 500

    finally:
        if 'cursor' in locals():
            cursor.close()
        if 'conn' in locals() and not conn.closed:
            conn.close()

@app.route('/api/check-email', methods=['POST'])
def check_email():
    data = request.get_json()
    email = data.get('email')

    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("SELECT COUNT(*) FROM users WHERE email = %s", (email,))
    exists = cursor.fetchone()[0] > 0

    cursor.close()
    conn.close()

    return jsonify({"available": not exists})

@app.route('/api/check-username', methods=['POST'])
def check_username():
    data = request.get_json()
    username = data.get('username')

    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("SELECT COUNT(*) FROM creators WHERE username = %s", (username,))
    exists = cursor.fetchone()[0] > 0

    cursor.close()
    conn.close()

    return jsonify({"available": not exists})


@app.route('/success', methods=['GET'])
def registration_success():
    return """
    <html>
        <head><title>Registration Successful</title></head>
        <body>
            <h1>Registration Successful!</h1>
            <p>Your brand has been registered successfully.</p>
            <p>You will be redirected to the dashboard shortly, or <a href="/dashboard">click here</a> to go to the dashboard immediately.</p>
            <script>
                setTimeout(function(){
                    window.location.href = '/dashboard';
                }, 5000); // Redirect after 5 seconds
            </script>
        </body>
    </html>
    """

    
# Route to fetch creator subscription packages (updated for new schema)
@app.route('/creators/<int:creator_id>/subscription-packages', methods=['GET'])
def get_creator_packages(creator_id):
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute('''
            SELECT id, package_name, deliverables, frequency, description, price, created_at
            FROM creator_subscription_packages 
            WHERE creator_id = %s
        ''', (creator_id,))
        packages = cursor.fetchall()
        print(f"📌 Fetched packages for creator_id {creator_id}: {packages}")
        conn.close()
        return jsonify(packages), 200
    except Exception as e:
        app.logger.error(f"Error fetching packages: {str(e)}")
        return jsonify({"error": str(e)}), 500

@app.route('/create-subscription-package', methods=['POST', 'OPTIONS'])
def create_subscription_package():
    if request.method == 'OPTIONS':
        print(f"📌 Handling OPTIONS request for /create-subscription-package")
        response = jsonify({"message": "CORS preflight successful"})
        response.headers['Access-Control-Allow-Origin'] = 'http://localhost:3000'
        response.headers['Access-Control-Allow-Credentials'] = 'true'
        response.headers['Access-Control-Allow-Methods'] = 'POST, OPTIONS'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization'
        return response, 200

    try:
        creator_id = session.get('creator_id')
        print(f"📌 Creating package with creator_id from session: {creator_id}")
        if not creator_id:
            app.logger.error("Unauthorized: No creator_id in session")
            return jsonify({"error": "Unauthorized: Must be logged in as a creator"}), 403

        data = request.json
        package_name = data.get('package_name')
        deliverables = data.get('deliverables')
        frequency = data.get('frequency')
        description = data.get('description')
        price = data.get('price')

        required_fields = {'package_name': package_name, 'deliverables': deliverables, 'frequency': frequency, 'description': description, 'price': price}
        missing_fields = [field for field, value in required_fields.items() if not value]
        if missing_fields:
            app.logger.error(f"Missing required fields: {missing_fields}")
            return jsonify({"error": f"Missing required fields: {', '.join(missing_fields)}"}), 400

        if not isinstance(deliverables, list) or not all(isinstance(d, dict) and 'type' in d and 'quantity' in d and 'platform' in d for d in deliverables):
            app.logger.error("Invalid deliverables format")
            return jsonify({"error": "Deliverables must be an array of objects with type, quantity, and platform"}), 400

        valid_frequencies = ['monthly', 'quarterly']
        if frequency not in valid_frequencies:
            app.logger.error(f"Invalid frequency: {frequency}")
            return jsonify({"error": f"Invalid frequency. Must be one of: {', '.join(valid_frequencies)}"}), 400

        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute('''
            INSERT INTO creator_subscription_packages (creator_id, package_name, deliverables, frequency, description, price, status)
            VALUES (%s, %s, %s::jsonb, %s, %s, %s, 'active') RETURNING id
        ''', (creator_id, package_name, json.dumps(deliverables), frequency, description, price))
        package_id = cursor.fetchone()['id']
        conn.commit()
        app.logger.info(f"Subscription package created: ID {package_id} for creator {creator_id}")
        cursor.close()
        conn.close()
        return jsonify({"message": "Subscription package created", "package_id": package_id}), 201
    except Exception as e:
        app.logger.error(f"Error creating subscription package: {str(e)}")
        return jsonify({"error": str(e)}), 500

@app.route('/my-subscription-packages', methods=['GET'])
def get_my_subscription_packages():
    try:
        creator_id = session.get('creator_id')
        if not creator_id:
            app.logger.error("Unauthorized: No creator_id in session")
            return jsonify({"error": "Unauthorized: Must be logged in as a creator"}), 403

        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute('''
            SELECT id, package_name, deliverables, frequency, description, price, created_at, status
            FROM creator_subscription_packages 
            WHERE creator_id = %s
        ''', (creator_id,))
        packages = cursor.fetchall()
        print(f"📌 Fetched my subscription packages for creator_id {creator_id}: {packages}")
        conn.close()
        return jsonify(packages), 200
    except Exception as e:
        app.logger.error(f"Error fetching my subscription packages: {str(e)}")
        return jsonify({"error": str(e)}), 500


@app.route('/subscriptions/<int:package_id>/subscribe', methods=['POST', 'OPTIONS'])
def subscribe_to_package(package_id):
    if request.method == 'OPTIONS':
        response = jsonify({"message": "CORS preflight successful"})
        response.headers['Access-Control-Allow-Origin'] = 'http://localhost:3000'
        response.headers['Access-Control-Allow-Credentials'] = 'true'
        response.headers['Access-Control-Allow-Methods'] = 'POST, OPTIONS'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization'
        return response, 200

    def attempt_paypal_create(payment, retries=3, delay=1):
        for attempt in range(retries):
            try:
                if payment.create():
                    return True
                app.logger.error(f"PayPal creation attempt {attempt + 1} failed: {payment.error}")
            except Exception as e:
                app.logger.error(f"PayPal creation attempt {attempt + 1} exception: {str(e)}")
            if attempt < retries - 1:
                time.sleep(delay)
        return False

    conn = None
    cursor = None
    try:
        brand_id = session.get('brand_id')
        if not brand_id:
            app.logger.error("No brand_id in session")
            return jsonify({"error": "Unauthorized: Must be logged in as a brand"}), 403

        data = request.get_json()
        payment_method = data.get('payment_method', 'stripe').lower()

        if payment_method not in ['stripe', 'paypal']:
            return jsonify({"error": "Payment method must be 'stripe' or 'paypal'"}), 400

        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Get brand details
        cursor.execute("""
            SELECT b.stripe_customer_id, u.email, u.id AS user_id, b.name AS brand_name
            FROM brands b
            JOIN users u ON b.user_id = u.id
            WHERE b.id = %s
        """, (brand_id,))
        brand = cursor.fetchone()
        if not brand:
            return jsonify({"error": "Brand not found"}), 404

        # Get package details
        cursor.execute("""
            SELECT csp.price, csp.creator_id, csp.package_name, c.user_id AS creator_user_id, c.username AS creator_username
            FROM creator_subscription_packages csp
            JOIN creators c ON csp.creator_id = c.id
            WHERE csp.id = %s
        """, (package_id,))
        package = cursor.fetchone()
        if not package:
            return jsonify({"error": "Package not found"}), 404

        monthly_price = package['price']
        start_date = datetime.datetime.now(timezone.utc)
        end_date = start_date + timedelta(days=30)
        duration_months = 1

        if payment_method == 'stripe':
            if not brand['stripe_customer_id'] or brand['stripe_customer_id'].startswith('cus_<'):
                app.logger.info(f"No valid stripe_customer_id for brand {brand_id}, creating new customer")
                customer = stripe.Customer.create(
                    email=brand['email'] or f"brand_{brand_id}@example.com",
                    metadata={'brand_id': brand_id}
                )
                cursor.execute("UPDATE brands SET stripe_customer_id = %s WHERE id = %s", (customer.id, brand_id))
                conn.commit()
            else:
                try:
                    customer = stripe.Customer.retrieve(brand['stripe_customer_id'])
                except stripe.error.InvalidRequestError as e:
                    if 'No such customer' in str(e):
                        app.logger.warning(f"Invalid or deleted customer {brand['stripe_customer_id']} for brand {brand_id}, creating new one")
                        customer = stripe.Customer.create(
                            email=brand['email'] or f"brand_{brand_id}@example.com",
                            metadata={'brand_id': brand_id}
                        )
                        cursor.execute("UPDATE brands SET stripe_customer_id = %s WHERE id = %s", (customer.id, brand_id))
                        conn.commit()
                    else:
                        raise e

            product = stripe.Product.create(
                name=f"Subscription Package {package_id}",
                metadata={'package_id': package_id}
            )
            price = stripe.Price.create(
                unit_amount=int(monthly_price * 100),
                currency='usd',
                recurring={'interval': 'month'},
                product=product.id
            )

            stripe_subscription = stripe.Subscription.create(
                customer=customer.id,
                items=[{'price': price.id}],
                payment_behavior='default_incomplete',
                metadata={'brand_id': brand_id, 'package_id': package_id},
                expand=['latest_invoice.payment_intent']
            )
            payment_intent = stripe_subscription.latest_invoice.payment_intent
            if not payment_intent:
                return jsonify({"error": "Failed to create payment intent"}), 500

            payment_response = {
                "client_secret": payment_intent.client_secret,
                "payment_intent_id": payment_intent.id,
                "subscription_id": stripe_subscription.id
            }
            transaction_id = payment_intent.id
        else:  # paypal
            formatted_price = "{:.2f}".format(float(monthly_price))
            payment = paypalrestsdk.BillingPlan({
                "name": f"Package {package_id} Subscription",
                "description": f"Monthly €{formatted_price} subscription",
                "type": "INFINITE",
                "payment_definitions": [{
                    "name": f"Monthly Package {package_id}",
                    "type": "REGULAR",
                    "frequency": "MONTH",
                    "frequency_interval": "1",
                    "cycles": "0",
                    "amount": {"value": formatted_price, "currency": "EUR"}
                }],
                "merchant_preferences": {
                    "return_url": "http://localhost:3000/payment-success",
                    "cancel_url": "http://localhost:3000/payment-failed",
                    "auto_bill_amount": "YES"
                }
            })
            app.logger.debug(f"Creating PayPal billing plan: {payment.to_dict()}")
            if not attempt_paypal_create(payment):
                app.logger.error(f"PayPal billing plan creation failed after retries: {payment.error}")
                return jsonify({"error": payment.error or "Failed to create billing plan"}), 400

            if not payment.activate():
                app.logger.error(f"PayPal billing plan activation failed: {payment.error}")
                return jsonify({"error": "Failed to activate billing plan"}), 400
            app.logger.debug(f"PayPal billing plan activated: {payment.id}")

            paypal_start_date = (datetime.datetime.now(timezone.utc) + timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M:%SZ')
            agreement = paypalrestsdk.BillingAgreement({
                "name": f"Package {package_id} Subscription",
                "description": f"Monthly €{formatted_price}",
                "start_date": paypal_start_date,
                "plan": {"id": payment.id},
                "payer": {"payment_method": "paypal"}
            })
            app.logger.debug(f"Creating PayPal billing agreement: {agreement.to_dict()}")
            if not attempt_paypal_create(agreement):
                app.logger.error(f"PayPal billing agreement creation failed after retries: {agreement.error}")
                return jsonify({"error": agreement.error or "Failed to create billing agreement"}), 400
            approval_url = next(link.href for link in agreement.links if link.rel == "approval_url")
            token = approval_url.split('token=')[1] if 'token=' in approval_url else None
            payment_response = {
                "agreement_id": agreement.id,
                "approval_url": approval_url,
                "subscription_id": None
            }
            transaction_id = agreement.id
            session['pending_agreement_id'] = agreement.id
            session['pending_subscription_id'] = None
            session['paypal_token'] = token

        # Insert subscription
        cursor.execute('''
            INSERT INTO brand_subscriptions (
                package_id, brand_id, start_date, end_date, duration_months, status, 
                total_cost, transaction_id, payment_method, updated_at
            )
            VALUES (%s, %s, %s, %s, %s, 'pending', %s, %s, %s, NOW())
            RETURNING id
        ''', (
            package_id, brand_id, start_date, end_date, duration_months, monthly_price, 
            transaction_id, payment_method
        ))
        sub_id = cursor.fetchone()['id']
        payment_response['subscription_id'] = sub_id
        session['pending_subscription_id'] = sub_id

        # Insert payment record
        cursor.execute('''
            INSERT INTO subscription_payments (
                subscription_id, amount, payment_intent_id, status, period_start, period_end, created_at
            )
            VALUES (%s, %s, %s, 'held', %s, %s, NOW())
        ''', (
            sub_id, monthly_price, transaction_id, start_date, end_date
        ))

        # Trigger notifications
        notification_data = {
            'subscription_id': sub_id,
            'package_name': package['package_name'],
            'brand_name': brand['brand_name'],
            'creator_username': package['creator_username'],
            'amount': float(monthly_price)
        }

        # Notify brand
        create_notification(
            user_id=brand['user_id'],
            user_role='brand',
            event_type='SUBSCRIPTION_INITIATED',
            data=notification_data,
            should_send_email=True
        )

        # Notify creator
        create_notification(
            user_id=package['creator_user_id'],
            user_role='creator',
            event_type='SUBSCRIPTION_INITIATED',
            data=notification_data,
            should_send_email=True
        )

        conn.commit()
        app.logger.info(f"Subscription {sub_id} initiated for package {package_id}, payment_method: {payment_method}")
        return jsonify({
            "message": "Subscription initiated",
            "subscription_id": sub_id,
            "payment": payment_response
        }), 201

    except Exception as e:
        app.logger.error(f"Error subscribing to package {package_id}: {str(e)}")
        if 'conn' in locals():
            conn.rollback()
        return jsonify({"error": str(e)}), 500

    finally:
        if cursor:
            cursor.close()
        if conn and not conn.closed:
            conn.close()

@app.route('/subscriptions/<int:subscription_id>/approve-deliverables', methods=['POST', 'OPTIONS'])
def approve_subscription_deliverables(subscription_id):
    if request.method == 'OPTIONS':
        response = jsonify({"message": "CORS preflight successful"})
        response.headers['Access-Control-Allow-Origin'] = 'http://localhost:3000'
        response.headers['Access-Control-Allow-Credentials'] = 'true'
        response.headers['Access-Control-Allow-Methods'] = 'POST, OPTIONS'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization'
        return response, 200

    conn = None
    cursor = None
    try:
        brand_id = session.get('brand_id')
        if not brand_id:
            app.logger.error("No brand_id in session")
            return jsonify({"error": "Unauthorized"}), 403

        data = request.get_json()
        deliverable_ids = data.get('deliverable_ids', [])

        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Get subscription and package details
        cursor.execute('''
            SELECT bs.id, bs.status, bs.payment_method, bs.total_cost, csp.package_name,
                   c.user_id AS creator_user_id, c.username AS creator_username,
                   b.user_id AS brand_user_id, b.name AS brand_name
            FROM brand_subscriptions bs
            JOIN creator_subscription_packages csp ON bs.package_id = csp.id
            JOIN creators c ON csp.creator_id = c.id
            JOIN brands b ON bs.brand_id = b.id
            WHERE bs.id = %s AND bs.brand_id = %s
        ''', (subscription_id, brand_id))
        subscription = cursor.fetchone()
        if not subscription:
            return jsonify({"error": "Subscription not found"}), 404

        cursor.execute('''
            SELECT id, payment_intent_id, status, amount
            FROM subscription_payments
            WHERE subscription_id = %s AND status = 'held'
            ORDER BY created_at ASC
            LIMIT 1
        ''', (subscription_id,))
        payment = cursor.fetchone()

        if not payment:
            return jsonify({"error": "No held payment found"}), 404

        if subscription['payment_method'] == 'stripe':
            intent = stripe.PaymentIntent.capture(payment['payment_intent_id'])
            if intent.status != 'succeeded':
                conn.rollback()
                return jsonify({"error": "Failed to capture payment"}), 400
        # PayPal capture would go here if implemented

        cursor.execute('''
            UPDATE subscription_payments
            SET status = 'completed',
                released_at = NOW()
            WHERE id = %s
        ''', (payment['id'],))

        if subscription['status'] == 'pending':
            cursor.execute('''
                UPDATE brand_subscriptions
                SET status = 'active',
                    updated_at = NOW()
                WHERE id = %s
            ''', (subscription_id,))

        # Trigger notifications
        notification_data = {
            'subscription_id': subscription_id,
            'package_name': subscription['package_name'],
            'brand_name': subscription['brand_name'],
            'creator_username': subscription['creator_username'],
            'amount': float(payment['amount'])
        }

        # Notify brand
        create_notification(
            user_id=subscription['brand_user_id'],
            user_role='brand',
            event_type='DELIVERABLES_APPROVED',
            data=notification_data,
            should_send_email=True
        )

        # Notify creator
        create_notification(
            user_id=subscription['creator_user_id'],
            user_role='creator',
            event_type='DELIVERABLES_APPROVED',
            data=notification_data,
            should_send_email=True
        )

        conn.commit()
        app.logger.info(f"Deliverables approved for subscription {subscription_id}, payment {payment['id']} released")
        return jsonify({"message": "Deliverables approved, payment released"}), 200

    except Exception as e:
        app.logger.error(f"Error approving deliverables for subscription {subscription_id}: {str(e)}")
        if 'conn' in locals():
            conn.rollback()
        return jsonify({"error": str(e)}), 500

    finally:
        if cursor:
            cursor.close()
        if conn and not conn.closed:
            conn.close()

@app.route('/subscriptions/<int:subscription_id>/complete-payment', methods=['POST', 'OPTIONS'])
def complete_subscription_payment(subscription_id):
    if request.method == 'OPTIONS':
        response = jsonify({"message": "CORS preflight successful"})
        response.headers['Access-Control-Allow-Origin'] = 'http://localhost:3000'
        response.headers['Access-Control-Allow-Credentials'] = 'true'
        response.headers['Access-Control-Allow-Methods'] = 'POST, OPTIONS'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization'
        return response, 200

    conn = None
    cursor = None
    try:
        brand_id = session.get('brand_id')
        if not brand_id:
            app.logger.error("No brand_id in session")
            return jsonify({"error": "Unauthorized"}), 403

        data = request.get_json()
        payment_intent_id = data.get('payment_intent_id')
        paypal_payment_id = data.get('paypal_payment_id')
        payer_id = data.get('payer_id')

        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Get subscription and package details
        cursor.execute('''
            SELECT bs.id, bs.brand_id, bs.total_cost, bs.status, bs.transaction_id, bs.payment_method,
                   csp.package_name, c.user_id AS creator_user_id, c.username AS creator_username,
                   b.user_id AS brand_user_id, b.name AS brand_name
            FROM brand_subscriptions bs
            JOIN creator_subscription_packages csp ON bs.package_id = csp.id
            JOIN creators c ON csp.creator_id = c.id
            JOIN brands b ON bs.brand_id = b.id
            WHERE bs.id = %s AND bs.brand_id = %s
        ''', (subscription_id, brand_id))
        subscription = cursor.fetchone()

        if not subscription:
            conn.rollback()
            return jsonify({"error": "Subscription not found"}), 404

        if subscription['status'] == 'active':
            conn.rollback()
            return jsonify({"message": "Subscription already active"}), 200
        if subscription['status'] != 'pending':
            conn.rollback()
            return jsonify({"error": f"Subscription not pending, current status: {subscription['status']}"}), 400

        transaction_id = None
        if payment_intent_id:
            intent = stripe.PaymentIntent.retrieve(payment_intent_id)
            if intent.status != 'succeeded':
                conn.rollback()
                return jsonify({"error": "Stripe payment not successful"}), 400
            if intent.amount != int(subscription['total_cost'] * 100):
                conn.rollback()
                return jsonify({"error": "Payment amount mismatch"}), 400
            transaction_id = payment_intent_id
        elif paypal_payment_id and payer_id:
            payment = paypalrestsdk.Payment.find(paypal_payment_id)
            if payment.state == "approved":
                transaction_id = paypal_payment_id
            elif payment.execute({"payer_id": payer_id}):
                transaction_id = paypal_payment_id
            else:
                conn.rollback()
                return jsonify({"error": f"PayPal execution failed: {payment.error.get('message')}"}), 400
        else:
            conn.rollback()
            return jsonify({"error": "Payment details required"}), 400

        cursor.execute('''
            UPDATE subscription_payments
            SET payment_intent_id = %s,
                status = 'completed'
            WHERE subscription_id = %s AND status = 'held'
        ''', (transaction_id, subscription_id))

        cursor.execute('''
            UPDATE brand_subscriptions
            SET status = 'active',
                transaction_id = %s,
                updated_at = NOW()
            WHERE id = %s
        ''', (transaction_id, subscription_id))

        # Trigger notifications
        notification_data = {
            'subscription_id': subscription_id,
            'package_name': subscription['package_name'],
            'brand_name': subscription['brand_name'],
            'creator_username': subscription['creator_username'],
            'amount': float(subscription['total_cost'])
        }

        # Notify brand
        create_notification(
            user_id=subscription['brand_user_id'],
            user_role='brand',
            event_type='PAYMENT_COMPLETED',
            data=notification_data,
            should_send_email=True
        )

        # Notify creator
        create_notification(
            user_id=subscription['creator_user_id'],
            user_role='creator',
            event_type='PAYMENT_COMPLETED',
            data=notification_data,
            should_send_email=True
        )

        conn.commit()
        app.logger.info(f"Subscription {subscription_id} activated with transaction_id={transaction_id}")
        return jsonify({"message": "Payment completed and subscription activated", "subscription_id": subscription_id}), 200

    except Exception as e:
        app.logger.error(f"Error completing subscription payment for subscription {subscription_id}: {str(e)}")
        if 'conn' in locals():
            conn.rollback()
        return jsonify({"error": str(e)}), 500

    finally:
        if cursor:
            cursor.close()
        if conn and not conn.closed:
            conn.close()

@app.route('/payment-success', methods=['GET'])
def payment_success():
    try:
        agreement_id = session.get('pending_agreement_id')
        subscription_id = session.get('pending_subscription_id')
        brand_id = session.get('brand_id')
        payment_id = request.args.get('paymentId')
        payer_id = request.args.get('PayerID')
        token = request.args.get('token')

        app.logger.debug(f"Payment success params: agreement_id={agreement_id}, subscription_id={subscription_id}, brand_id={brand_id}, payment_id={payment_id}, payer_id={payer_id}, token={token}")

        if not all([agreement_id, subscription_id, brand_id, payer_id]):
            app.logger.error(f"Missing required parameters")
            return jsonify({"error": "Missing payment parameters"}), 400

        # Execute the billing agreement
        agreement = paypalrestsdk.BillingAgreement.find(agreement_id)
        app.logger.debug(f"Executing PayPal billing agreement: {agreement_id} with payer_id: {payer_id}")
        execution_result = agreement.execute({"payer_id": payer_id})
        if not execution_result:
            app.logger.error(f"PayPal billing agreement execution failed: {agreement.error}")
            return jsonify({"error": agreement.error or "Failed to execute agreement"}), 400

        app.logger.debug(f"PayPal execution response: {execution_result}")

        # Update subscription
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute('''
            UPDATE brand_subscriptions
            SET status = 'active',
                transaction_id = %s,
                updated_at = NOW()
            WHERE id = %s AND brand_id = %s
        ''', (agreement_id, subscription_id, brand_id))

        cursor.execute('''
            UPDATE subscription_payments
            SET status = 'completed',
                payment_intent_id = %s,
                updated_at = NOW()
            WHERE subscription_id = %s AND status = 'held'
        ''', (agreement_id, subscription_id))

        if cursor.rowcount == 0:
            app.logger.warning(f"No payment record updated for subscription {subscription_id}")

        conn.commit()
        cursor.close()
        conn.close()

        # Clear session
        session.pop('pending_agreement_id', None)
        session.pop('pending_subscription_id', None)
        session.pop('paypal_token', None)

        app.logger.info(f"PayPal billing agreement {agreement_id} executed for subscription {subscription_id}")
        return jsonify({"message": "Payment completed successfully", "subscription_id": subscription_id}), 200
    except Exception as e:
        app.logger.error(f"Error in payment success: {str(e)}")
        if 'conn' in locals():
            conn.close()
        return jsonify({"error": str(e)}), 500

@app.route('/payment-failed', methods=['GET'])
def payment_failed():
    try:
        agreement_id = session.pop('pending_agreement_id', None)
        subscription_id = session.pop('pending_subscription_id', None)
        app.logger.warning(f"Payment failed for agreement_id={agreement_id}, subscription_id={subscription_id}")
        return jsonify({"error": "Payment was cancelled or failed"}), 400
    except Exception as e:
        app.logger.error(f"Error in payment failed: {str(e)}")
        return jsonify({"error": str(e)}), 500

@app.route('/subscriptions/<int:subscription_id>/confirm-content', methods=['POST', 'OPTIONS'])
def confirm_subscription_content(subscription_id):
    if request.method == 'OPTIONS':
        response = jsonify({"message": "CORS preflight successful"})
        response.headers['Access-Control-Allow-Origin'] = 'http://localhost:3000'
        response.headers['Access-Control-Allow-Credentials'] = 'true'
        response.headers['Access-Control-Allow-Methods'] = 'POST, OPTIONS'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization'
        return response, 200

    try:
        brand_id = session.get('brand_id')
        if not brand_id:
            return jsonify({"error": "Unauthorized"}), 403

        data = request.get_json()
        deliverable_ids = data.get('deliverable_ids', [])

        with get_db_connection() as conn:
            conn.autocommit = False
            cursor = conn.cursor(cursor_factory=RealDictCursor)

            cursor.execute('''
                SELECT id, status
                FROM brand_subscriptions WHERE id = %s AND brand_id = %s
            ''', (subscription_id, brand_id))
            subscription = cursor.fetchone()
            if not subscription:
                conn.rollback()
                return jsonify({"error": "Subscription not found"}), 404

            if subscription['status'] != 'active':
                conn.rollback()
                return jsonify({"error": "Subscription must be active to confirm content"}), 400

            # Update deliverables to Delivered
            if deliverable_ids:
                cursor.execute('''
                    UPDATE subscription_deliverables
                    SET status = 'Delivered',
                        delivered_at = NOW(),
                        updated_at = NOW()
                    WHERE subscription_id = %s AND id = ANY(%s)
                ''', (subscription_id, deliverable_ids))

            # Check if all deliverables are delivered
            cursor.execute('''
                SELECT COUNT(*) AS total, SUM(CASE WHEN status = 'Delivered' THEN 1 ELSE 0 END) AS delivered
                FROM subscription_deliverables
                WHERE subscription_id = %s
            ''', (subscription_id,))
            deliverable_status = cursor.fetchone()
            if deliverable_status['total'] == deliverable_status['delivered']:
                cursor.execute('''
                    UPDATE brand_subscriptions
                    SET updated_at = NOW()
                    WHERE id = %s
                ''', (subscription_id,))

            conn.commit()
            app.logger.info(f"Content confirmed for subscription {subscription_id}, deliverables: {deliverable_ids}")
            return jsonify({"message": "Content confirmed successfully"}), 200
    except Exception as e:
        app.logger.error(f"Error confirming subscription content: {str(e)}")
        conn.rollback()
        return jsonify({"error": str(e)}), 500

@app.route('/webhook/stripe', methods=['POST'])
def stripe_webhook():
    payload = request.get_data(as_text=True)
    sig_header = request.headers.get('Stripe-Signature')

    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, '<your_webhook_secret>'
        )
    except ValueError as e:
        return jsonify({"error": "Invalid payload"}), 400
    except stripe.error.SignatureVerificationError as e:
        return jsonify({"error": "Invalid signature"}), 400

    if event['type'] == 'invoice.payment_succeeded':
        invoice = event['data']['object']
        subscription_id = invoice['subscription']
        amount = invoice['amount_paid'] / 100.0
        payment_intent_id = invoice['payment_intent']

        app.logger.info(f"Received invoice.payment_succeeded for Stripe subscription {subscription_id}, payment_intent {payment_intent_id}")

        with get_db_connection() as conn:
            conn.autocommit = False
            cursor = conn.cursor(cursor_factory=RealDictCursor)

            # Find subscription by transaction_id (payment_intent_id)
            cursor.execute('''
                SELECT id, end_date
                FROM brand_subscriptions WHERE transaction_id = %s AND payment_method = 'stripe'
            ''', (payment_intent_id,))
            subscription = cursor.fetchone()
            if not subscription:
                app.logger.warning(f"No subscription found for payment_intent {payment_intent_id}")
                conn.rollback()
                return jsonify({"error": "Subscription not found"}), 404

            period_start = datetime.now()
            period_end = period_start + timedelta(days=30)
            cursor.execute('''
                INSERT INTO subscription_payments (
                    subscription_id, amount, payment_intent_id, status, period_start, period_end, created_at
                )
                VALUES (%s, %s, %s, 'held', %s, %s, NOW())
            ''', (subscription['id'], amount, payment_intent_id, period_start, period_end))

            cursor.execute('''
                UPDATE brand_subscriptions
                SET end_date = %s,
                    updated_at = NOW()
                WHERE id = %s
            ''', (period_end, subscription['id']))

            conn.commit()

    return jsonify({"status": "success"}), 200

@app.route('/brand/subscriptions', methods=['GET'])
def get_brand_subscriptions():
    try:
        brand_id = session.get('brand_id')
        if not brand_id:
            return jsonify({"error": "Unauthorized"}), 403

        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        cursor.execute('''
            SELECT bs.id, bs.package_id, bs.start_date, bs.end_date, bs.status, bs.total_cost, bs.duration_months,
                   csp.package_name, csp.creator_id, c.username AS creator_name,
                   (SELECT MAX(updated_at) FROM subscription_deliverables sd WHERE sd.subscription_id = bs.id) AS updated_at
            FROM brand_subscriptions bs
            JOIN creator_subscription_packages csp ON bs.package_id = csp.id
            JOIN creators c ON csp.creator_id = c.id
            WHERE bs.brand_id = %s
        ''', (brand_id,))
        subscriptions = cursor.fetchall()

        conn.close()
        return jsonify(subscriptions), 200
    except Exception as e:
        app.logger.error(f"Error fetching brand subscriptions: {str(e)}")
        return jsonify({"error": str(e)}), 500

@app.route('/subscriptions/<int:subscription_id>/cancel', methods=['PUT'])
def cancel_subscription(subscription_id):
    try:
        brand_id = session.get('brand_id')
        if not brand_id:
            return jsonify({"error": "Unauthorized"}), 403

        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute('''
            UPDATE brand_subscriptions 
            SET status = 'canceled', end_date = NOW()
            WHERE id = %s AND brand_id = %s AND status = 'active'
            RETURNING id
        ''', (subscription_id, brand_id))
        result = cursor.fetchone()
        conn.commit()
        conn.close()
        if not result:
            return jsonify({"error": "Subscription not found or already canceled"}), 404
        return jsonify({"message": "Subscription canceled"}), 200
    except Exception as e:
        app.logger.error(f"Error canceling subscription: {str(e)}")
        return jsonify({"error": str(e)}), 500


@app.route('/messages/subscription/<int:subscription_id>', methods=['GET'])
def get_subscription_messages(subscription_id):
    try:
        brand_id = session.get('brand_id')
        creator_id = session.get('creator_id')
        
        if not brand_id and not creator_id:
            return jsonify({"error": "Unauthorized: No valid session"}), 403

        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        
        # Fetch subscription details
        cursor.execute('''
            SELECT bs.brand_id, csp.creator_id
            FROM brand_subscriptions bs
            JOIN creator_subscription_packages csp ON bs.package_id = csp.id
            WHERE bs.id = %s AND bs.status = 'active'
        ''', (subscription_id,))
        subscription = cursor.fetchone()
        
        if not subscription:
            return jsonify({"error": "Subscription not found"}), 404

        # Authorization check
        if (brand_id and subscription['brand_id'] != brand_id) or (creator_id and subscription['creator_id'] != creator_id):
            return jsonify({"error": "Unauthorized: You do not have access to this subscription"}), 403

        # Fetch messages
        cursor.execute('''
            SELECT id, sender_type, message, created_at
            FROM messages 
            WHERE subscription_id = %s 
            ORDER BY created_at ASC
        ''', (subscription_id,))
        messages = cursor.fetchall()
        
        conn.close()
        return jsonify(messages), 200
    except Exception as e:
        app.logger.error(f"Error fetching subscription messages: {str(e)}")
        return jsonify({"error": str(e)}), 500

@app.route('/messages/subscription/<int:subscription_id>', methods=['POST'])
def send_subscription_message(subscription_id):
    try:
        brand_id = session.get('brand_id')
        creator_id = session.get('creator_id')
        
        if not brand_id and not creator_id:
            return jsonify({"error": "Unauthorized: No valid session"}), 403

        data = request.get_json()
        message_text = data.get('message')
        sender_type = data.get('sender_type')

        if not message_text or not sender_type:
            return jsonify({"error": "Message and sender_type are required"}), 400

        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        
        cursor.execute(
            '''
            SELECT bs.brand_id, csp.creator_id
            FROM brand_subscriptions bs
            JOIN creator_subscription_packages csp ON bs.package_id = csp.id
            WHERE bs.id = %s AND bs.status = 'active'
            ''',
            (subscription_id,)
        )
        subscription = cursor.fetchone()
        
        if not subscription:
            return jsonify({"error": "Subscription not found"}), 404

        if (brand_id and subscription['brand_id'] != brand_id) or (creator_id and subscription['creator_id'] != creator_id):
            return jsonify({"error": "Unauthorized: You do not have access to this subscription"}), 403

        cursor.execute(
            '''
            INSERT INTO messages (subscription_id, sender_type, message, created_at)
            VALUES (%s, %s, %s, NOW())
            RETURNING id, sender_type, message, created_at
            ''',
            (subscription_id, sender_type, message_text)
        )
        new_message = cursor.fetchone()
        
        # Notify recipient via email (no badge notification for NEW_MESSAGE)
        recipient_id = subscription['brand_id'] if sender_type == 'creator' else subscription['creator_id']
        recipient_role = 'brand' if sender_type == 'creator' else 'creator'
        cursor.execute('SELECT username FROM creators WHERE id = %s', (subscription['creator_id'],))
        creator = cursor.fetchone()
        cursor.execute('SELECT name FROM brands WHERE id = %s', (subscription['brand_id'],))
        brand = cursor.fetchone()

        sender_name = creator['username'] if sender_type == 'creator' else brand['name']
        if recipient_role == 'creator':
            cursor.execute('SELECT user_id FROM creators WHERE id = %s', (recipient_id,))
        else:
            cursor.execute('SELECT user_id FROM brands WHERE id = %s', (recipient_id,))
        recipient = cursor.fetchone()
        if recipient:
            create_notification(
                user_id=recipient['user_id'],
                user_role=recipient_role,
                event_type='NEW_MESSAGE',
                message=f"New message from {sender_name}: {message_text}",
                data={'subscription_id': subscription_id, 'message_id': new_message['id']},
                should_send_email=True
            )

        conn.commit()
        conn.close()
        return jsonify(new_message), 201
    except Exception as e:
        app.logger.error(f"Error sending subscription message: {str(e)}")
        return jsonify({"error": str(e)}), 500

@app.route('/submit-content/subscription/<int:subscription_id>', methods=['POST'])
def submit_subscription_content(subscription_id):
    try:
        creator_id = session.get('creator_id')
        if not creator_id:
            return jsonify({"error": "Unauthorized"}), 403

        deliverable_ids = request.form.get('deliverable_ids')  # JSON string of indices
        content_link = request.form.get('content_link')
        submission_notes = request.form.get('submission_notes', '')
        file = request.files.get('file')

        if not deliverable_ids:
            return jsonify({"error": "No deliverables selected"}), 400

        deliverable_indices = json.loads(deliverable_ids)
        if not isinstance(deliverable_indices, list) or not deliverable_indices:
            return jsonify({"error": "Invalid deliverable IDs"}), 400

        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Verify subscription and get base deliverables
        cursor.execute('''
            SELECT bs.id, csp.deliverables
            FROM brand_subscriptions bs
            JOIN creator_subscription_packages csp ON bs.package_id = csp.id
            WHERE bs.id = %s AND csp.creator_id = %s AND bs.status IN ('pending', 'active')
        ''', (subscription_id, creator_id))
        subscription = cursor.fetchone()
        if not subscription:
            conn.close()
            app.logger.warning(f"Subscription {subscription_id} not found or unauthorized for creator {creator_id}")
            return jsonify({"error": "Subscription not found or unauthorized"}), 404

        base_deliverables = subscription['deliverables']
        if not isinstance(base_deliverables, list):
            conn.close()
            return jsonify({"error": "Invalid deliverables data"}), 500

        # Validate indices
        valid_indices = set(range(len(base_deliverables)))
        if not all(isinstance(i, int) and i in valid_indices for i in deliverable_indices):
            conn.close()
            return jsonify({"error": "Invalid deliverable indices"}), 400

        file_url = None
        if file and allowed_file(file.filename):
            file_url = upload_file_to_supabase(file, "creators")

        # Process each selected deliverable
        for index in deliverable_indices:
            deliverable = base_deliverables[index]
            # Count existing submissions for this deliverable
            cursor.execute('''
                SELECT COALESCE(MAX(submission_index), -1) + 1 AS next_index
                FROM subscription_deliverables
                WHERE subscription_id = %s AND type = %s AND platform = %s
            ''', (subscription_id, deliverable['type'], deliverable['platform']))
            next_index = cursor.fetchone()['next_index']

            # Check if quantity limit is reached
            if next_index >= deliverable['quantity']:
                continue  # Skip if all units are submitted

            # Insert new submission
            cursor.execute('''
                INSERT INTO subscription_deliverables (
                    subscription_id, creator_id, type, platform, quantity, status, 
                    content_link, file_url, submission_notes, submitted_at, updated_at, submission_index
                )
                VALUES (%s, %s, %s, %s, %s, 'Submitted', %s, %s, %s, NOW(), NOW(), %s)
            ''', (
                subscription_id, creator_id, deliverable['type'], deliverable['platform'],
                deliverable['quantity'], content_link, file_url, submission_notes, next_index
            ))

        conn.commit()
        conn.close()
        app.logger.info(f"Content submitted for subscription {subscription_id} by creator {creator_id}")
        return jsonify({"message": "Deliverables submitted successfully"}), 200
    except Exception as e:
        app.logger.error(f"Error submitting subscription content: {str(e)}")
        if 'conn' in locals():
            conn.close()
        return jsonify({"error": str(e)}), 500


@app.route('/subscriptions/<int:subscription_id>/deliverables', methods=['GET'])
def get_subscription_deliverables(subscription_id):
    try:
        user_role = session.get('user_role')
        creator_id = session.get('creator_id')
        brand_id = session.get('brand_id')

        app.logger.debug(f"User role: {user_role}, Creator ID: {creator_id}, Brand ID: {brand_id}")

        if not user_role or (user_role == 'creator' and not creator_id) or (user_role == 'brand' and not brand_id):
            return jsonify({"error": "Unauthorized"}), 403

        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Fetch base deliverables from creator_subscription_packages
        cursor.execute('''
            SELECT csp.deliverables
            FROM brand_subscriptions bs
            JOIN creator_subscription_packages csp ON bs.package_id = csp.id
            WHERE bs.id = %s AND (csp.creator_id = %s OR bs.brand_id = %s)
        ''', (subscription_id, creator_id or 0, brand_id or 0))
        result = cursor.fetchone()
        if not result or not result['deliverables']:
            app.logger.debug(f"No deliverables found for subscription {subscription_id}")
            conn.close()
            return jsonify([]), 200

        base_deliverables = result['deliverables']
        app.logger.debug(f"Base deliverables: {base_deliverables}")

        # Fetch submitted deliverables based on user role
        if user_role == 'creator':
            cursor.execute('''
                SELECT type, platform, quantity, status, content_link, file_url, 
                       submission_notes, revision_notes, delivered_at, submission_index
                FROM subscription_deliverables
                WHERE subscription_id = %s AND creator_id = %s
                ORDER BY submission_index
            ''', (subscription_id, creator_id))
        else:  # user_role == 'brand'
            cursor.execute('''
                SELECT sd.type, sd.platform, sd.quantity, sd.status, sd.content_link, sd.file_url, 
                       sd.submission_notes, sd.revision_notes, sd.delivered_at, sd.submission_index
                FROM subscription_deliverables sd
                JOIN brand_subscriptions bs ON sd.subscription_id = bs.id
                WHERE sd.subscription_id = %s AND bs.brand_id = %s
                ORDER BY sd.submission_index
            ''', (subscription_id, brand_id))

        submitted = cursor.fetchall()
        app.logger.debug(f"Submitted deliverables: {submitted}")

        # Aggregate submissions by type and platform
        deliverable_status = {}
        for d in submitted:
            key = (d['type'], d['platform'])
            if key not in deliverable_status:
                deliverable_status[key] = []
            deliverable_status[key].append(d)

        # Build response with remaining quantities
        deliverables = []
        for i, base in enumerate(base_deliverables):
            key = (base['type'], base['platform'])
            submitted_list = deliverable_status.get(key, [])
            total_submitted = len(submitted_list)
            remaining = base['quantity'] - total_submitted
            status = "Delivered" if remaining <= 0 else ("Submitted" if total_submitted > 0 else "Pending")
            deliverables.append({
                "index": i,
                "type": base['type'],
                "platform": base['platform'],
                "quantity": base['quantity'],
                "submitted": total_submitted,
                "remaining": max(0, remaining),
                "status": status,
                "submissions": submitted_list  # Include all submission details
            })

        conn.close()
        return jsonify(deliverables), 200
    except Exception as e:
        app.logger.error(f"Error fetching subscription deliverables: {str(e)}")
        return jsonify({"error": str(e)}), 500

@app.route('/subscriptions/<int:subscription_id>/status', methods=['GET'])
def get_subscription_status(subscription_id):
    try:
        brand_id = session.get('brand_id')
        if not brand_id:
            return jsonify({"error": "Unauthorized"}), 403

        with get_db_connection() as conn:
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            cursor.execute('''
                SELECT transaction_id
                FROM brand_subscriptions
                WHERE id = %s AND brand_id = %s
            ''', (subscription_id, brand_id))
            subscription = cursor.fetchone()
            if not subscription:
                return jsonify({"error": "Subscription not found"}), 404
            return jsonify({"transaction_id": subscription['transaction_id']}), 200
    except Exception as e:
        app.logger.error(f"Error fetching subscription status: {str(e)}")
        return jsonify({"error": str(e)}), 500

@app.route('/creators/<int:creator_id>/subscriptions', methods=['GET'])
def get_creator_subscriptions(creator_id):
    try:
        creator_id_session = session.get('creator_id')
        if not creator_id_session or creator_id_session != creator_id:
            return jsonify({"error": "Unauthorized"}), 403

        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute('''
            SELECT bs.id, bs.package_id, bs.brand_id, b.name AS brand_name, 
                   csp.package_name, bs.duration_months, bs.start_date, bs.end_date, 
                   bs.status, bs.total_cost,
                   COALESCE((
                       SELECT COUNT(*) 
                       FROM messages m 
                       WHERE m.subscription_id = bs.id 
                       AND m.sender_type = 'brand' 
                       AND m.created_at > (
                           SELECT COALESCE(MAX(m2.created_at), '1970-01-01')
                           FROM messages m2 
                           WHERE m2.subscription_id = bs.id 
                           AND m2.sender_type = 'creator'
                       )
                   ), 0) AS unread_count
            FROM brand_subscriptions bs
            JOIN creator_subscription_packages csp ON bs.package_id = csp.id
            JOIN brands b ON bs.brand_id = b.id
            WHERE csp.creator_id = %s AND bs.status = 'active'
            ORDER BY bs.start_date DESC
        ''', (creator_id,))
        subscriptions = cursor.fetchall()
        conn.close()
        return jsonify(subscriptions), 200
    except Exception as e:
        app.logger.error(f"Error fetching creator subscriptions: {str(e)}")
        return jsonify({"error": str(e)}), 500


@app.route('/brands/me/stats', methods=['GET'])
def get_brand_stats():
    brand_id = session.get('brand_id')
    if not brand_id:
        return jsonify({"error": "Unauthorized"}), 403
    
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Active Campaigns: Count of campaigns with status 'active'
        cursor.execute('''
            SELECT COUNT(*) AS active_campaigns
            FROM campaigns
            WHERE brand_id = %s AND status = 'active'
        ''', (brand_id,))
        active_campaigns = cursor.fetchone()['active_campaigns']

        # Creator Applications: Total count of applications for the brand’s campaigns
        cursor.execute('''
            SELECT COUNT(*) AS creator_applications
            FROM campaign_applications
            WHERE brand_id = %s
        ''', (brand_id,))
        creator_applications = cursor.fetchone()['creator_applications']

        # Total Spend: Sum of campaign budgets and subscription total_cost
        cursor.execute('''
            SELECT 
                COALESCE(SUM(budget), 0) AS campaign_spend
            FROM campaigns
            WHERE brand_id = %s
        ''', (brand_id,))
        campaign_spend = cursor.fetchone()['campaign_spend']

        cursor.execute('''
            SELECT 
                COALESCE(SUM(total_cost), 0) AS subscription_spend
            FROM brand_subscriptions
            WHERE brand_id = %s
        ''', (brand_id,))
        subscription_spend = cursor.fetchone()['subscription_spend']

        total_spend = (campaign_spend or 0) + (subscription_spend or 0)

        conn.close()

        stats = {
            "active_campaigns": active_campaigns,
            "creator_applications": creator_applications,
            "total_spend": float(total_spend) if total_spend else 0.0
        }
        return jsonify(stats), 200
    except Exception as e:
        app.logger.error(f"Error fetching brand stats: {str(e)}")
        return jsonify({"error": str(e)}), 500
    finally:
        if 'conn' in locals():
            conn.close()

@app.route('/sponsor-draft', methods=['POST'])
def submit_sponsor_draft():
    try:
        # Log request details
        app.logger.info(f"📌 Attempting to create sponsor draft")
        app.logger.info(f"📌 Session Data: {dict(session)}")
        app.logger.info(f"📌 Form Data: {request.form.to_dict()}")
        app.logger.info(f"📌 Files Received: {request.files}")

        # Validate session
        creator_id = session.get('creator_id')
        if not creator_id:
            app.logger.error("Unauthorized: No creator_id in session")
            return jsonify({"error": "Unauthorized"}), 403

        # Extract form data
        description = request.form.get('description')
        platforms = request.form.get('platforms')
        min_bid = request.form.get('min_bid')
        audience_targets = request.form.get('audience_targets')
        content_format = request.form.get('content_format')
        topics = request.form.get('topics')
        gifting_invite_required = request.form.get('gifting_invite_required')
        projected_views = request.form.get('projected_views')
        bidding_deadline = request.form.get('bidding_deadline')
        snippet = request.files.get('snippet')

        # Validate required fields
        required_fields = {
            'description': description,
            'platforms': platforms,
            'audience_targets': audience_targets,
            'content_format': content_format,
            'topics': topics,
            'projected_views': projected_views,
            'bidding_deadline': bidding_deadline
        }
        missing_fields = [field for field, value in required_fields.items() if not value]
        if missing_fields:
            app.logger.error(f"Missing required fields: {missing_fields}")
            return jsonify({"error": f"Description, platforms, audience targets, content format, topics, projected views, and bidding deadline are required"}), 400

        # Validate min_bid as a number (if provided)
        if min_bid:
            try:
                min_bid = float(min_bid)
            except ValueError:
                app.logger.error(f"Invalid min_bid value: {min_bid}")
                return jsonify({"error": "Min bid must be a valid number"}), 400

        # Validate projected_views as a non-empty string
        if not isinstance(projected_views, str) or not projected_views.strip():
            app.logger.error(f"Invalid projected_views: {projected_views}")
            return jsonify({"error": "Projected views must be a non-empty string (e.g., '10K-50K' or '10000')"}), 400

        # Log bidding_deadline for debugging
        app.logger.info(f"Received bidding_deadline: {bidding_deadline}")

        # Handle snippet upload
        snippet_url = None
        if snippet:
            if not allowed_file(snippet.filename):
                app.logger.error(f"Invalid file type for snippet: {snippet.filename}")
                return jsonify({"error": "Invalid file type. Only video files are allowed (e.g., mp4, mov, webm, avi)"}), 400

            if snippet.content_length and snippet.content_length > 5 * 1024 * 1024:
                app.logger.error(f"Snippet file too large: {snippet.content_length} bytes")
                return jsonify({"error": "Snippet must be under 5MB"}), 400

            app.logger.info(f"Uploading snippet: {snippet.filename}")
            snippet_url = upload_file_to_supabase(snippet, "sponsor-snippets")
            if not snippet_url:
                app.logger.error("Failed to upload snippet to Supabase")
                return jsonify({"error": "Failed to upload snippet"}), 500
            app.logger.info(f"Snippet uploaded successfully: {snippet_url}")

        # Connect to database
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute(
            '''
            INSERT INTO sponsor_drafts (
                creator_id, description, platforms, min_bid, snippet_url, audience_target,
                content_format, topics, gifting_invite_required, projected_views, status, bidding_deadline
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'Approved', %s)
            RETURNING id
            ''',
            (
                creator_id, description, platforms, min_bid, snippet_url, audience_targets,
                content_format, topics, gifting_invite_required, projected_views, bidding_deadline
            )
        )
        draft = cursor.fetchone()
        if not draft:
            app.logger.error("Failed to insert sponsor draft")
            return jsonify({"error": "Failed to create sponsor draft"}), 500

        draft_id = draft['id']
        conn.commit()

        app.logger.info(f"Draft submitted successfully: ID {draft_id}")
        return jsonify({"message": "Draft submitted successfully", "draft_id": draft_id}), 201

    except Exception as e:
        app.logger.error(f"Error submitting sponsor draft: {str(e)}")
        if 'conn' in locals() and not conn.closed:
            conn.rollback()
        if 'cursor' in locals():
            cursor.close()
        if 'conn' in locals() and not conn.closed:
            conn.close()
        return jsonify({"error": str(e)}), 500

@app.route('/sponsor-drafts/<int:draft_id>/bid', methods=['POST'])
def submit_sponsor_bid(draft_id):
    try:
        brand_id = session.get('brand_id')
        if not brand_id:
            app.logger.error("Unauthorized: No brand_id in session")
            return jsonify({"error": "Unauthorized"}), 403

        data = request.get_json()
        app.logger.info(f"Received bid payload: {data}")
        creator_id = data.get('creator_id')
        bid_amount = data.get('bid_amount')
        pitch = data.get('pitch')

        # Validate required fields
        if not creator_id:
            app.logger.error(f"Missing creator_id in payload: {data}")
            return jsonify({"error": "Creator ID is required"}), 400
        if not bid_amount or not isinstance(bid_amount, (int, float)) or bid_amount <= 0:
            app.logger.error(f"Invalid or missing bid_amount: {bid_amount}")
            return jsonify({"error": "Bid amount is required and must be a positive number"}), 400
        try:
            creator_id = int(creator_id)
        except (ValueError, TypeError):
            app.logger.error(f"Invalid creator_id: {creator_id}")
            return jsonify({"error": "Creator ID must be a valid integer"}), 400

        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Check if the draft exists, is approved, and matches creator_id
        cursor.execute(
            '''
            SELECT id, creator_id, min_bid 
            FROM sponsor_drafts 
            WHERE id = %s AND LOWER(status) = LOWER('approved') AND creator_id = %s
            ''',
            (draft_id, creator_id)
        )
        draft = cursor.fetchone()

        if not draft:
            conn.close()
            app.logger.error(f"Draft not found or not approved: draft_id={draft_id}, creator_id={creator_id}")
            return jsonify({"error": "Draft not found, not approved, or creator ID mismatch"}), 404

        if bid_amount < draft['min_bid']:
            conn.close()
            app.logger.error(f"Bid amount {bid_amount} is below minimum bid {draft['min_bid']} for draft_id={draft_id}")
            return jsonify({"error": f"Bid amount must be at least €{draft['min_bid']}"}), 400

        # Check for existing active bids
        cursor.execute(
            '''
            SELECT id 
            FROM sponsor_bids 
            WHERE draft_id = %s AND brand_id = %s AND status IN ('Pending', 'Accepted')
            ''',
            (draft_id, brand_id)
        )
        existing_bid = cursor.fetchone()
        if existing_bid:
            conn.close()
            app.logger.info(f"Duplicate bid detected: draft_id={draft_id}, brand_id={brand_id}, existing_bid_id={existing_bid['id']}")
            return jsonify({"error": "You already submitted a bid for this draft"}), 400

        # Insert the new bid 
        cursor.execute(
            '''
            INSERT INTO sponsor_bids (draft_id, brand_id, bid_amount, pitch, status, created_at)
            VALUES (%s, %s, %s, %s, 'Pending', NOW())
            RETURNING id
            ''',
            (draft_id, brand_id, bid_amount, pitch)
        )
        bid = cursor.fetchone()
        bid_id = bid['id']

        # Notify creator
        cursor.execute('SELECT user_id, username FROM creators WHERE id = %s', (creator_id,))
        creator = cursor.fetchone()
        if creator:
            cursor.execute('SELECT name FROM brands WHERE id = %s', (brand_id,))
            brand = cursor.fetchone()
            brand_name = brand['name'] if brand else f"Brand ID {brand_id}"
            create_notification(
                user_id=creator['user_id'],
                user_role='creator',
                event_type='Bid Submitted',
                data={
                    'draft_id': draft_id,
                    'bid_id': bid_id,
                    'brand_name': brand_name,
                    'bid_amount': bid_amount,
                    'pitch': pitch,
                    'creator_username': creator['username']
                },
                should_send_email=True
            )

        conn.commit()
        conn.close()
        app.logger.info(f"Bid placed successfully: bid_id={bid_id}")
        return jsonify({"message": "Bid submitted successfully", "bid_id": bid_id}), 200
    except Exception as e:
        app.logger.error(f"Error submitting sponsor bid: {str(e)}")
        if 'conn' in locals():
            conn.close()
        return jsonify({"error": str(e)}), 500

@app.route('/my-sponsor-drafts', methods=['GET'])
def get_my_sponsor_drafts():
    try:
        creator_id = session.get('creator_id')
        if not creator_id:
            return jsonify({"error": "Unauthorized"}), 403

        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute('''
            SELECT 
                sd.id, 
                sd.description, 
                sd.platforms, 
                sd.min_bid, 
                sd.snippet_url,
                sd.audience_target AS audience_targets, 
                sd.content_format, 
                sd.topics,
                sd.gifting_invite_required, 
                sd.projected_views, 
                sd.status,
                sd.posting_date,
                sd.bidding_deadline,
                c.id AS creator_id,
                c.username,
                c.followers_count,
                c.engagement_rate,
                c.regions,
                c.image_profile,
                COALESCE((
                    SELECT json_agg(json_build_object(
                        'bid_id', sb.id,
                        'brand_id', sb.brand_id,
                        'brand_name', b.name,
                        'bid_amount', sb.bid_amount,
                        'pitch', sb.pitch,
                        'status', sb.status,
                        'booking_id', bk.id,
                        'booking_status', bk.status,
                        'content_status', bk.content_status,
                        'content_link', bk.content_link,
                        'submission_notes', bk.submission_notes,
                        'revision_notes', bk.revision_notes,
                        'payment_status', bk.payment_status
                    ))
                    FROM sponsor_bids sb
                    JOIN brands b ON sb.brand_id = b.id
                    LEFT JOIN bookings bk ON sb.draft_id = bk.draft_id AND sb.brand_id = bk.brand_id AND sb.status = 'Accepted'
                    WHERE sb.draft_id = sd.id
                ), '[]'::json) AS bids
            FROM sponsor_drafts sd
            JOIN creators c ON sd.creator_id = c.id
            WHERE sd.creator_id = %s
            ORDER BY sd.created_at DESC
        ''', (creator_id,))
        drafts = cursor.fetchall()

        # Parse JSONB fields into Python lists
        for draft in drafts:
            draft['platforms'] = json.loads(draft['platforms']) if draft['platforms'] and isinstance(draft['platforms'], str) else draft['platforms'] if isinstance(draft['platforms'], list) else []
            draft['audience_targets'] = json.loads(draft['audience_targets']) if draft['audience_targets'] and isinstance(draft['audience_targets'], str) else draft['audience_targets'] if isinstance(draft['audience_targets'], list) else []
            draft['topics'] = json.loads(draft['topics']) if draft['topics'] and isinstance(draft['topics'], str) else draft['topics'] if isinstance(draft['topics'], list) else []
            draft['bids'] = json.loads(draft['bids']) if draft['bids'] and isinstance(draft['bids'], str) else draft['bids'] if isinstance(draft['bids'], list) else []
            draft['regions'] = json.loads(draft['regions']) if draft['regions'] and isinstance(draft['regions'], str) else draft['regions'] if isinstance(draft['regions'], list) else []

        conn.close()
        return jsonify(drafts), 200
    except Exception as e:
        app.logger.error(f"Error fetching my sponsor drafts: {str(e)}")
        return jsonify({"error": str(e)}), 500

@app.route('/sponsor-bids/<int:bid_id>/action', methods=['POST'])
def handle_sponsor_bid_action(bid_id):
    try:
        creator_id = session.get('creator_id')
        if not creator_id:
            return jsonify({"error": "Unauthorized"}), 403

        data = request.get_json()
        action = data.get('action')

        if action not in ['accept', 'reject']:
            return jsonify({"error": "Invalid action"}), 400

        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        cursor.execute('''
            SELECT sb.id, sb.draft_id, sb.brand_id, sb.bid_amount, sb.pitch, sb.status,
                   sd.creator_id, sd.gifting_invite_required, sd.description, sd.platforms, sd.posting_date
            FROM sponsor_bids sb
            JOIN sponsor_drafts sd ON sb.draft_id = sd.id
            WHERE sb.id = %s AND sd.creator_id = %s
        ''', (bid_id, creator_id))
        bid = cursor.fetchone()

        if not bid:
            conn.close()
            return jsonify({"error": "Bid not found or not authorized"}), 404

        if bid['status'] != 'Pending':
            conn.close()
            return jsonify({"error": "Bid is not in a pending state"}), 400

        cursor.execute('''
            UPDATE sponsor_bids 
            SET status = %s 
            WHERE id = %s
        ''', (action.capitalize(), bid_id))

        booking_id = None
        if action == 'accept':
            is_gifting = bid['gifting_invite_required'] == "Yes"
            brief = bid['description'] or "Sponsored post via Sponsor My Post"
            platforms = bid['platforms'] if isinstance(bid['platforms'], list) else json.loads(bid['platforms']) if isinstance(bid['platforms'], str) else []
            product_name = f"Sponsored Post on {platforms[0]}" if platforms else "Sponsored Post"
            promotion_date = bid['posting_date'] or datetime.datetime.now().strftime('%Y-%m-%d')

            app.logger.info(f"Creating booking for bid {bid_id} with type='Sponsor'")
            cursor.execute('''
                INSERT INTO bookings (
                    creator_id, brand_id, draft_id, promotion_date, is_gifting, product_name, product_link, 
                    brief, payment_method, status, content_status, payment_status, bid_amount, type, created_at, updated_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
                RETURNING id, type
            ''', (
                bid['creator_id'], bid['brand_id'], bid['draft_id'], promotion_date, is_gifting, product_name,
                "https://example.com/product", brief, "stripe", "Confirmed", "Confirmed", "On Hold", bid['bid_amount'], "Sponsor"
            ))
            result = cursor.fetchone()
            booking_id = result['id']
            booking_type = result['type']
            app.logger.info(f"Booking created: id={booking_id}, type={booking_type}")

            if booking_type != "Sponsor":
                conn.rollback()
                app.logger.error(f"Booking {booking_id} type not set correctly: {booking_type}")
                return jsonify({"error": "Failed to set booking type"}), 500

            cursor.execute('''
                UPDATE sponsor_drafts 
                SET status = 'Sponsored' 
                WHERE id = %s
            ''', (bid['draft_id'],)
            )

            # Notify brand
            cursor.execute('SELECT user_id FROM brands WHERE id = %s', (bid['brand_id'],))
            brand = cursor.fetchone()
            if brand:
                cursor.execute('SELECT username FROM creators WHERE id = %s', (bid['creator_id'],))
                creator = cursor.fetchone()
                creator_username = creator['username'] if creator else f"Creator ID {bid['creator_id']}"
                try:
                    create_notification(
                        user_id=brand['user_id'],
                        user_role='brand',
                        event_type='BID_ACCEPTED',
                        data={
                            'draft_id': bid['draft_id'],
                            'bid_id': bid_id,
                            'booking_id': booking_id,
                            'bid_amount': float(bid['bid_amount']),  # Convert Decimal to float
                            'creator_username': creator_username
                        },
                        should_send_email=True
                    )
                except Exception as e:
                    app.logger.error(f"Failed to send brand notification for bid {bid_id}: {str(e)}")
                    # Continue execution despite notification failure

        conn.commit()
        conn.close()

        return jsonify({"message": f"Bid {action}ed successfully!", "booking_id": booking_id}), 200
    except Exception as e:
        app.logger.error(f"Error handling sponsor bid {bid_id}: {str(e)}")
        if 'conn' in locals():
            conn.rollback()
            conn.close()
        return jsonify({"error": str(e)}), 500

@app.route('/sponsor-drafts/<int:draft_id>', methods=['PUT'])
def update_sponsor_draft(draft_id):
    try:
        creator_id = session.get('creator_id')
        if not creator_id:
            app.logger.error("Unauthorized: No creator_id in session")
            return jsonify({"error": "Unauthorized"}), 403

        app.logger.info(f"Received form data for update: {dict(request.form)}")

        description = request.form.get('description')
        platforms = request.form.get('platforms')
        min_bid = request.form.get('min_bid')
        audience_targets = request.form.get('audience_targets')
        content_format = request.form.get('content_format')
        topics = request.form.get('topics')
        gifting_invite_required = request.form.get('gifting_invite_required')
        projected_views = request.form.get('projected_views')

        # Log the received values
        app.logger.info(f"Received values - description: {description}, platforms: {platforms}, audience_targets: {audience_targets}, topics: {topics}, min_bid: {min_bid}, gifting_invite_required: {gifting_invite_required}, projected_views: {projected_views}")

        if not description or not content_format or not projected_views:
            app.logger.error("Missing required fields")
            return jsonify({"error": "Description, content format, and projected views are required"}), 400

        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Fetch the current draft data
        cursor.execute('''
            SELECT description, platforms, min_bid, audience_target, content_format, topics,
                   gifting_invite_required, projected_views, status
            FROM sponsor_drafts
            WHERE id = %s AND creator_id = %s
        ''', (draft_id, creator_id))
        draft = cursor.fetchone()
        if not draft:
            conn.close()
            return jsonify({"error": "Draft not found or unauthorized"}), 404

        # Log the current draft data before update
        app.logger.info(f"Draft before update: {draft}")

        # Check if there are accepted bids
        cursor.execute('SELECT COUNT(*) FROM sponsor_bids WHERE draft_id = %s AND status = %s', (draft_id, 'Accepted'))
        accepted_bids = cursor.fetchone()['count']
        if accepted_bids > 0:
            conn.close()
            return jsonify({"error": "Cannot edit draft with accepted bids"}), 400

        # Preserve existing data if new values are empty or invalid
        updated_platforms = platforms if platforms and platforms != '[]' and platforms != '' else draft['platforms']
        updated_audience_targets = audience_targets if audience_targets and audience_targets != '[]' and audience_targets != '' else draft['audience_target']
        updated_topics = topics if topics and topics != '[]' and topics != '' else draft['topics']
        updated_min_bid = min_bid if min_bid else draft['min_bid']
        updated_gifting_invite_required = gifting_invite_required if gifting_invite_required else draft['gifting_invite_required']

        # Log the values being used for the update
        app.logger.info(f"Values for update - description: {description}, platforms: {updated_platforms}, audience_targets: {updated_audience_targets}, topics: {updated_topics}, min_bid: {updated_min_bid}, gifting_invite_required: {updated_gifting_invite_required}, projected_views: {projected_views}")

        # Update the draft
        cursor.execute('''
            UPDATE sponsor_drafts
            SET description = %s, platforms = %s::jsonb, min_bid = %s, audience_target = %s::jsonb,
                content_format = %s, topics = %s::jsonb, gifting_invite_required = %s, projected_views = %s
            WHERE id = %s AND creator_id = %s
        ''', (
            description, updated_platforms, updated_min_bid, updated_audience_targets,
            content_format, updated_topics, updated_gifting_invite_required, projected_views,
            draft_id, creator_id
        ))
        conn.commit()

        # Fetch the updated draft to confirm the changes
        cursor.execute('SELECT * FROM sponsor_drafts WHERE id = %s AND creator_id = %s', (draft_id, creator_id))
        updated_draft = cursor.fetchone()
        app.logger.info(f"Draft after update: {updated_draft}")

        conn.close()

        app.logger.info(f"Draft updated successfully: ID {draft_id}")
        return jsonify({"message": "Draft updated successfully"}), 200
    except Exception as e:
        app.logger.error(f"Error updating sponsor draft: {str(e)}")
        return jsonify({"error": str(e)}), 500

@app.route('/bookings/<int:booking_id>/content', methods=['POST'])
def submit_booking_content(booking_id):
    try:
        user_id = session.get('user_id')
        user_role = session.get('user_role')
        if not user_id or not user_role:
            app.logger.error("No user_id or user_role in session")
            return jsonify({"error": "Unauthorized"}), 403

        data = request.json
        content_file_url = data.get('content_file_url')
        submission_notes = data.get('submission_notes')

        if not content_file_url:
            app.logger.error("No content_file_url provided")
            return jsonify({"error": "Content file URL is required"}), 400

        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Fetch booking details
        cursor.execute(
            '''
            SELECT b.*, c.user_id AS creator_user_id, br.user_id AS brand_user_id
            FROM bookings b
            JOIN creators c ON b.creator_id = c.id
            JOIN brands br ON b.brand_id = br.id
            WHERE b.id = %s
            ''',
            (booking_id,)
        )
        booking = cursor.fetchone()
        if not booking:
            conn.close()
            app.logger.error(f"Booking {booking_id} not found")
            return jsonify({"error": "Booking not found"}), 404

        # Authorize user (only creator can submit content)
        if user_role != 'creator' or str(booking['creator_user_id']) != str(user_id):
            conn.close()
            app.logger.error(f"Unauthorized: creator_user_id={booking['creator_user_id']} does not match user_id={user_id}")
            return jsonify({"error": "Unauthorized"}), 403

        # Update booking with content
        cursor.execute(
            '''
            UPDATE bookings
            SET content_file_url = %s, submission_notes = %s, status = %s, content_status = %s, updated_at = %s
            WHERE id = %s
            RETURNING id, status, content_status, updated_at
            ''',
            (content_file_url, submission_notes, 'Draft Submitted', 'Draft Submitted', datetime.datetime.now(), booking_id)
        )
        updated_booking = cursor.fetchone()

        if not updated_booking:
            conn.rollback()
            conn.close()
            app.logger.error(f"Failed to update booking {booking_id}")
            return jsonify({"error": "Failed to update booking"}), 500

        # Notify creator
        create_notification(
            user_id=booking['creator_user_id'],
            user_role='creator',
            event_type='BOOKING_STEP_UPDATE',
            message=f"New content submitted for booking #{booking_id}",
            data={'booking_id': booking_id, 'status': 'Draft Submitted'},
            should_send_email=True
        )
        # Notify brand
        create_notification(
            user_id=booking['brand_user_id'],
            user_role='brand',
            event_type='BOOKING_STEP_UPDATE',
            message=f"New content submitted for booking #{booking_id}",
            data={'booking_id': booking_id, 'status': 'Draft Submitted'},
            should_send_email=True
        )

        conn.commit()
        conn.close()

        app.logger.info(f"Content submitted for booking {booking_id}")
        return jsonify({
            "message": "Content submitted successfully",
            "booking_id": updated_booking['id'],
            "status": updated_booking['status'],
            "content_status": updated_booking['content_status'],
            "updated_at": updated_booking['updated_at'].isoformat()
        }), 200
    except Exception as e:
        app.logger.error(f"Error submitting content for booking {booking_id}: {str(e)}")
        if 'conn' in locals():
            conn.rollback()
            conn.close()
        return jsonify({"error": str(e)}), 500

@app.route('/bookings/<int:booking_id>/confirm', methods=['PUT'])
def confirm_booking(booking_id):
    try:
        brand_id = session.get('brand_id')
        if not brand_id:
            return jsonify({"error": "Unauthorized: Must be logged in as a brand"}), 403

        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute('''
            UPDATE bookings 
            SET status = 'Confirmed', content_status = 'Confirmed', updated_at = NOW()
            WHERE id = %s AND brand_id = %s AND status = 'Pending'
            RETURNING id, status, content_status
        ''', (booking_id, brand_id))
        booking = cursor.fetchone()
        conn.commit()
        conn.close()
        if not booking:
            return jsonify({"error": "Booking not found, not authorized, or not in Pending status"}), 404
        app.logger.info(f"Booking {booking_id} confirmed: status={booking['status']}, content_status={booking['content_status']}")
        return jsonify({"message": "Booking confirmed", "booking": booking}), 200
    except Exception as e:
        app.logger.error(f"Error confirming booking {booking_id}: {str(e)}")
        return jsonify({"error": str(e)}), 500

@app.route('/sponsor-drafts/<int:draft_id>', methods=['DELETE'])
def delete_sponsor_draft(draft_id):
    try:
        creator_id = session.get('creator_id')
        if not creator_id:
            return jsonify({"error": "Unauthorized"}), 403

        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        
        # Check if draft exists and belongs to creator
        cursor.execute('SELECT status FROM sponsor_drafts WHERE id = %s AND creator_id = %s', (draft_id, creator_id))
        draft = cursor.fetchone()
        if not draft:
            return jsonify({"error": "Draft not found or unauthorized"}), 404

        # Check if there are accepted bids
        cursor.execute('SELECT COUNT(*) FROM sponsor_bids WHERE draft_id = %s AND status = %s', (draft_id, 'Accepted'))
        accepted_bids = cursor.fetchone()['count']
        if accepted_bids > 0:
            return jsonify({"error": "Cannot delete draft with accepted bids"}), 400

        # Delete the draft and any pending bids
        cursor.execute('DELETE FROM sponsor_bids WHERE draft_id = %s', (draft_id,))
        cursor.execute('DELETE FROM sponsor_drafts WHERE id = %s', (draft_id,))
        conn.commit()
        conn.close()

        return jsonify({"message": "Draft deleted successfully"}), 200
    except Exception as e:
        app.logger.error(f"Error deleting sponsor draft: {str(e)}")
        return jsonify({"error": str(e)}), 500

@app.route('/sponsor-drafts', methods=['GET'])
def get_sponsor_drafts():
    try:
        app.logger.info(f"🟢 Accessing /sponsor-drafts with session: {dict(session)}")
        if 'user_id' not in session or 'user_role' not in session or session['user_role'] != 'brand':
            app.logger.warning(f"Unauthorized access to /sponsor-drafts: session={dict(session)}")
            return jsonify({"error": "Unauthorized"}), 403

        brand_id = session.get('brand_id')
        if not brand_id:
            # Recover brand_id from database
            conn = get_db_connection()
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            cursor.execute('SELECT id FROM brands WHERE user_id = %s', (session['user_id'],))
            brand = cursor.fetchone()
            conn.close()
            if not brand:
                app.logger.error(f"No brand found for user_id={session.get('user_id')}")
                return jsonify({"error": "Unauthorized: No brand ID"}), 403
            brand_id = brand['id']
            session['brand_id'] = brand_id
            session.modified = True
            app.logger.info(f"🟢 Recovered brand_id={brand_id} for user_id={session.get('user_id')}")

        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute('''
            SELECT 
                sd.id, 
                sd.description, 
                sd.platforms, 
                sd.min_bid, 
                sd.snippet_url,
                sd.audience_target AS audience_targets, 
                sd.content_format, 
                sd.topics,
                sd.gifting_invite_required, 
                sd.projected_views, 
                sd.status,
                sd.posting_date,
                sd.bidding_deadline,
                c.id AS creator_id,
                c.username,
                c.followers_count,
                c.engagement_rate,
                c.regions,
                c.image_profile,
                (SELECT COUNT(*) FROM sponsor_bids sb WHERE sb.draft_id = sd.id) AS bid_count
            FROM sponsor_drafts sd
            JOIN creators c ON sd.creator_id = c.id
            WHERE sd.status = 'Approved'
            ORDER BY sd.created_at DESC
        ''')
        drafts = cursor.fetchall()

        # Parse JSONB fields into Python lists
        for draft in drafts:
            draft['platforms'] = json.loads(draft['platforms']) if draft['platforms'] and isinstance(draft['platforms'], str) else draft['platforms'] if isinstance(draft['platforms'], list) else []
            draft['audience_targets'] = json.loads(draft['audience_targets']) if draft['audience_targets'] and isinstance(draft['audience_targets'], str) else draft['audience_targets'] if isinstance(draft['audience_targets'], list) else []
            draft['topics'] = json.loads(draft['topics']) if draft['topics'] and isinstance(draft['topics'], str) else draft['topics'] if isinstance(draft['topics'], list) else []
            draft['regions'] = json.loads(draft['regions']) if draft['regions'] and isinstance(draft['regions'], str) else draft['regions'] if isinstance(draft['regions'], list) else []

        conn.close()
        return jsonify(drafts), 200
    except Exception as e:
        app.logger.error(f"🔥 Error fetching sponsor drafts: {str(e)}")
        return jsonify({"error": str(e)}), 500

@app.route('/my-sponsor-bids', methods=['GET'])
def get_my_sponsor_bids():
    try:
        app.logger.info(f"🟢 Accessing /my-sponsor-bids with session: {dict(session)}")
        if 'user_id' not in session or 'user_role' not in session or session['user_role'] != 'brand':
            app.logger.warning(f"Unauthorized access to /my-sponsor-bids: session={dict(session)}")
            return jsonify({"error": "Unauthorized"}), 403

        brand_id = session.get('brand_id')
        if not brand_id:
            # Recover brand_id from database
            conn = get_db_connection()
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            cursor.execute('SELECT id FROM brands WHERE user_id = %s', (session['user_id'],))
            brand = cursor.fetchone()
            conn.close()
            if not brand:
                app.logger.error(f"No brand found for user_id={session.get('user_id')}")
                return jsonify({"error": "Unauthorized: No brand ID"}), 403
            brand_id = brand['id']
            session['brand_id'] = brand_id
            session.modified = True
            app.logger.info(f"🟢 Recovered brand_id={brand_id} for user_id={session.get('user_id')}")

        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute('''
            SELECT 
                sb.id AS bid_id,
                sb.draft_id,
                sb.bid_amount,
                sb.pitch,
                sb.status,
                sd.description AS draft_description,
                c.id AS creator_id,
                c.username AS creator_username,
                c.followers_count,
                c.engagement_rate,
                c.image_profile,
                sd.platforms,
                sd.projected_views,
                sd.bidding_deadline,
                bk.id AS booking_id,
                bk.status AS booking_status,
                bk.content_status,
                bk.content_link,
                bk.submission_notes,
                bk.revision_notes,
                bk.payment_status
            FROM sponsor_bids sb
            JOIN sponsor_drafts sd ON sb.draft_id = sd.id
            JOIN creators c ON sd.creator_id = c.id
            LEFT JOIN bookings bk ON sb.draft_id = bk.draft_id AND sb.brand_id = bk.brand_id AND sb.status = 'Accepted'
            WHERE sb.brand_id = %s
            ORDER BY sb.created_at DESC
        ''', (brand_id,))
        bids = cursor.fetchall()

        # Parse JSONB fields
        for bid in bids:
            bid['platforms'] = json.loads(bid['platforms']) if bid['platforms'] and isinstance(bid['platforms'], str) else bid['platforms'] if isinstance(bid['platforms'], list) else []

        conn.close()
        return jsonify(bids), 200
    except Exception as e:
        app.logger.error(f"🔥 Error fetching sponsor bids: {str(e)}")
        return jsonify({"error": str(e)}), 500



@app.route('/bookings/<int:booking_id>/review-content', methods=['POST'])
def review_booking_content(booking_id):
    try:
        brand_id = session.get('brand_id')
        if not brand_id:
            app.logger.error("Unauthorized: No brand_id in session")
            return jsonify({"error": "Unauthorized"}), 403

        data = request.get_json()
        action = data.get('action')  # "approve" or "request-revision"
        revision_notes = data.get('revision_notes')

        if action not in ['approve', 'request-revision']:
            app.logger.error(f"Invalid action for booking {booking_id}: {action}")
            return jsonify({"error": "Invalid action"}), 400

        if action == 'request-revision' and not revision_notes:
            app.logger.error(f"Revision notes required for request-revision on booking {booking_id}")
            return jsonify({"error": "Revision notes are required when requesting revisions"}), 400

        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Fetch booking details with user IDs
        cursor.execute('''
            SELECT b.id, b.brand_id, b.creator_id, b.content_status, b.product_name,
                   c.user_id AS creator_user_id, br.user_id AS brand_user_id,
                   c.username AS creator_username
            FROM bookings b
            JOIN creators c ON b.creator_id = c.id
            JOIN brands br ON b.brand_id = br.id
            WHERE b.id = %s AND b.brand_id = %s
        ''', (booking_id, brand_id))
        booking = cursor.fetchone()

        if not booking:
            conn.close()
            app.logger.error(f"Booking {booking_id} not found or not authorized for brand {brand_id}")
            return jsonify({"error": "Booking not found or not authorized"}), 404

        # Allow both "Submitted" and "Draft Submitted" as reviewable statuses
        if booking['content_status'] not in ['Submitted', 'Draft Submitted']:
            conn.close()
            app.logger.error(f"Cannot review content for booking {booking_id} in status {booking['content_status']}")
            return jsonify({"error": "Content cannot be reviewed in the current status"}), 400

        # Update booking status
        new_status = 'Approved' if action == 'approve' else 'Revision Requested'
        if action == 'approve':
            cursor.execute('''
                UPDATE bookings
                SET status = %s,
                    content_status = %s,
                    revision_notes = NULL,
                    updated_at = NOW()
                WHERE id = %s
            ''', (new_status, new_status, booking_id))
        else:  # request-revision
            cursor.execute('''
                UPDATE bookings
                SET status = %s,
                    content_status = %s,
                    revision_notes = %s,
                    updated_at = NOW()
                WHERE id = %s
            ''', (new_status, new_status, revision_notes, booking_id))

        # Send notifications
        if action == 'approve':
            # Notify creator
            create_notification(
                user_id=booking['creator_user_id'],
                user_role='creator',
                event_type='CONTENT_APPROVED',
                message=f"Your content for booking #{booking_id} was approved by the brand.",
                data={'booking_id': booking_id, 'product_name': booking['product_name']},
                should_send_email=True
            )
            # Notify brand
            create_notification(
                user_id=booking['brand_user_id'],
                user_role='brand',
                event_type='CONTENT_APPROVED_CONFIRMATION',
                message=f"You approved content for booking #{booking_id} by @{booking['creator_username']}.",
                data={'booking_id': booking_id, 'product_name': booking['product_name']},
                should_send_email=True
            )
        elif action == 'request-revision':
            # Notify creator
            create_notification(
                user_id=booking['creator_user_id'],
                user_role='creator',
                event_type='CONTENT_REVISION_REQUESTED',
                message=f"Revision requested for booking #{booking_id}. Feedback: {revision_notes}",
                data={'booking_id': booking_id, 'product_name': booking['product_name'], 'revision_notes': revision_notes},
                should_send_email=True
            )

        conn.commit()
        app.logger.info(f"✅ Booking {booking_id} content {action}d: new_status={new_status}")
        conn.close()
        return jsonify({"message": f"Content {action}d successfully"}), 200
    except Exception as e:
        app.logger.error(f"🔥 Error reviewing content for booking {booking_id}: {str(e)}")
        if 'conn' in locals():
            conn.rollback()
            conn.close()
        return jsonify({"error": str(e)}), 500

@app.route('/bookings/<int:booking_id>/confirm-published', methods=['POST'])
def confirm_published(booking_id):
    try:
        brand_id = session.get('brand_id')
        if not brand_id:
            return jsonify({"error": "Unauthorized"}), 403

        data = request.get_json()
        payment_method = data.get('payment_method')
        app.logger.info(f"Confirming payment for booking {booking_id} with method {payment_method}")

        if not payment_method:
            return jsonify({"error": "Payment method is required"}), 400

        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        cursor.execute('SELECT content_status, payment_status, bid_amount FROM bookings WHERE id = %s AND brand_id = %s', (booking_id, brand_id))
        booking = cursor.fetchone()

        if not booking:
            conn.close()
            return jsonify({"error": "Booking not found"}), 404

        app.logger.info(f"Booking {booking_id} state: content_status={booking['content_status']}, payment_status={booking['payment_status']}, bid_amount={booking['bid_amount']}")

        if booking['content_status'] != 'Published' or booking['payment_status'] not in ['On Hold', 'Pending']:
            conn.close()
            return jsonify({"error": "Booking not in correct state for payment (must be Published and On Hold or Pending)"}), 400

        if booking['bid_amount'] is None or booking['bid_amount'] <= 0:
            conn.close()
            return jsonify({"error": "Invalid or missing bid amount for payment"}), 400

        if payment_method == 'stripe':
            intent = stripe.PaymentIntent.create(
                amount=int(booking['bid_amount'] * 100),
                currency='eur',
                metadata={'booking_id': booking_id},
            )
            # Update to Pending only after successful intent creation
            cursor.execute('''
                UPDATE bookings 
                SET payment_status = 'Pending', updated_at = NOW()
                WHERE id = %s
            ''', (booking_id,))
            conn.commit()
            conn.close()
            return jsonify({"client_secret": intent.client_secret}), 200
        elif payment_method == 'paypal':
            payment = paypalrestsdk.Payment({
                "intent": "sale",
                "payer": {"payment_method": "paypal"},
                "transactions": [{
                    "amount": {"total": str(booking['bid_amount']), "currency": "EUR"},
                    "description": f"Payment for booking {booking_id}"
                }],
                "redirect_urls": {
                    "return_url": "http://localhost:3000/payment-success",
                    "cancel_url": "http://localhost:3000/payment-failed"
                }
            })

            if payment.create():
                approval_url = next(link.href for link in payment.links if link.rel == "approval_url")
                # Update to Pending only after successful payment creation
                cursor.execute('''
                    UPDATE bookings 
                    SET payment_status = 'Pending', updated_at = NOW()
                    WHERE id = %s
                ''', (booking_id,))
                conn.commit()
                conn.close()
                return jsonify({"approval_url": approval_url}), 200
            else:
                conn.close()
                return jsonify({"error": payment.error.get('message', 'PayPal payment creation failed')}), 400
        else:
            conn.close()
            return jsonify({"error": "Invalid payment method"}), 400
    except Exception as e:
        app.logger.error(f"Error confirming published content: {str(e)}")
        if 'conn' in locals():
            conn.close()
        return jsonify({"error": str(e)}), 500

@app.route('/bookings/<int:booking_id>/complete-payment', methods=['POST'])
def complete_payment(booking_id):
    try:
        brand_id = session.get('brand_id')
        if not brand_id:
            return jsonify({"error": "Unauthorized"}), 403

        data = request.get_json()
        payment_intent_id = data.get('payment_intent_id')  # For Stripe

        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        cursor.execute('SELECT payment_status FROM bookings WHERE id = %s AND brand_id = %s', (booking_id, brand_id))
        booking = cursor.fetchone()

        if not booking or booking['payment_status'] != 'Pending':
            conn.close()
            return jsonify({"error": "Payment not pending or booking not found"}), 400

        cursor.execute('''
            UPDATE bookings 
            SET content_status = 'Completed', status = 'Completed', payment_status = 'Paid', 
                updated_at = NOW()
            WHERE id = %s
        ''', (booking_id,))
        conn.commit()
        conn.close()
        return jsonify({"message": "Payment completed successfully"}), 200
    except Exception as e:
        app.logger.error(f"Error completing payment: {str(e)}")
        return jsonify({"error": str(e)}), 500

@app.route('/bookings/<int:booking_id>/complete-payment', methods=['POST', 'OPTIONS'])
def complete_booking_payment(booking_id):
    if request.method == 'OPTIONS':
        response = jsonify({"message": "CORS preflight successful"})
        response.headers['Access-Control-Allow-Origin'] = 'http://localhost:3000'
        response.headers['Access-Control-Allow-Credentials'] = 'true'
        response.headers['Access-Control-Allow-Methods'] = 'POST, OPTIONS'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization'
        return response, 200

    try:
        brand_id = session.get('brand_id')
        app.logger.debug(f"Session data for booking {booking_id}: {dict(session)}")
        if not brand_id:
            app.logger.error(f"No brand_id in session for booking {booking_id}")
            return jsonify({"error": "Unauthorized: No brand ID in session"}), 403

        data = request.get_json()
        app.logger.debug(f"Received payload for booking {booking_id}: {data}")
        payment_intent_id = data.get('payment_intent_id')  # For Stripe
        payment_id = data.get('payment_id')  # For PayPal
        payer_id = data.get('payer_id')  # For PayPal
        token = data.get('token')  # For PayPal (optional)

        with get_db_connection() as conn:
            conn.autocommit = False
            cursor = conn.cursor(cursor_factory=RealDictCursor)

            # Fetch booking details
            cursor.execute(
                '''
                SELECT id, brand_id, creator_id, content_status, payment_status, bid_amount, price, transaction_id,
                       payment_method, platform_fee, c.user_id AS creator_user_id, br.user_id AS brand_user_id, c.username AS creator_username
                FROM bookings b
                JOIN creators c ON b.creator_id = c.id
                JOIN brands br ON b.brand_id = br.id
                WHERE id = %s AND brand_id = %s
                ''',
                (booking_id, brand_id)
            )
            booking = cursor.fetchone()

            if not booking:
                conn.rollback()
                app.logger.error(f"Booking {booking_id} not found for brand {brand_id}")
                # Debug: Check if booking exists at all
                cursor.execute('SELECT id, brand_id, payment_method FROM bookings WHERE id = %s', (booking_id,))
                any_booking = cursor.fetchone()
                app.logger.debug(f"Booking existence check: {any_booking}")
                return jsonify({"error": "Booking not found or not authorized"}), 404

            app.logger.debug(f"Booking {booking_id} details: {dict(booking)}")

            if booking['payment_status'] == 'Completed':
                conn.rollback()
                app.logger.info(f"Payment already completed for booking {booking_id}")
                return jsonify({"message": "Payment already completed", "booking": dict(booking)}), 200

            if booking['payment_status'] != 'Pending':
                conn.rollback()
                app.logger.error(f"Payment not pending for booking {booking_id}, current status: {booking['payment_status']}")
                return jsonify({"error": f"Payment not pending, current status: {booking['payment_status']}"}), 400

            if booking['content_status'] != 'Published':
                conn.rollback()
                app.logger.error(f"Booking {booking_id} not Published, current content_status: {booking['content_status']}")
                return jsonify({"error": "Booking must be Published"}), 400

            amount = booking['bid_amount'] or booking['price']
            if not amount:
                conn.rollback()
                app.logger.error(f"Booking {booking_id} has no valid amount")
                return jsonify({"error": "Booking amount not set"}), 400

            transaction_id = None
            if booking['payment_method'] == 'stripe' and payment_intent_id:
                intent = stripe.PaymentIntent.retrieve(payment_intent_id)
                if intent.status != 'succeeded':
                    conn.rollback()
                    app.logger.error(f"Stripe payment {payment_intent_id} not succeeded for booking {booking_id}")
                    return jsonify({"error": "Stripe payment not successful"}), 400
                if intent.amount != int(amount * 100):
                    conn.rollback()
                    app.logger.error(f"Stripe payment amount mismatch for booking {booking_id}: expected {int(amount * 100)}, got {intent.amount}")
                    return jsonify({"error": "Payment amount mismatch"}), 400
                transaction_id = payment_intent_id

                commission_amount = booking['platform_fee'] * 100 if booking['platform_fee'] else 0
                transfer_amount = intent.amount - commission_amount
                app.logger.info(f"Stripe PaymentIntent {payment_intent_id} for booking {booking_id}: total={intent.amount}, commission={commission_amount}, transfer={transfer_amount}")

            elif booking['payment_method'] == 'paypal' and payment_id and payer_id:
                try:
                    payment = Payment.find(payment_id)
                    app.logger.debug(f"PayPal payment {payment_id} state before execution: {payment.state}")
                    if payment.execute({"payer_id": payer_id}):
                        if payment.state != 'approved':
                            conn.rollback()
                            app.logger.error(f"PayPal payment {payment_id} not approved for booking {booking_id}: state={payment.state}")
                            return jsonify({"error": "PayPal payment not approved"}), 400
                        payment_amount = float(payment.transactions[0].amount.total)
                        if int(payment_amount * 100) != int(amount * 100):
                            conn.rollback()
                            app.logger.error(f"PayPal payment amount mismatch for booking {booking_id}: expected {int(amount * 100)}, got {int(payment_amount * 100)}")
                            return jsonify({"error": "PayPal payment amount mismatch"}), 400
                        transaction_id = payment_id

                        commission_amount = booking['platform_fee'] * 100 if booking['platform_fee'] else 0
                        transfer_amount = int(payment_amount * 100) - commission_amount
                        app.logger.info(f"PayPal payment {payment_id} for booking {booking_id}: total={payment_amount*100}, commission={commission_amount}, transfer={transfer_amount}")
                    else:
                        conn.rollback()
                        app.logger.error(f"PayPal payment execution failed for booking {booking_id}: {payment.error}")
                        return jsonify({"error": f"PayPal payment execution failed: {payment.error}"}), 400
                except paypalrestsdk.exceptions.ResourceNotFound:
                    conn.rollback()
                    app.logger.error(f"PayPal payment {payment_id} not found for booking {booking_id}")
                    return jsonify({"error": "PayPal payment not found"}), 404
                except Exception as e:
                    conn.rollback()
                    app.logger.error(f"PayPal API error for payment {payment_id} in booking {booking_id}: {str(e)}")
                    return jsonify({"error": f"PayPal API error: {str(e)}"}), 400
            else:
                conn.rollback()
                app.logger.error(f"Invalid payment details for booking {booking_id}: payment_method={booking['payment_method']}, payment_intent_id={payment_intent_id}, payment_id={payment_id}, payer_id={payer_id}")
                return jsonify({"error": "Invalid payment details provided: incorrect payment method or missing IDs"}), 400

            # Update booking status
            cursor.execute(
                '''
                UPDATE bookings
                SET payment_status = 'Completed',
                    status = 'Completed',
                    content_status = 'Completed',
                    transaction_id = %s,
                    updated_at = NOW()
                WHERE id = %s
                RETURNING id, payment_status, status, content_status, transaction_id, platform_fee
                ''',
                (transaction_id, booking_id)
            )
            updated_booking = cursor.fetchone()

            # Send notifications
            notification_data = {
                'booking_id': booking_id,
                'amount': float(amount),
                'platform_fee': float(booking['platform_fee'] or 0),
                'creator_username': booking['creator_username']
            }

            try:
                create_notification(
                    user_id=booking['creator_user_id'],
                    user_role='creator',
                    event_type='PAYMENT_COMPLETED',
                    data=notification_data,
                    should_send_email=True
                )
            except Exception as e:
                app.logger.error(f"Failed to send creator notification for booking {booking_id}: {str(e)}")

            try:
                create_notification(
                    user_id=booking['brand_user_id'],
                    user_role='brand',
                    event_type='PAYMENT_COMPLETED',
                    data=notification_data,
                    should_send_email=True
                )
            except Exception as e:
                app.logger.error(f"Failed to send brand notification for booking {booking_id}: {str(e)}")

            conn.commit()
            app.logger.info(f"Payment completed for booking {booking_id}: platform_fee={updated_booking['platform_fee']}, transaction_id={transaction_id}")
            return jsonify({
                "message": "Payment completed successfully",
                "booking_id": updated_booking['id'],
                "payment_status": updated_booking['payment_status'],
                "status": updated_booking['status'],
                "content_status": updated_booking['content_status'],
                "transaction_id": updated_booking['transaction_id'],
                "platform_fee": updated_booking['platform_fee']
            }), 200

    except stripe.error.StripeError as e:
        app.logger.error(f"Stripe error completing payment for booking {booking_id}: {str(e)}")
        if 'conn' in locals():
            conn.rollback()
        return jsonify({"error": f"Stripe error: {str(e)}"}), 400
    except Exception as e:
        app.logger.error(f"Error completing payment for booking {booking_id}: {str(e)}")
        if 'conn' in locals():
            conn.rollback()
        return jsonify({"error": str(e)}), 500

# Endpoint to check payment status and update booking (optional for polling)
@app.route('/bookings/<int:booking_id>/payment-status', methods=['GET'])
def check_payment_status(booking_id):
    try:
        brand_id = session.get('brand_id')
        if not brand_id:
            return jsonify({"error": "Unauthorized"}), 403

        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        cursor.execute('''
            SELECT id, payment_status, transaction_id
            FROM bookings
            WHERE id = %s AND brand_id = %s
        ''', (booking_id, brand_id))
        booking = cursor.fetchone()

        if not booking:
            conn.close()
            return jsonify({"error": "Booking not found or not authorized"}), 404

        if booking['payment_status'] == 'Completed':
            conn.close()
            return jsonify({"status": "Completed", "transaction_id": booking['transaction_id']}), 200

        if booking['transaction_id']:
            intent = stripe.PaymentIntent.retrieve(booking['transaction_id'])
            if intent.status == 'succeeded':
                cursor.execute('''
                    UPDATE bookings
                    SET payment_status = 'Completed',
                        status = 'Completed',
                        content_status = 'Completed',
                        updated_at = NOW()
                    WHERE id = %s
                ''', (booking_id,))
                conn.commit()
                conn.close()
                return jsonify({"status": "Completed", "transaction_id": booking['transaction_id']}), 200

        conn.close()
        return jsonify({"status": "Pending"}), 200

    except stripe.error.StripeError as e:
        app.logger.error(f"Stripe error checking payment status: {str(e)}")
        conn.close()
        return jsonify({"error": f"Payment status error: {str(e)}"}), 400
    except Exception as e:
        app.logger.error(f"Error checking payment status: {str(e)}")
        conn.close()
        return jsonify({"error": str(e)}), 500


@app.route('/packages', methods=['GET'])
def get_packages():
    try:
        creator_id = session.get("creator_id")
        if not creator_id:
            print("🚨 ERROR: Creator ID is missing from session!")
            return jsonify({"error": "Unauthorized - Creator ID not found in session"}), 403
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("SELECT * FROM creator_subscription_packages WHERE creator_id = %s", (creator_id,))
        packages = cursor.fetchall()
        cursor.close()
        conn.close()
        print(f"🟢 Returning packages for creator_id {creator_id}:", packages)  # Log returned data
        return jsonify(packages), 200
    except Exception as e:
        print(f"🔥 Error fetching packages: {e}")
        return jsonify({"error": str(e)}), 500



# Route to update package status
@app.route('/packages/<int:id>/status', methods=['PUT', 'OPTIONS'])
def update_package_status(id):
    if request.method == 'OPTIONS':
        print(f"📌 Handling OPTIONS request for /packages/{id}/status")
        response = jsonify({"message": "CORS preflight successful"})
        response.headers['Access-Control-Allow-Origin'] = 'http://localhost:3000'
        response.headers['Access-Control-Allow-Credentials'] = 'true'
        response.headers['Access-Control-Allow-Methods'] = 'PUT, OPTIONS'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization'
        return response, 200

    try:
        data = request.json
        new_status = data.get('status')
        
        if new_status not in ['active', 'paused', 'inactive']:
            app.logger.error(f"Invalid status: {new_status}")
            return jsonify({"error": "Invalid status"}), 400

        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute('''
            UPDATE creator_subscription_packages 
            SET status = %s, updated_at = NOW()
            WHERE id = %s
            RETURNING id
        ''', (new_status, id))

        updated_id = cursor.fetchone()
        if not updated_id:
            conn.close()
            return jsonify({"error": "Package not found"}), 404

        conn.commit()
        cursor.close()
        conn.close()

        print(f"🟢 Package {id} status updated to {new_status}")
        return jsonify({"message": "Package status updated successfully!"}), 200
    except Exception as e:
        app.logger.error(f"Error updating package status {id}: {str(e)}")
        return jsonify({"error": str(e)}), 500


# Route to update a package (offer)
@app.route('/packages/<int:id>', methods=['PUT', 'OPTIONS'])
def update_package(id):
    if request.method == 'OPTIONS':
        print(f"📌 Handling OPTIONS request for /packages/{id}")
        response = jsonify({"message": "CORS preflight successful"})
        response.headers['Access-Control-Allow-Origin'] = 'http://localhost:3000'
        response.headers['Access-Control-Allow-Credentials'] = 'true'
        response.headers['Access-Control-Allow-Methods'] = 'PUT, OPTIONS'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization'
        return response, 200

    try:
        data = request.get_json()
        print(f"🟢 Received PUT Data for Package {id}:", data)

        package_name = data.get("package_name")
        deliverables = data.get("deliverables")
        frequency = data.get("frequency")
        description = data.get("description")
        price = data.get("price")

        if not all([package_name, deliverables, frequency, description, price]):
            app.logger.error("Missing required fields")
            return jsonify({"error": "All fields (package_name, deliverables, frequency, description, price) are required"}), 400

        # Updated validation: Accept 'platform' instead of 'platforms', allow optional fields
        if not isinstance(deliverables, list) or not all(
            isinstance(d, dict) and 'type' in d and 'quantity' in d and ('platform' in d or 'platforms' in d) 
            for d in deliverables
        ):
            app.logger.error("Invalid deliverables format")
            return jsonify({"error": "Deliverables must be an array of objects with type, quantity, and platform"}), 400

        valid_frequencies = ['monthly', 'quarterly']
        if frequency not in valid_frequencies:
            app.logger.error(f"Invalid frequency: {frequency}")
            return jsonify({"error": f"Invalid frequency. Must be one of: {', '.join(valid_frequencies)}"}), 400

        # Normalize deliverables to use 'platforms' if needed (optional compatibility)
        normalized_deliverables = [
            {**d, 'platforms': d.get('platform') if 'platform' in d else d.get('platforms', [])} 
            for d in deliverables
        ]

        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute('''
            UPDATE creator_subscription_packages 
            SET package_name = %s, deliverables = %s::jsonb, frequency = %s, description = %s, price = %s, updated_at = NOW()
            WHERE id = %s
            RETURNING id
        ''', (package_name, json.dumps(normalized_deliverables), frequency, description, price, id))

        updated_id = cursor.fetchone()
        if not updated_id:
            conn.close()
            return jsonify({"error": "Package not found"}), 404

        conn.commit()
        cursor.close()
        conn.close()

        print(f"🟢 Package {id} updated successfully")
        return jsonify({"message": "Package updated successfully!"}), 200
    except Exception as e:
        app.logger.error(f"Error updating package {id}: {str(e)}")
        return jsonify({"error": str(e)}), 500

# Route to delete a package
@app.route('/packages/<int:id>', methods=['DELETE'])
def delete_package(id):
    try:
        creator_id = session.get('creator_id', 1)  # Hardcode for testing, replace with session logic
        
        if not creator_id:
            return jsonify({'error': 'Unauthorized'}), 403
        
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute('DELETE FROM packages WHERE id = %s AND creator_id = %s', (id, creator_id))

        if cursor.rowcount == 0:  # Check if a row was deleted
            return jsonify({'error': 'Package not found or unauthorized action'}), 404

        conn.commit()
        cursor.close()
        conn.close()

        return jsonify({'message': 'Package deleted successfully!'}), 200

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/creator-offers', methods=['GET'])
def get_creator_offers():
    try:
        keyword = request.args.get('keyword', '').strip().lower()
        keyword = f"%{keyword}%" if keyword else '%'
        content_formats = request.args.getlist('content_formats[]') or None
        platforms = request.args.getlist('platforms[]') or None
        topics = request.args.getlist('topics[]') or None
        audience_targets = request.args.getlist('audience_target[]') or None
        gifting_invite_required = request.args.get('gifting_invite_required', type=str, default=None)
        min_followers = request.args.get('min_followers', type=int, default=None)
        max_followers = request.args.get('max_followers', type=int, default=None)
        min_bid = request.args.get('min_bid', type=float, default=None)
        max_bid = request.args.get('max_bid', type=float, default=None)
        projected_views = request.args.getlist('projected_views[]') or None

        conn = get_db_connection()
        if not conn:
            return jsonify({'error': 'Database connection failed'}), 500
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Debug: Verify schema and search path
        cursor.execute("SHOW search_path")
        search_path = cursor.fetchone()['search_path']
        app.logger.debug(f"Current search_path: {search_path}")
        cursor.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'sponsor_drafts'")
        columns = [row['column_name'] for row in cursor.fetchall()]
        app.logger.debug(f"sponsor_drafts columns: {columns}")
        if platforms:
            app.logger.debug(f"Platforms filter: {platforms}")

        query = '''
            SELECT 
                c.id AS creator_id,
                c.username AS name,
                c.bio,
                c.followers_count,
                c.niche,
                c.regions,
                c.platforms AS creator_platforms,
                c.primary_age_range,
                c.image_profile,
                c.social_links,
                p.id AS offer_id,
                p.package_name,
                p.price,
                p.content_type AS content_format,
                NULL AS deliverables,
                p.description,
                NULL AS frequency,
                p.status,
                'Package' AS type,
                NULL::numeric AS min_bid,
                NULL::date AS bidding_deadline,
                NULL AS projected_views,
                NULL AS platforms,
                NULL AS topics,
                NULL AS audience_target,
                NULL AS gifting_invite_required,
                NULL AS snippet_url
            FROM 
                creators c
            JOIN 
                packages p ON c.id = p.creator_id
            WHERE 
                p.status = 'active'
            UNION
            SELECT 
                c.id AS creator_id,
                c.username AS name,
                c.bio,
                c.followers_count,
                c.niche,
                c.regions,
                c.platforms AS creator_platforms,
                c.primary_age_range,
                c.image_profile,
                c.social_links,
                sp.id AS offer_id,
                sp.package_name,
                sp.price,
                NULL AS content_format,
                sp.deliverables,
                sp.description,
                sp.frequency,
                sp.status,
                'Subscription' AS type,
                NULL::numeric AS min_bid,
                NULL::date AS bidding_deadline,
                NULL AS projected_views,
                NULL AS platforms,
                NULL AS topics,
                NULL AS audience_target,
                NULL AS gifting_invite_required,
                NULL AS snippet_url
            FROM 
                creators c
            JOIN 
                creator_subscription_packages sp ON c.id = sp.creator_id
            WHERE 
                sp.status = 'active'
            UNION
            SELECT 
                c.id AS creator_id,
                c.username AS name,
                c.bio,
                c.followers_count,
                c.niche,
                c.regions,
                c.platforms AS creator_platforms,
                c.primary_age_range,
                c.image_profile,
                c.social_links,
                sd.id AS offer_id,
                sd.description AS package_name,
                sd.min_bid AS price,
                sd.content_format,
                NULL AS deliverables,
                sd.description,
                NULL AS frequency,
                sd.status,
                'Sponsor' AS type,
                sd.min_bid,
                sd.bidding_deadline,
                sd.projected_views,
                sd.platforms,
                sd.topics,
                sd.audience_target,
                sd.gifting_invite_required,
                sd.snippet_url AS snippet_url
            FROM 
                creators c
            JOIN 
                public.sponsor_drafts sd ON c.id = sd.creator_id
            WHERE 
                sd.status = 'Approved'
        '''
        params = []

        full_query = f'''
            SELECT * FROM ({query}) AS combined_offers
            WHERE 1=1
        '''

        if keyword != '%':
            full_query += '''
                AND (name ILIKE %s
                OR bio ILIKE %s
                OR package_name ILIKE %s
                OR description ILIKE %s)
            '''
            params.extend([keyword] * 4)

        if content_formats:
            full_query += '''
                AND content_format = ANY(%s)
            '''
            params.append(content_formats)

        if platforms:
            full_query += '''
                AND EXISTS (
                    SELECT 1
                    FROM json_array_elements_text(platforms::json) AS platform
                    WHERE platform = ANY(%s)
                )
            '''
            params.append(platforms)

        if topics:
            full_query += '''
                AND EXISTS (
                    SELECT 1
                    FROM json_array_elements_text(topics::json) AS topic
                    WHERE topic = ANY(%s)
                )
            '''
            params.append(topics)

        if audience_targets:
            full_query += '''
                AND EXISTS (
                    SELECT 1
                    FROM json_array_elements_text(audience_target::json) AS target
                    WHERE target = ANY(%s)
                )
            '''
            params.append(audience_targets)

        if gifting_invite_required:
            full_query += '''
                AND gifting_invite_required = %s
            '''
            params.append(gifting_invite_required)

        if min_followers is not None:
            full_query += " AND followers_count >= %s"
            params.append(min_followers)

        if max_followers is not None:
            full_query += " AND followers_count <= %s"
            params.append(max_followers)

        if min_bid is not None:
            full_query += " AND (price >= %s OR min_bid >= %s)"
            params.append(min_bid)
            params.append(min_bid)

        if max_bid is not None:
            full_query += " AND (price <= %s OR min_bid <= %s)"
            params.append(max_bid)
            params.append(max_bid)

        if projected_views:
            full_query += " AND projected_views = ANY(%s)"
            params.append(projected_views)

        full_query += " ORDER BY followers_count DESC"

        app.logger.debug(f"Executing query: {full_query} with params: {params}")
        cursor.execute(full_query, params)
        results = cursor.fetchall()

        for result in results:
            try:
                if isinstance(result['social_links'], str):
                    result['social_links'] = json.loads(result['social_links'])
                elif result['social_links'] is None:
                    result['social_links'] = []
                # Ensure social_links has followersCount
                result['social_links'] = [
                    link for link in result['social_links']
                    if isinstance(link, dict) and 'platform' in link and 'followersCount' in link
                ]
                for link in result['social_links']:
                    if 'followersCount' not in link or link['followersCount'] is None:
                        link['followersCount'] = 0
                        app.logger.warning(f"Missing followersCount for platform {link.get('platform')} in creator_id {result['creator_id']}")
            except json.JSONDecodeError as e:
                app.logger.error(f"Failed to parse social_links for creator_id {result['creator_id']}: {str(e)}")
                result['social_links'] = []

            try:
                if isinstance(result['niche'], str):
                    result['niche'] = json.loads(result['niche']) if result['niche'].startswith('[') else result['niche'].split(',')
                elif result['niche'] is None:
                    result['niche'] = []
            except (json.JSONDecodeError, ValueError) as e:
                app.logger.error(f"Failed to parse niche for creator_id {result['creator_id']}: {str(e)}")
                result['niche'] = []

            try:
                if isinstance(result['regions'], str):
                    result['regions'] = json.loads(result['regions']) if result['regions'].startswith('[') else result['regions'].split(',')
                elif result['regions'] is None:
                    result['regions'] = []
            except (json.JSONDecodeError, ValueError) as e:
                app.logger.error(f"Failed to parse regions for creator_id {result['creator_id']}: {str(e)}")
                result['regions'] = []

            try:
                if isinstance(result['platforms'], str):
                    result['platforms'] = json.loads(result['platforms']) if result['platforms'].startswith('[') else result['platforms'].split(',')
                elif result['platforms'] is None:
                    result['platforms'] = []
            except (json.JSONDecodeError, ValueError) as e:
                app.logger.error(f"Failed to parse platforms for creator_id {result['creator_id']}: {str(e)}")
                result['platforms'] = []

            try:
                if isinstance(result['topics'], str):
                    result['topics'] = json.loads(result['topics']) if result['topics'].startswith('[') else result['topics'].split(',')
                elif result['topics'] is None:
                    result['topics'] = []
            except (json.JSONDecodeError, ValueError) as e:
                app.logger.error(f"Failed to parse topics for creator_id {result['creator_id']}: {str(e)}")
                result['topics'] = []

            try:
                if isinstance(result['audience_target'], str):
                    result['audience_target'] = json.loads(result['audience_target']) if result['audience_target'].startswith('[') else result['audience_target'].split(',')
                elif result['audience_target'] is None:
                    result['audience_target'] = []
            except (json.JSONDecodeError, ValueError) as e:
                app.logger.error(f"Failed to parse audience_target for creator_id {result['creator_id']}: {str(e)}")
                result['audience_target'] = []

            # Normalize snippet_url (simple check without re)
            if 'snippet_url' in result and (result['snippet_url'] is None or 
                                          isinstance(result['snippet_url'], str) and 
                                          result['snippet_url'].strip() == ''):
                result['snippet_url'] = None
                app.logger.debug(f"Normalized empty snippet_url to null for offer_id {result['offer_id']}")

        conn.close()
        app.logger.info(f"Fetched {len(results)} active offers")
        return jsonify(results), 200
    except Exception as e:
        app.logger.error(f"Error fetching creator offers: {str(e)}")
        return jsonify({'error': str(e)}), 500

# Route to create a new offer
@app.route('/create-offer', methods=['POST'])
def create_offer():
    try:
        # ✅ Debugging session before using it
        print(f"📌 [HEADERS DEBUG] Request Headers: {dict(request.headers)}")
        print(f"📌 [COOKIES DEBUG] Request Cookies: {request.cookies}")
        print(f"📌 [SESSION DEBUG] Current Session: {dict(session)}")

        creator_id = session.get("creator_id")  # ✅ Get from session

        if not creator_id:
            print("🚨 ERROR: Creator ID is missing from session!")
            return jsonify({"error": "Unauthorized: Creator ID missing from session"}), 403

        data = request.get_json()

        package_name = data.get("package_name")
        platforms = data.get("platforms")
        content_type = data.get("content_type")
        price = data.get("price")
        description = data.get("description")
        quantity = data.get("quantity", 1)

        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute('''
            INSERT INTO packages 
            (creator_id, package_name, platforms, content_type, price, description, quantity, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
        ''', (creator_id, package_name, platform, content_type, price, description, quantity))

        conn.commit()
        cursor.close()
        conn.close()

        print(f"🟢 Offer created successfully for creator_id={creator_id}")
        return jsonify({"message": "Offer created successfully!"}), 201

    except Exception as e:
        print(f"🔥 Error creating offer: {e}")
        return jsonify({"error": str(e)}), 500





@app.route('/creators/<int:id>', methods=['GET'])
def get_creator_by_id(id):
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        cursor.execute('''
            SELECT id, username, bio, followers_count, platforms, image_profile, social_links,
                   total_posts, total_views, total_likes, total_comments, total_shares, portfolio_links,
                   engagement_rate, niche, regions, primary_age_range, top_locations,
                   (SELECT COUNT(*) FROM bookings WHERE creator_id = %s AND status = 'Completed') AS collaborations_count
            FROM creators
            WHERE id = %s
        ''', (id, id))
        creator = cursor.fetchone()

        if not creator:
            return jsonify({'error': 'Creator not found'}), 404

        # Parse JSON fields
        creator['social_links'] = json.loads(creator['social_links']) if creator['social_links'] else []
        creator['portfolio_links'] = json.loads(creator['portfolio_links']) if creator['portfolio_links'] else []

        conn.close()
        return jsonify(creator), 200
    except Exception as e:
        app.logger.error(f"Error fetching creator profile {id}: {str(e)}")
        return jsonify({'error': str(e)}), 500


@app.route('/creators/<int:creator_id>/offers', methods=['GET'])
def get_offers_by_creator(creator_id):
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        query = '''
            SELECT 
                p.id AS offer_id,
                p.package_name,
                p.price,
                p.content_type,
                NULL AS deliverables,
                p.description,
                NULL AS frequency,
                p.status
            FROM 
                packages p
            WHERE 
                p.creator_id = %s AND p.status = 'active'
            UNION
            SELECT 
                sp.id AS offer_id,
                sp.package_name,
                sp.price,
                NULL AS content_type,
                sp.deliverables,
                sp.description,
                sp.frequency,
                sp.status
            FROM 
                creator_subscription_packages sp
            WHERE 
                sp.creator_id = %s AND sp.status = 'active'
        '''
        cursor.execute(query, (creator_id, creator_id))
        results = cursor.fetchall()

        conn.close()
        app.logger.info(f"Fetched {len(results)} active offers for creator {creator_id}: {results}")
        return jsonify(results), 200
    except Exception as e:
        app.logger.error(f"Error fetching offers for creator {creator_id}: {str(e)}")
        return jsonify({'error': str(e)}), 500




@app.route('/upload-logo', methods=['POST'])
def upload_logo():
    try:
        if 'brandLogo' not in request.files:
            return jsonify({'error': 'No logo file part'}), 400

        file = request.files['brandLogo']

        if file.filename == '':
            return jsonify({'error': 'No selected file'}), 400

        if file and allowed_file(file.filename):
            filename = secure_filename(file.filename)
            file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file.save(file_path)

            # Save file path in your database (assuming SQLAlchemy)
            # For now, return a success message with the file path
            return jsonify({'message': 'File uploaded successfully', 'file_path': file_path}), 200

        return jsonify({'error': 'Invalid file type'}), 400
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    

# Total Active Campaigns
@app.route('/analytics/total_active_campaigns', methods=['GET'])
def total_active_campaigns():
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("SELECT COUNT(*) FROM campaigns WHERE status = 'active'")
        total_active_campaigns = cursor.fetchone()[0]
        
        conn.close()
        
        return jsonify({"total_active_campaigns": total_active_campaigns}), 200
    except Exception as e:
        app.logger.error(f"Error fetching total active campaigns: {str(e)}")
        return jsonify({"error": "Failed to fetch total active campaigns"}), 500

# Total Creator Applications
@app.route('/analytics/total_creator_applications', methods=['GET'])
def total_creator_applications():
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("SELECT COUNT(*) FROM campaign_applications")
        total_creator_applications = cursor.fetchone()[0]
        
        conn.close()
        
        return jsonify({"total_creator_applications": total_creator_applications}), 200
    except Exception as e:
        app.logger.error(f"Error fetching total creator applications: {str(e)}")
        return jsonify({"error": "Failed to fetch total creator applications"}), 500

# Total Creators Booked
@app.route('/analytics/total_creators_booked', methods=['GET'])
def total_creators_booked():
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("SELECT COUNT(DISTINCT creator_id) FROM campaign_applications WHERE status = 'booked'")
        total_creators_booked = cursor.fetchone()[0]
        
        conn.close()
        
        return jsonify({"total_creators_booked": total_creators_booked}), 200
    except Exception as e:
        app.logger.error(f"Error fetching total creators booked: {str(e)}")
        return jsonify({"error": "Failed to fetch total creators booked"}), 500


    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


    
    try:
        # Example stats query, adjust according to your actual schema and stats needs
        cursor.execute(
            '''
            SELECT 
                COUNT(*) AS total_requests,
                SUM(CASE WHEN status = 'approved' THEN 1 ELSE 0 END) AS approved_requests,
                SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END) AS pending_requests,
                SUM(CASE WHEN status = 'rejected' THEN 1 ELSE 0 END) AS rejected_requests
            FROM collaboration_requests
            WHERE creator_id = %s
            ''', 
            (creator_id,)
        )
        stats = cursor.fetchone()
        return stats

    except Exception as e:
        print(f"Error fetching collaboration stats: {e}")
        return None

    finally:
        conn.close()

def query_to_fetch_recent_requests(creator_id):
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    try:
        # Fetch 6 most recent collaboration requests for the creator
        cursor.execute(
            '''
            SELECT cr.id, cr.status, cr.created_at, b.name AS brand_name, cr.content_brief
            FROM collaboration_requests cr
            JOIN brands b ON cr.brand_id = b.id
            WHERE cr.creator_id = %s
            ORDER BY cr.created_at DESC
            LIMIT 6;
            ''', 
            (creator_id,)
        )
        requests = cursor.fetchall()
        return requests

    except Exception as e:
        print(f"Error fetching recent collaboration requests: {e}")
        return None

    finally:
        conn.close()


def query_to_fetch_submission_metrics(creator_id):
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    try:
        cursor.execute('''
            SELECT 
                COUNT(*) AS total_requests,
                SUM(CASE WHEN status = 'accepted' THEN 1 ELSE 0 END) AS accepted_requests,
                SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END) AS pending_requests,
                SUM(CASE WHEN status = 'declined' THEN 1 ELSE 0 END) AS declined_requests,
                SUM(CASE WHEN status = 'waiting_for_information' THEN 1 ELSE 0 END) AS waiting_requests
            FROM collaboration_requests
            WHERE creator_id = %s
        ''', (creator_id,))
        
        stats = cursor.fetchone()
        return stats
    except Exception as e:
        print(f"Error fetching submission metrics: {e}")
        return {}
    finally:
        conn.close()

@app.route('/bookings', methods=['POST', 'OPTIONS'])
def create_booking():
    if request.method == 'OPTIONS':
        response = jsonify({"message": "CORS preflight successful"})
        response.headers['Access-Control-Allow-Origin'] = 'http://localhost:3000'
        response.headers['Access-Control-Allow-Credentials'] = 'true'
        response.headers['Access-Control-Allow-Methods'] = 'POST, OPTIONS'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization'
        return response, 200

    data = request.json
    print("📌 Received Booking Data:", data)

    creator_id = data.get('creator_id')
    brand_id = data.get('brand_id')
    offer_id = data.get('offer_id')
    promotion_date = data.get('promotion_date')
    product_name = data.get('product_name')
    product_link = data.get('product_link')
    brief = data.get('brief')
    free_sample = data.get('free_sample', False)
    payment_method = data.get('payment_method', '').lower()
    transaction_id = data.get('transaction_id')
    bid_amount = data.get('bid_amount')
    offer_price = data.get('offer_price')

    session_brand_id = session.get('brand_id')
    if not session_brand_id:
        print("❌ No brand ID in session")
        return jsonify({"error": "Unauthorized: Must be logged in as a brand"}), 403
    if brand_id != session_brand_id:
        print(f"⚠️ Overriding provided brand_id {brand_id} with session brand_id {session_brand_id}")
        brand_id = session_brand_id

    required_fields = [creator_id, brand_id, promotion_date, product_name, product_link, brief, payment_method]
    if not all(required_fields):
        missing = [k for k, v in data.items() if v is None and k in ['creator_id', 'brand_id', 'promotion_date', 'product_name', 'product_link', 'brief', 'payment_method']]
        print(f"❌ Missing required fields: {missing}")
        return jsonify({"error": f"All fields are required, missing: {missing}"}), 400

    valid_payment_methods = ['stripe', 'paypal']
    if payment_method not in valid_payment_methods:
        print(f"❌ Invalid Payment Method: {payment_method}")
        return jsonify({"error": "Invalid payment method"}), 400

    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        booking_type = "One-off Partnership" if offer_id else "Sponsor"
        final_price = float(offer_price) if offer_price is not None and offer_id else None
        final_bid_amount = float(bid_amount) if bid_amount is not None and not offer_id else None

        if offer_id:
            cursor.execute("SELECT id, price FROM packages WHERE id = %s", (offer_id,))
            offer = cursor.fetchone()
            if not offer:
                print(f"🚨 Offer ID {offer_id} does not exist in the database!")
                conn.close()
                return jsonify({"error": "Invalid offer details"}), 400
            print(f"✅ Offer ID {offer_id} found with price: {offer['price']}")
            final_price = final_price if final_price is not None else offer['price']

        cursor.execute(
            '''
            INSERT INTO bookings (
                creator_id, brand_id, offer_id, promotion_date, product_name, product_link, 
                brief, free_sample, payment_method, payment_status, payment_hold_status, 
                content_status, transaction_id, bid_amount, type, price
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id, status, created_at, payment_status, payment_hold_status, content_status, bid_amount, type, price
            ''',
            (creator_id, brand_id, offer_id, promotion_date, product_name, product_link, 
             brief, free_sample, payment_method, 'Pending', 'On Hold', 'Pending', transaction_id, 
             final_bid_amount, booking_type, final_price)
        )

        booking = cursor.fetchone()

        # Get creator's user_id
        cursor.execute('SELECT user_id FROM creators WHERE id = %s', (creator_id,))
        creator = cursor.fetchone()
        if creator:
            create_notification(
                user_id=creator['user_id'],
                user_role='creator',
                event_type='NEW_BOOKING',
                message=f"New booking received for {product_name}",
                data={'booking_id': booking['id'], 'product_name': product_name},
                send_email=True
            )

        conn.commit()
        conn.close()
        print(f"✅ Booking Created: ID {booking['id']}, Type: {booking['type']}, Price: {booking['price']}, Bid: {booking['bid_amount']}")
        return jsonify({
            "message": "Booking created successfully",
            "booking_id": booking['id'],
            "status": booking['status'],
            "created_at": booking['created_at'].isoformat(),
            "payment_status": booking['payment_status'],
            "payment_hold_status": booking['payment_hold_status'],
            "content_status": booking['content_status'],
            "bid_amount": booking['bid_amount'],
            "type": booking['type'],
            "price": booking['price']
        }), 201

    except Exception as e:
        logger.error(f"Error creating booking: {e}")
        print(f"🔥 ERROR creating booking: {str(e)}")
        return jsonify({"error": str(e)}), 500

@app.route('/create-stripe-payment', methods=['POST', 'OPTIONS'])
def create_stripe_payment():
    if request.method == 'OPTIONS':
        response = jsonify({"message": "CORS preflight successful"})
        response.headers['Access-Control-Allow-Origin'] = 'http://localhost:3000'
        response.headers['Access-Control-Allow-Credentials'] = 'true'
        response.headers['Access-Control-Allow-Methods'] = 'POST, OPTIONS'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization'
        return response, 200

    try:
        brand_id = session.get('brand_id')
        if not brand_id:
            app.logger.error("No brand ID in session")
            return jsonify({"error": "Unauthorized: No brand ID in session"}), 403

        data = request.json
        amount = data.get('amount')  # In cents (e.g., 50000 for €500)
        creator_id = data.get('creator_id')
        booking_id = data.get('booking_id')
        booking_type = data.get('booking_type')

        missing = [k for k in ['amount', 'creator_id', 'booking_id'] if data.get(k) is None]
        if missing:
            app.logger.error(f"Missing required parameters: {missing}")
            return jsonify({"error": f"Missing required parameters: {', '.join(missing)}"}), 400

        conn = get_db_connection()
        conn.autocommit = False
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        try:
            # Fetch booking details
            cursor.execute(
                '''
                SELECT id, bid_amount, brand_id, status, content_status, payment_status, price, transaction_id
                FROM bookings 
                WHERE id = %s AND brand_id = %s
                ''',
                (booking_id, brand_id)
            )
            booking = cursor.fetchone()
            if not booking:
                conn.rollback()
                return jsonify({"error": "Booking not found or not authorized"}), 404

            # Validate booking type (Sponsor or Campaign Invite)
            if booking_type not in ['Sponsor', 'Campaign Invite']:
                conn.rollback()
                return jsonify({"error": "Invalid booking type for payment"}), 400

            # Ensure content_status is Published
            if booking['content_status'] != 'Published':
                conn.rollback()
                return jsonify({"error": "Payment can only be initiated for Published bookings"}), 400

            # Check payment status
            if booking['payment_status'] == 'Completed':
                conn.rollback()
                return jsonify({"error": "Payment already completed"}), 400

            if booking['payment_status'] not in ['On Hold', 'Pending']:
                conn.rollback()
                return jsonify({"error": f"Payment status must be On Hold or Pending, current: {booking['payment_status']}"}), 400

            # Validate amount (use bid_amount for Sponsor bookings)
            expected_amount = booking['bid_amount']
            if expected_amount is None or int(expected_amount * 100) != amount:
                conn.rollback()
                return jsonify({"error": "Amount mismatch with booking bid_amount"}), 400

            # Calculate commission (15%)
            commission_rate = 0.15
            commission_amount = int(amount * commission_rate)  # In cents

            # Fetch creator's Stripe connected account
            cursor.execute('SELECT stripe_account_id FROM creators WHERE id = %s', (creator_id,))
            creator = cursor.fetchone()
            if not creator or not creator['stripe_account_id']:
                conn.rollback()
                return jsonify({"error": "Creator's payment account not set up"}), 400

            # Create Stripe PaymentIntent with application fee only
            intent = stripe.PaymentIntent.create(
                amount=amount,
                currency='eur',
                payment_method_types=['card'],
                metadata={'booking_id': booking_id, 'creator_id': creator_id, 'brand_id': brand_id, 'booking_type': booking_type},
                transfer_data={
                    'destination': creator['stripe_account_id']
                },
                application_fee_amount=commission_amount
            )

            # Update booking with transaction_id, payment_status, and platform_fee
            cursor.execute(
                '''
                UPDATE bookings
                SET payment_status = 'Pending',
                    transaction_id = %s,
                    platform_fee = %s,
                    updated_at = NOW()
                WHERE id = %s AND brand_id = %s
                RETURNING payment_status, transaction_id, platform_fee
                ''',
                (intent.id, commission_amount / 100.0, booking_id, brand_id)
            )
            updated_booking = cursor.fetchone()
            if not updated_booking:
                conn.rollback()
                return jsonify({"error": "Failed to update booking"}), 500

            app.logger.info(f"Booking {booking_id} payment initiated: payment_status={updated_booking['payment_status']}, transaction_id={updated_booking['transaction_id']}, platform_fee={updated_booking['platform_fee']}")

            conn.commit()
            return jsonify({"client_secret": intent.client_secret}), 200

        finally:
            cursor.close()
            if not conn.closed:
                conn.close()

    except stripe.error.StripeError as e:
        app.logger.error(f"Stripe error creating payment for booking {booking_id}: {str(e)}")
        return jsonify({"error": f"Stripe error: {str(e)}"}), 400
    except Exception as e:
        app.logger.error(f"Error creating Stripe payment for booking {booking_id}: {str(e)}")
        return jsonify({"error": str(e)}), 500


@app.route('/confirm-content/<int:booking_id>', methods=['PUT'])
def confirm_content(booking_id):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # ✅ Update content status to "Published"
        cursor.execute("UPDATE bookings SET content_status = 'Published' WHERE id = %s RETURNING id", (booking_id,))
        updated_booking = cursor.fetchone()

        if not updated_booking:
            return jsonify({"error": "Booking not found"}), 404

        conn.commit()
        cursor.close()
        conn.close()

        return jsonify({"message": "Content marked as published", "booking_id": booking_id}), 200

    except Exception as e:
        app.logger.error(f"Error confirming content: {e}")
        return jsonify({"error": "Internal server error"}), 500

# ✅ Creator submits content
@app.route('/submit-content/<int:booking_id>', methods=['POST'])
def submit_content(booking_id):
    try:
        creator_id = session.get('creator_id')
        if not creator_id:
            app.logger.error("Unauthorized: No creator_id in session")
            return jsonify({"error": "Unauthorized: Please log in as a creator"}), 403

        content_link = request.form.get('content_link')
        submission_notes = request.form.get('submission_notes', '')
        file = request.files.get('file')

        if not content_link and not file:
            app.logger.error(f"No content link or file provided for booking {booking_id}")
            return jsonify({"error": "Please provide either a content link or a file"}), 400

        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        cursor.execute('''
            SELECT b.status, b.content_status, b.creator_id, b.brand_id, b.product_name,
                   c.user_id AS creator_user_id, br.user_id AS brand_user_id, c.username AS creator_username
            FROM bookings b
            JOIN creators c ON b.creator_id = c.id
            JOIN brands br ON b.brand_id = br.id
            WHERE b.id = %s AND b.creator_id = %s
        ''', (booking_id, creator_id))
        booking = cursor.fetchone()

        if not booking:
            conn.close()
            app.logger.error(f"Booking {booking_id} not found for creator {creator_id}")
            return jsonify({"error": "Booking not found or you are not authorized to submit content for this booking"}), 404

        file_url = None
        if file:
            if not allowed_file(file.filename):
                conn.close()
                app.logger.error(f"Invalid file type: {file.filename}")
                return jsonify({"error": "Invalid file type. Allowed types: png, jpg, jpeg, pdf, txt, mp4, mov, webm, avi"}), 400
            file_url = upload_file_to_supabase(file, "creators")
            if not file_url:
                conn.close()
                app.logger.error("Failed to upload file to Supabase")
                return jsonify({"error": "Failed to upload file. Please try again."}), 500

        current_status = booking.get('content_status') or booking.get('status')
        if current_status in ['Confirmed', 'Revision Requested']:
            new_status = "Draft Submitted"
        elif current_status == 'Approved':
            new_status = "Published"
        else:
            conn.close()
            app.logger.error(f"Cannot submit content for booking {booking_id} in status {current_status}")
            return jsonify({"error": f"Cannot submit content in the current status: {current_status}"}), 400

        cursor.execute(''' 
            UPDATE bookings 
            SET content_status = %s, content_link = %s, content_file_url = %s, 
                submission_notes = %s, updated_at = NOW()
            WHERE id = %s
            RETURNING updated_at
        ''', (new_status, content_link, file_url, submission_notes, booking_id))

        updated_booking = cursor.fetchone()
        if not updated_booking:
            conn.rollback()
            conn.close()
            app.logger.error(f"Failed to update booking {booking_id}")
            return jsonify({"error": "Failed to update booking. Please try again."}), 500

        # Notify creator
        try:
            create_notification(
                user_id=booking['creator_user_id'],
                user_role='creator',
                event_type='CONTENT_SUBMITTED',
                data={
                    'booking_id': booking_id,
                    'product_name': booking['product_name'],
                    'status': new_status
                },
                should_send_email=True
            )
        except Exception as e:
            app.logger.error(f"Failed to send creator notification for booking {booking_id}: {str(e)}")
            # Continue execution despite notification failure

        # Notify brand
        try:
            create_notification(
                user_id=booking['brand_user_id'],
                user_role='brand',
                event_type='CONTENT_SUBMITTED',
                data={
                    'booking_id': booking_id,
                    'product_name': booking['product_name'],
                    'creator_username': booking['creator_username'],
                    'status': new_status
                },
                should_send_email=True
            )
        except Exception as e:
            app.logger.error(f"Failed to send brand notification for booking {booking_id}: {str(e)}")
            # Continue execution despite notification failure

        conn.commit()
        app.logger.info(f"✅ Content submitted for booking {booking_id} by creator {creator_id}, status: {new_status}")
        conn.close()
        return jsonify({
            "message": f"Content submitted successfully, status updated to {new_status}",
            "updated_at": updated_booking['updated_at']
        }), 200
    except Exception as e:
        app.logger.error(f"🔥 Error submitting content for booking {booking_id}: {str(e)}")
        if 'conn' in locals():
            conn.rollback()
            conn.close()
        return jsonify({"error": f"An error occurred: {str(e)}. Please try again or contact support."}), 500

# ✅ Brand approves content
@app.route('/approve-content/<int:booking_id>', methods=['POST'])
def approve_content(booking_id):
    try:
        brand_id = session.get('brand_id')
        if not brand_id:
            app.logger.error("Unauthorized: No brand_id in session")
            return jsonify({"error": "Unauthorized"}), 403

        data = request.json or {}
        app.logger.debug(f"Received review request for booking {booking_id}: {data}")
        action = data.get('action', 'approve')
        revision_notes = data.get('revision_notes', '')

        if action not in ['approve', 'request_revision']:
            app.logger.error(f"Invalid action for booking {booking_id}: {action}")
            return jsonify({"error": "Invalid action"}), 400

        if action == 'request_revision' and not revision_notes:
            app.logger.error(f"Revision notes required for request-revision on booking {booking_id}")
            return jsonify({"error": "Revision notes are required when requesting revisions"}), 400

        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Fetch booking details with user IDs
        cursor.execute('''
            SELECT b.id, b.brand_id, b.creator_id, b.content_status, b.product_name,
                   c.user_id AS creator_user_id, br.user_id AS brand_user_id,
                   c.username AS creator_username
            FROM bookings b
            JOIN creators c ON b.creator_id = c.id
            JOIN brands br ON b.brand_id = br.id
            WHERE b.id = %s AND b.brand_id = %s
        ''', (booking_id, brand_id))
        booking = cursor.fetchone()

        if not booking:
            conn.close()
            app.logger.error(f"Booking {booking_id} not found or not authorized for brand {brand_id}")
            return jsonify({"error": "Booking not found or not authorized"}), 404

        # Allow only "Submitted" or "Draft Submitted" for review
        if booking['content_status'] not in ['Submitted', 'Draft Submitted']:
            conn.close()
            app.logger.error(f"Cannot review content for booking {booking_id} in status {booking['content_status']}")
            return jsonify({"error": "Content cannot be reviewed in the current status"}), 400

        # Determine new status
        new_status = 'Approved' if action == 'approve' else 'Revision Requested'

        # Update booking
        cursor.execute('''
            UPDATE bookings 
            SET status = %s,  -- Sync status with content_status
                content_status = %s,
                revision_notes = %s,
                updated_at = NOW()
            WHERE id = %s
            RETURNING updated_at
        ''', (new_status, new_status, revision_notes if action == 'request_revision' else None, booking_id))

        updated_booking = cursor.fetchone()
        if not updated_booking:
            conn.rollback()
            conn.close()
            app.logger.error(f"Failed to update booking {booking_id}")
            return jsonify({"error": "Booking not found"}), 404

         # Send notifications
        if action == 'approve':
            app.logger.info(f"Preparing approval notifications for booking {booking_id}")
            # Notify creator
            try:
                create_notification(
                    user_id=booking['creator_user_id'],
                    user_role='creator',
                    event_type='CONTENT_APPROVED',
                    data={
                        'booking_id': booking_id,
                        'product_name': booking['product_name']
                    },
                    should_send_email=True
                )
            except Exception as e:
                app.logger.error(f"Failed to send creator notification for booking {booking_id}: {str(e)}")
                # Continue execution despite notification failure

            # Notify brand
            try:
                create_notification(
                    user_id=booking['brand_user_id'],
                    user_role='brand',
                    event_type='CONTENT_APPROVED_CONFIRMATION',
                    data={
                        'booking_id': booking_id,
                        'product_name': booking['product_name'],
                        'creator_username': booking['creator_username']
                    },
                    should_send_email=True
                )
            except Exception as e:
                app.logger.error(f"Failed to send brand notification for booking {booking_id}: {str(e)}")
                # Continue execution despite notification failure
        elif action == 'request_revision':
            app.logger.info(f"Preparing revision notification for booking {booking_id}, creator_user_id: {booking['creator_user_id']}")
            if not booking['creator_user_id']:
                app.logger.error(f"No creator_user_id found for booking {booking_id}")
            else:
                # Notify creator
                try:
                    create_notification(
                        user_id=booking['creator_user_id'],
                        user_role='creator',
                        event_type='CONTENT_REVISION_REQUESTED',
                        data={
                            'booking_id': booking_id,
                            'product_name': booking['product_name'],
                            'revision_notes': revision_notes
                        },
                        should_send_email=True
                    )
                except Exception as e:
                    app.logger.error(f"Failed to send creator revision notification for booking {booking_id}: {str(e)}")
                    # Continue execution despite notification failure

        conn.commit()
        app.logger.info(f"✅ Booking {booking_id} content {action}d: new_status={new_status}")
        conn.close()
        return jsonify({"message": f"Content {action}d"}), 200
    except Exception as e:
        app.logger.error(f"🔥 Error processing content for booking {booking_id}: {str(e)}")
        if 'conn' in locals():
            conn.rollback()
            conn.close()
        return jsonify({"error": str(e)}), 500


# ❌ Brand rejects content
@app.route('/reject-content/<int:booking_id>', methods=['POST'])
def reject_content(booking_id):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # Update content_status to "Rejected"
        cursor.execute('''
            UPDATE bookings
            SET content_status = 'Rejected'
            WHERE id = %s
        ''', (booking_id,))

        conn.commit()
        cursor.close()
        conn.close()

        return jsonify({"message": "Content rejected, creator needs to resubmit"}), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/release-payment/<int:booking_id>', methods=['POST'])
def release_payment(booking_id):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # ✅ Fetch booking details
        cursor.execute("SELECT creator_id, payment_hold_status FROM bookings WHERE id = %s", (booking_id,))
        booking = cursor.fetchone()

        if not booking:
            return jsonify({"error": "Booking not found"}), 404
        
        creator_id, payment_hold_status = booking

        if payment_hold_status != "On Hold":
            return jsonify({"error": "Payment has already been released"}), 400

        # ✅ Update payment status to "Released"
        cursor.execute("UPDATE bookings SET payment_hold_status = 'Released' WHERE id = %s", (booking_id,))
        conn.commit()

        cursor.close()
        conn.close()

        return jsonify({"message": "Payment released to creator", "creator_id": creator_id}), 200

    except Exception as e:
        app.logger.error(f"Error releasing payment: {e}")
        return jsonify({"error": "Internal server error"}), 500

@app.route('/bookings/<int:booking_id>', methods=['PUT'])
def update_booking(booking_id):
    try:
        data = request.get_json()
        app.logger.info(f"📌 Updating booking ID {booking_id} with data: {data}")

        # Validate session
        if 'user_id' not in session or 'brand_id' not in session:
            app.logger.warning(f"Unauthorized update attempt: session={dict(session)}")
            return jsonify({'error': 'Unauthorized'}), 403

        # Validate required fields
        required_fields = ['payment_method', 'payment_status']
        missing_fields = [field for field in required_fields if field not in data or data[field] is None]
        if missing_fields:
            return jsonify({'error': f'Missing required fields: {", ".join(missing_fields)}'}), 400

        # Extract fields
        transaction_id = data.get('transaction_id')
        payment_method = data['payment_method']
        payment_status = data['payment_status']
        status = data.get('status', 'Confirmed')
        content_status = data.get('content_status', 'Confirmed')

        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Verify booking exists and belongs to the brand
        cursor.execute('SELECT * FROM bookings WHERE id = %s AND brand_id = %s', (booking_id, session['brand_id']))
        booking = cursor.fetchone()
        if not booking:
            conn.close()
            return jsonify({'error': 'Booking not found or unauthorized'}), 404

        # Update booking
        cursor.execute(
            '''
            UPDATE bookings
            SET 
                transaction_id = %s,
                payment_method = %s,
                payment_status = %s,
                status = %s,
                content_status = %s,
                updated_at = NOW()
            WHERE id = %s
            RETURNING *
            ''',
            (transaction_id, payment_method, payment_status, status, content_status, booking_id)
        )
        updated_booking = cursor.fetchone()

        if not updated_booking:
            conn.close()
            return jsonify({'error': 'Failed to update booking'}), 500

        conn.commit()
        conn.close()

        app.logger.info(f"✅ Booking {booking_id} updated successfully: {updated_booking}")
        return jsonify(updated_booking), 200
    except Exception as e:
        app.logger.error(f"🔥 Error updating booking {booking_id}: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/bookings/<int:booking_id>/status', methods=['PUT'])
def update_booking_status(booking_id):
    try:
        user_id = session.get('user_id')
        user_role = session.get('user_role')
        if not user_id or not user_role:
            app.logger.error("No user_id or user_role in session")
            return jsonify({"error": "Unauthorized"}), 403

        data = request.json
        new_status = data.get("status")
        content_file_url = data.get("content_file_url")  # For content submission

        valid_statuses = [
            "Pending", "Confirmed", "In Progress", "Draft Submitted",
            "Under Review", "Revision Requested", "Approved", "Published",
            "Completed", "Canceled"
        ]
        if new_status and new_status not in valid_statuses:
            app.logger.error(f"Invalid status: {new_status}, expected one of {valid_statuses}")
            return jsonify({"error": "Invalid status"}), 400

        if not (new_status or content_file_url):
            app.logger.error("No valid fields provided for booking status update")
            return jsonify({"error": "Status or content_file_url is required"}), 400

        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Fetch booking details
        cursor.execute(
            '''
            SELECT b.*, c.user_id AS creator_user_id, br.user_id AS brand_user_id
            FROM bookings b
            JOIN creators c ON b.creator_id = c.id
            JOIN brands br ON b.brand_id = br.id
            WHERE b.id = %s
            ''',
            (booking_id,)
        )
        booking = cursor.fetchone()
        if not booking:
            conn.close()
            app.logger.error(f"Booking {booking_id} not found")
            return jsonify({"error": "Booking not found"}), 404

        # Authorize user
        if user_role == 'creator' and str(booking['creator_user_id']) != str(user_id):
            conn.close()
            app.logger.error(f"Unauthorized: creator_user_id={booking['creator_user_id']} does not match user_id={user_id}")
            return jsonify({"error": "Unauthorized"}), 403
        if user_role == 'brand' and str(booking['brand_user_id']) != str(user_id):
            conn.close()
            app.logger.error(f"Unauthorized: brand_user_id={booking['brand_user_id']} does not match user_id={user_id}")
            return jsonify({"error": "Unauthorized"}), 403

        # Prepare update query
        update_fields = []
        update_values = []
        notification_message = None

        if new_status:
            update_fields.append("status = %s")
            update_fields.append("content_status = %s")
            update_values.extend([new_status, new_status])
            notification_message = f"Booking for {booking['product_name']} updated to {new_status}"
        if content_file_url:
            update_fields.append("content_file_url = %s")
            update_values.append(content_file_url)
            # Set status to "Draft Submitted" if not explicitly provided
            if not new_status:
                new_status = "Draft Submitted"
                update_fields.append("status = %s")
                update_fields.append("content_status = %s")
                update_values.extend([new_status, new_status])
            notification_message = f"New content submitted for booking #{booking_id}"

        update_fields.append("updated_at = %s")
        update_values.append(datetime.datetime.now())

        update_query = f'''
            UPDATE bookings
            SET {', '.join(update_fields)}
            WHERE id = %s
            RETURNING id, status, content_status, updated_at
        '''
        update_values.append(booking_id)

        cursor.execute(update_query, update_values)
        updated_booking = cursor.fetchone()

        if not updated_booking:
            conn.rollback()
            conn.close()
            app.logger.error(f"Failed to update booking {booking_id}")
            return jsonify({"error": "Failed to update booking"}), 500

        # Notify creator
        if notification_message:
            create_notification(
                user_id=booking['creator_user_id'],
                user_role='creator',
                event_type='BOOKING_STEP_UPDATE',
                message=notification_message,
                data={'booking_id': booking_id, 'status': new_status or 'Draft Submitted'},
                should_send_email=True
            )
            # Notify brand
            create_notification(
                user_id=booking['brand_user_id'],
                user_role='brand',
                event_type='BOOKING_STEP_UPDATE',
                message=notification_message,
                data={'booking_id': booking_id, 'status': new_status or 'Draft Submitted'},
                should_send_email=True
            )

        conn.commit()
        conn.close()

        logger.info(f"Booking {booking_id} updated successfully to status '{new_status or 'Draft Submitted'}'")
        return jsonify({
            "message": f"Booking updated successfully",
            "booking_id": updated_booking['id'],
            "status": updated_booking['status'],
            "content_status": updated_booking['content_status'],
            "updated_at": updated_booking['updated_at'].isoformat()
        }), 200
    except Exception as e:
        logger.error(f"Error updating booking {booking_id}: {str(e)}")
        if 'conn' in locals():
            conn.rollback()
            conn.close()
        return jsonify({"error": str(e)}), 500


@app.route('/bookings', methods=['GET'])
def get_bookings():
    try:
        creator_id = request.args.get('creator_id')
        brand_id = request.args.get('brand_id')
        status = request.args.get('status')
        search = request.args.get('search', '').strip()
        start_date = request.args.get('start_date')
        end_date = request.args.get('end_date')

        user_role = session.get('user_role')
        session_creator_id = session.get('creator_id')
        session_brand_id = session.get('brand_id')

        app.logger.debug(f"Session data: {dict(session)}")

        if not user_role:
            app.logger.error("Unauthorized: No user role in session")
            return jsonify({'error': 'Unauthorized: No user role in session'}), 403

        # Validate brand_id only for brand role
        if user_role == 'brand':
            if brand_id:
                if brand_id == 'undefined' or not brand_id.isdigit():
                    app.logger.warning(f"Invalid brand_id: {brand_id}")
                    return jsonify({'error': 'Invalid brand_id'}), 400
                brand_id = int(brand_id)
            elif session_brand_id:
                brand_id = session_brand_id
            else:
                app.logger.warning("No valid brand_id provided and no session brand_id")
                return jsonify({'error': 'Brand ID required'}), 400

        # Validate creator_id for creator role
        if user_role == 'creator':
            if creator_id:
                if creator_id == 'undefined' or not creator_id.isdigit():
                    app.logger.warning(f"Invalid creator_id: {creator_id}")
                    return jsonify({'error': 'Invalid creator_id'}), 400
                creator_id = int(creator_id)
            elif session_creator_id:
                creator_id = session_creator_id
            else:
                app.logger.warning("No valid creator_id provided and no session creator_id")
                return jsonify({'error': 'Creator ID required'}), 400

        valid_statuses = ["Pending", "Confirmed", "In Progress", "Draft Submitted", "Under Review", "Revision Requested", "Approved", "Published", "Completed", "Canceled"]
        if status and status not in valid_statuses:
            app.logger.error(f"Invalid status: {status}. Valid options: {valid_statuses}")
            return jsonify({'error': f"Invalid status. Valid options: {', '.join(valid_statuses)}"}), 400

        conn = get_db_connection()
        if not conn:
            app.logger.error("Database connection failed")
            return jsonify({'error': 'Database connection failed'}), 500
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        unread_count_condition = (
            "AND m.sender_type = 'creator'" if user_role == 'brand'
            else "AND m.sender_type = 'brand'" if user_role == 'creator'
            else ""
        )

        query = ''' 
            SELECT b.*, 
                   COALESCE(b.bid_amount, 0.0) AS bid_amount, 
                   COALESCE(b.type, 'Sponsor') AS type, 
                   b.price, 
                   c.username AS creator_name, 
                   c.image_profile AS creator_profile, 
                   p.package_name AS offer_name,
                   p.price AS offer_price, 
                   p.platform,
                   br.name AS brand_name,
                   br.logo AS brand_logo,
                   b.content_file_url AS file_url, 
                   b.submission_notes, 
                   b.revision_notes,
                   sd.description, 
                   sd.platforms, 
                   sd.audience_target AS audience_targets, 
                   sd.topics,
                   sd.bidding_deadline,
                   COALESCE((
                       SELECT COUNT(*) 
                       FROM messages m 
                       WHERE m.booking_id = b.id 
                       AND m.is_read = FALSE %s
                   ), 0) AS unread_count
            FROM bookings b
            LEFT JOIN creators c ON b.creator_id = c.id
            LEFT JOIN packages p ON b.offer_id = p.id
            LEFT JOIN brands br ON b.brand_id = br.id
            LEFT JOIN sponsor_drafts sd ON b.draft_id = sd.id
            WHERE b.type != 'Campaign Invite'
        ''' % unread_count_condition
        params = []

        if creator_id:
            query += ' AND b.creator_id = %s'
            params.append(creator_id)
        elif user_role == 'creator' and session_creator_id:
            query += ' AND b.creator_id = %s'
            params.append(session_creator_id)

        if brand_id:
            query += ' AND b.brand_id = %s'
            params.append(brand_id)
        elif user_role == 'brand' and session_brand_id:
            query += ' AND b.brand_id = %s'
            params.append(session_brand_id)

        if status:
            query += ' AND b.status = %s'
            params.append(status)

        if search:
            query += ''' 
                AND (c.username ILIKE %s OR p.package_name ILIKE %s OR br.name ILIKE %s)
            '''
            search_pattern = f"%{search}%"
            params.extend([search_pattern, search_pattern, search_pattern])

        if start_date and end_date:
            query += ' AND b.promotion_date BETWEEN %s AND %s'
            params.append(start_date)
            params.append(end_date)

        app.logger.debug(f"Executing bookings query: {query} with params: {params}")
        cursor.execute(query, params)
        bookings = cursor.fetchall()

        for booking in bookings:
            if 'updated_at' not in booking or booking['updated_at'] is None:
                booking['updated_at'] = booking.get('created_at') or datetime.now().isoformat()
                app.logger.warning(f"Booking {booking['id']} missing updated_at, using fallback: {booking['updated_at']}")
            # Safely convert bid_amount to float
            try:
                booking['bid_amount'] = float(booking['bid_amount'] or 0.0)
            except (ValueError, TypeError) as e:
                app.logger.warning(f"Invalid bid_amount for booking {booking['id']}: {booking['bid_amount']}, error: {str(e)}")
                booking['bid_amount'] = 0.0

        # Subscription handling for creators
        if user_role == 'creator' and creator_id:
            cursor.execute(''' 
                SELECT bs.id, bs.start_date, bs.end_date, bs.status, bs.total_cost, bs.duration_months,
                       bs.brand_id AS brand_id, bs.transaction_id, bs.payment_method,
                       csp.package_name, csp.deliverables AS base_deliverables, csp.frequency,
                       br.name AS brand_name, br.logo AS brand_logo,
                       COALESCE((
                           SELECT COUNT(*) 
                           FROM messages m 
                           WHERE m.subscription_id = bs.id 
                           AND m.is_read = FALSE 
                           AND m.sender_type = 'brand'
                       ), 0) AS unread_count,
                       (SELECT MAX(updated_at) 
                        FROM subscription_deliverables sd 
                        WHERE sd.subscription_id = bs.id) AS latest_deliverable_update
                FROM brand_subscriptions bs
                JOIN creator_subscription_packages csp ON bs.package_id = csp.id
                JOIN brands br ON bs.brand_id = br.id
                WHERE csp.creator_id = %s AND bs.status IN ('pending', 'active')
            ''', (creator_id,))
            subscriptions = cursor.fetchall()

            for sub in subscriptions:
                cursor.execute(''' 
                    SELECT type, platform, quantity, status, submission_index,
                           content_link, file_url, submission_notes
                    FROM subscription_deliverables
                    WHERE subscription_id = %s AND creator_id = %s
                    ORDER BY submission_index
                ''', (sub['id'], creator_id))
                submitted = cursor.fetchall()

                base_deliverables = sub.get('base_deliverables', []) or []
                if not isinstance(base_deliverables, list):
                    app.logger.warning(f"Invalid base_deliverables for subscription {sub['id']}: {base_deliverables}")
                    base_deliverables = []

                deliverable_status = {}
                for d in submitted:
                    key = (d.get('type', 'Unknown'), d.get('platform', 'Unknown'))
                    if key not in deliverable_status:
                        deliverable_status[key] = []
                    deliverable_status[key].append({
                        "type": d.get('type', 'Unknown'),
                        "platform": d.get('platform', 'Unknown'),
                        "status": d.get('status', 'Pending'),
                        "submission_index": d.get('submission_index', 0),
                        "content_link": d.get('content_link'),
                        "file_url": d.get('file_url'),
                        "submission_notes": d.get('submission_notes')
                    })

                deliverables = []
                for i, base in enumerate(base_deliverables):
                    key = (base.get('type', 'Unknown'), base.get('platform', 'Unknown'))
                    submitted_list = deliverable_status.get(key, [])
                    total_submitted = len(submitted_list)
                    quantity = base.get('quantity', 0)
                    remaining = quantity - total_submitted
                    status = "Delivered" if remaining <= 0 else ("Submitted" if total_submitted > 0 else "Pending")
                    deliverables.append({
                        "index": i,
                        "type": base.get('type', 'Unknown'),
                        "platform": base.get('platform', 'Unknown'),
                        "quantity": quantity,
                        "submitted": total_submitted,
                        "remaining": max(0, remaining),
                        "status": status,
                        "submissions": submitted_list
                    })

                sub['type'] = 'Subscription'
                sub['cost'] = float(sub['total_cost'] / (sub['duration_months'] or 1)) if sub.get('total_cost') else 0.0
                sub['deliverables'] = deliverables
                sub['updated_at'] = (
                    sub.get('latest_deliverable_update') or
                    sub.get('start_date') or
                    datetime.now().isoformat()
                )
                bookings.append(sub)

        # Subscription handling for brands (unchanged)
        elif user_role == 'brand' and brand_id:
            cursor.execute(''' 
                SELECT bs.id, bs.start_date, bs.end_date, bs.status, bs.total_cost, bs.duration_months,
                       bs.brand_id AS brand_id, bs.transaction_id, bs.payment_method,
                       csp.package_name, csp.deliverables AS base_deliverables, csp.frequency,
                       c.username AS creator_name, c.id AS creator_id, c.image_profile AS creator_profile,
                       COALESCE((
                           SELECT COUNT(*) 
                           FROM messages m 
                           WHERE m.subscription_id = bs.id 
                           AND m.is_read = FALSE 
                           AND m.sender_type = 'creator'
                       ), 0) AS unread_count,
                       (SELECT MAX(updated_at) 
                        FROM subscription_deliverables sd 
                        WHERE sd.subscription_id = bs.id) AS latest_deliverable_update
                FROM brand_subscriptions bs
                JOIN creator_subscription_packages csp ON bs.package_id = csp.id
                JOIN creators c ON csp.creator_id = c.id
                WHERE bs.brand_id = %s AND bs.status IN ('pending', 'active')
            ''', (brand_id,))
            subscriptions = cursor.fetchall()

            for sub in subscriptions:
                cursor.execute(''' 
                    SELECT type, platform, quantity, status, submission_index, content_link, file_url, submission_notes
                    FROM subscription_deliverables
                    WHERE subscription_id = %s
                    ORDER BY submission_index
                ''', (sub['id'],))
                submitted = cursor.fetchall()

                base_deliverables = sub.get('base_deliverables', []) or []
                if not isinstance(base_deliverables, list):
                    app.logger.warning(f"Invalid base_deliverables for subscription {sub['id']}: {base_deliverables}")
                    base_deliverables = []

                deliverable_status = {}
                for d in submitted:
                    key = (d.get('type', 'Unknown'), d.get('platform', 'Unknown'))
                    if key not in deliverable_status:
                        deliverable_status[key] = []
                    deliverable_status[key].append({
                        "type": d.get('type', 'Unknown'),
                        "platform": d.get('platform', 'Unknown'),
                        "status": d.get('status', 'Pending'),
                        "submission_index": d.get('submission_index', 0),
                        "content_link": d.get('content_link'),
                        "file_url": d.get('file_url'),
                        "submission_notes": d.get('submission_notes')
                    })

                deliverables = []
                for i, base in enumerate(base_deliverables):
                    key = (base.get('type', 'Unknown'), base.get('platform', 'Unknown'))
                    submitted_list = deliverable_status.get(key, [])
                    total_submitted = len(submitted_list)
                    quantity = base.get('quantity', 0)
                    remaining = quantity - total_submitted
                    status = "Delivered" if remaining <= 0 else ("Submitted" if total_submitted > 0 else "Pending")
                    deliverables.append({
                        "index": i,
                        "type": base.get('type', 'Unknown'),
                        "platform": base.get('platform', 'Unknown'),
                        "quantity": quantity,
                        "submitted": total_submitted,
                        "remaining": max(0, remaining),
                        "status": status,
                        "submissions": submitted_list
                    })

                sub['type'] = 'Subscription'
                sub['cost'] = float(sub['total_cost'] / (sub['duration_months'] or 1)) if sub.get('total_cost') else 0.0
                sub['deliverables'] = deliverables
                sub['updated_at'] = (
                    sub.get('latest_deliverable_update') or
                    sub.get('start_date') or
                    datetime.now().isoformat()
                )
                bookings.append(sub)

        app.logger.info(f"Bookings fetched: {len(bookings)} items: {[b['id'] for b in bookings]}")
        return jsonify(bookings), 200
    except Exception as e:
        app.logger.error(f"Error in get_bookings: {str(e)}")
        if 'conn' in locals() and not conn.closed:
            conn.close()
        return jsonify({'error': 'Internal server error', 'details': str(e)}), 500


@app.route('/bookings/<int:booking_id>', methods=['GET'])
def get_booking(booking_id):
    try:
        user_role = session.get('user_role')
        creator_id = session.get('creator_id')
        brand_id = session.get('brand_id')

        if not user_role:
            app.logger.error(f"Unauthorized access to booking {booking_id}: No user role in session")
            return jsonify({"error": "Unauthorized: No user role in session"}), 403

        if user_role not in ['creator', 'brand']:
            app.logger.error(f"Unauthorized access to booking {booking_id}: Invalid role {user_role}")
            return jsonify({"error": "Unauthorized: Invalid user role"}), 403

        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        cursor.execute(''' 
                SELECT b.*,
                   COALESCE(b.bid_amount, 0) AS bid_amount,
                   COALESCE(b.platform_fee, b.bid_amount * 0.15, 0) AS platform_fee,
                   br.name AS brand_name,
                   br.logo AS brand_logo,
                   c.username AS creator_name,
                   c.image_profile AS creator_profile,
                   p.package_name AS offer_name,
                   p.price AS offer_price,
                   p.platform AS offer_platform,
                   sd.platforms AS sponsor_platforms,
                   b.platforms AS booking_platforms
            FROM bookings b
            LEFT JOIN brands br ON b.brand_id = br.id
            LEFT JOIN creators c ON b.creator_id = c.id
            LEFT JOIN packages p ON b.offer_id = p.id
            LEFT JOIN sponsor_drafts sd ON b.draft_id = sd.id
            WHERE b.id = %s
        ''', (booking_id,))
        booking = cursor.fetchone()

        if not booking:
            conn.close()
            app.logger.error(f"Booking {booking_id} not found")
            return jsonify({"error": "Booking not found"}), 404

        # Authorization check
        if user_role == 'creator' and booking['creator_id'] != creator_id:
            conn.close()
            app.logger.error(f"Creator {creator_id} not authorized for booking {booking_id}")
            return jsonify({"error": "Unauthorized: Booking does not belong to this creator"}), 403
        elif user_role == 'brand' and booking['brand_id'] != brand_id:
            conn.close()
            app.logger.error(f"Brand {brand_id} not authorized for booking {booking_id}")
            return jsonify({"error": "Unauthorized: Booking does not belong to this brand"}), 403

        # Normalize platforms
        platforms = []
        if booking['booking_platforms']:
            try:
                platforms = (
                    json.loads(booking['booking_platforms'])
                    if isinstance(booking['booking_platforms'], str)
                    else booking['booking_platforms']
                )
            except json.JSONDecodeError:
                app.logger.warning(f"Failed to parse booking_platforms for booking {booking_id}")
                platforms = []
        elif booking['sponsor_platforms']:
            try:
                platforms = (
                    json.loads(booking['sponsor_platforms'])
                    if isinstance(booking['sponsor_platforms'], str)
                    else booking['sponsor_platforms']
                )
            except json.JSONDecodeError:
                app.logger.warning(f"Failed to parse sponsor_platforms for booking {booking_id}")
                platforms = []
        elif booking['offer_platform']:
            platforms = [booking['offer_platform']]

        booking['platforms'] = platforms
        booking['net_earnings'] = (
            str(round(float(booking['bid_amount']) - float(booking['platform_fee']), 2))
            if booking['bid_amount'] != 0 and float(booking['bid_amount']) >= float(booking['platform_fee'])
            else "0.00"
        )
        del booking['booking_platforms']
        del booking['sponsor_platforms']

        conn.close()
        app.logger.info(f"Booking {booking_id} fetched successfully for {user_role} (ID: {creator_id or brand_id})")
        return jsonify(booking), 200
    except Exception as e:
        app.logger.error(f"Error fetching booking {booking_id}: {str(e)}")
        if 'conn' in locals() and not conn.closed:
            conn.close()
        return jsonify({"error": str(e)}), 500

# 💰 Create Payment (PayPal)
@app.route('/create-paypal-payment', methods=['POST', 'OPTIONS'])
def create_paypal_payment():
    if request.method == 'OPTIONS':
        response = jsonify({"message": "CORS preflight successful"})
        response.headers['Access-Control-Allow-Origin'] = 'http://localhost:3000'
        response.headers['Access-Control-Allow-Credentials'] = 'true'
        response.headers['Access-Control-Allow-Methods'] = 'POST, OPTIONS'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization'
        return response, 200

    try:
        brand_id = session.get('brand_id')
        app.logger.debug(f"Session data: {dict(session)}")
        if not brand_id:
            app.logger.error("No brand ID in session")
            return jsonify({"error": "Unauthorized: No brand ID in session"}), 403

        data = request.json
        app.logger.debug(f"Received payload for PayPal payment: {data}")
        amount = data.get("amount")  # In cents
        booking_id = data.get("booking_id")
        creator_id = data.get("creator_id")
        booking_type = data.get("booking_type")
        return_url = data.get("return_url")
        cancel_url = data.get("cancel_url")
        payment_method = data.get("payment_method")

        # Validate required fields
        required_fields = ['amount', 'booking_id', 'creator_id', 'booking_type', 'return_url', 'cancel_url', 'payment_method']
        missing = [k for k in required_fields if data.get(k) is None]
        if missing:
            app.logger.error(f"Missing required parameters: {missing}, received payload: {data}")
            return jsonify({"error": f"Missing required parameters: {', '.join(missing)}"}), 400

        if payment_method != 'paypal':
            app.logger.error(f"Invalid payment method: {payment_method}")
            return jsonify({"error": f"Invalid payment method: expected 'paypal', got '{payment_method}'"}), 400

        if not isinstance(amount, (int, float)) or amount <= 0:
            app.logger.error(f"Invalid amount: {amount}")
            return jsonify({"error": "Amount must be a positive number"}), 400

        if booking_type not in ['Sponsor', 'Campaign Invite']:
            app.logger.error(f"Invalid booking type: {booking_type}")
            return jsonify({"error": f"Invalid booking type: expected 'Sponsor' or 'Campaign Invite', got '{booking_type}'"}), 400

        conn = get_db_connection()
        conn.autocommit = False
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        try:
            # Verify booking exists and is in correct state
            cursor.execute(
                '''
                SELECT id, brand_id, status, content_status, payment_status, price, type, transaction_id, bid_amount
                FROM bookings 
                WHERE id = %s AND brand_id = %s
                ''',
                (booking_id, brand_id)
            )
            booking = cursor.fetchone()
            if not booking:
                conn.rollback()
                app.logger.error(f"Booking {booking_id} not found or not authorized for brand {brand_id}")
                return jsonify({"error": "Booking not found or not authorized"}), 404
            
            # Validate content_status for non-subscription bookings
            if booking['type'] != 'Subscription' and booking['content_status'] != 'Published':
                conn.rollback()
                app.logger.error(f"Cannot initiate payment for booking {booking_id} in content_status {booking['content_status']}")
                return jsonify({"error": f"Payment can only be initiated for Published bookings, current status: {booking['content_status']}"}), 400

            # Log current state for debugging
            app.logger.debug(f"Booking {booking_id} current state: type={booking['type']}, status={booking['status']}, content_status={booking['content_status']}, payment_status={booking['payment_status']}")

            # Check payment status
            if booking['payment_status'] == 'Completed':
                conn.rollback()
                app.logger.error(f"Payment already completed for booking {booking_id}")
                return jsonify({"error": "Payment already completed"}), 400
            
            # Allow On Hold or Pending with transaction verification
            if booking['payment_status'] not in ['On Hold', 'Pending']:
                conn.rollback()
                app.logger.error(f"Payment not in On Hold or Pending state for booking {booking_id}: {booking['payment_status']}")
                return jsonify({"error": f"Payment status must be On Hold or Pending, current: {booking['payment_status']}"}), 400
            
            # Verify transaction status if Pending
            if booking['payment_status'] == 'Pending' and booking['transaction_id']:
                try:
                    payment = paypalrestsdk.Payment.find(booking['transaction_id'])
                    if payment.state == 'approved':
                        conn.rollback()
                        app.logger.error(f"Payment already completed for booking {booking_id}: {booking['transaction_id']}")
                        return jsonify({"error": "Payment already completed"}), 400
                    elif payment.state in ['created', 'pending']:
                        approval_url = next(link.href for link in payment.links if link.rel == "approval_url")
                        app.logger.debug(f"Returning existing approval_url for booking {booking_id}: {approval_url}")
                        conn.commit()
                        cursor.close()
                        conn.close()
                        return jsonify({"payment_id": payment.id, "approval_url": approval_url}), 200
                except paypalrestsdk.exceptions.ResourceNotFound:
                    app.logger.warning(f"Invalid transaction_id for booking {booking_id}: {booking['transaction_id']}, resetting")
                    cursor.execute(
                        '''
                        UPDATE bookings
                        SET transaction_id = NULL,
                            payment_status = 'On Hold',
                            updated_at = NOW()
                        WHERE id = %s AND brand_id = %s
                        ''',
                        (booking_id, brand_id)
                    )
                    conn.commit()
                except Exception as e:
                    conn.rollback()
                    app.logger.error(f"Error verifying PayPal payment for booking {booking_id}: {str(e)}")
                    return jsonify({"error": f"Error verifying payment status: {str(e)}"}), 500

            # Validate amount
            expected_amount = booking['bid_amount'] if booking['bid_amount'] is not None else booking['price']
            if expected_amount is None or int(expected_amount * 100) != amount:
                conn.rollback()
                app.logger.error(f"Amount mismatch: Expected {expected_amount} vs Received {amount/100}")
                return jsonify({"error": f"Amount mismatch: expected {expected_amount}, got {amount/100}"}), 400

            # Calculate commission (15%)
            commission_rate = 0.15
            commission_amount = int(amount * commission_rate)  # In cents
            net_amount = (amount - commission_amount) / 100.0  # In euros
            total_amount = amount / 100.0  # In euros

            # Fetch creator's email from users table via creators.user_id
            cursor.execute(
                '''
                SELECT u.email 
                FROM creators c
                JOIN users u ON c.user_id = u.id
                WHERE c.id = %s
                ''',
                (creator_id,)
            )
            creator = cursor.fetchone()
            if not creator or not creator['email']:
                conn.rollback()
                app.logger.error(f"Creator {creator_id} has no email for PayPal payment")
                return jsonify({"error": "Creator's PayPal account not set up"}), 400

            # Create PayPal payment
            payment = paypalrestsdk.Payment({
                "intent": "sale",
                "payer": {"payment_method": "paypal"},
                "transactions": [{
                    "amount": {
                        "total": f"{total_amount:.2f}",
                        "currency": "EUR",
                        "details": {
                            "subtotal": f"{net_amount:.2f}",
                            "fee": f"{commission_amount / 100.0:.2f}"
                        }
                    },
                    "description": f"Payment for booking {booking_id}",
                    "payee": {"email": creator['email']}
                }],
                "redirect_urls": {
                    "return_url": return_url,
                    "cancel_url": cancel_url
                }
            })

            if payment.create():
                approval_url = next(link.href for link in payment.links if link.rel == "approval_url")
                # Update booking with transaction_id, payment_status, payment_method, and platform_fee
                cursor.execute(
                    '''
                    UPDATE bookings 
                    SET payment_status = 'Pending', 
                        transaction_id = %s, 
                        payment_method = 'paypal', 
                        platform_fee = %s,
                        updated_at = NOW() 
                    WHERE id = %s AND brand_id = %s
                    RETURNING payment_status, transaction_id, payment_method, platform_fee, status, content_status
                    ''',
                    (payment.id, commission_amount / 100.0, booking_id, brand_id)
                )
                updated_booking = cursor.fetchone()
                if not updated_booking:
                    conn.rollback()
                    app.logger.error(f"Failed to update booking {booking_id}")
                    return jsonify({"error": "Failed to update booking"}), 500
                app.logger.info(f"Booking {booking_id} updated: payment_status={updated_booking['payment_status']}, transaction_id={updated_booking['transaction_id']}, payment_method={updated_booking['payment_method']}, platform_fee={updated_booking['platform_fee']}, status={updated_booking['status']}, content_status={updated_booking['content_status']}")
                conn.commit()
                cursor.close()
                conn.close()
                return jsonify({"payment_id": payment.id, "approval_url": approval_url}), 200
            else:
                conn.rollback()
                app.logger.error(f"PayPal payment creation failed: {payment.error}")
                return jsonify({"error": payment.error.get('message', 'PayPal payment creation failed')}), 400

        finally:
            if 'cursor' in locals():
                cursor.close()
            if 'conn' in locals() and not conn.closed:
                conn.close()

    except Exception as e:
        app.logger.error(f"Error creating PayPal payment for booking {booking_id}: {str(e)}")
        if 'conn' in locals() and not conn.closed:
            conn.rollback()
        return jsonify({"error": str(e)}), 500


@app.route('/connect-stripe-account', methods=['POST'])
def connect_stripe_account():
    try:
        creator_id = session.get('creator_id')
        if not creator_id:
            app.logger.error("No creator_id in session")
            return jsonify({"error": "Unauthorized"}), 403

        data = request.json
        email = data.get('email')
        if not email:
            return jsonify({"error": "Email is required"}), 400

        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Check if creator already has a Stripe account
        cursor.execute('SELECT stripe_account_id FROM creators WHERE id = %s', (creator_id,))
        creator = cursor.fetchone()
        if creator['stripe_account_id']:
            conn.close()
            return jsonify({"message": "Stripe account already connected"}), 200

        # Create Stripe Express account
        account = stripe.Account.create(
            type='express',
            country='DE',  # Adjust based on your region
            email=email,
            capabilities={'card_payments': {'requested': True}, 'transfers': {'requested': True}},
            metadata={'creator_id': creator_id}
        )

        # Store Stripe account ID
        cursor.execute(
            'UPDATE creators SET stripe_account_id = %s WHERE id = %s',
            (account.id, creator_id)
        )
        conn.commit()
        cursor.close()
        conn.close()

        # Create account link for onboarding
        account_link = stripe.AccountLink.create(
            account=account.id,
            refresh_url='https://yourplatform.com/stripe/reauth',
            return_url='https://yourplatform.com/stripe/success',
            type='account_onboarding'
        )

        app.logger.info(f"Stripe account created for creator {creator_id}: {account.id}")
        return jsonify({"url": account_link.url}), 200

    except stripe.error.StripeError as e:
        app.logger.error(f"Stripe error creating account for creator {creator_id}: {str(e)}")
        return jsonify({"error": f"Stripe error: {str(e)}"}), 400
    except Exception as e:
        app.logger.error(f"Error connecting Stripe account for creator {creator_id}: {str(e)}")
        return jsonify({"error": str(e)}), 500

@app.route('/creator/stripe-account-status', methods=['GET'])
def get_stripe_account_status():
    try:
        creator_id = session.get('creator_id')
        if not creator_id:
            app.logger.error("No creator ID in session")
            return jsonify({"error": "Unauthorized: No creator ID in session"}), 403

        conn = get_db_connection()
        if not conn:
            app.logger.error("Database connection failed")
            return jsonify({"error": "Database connection failed"}), 500

        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute('''
            SELECT c.stripe_account_id, u.email 
            FROM creators c
            JOIN users u ON c.user_id = u.id
            WHERE c.id = %s
        ''', (creator_id,))
        creator = cursor.fetchone()
        conn.close()

        if not creator:
            app.logger.error(f"Creator {creator_id} not found")
            return jsonify({"error": "Creator not found"}), 404

        app.logger.info(f"Retrieved Stripe account status for creator {creator_id}")
        return jsonify({
            "stripe_account_id": creator['stripe_account_id'],
            "email": creator['email']
        }), 200
    except Exception as e:
        app.logger.error(f"Error checking Stripe account status: {str(e)}")
        return jsonify({"error": str(e)}), 500




@app.route('/creator/stripe-dashboard', methods=['GET'])
def get_stripe_dashboard():
    try:
        creator_id = session.get('creator_id')
        if not creator_id:
            app.logger.error("No creator ID in session")
            return jsonify({"error": "Unauthorized: No creator ID in session"}), 403

        conn = get_db_connection()
        if not conn:
            app.logger.error("Database connection failed")
            return jsonify({"error": "Database connection failed"}), 500

        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute('SELECT stripe_account_id FROM creators WHERE id = %s', (creator_id,))
        creator = cursor.fetchone()
        conn.close()

        if not creator:
            app.logger.error(f"Creator {creator_id} not found")
            return jsonify({"error": "Creator not found"}), 404

        if not creator['stripe_account_id']:
            app.logger.error(f"No Stripe account connected for creator {creator_id}")
            return jsonify({"error": "No Stripe account connected"}), 400

        # Generate a login link for the Stripe Express Dashboard
        login_link = stripe.Account.create_login_link(
            creator['stripe_account_id'],
            redirect_url='http://localhost:3000/creator/payments'  # Redirect back to payments page
        )

        app.logger.info(f"Generated Stripe dashboard link for creator {creator_id}")
        return jsonify({"url": login_link.url}), 200

    except stripe.error.StripeError as e:
        app.logger.error(f"Stripe error generating dashboard link for creator {creator_id}: {str(e)}")
        return jsonify({"error": f"Stripe error: {str(e)}"}), 400
    except Exception as e:
        app.logger.error(f"Error generating Stripe dashboard link for creator {creator_id}: {str(e)}")
        return jsonify({"error": str(e)}), 500


@app.route('/debug/session', methods=['GET'])
def debug_session():
    print(f"📌 [SESSION DEBUG] Full Session Data: {dict(session)}")
    print(f"📌 [SESSION DEBUG] Cookies: {request.cookies}")
    return jsonify({
        "user_id": session.get('user_id'),
        "user_role": session.get('user_role'),
        "session_contents": dict(session),
        "cookies": request.cookies
    })






# Enable CORS for all routes
app.secret_key = os.urandom(24)

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)