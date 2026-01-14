"""
Test email sending functionality
IMPORTANT: Only sends to mahery92@hotmail.fr for testing
"""
import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from dotenv import load_dotenv

load_dotenv()

TEST_EMAIL = "mahery92@hotmail.fr"

def test_smtp_connection():
    """Test SMTP connection and credentials"""
    print("\n" + "="*60)
    print("TESTING EMAIL CONFIGURATION")
    print("="*60 + "\n")

    smtp_server = os.getenv('SMTP_SERVER', 'smtp.gmail.com')
    smtp_port = int(os.getenv('SMTP_PORT', 587))
    smtp_username = os.getenv('SMTP_USERNAME')
    smtp_password = os.getenv('SMTP_PASSWORD')

    print(f"SMTP Server: {smtp_server}")
    print(f"SMTP Port: {smtp_port}")
    print(f"SMTP Username: {smtp_username}")
    print(f"SMTP Password: {'*' * len(smtp_password) if smtp_password else 'NOT SET'}")
    print(f"Test Email: {TEST_EMAIL}\n")

    if not smtp_username or not smtp_password:
        print("[ERROR] SMTP credentials not configured in .env file!")
        return False

    # Test connection
    print("Testing SMTP connection...")
    try:
        server = smtplib.SMTP(smtp_server, smtp_port)
        server.starttls()
        print("[SUCCESS] TLS connection established")

        server.login(smtp_username, smtp_password)
        print("[SUCCESS] Authentication successful")

        server.quit()
        print("[SUCCESS] SMTP connection test passed!\n")
        return True

    except smtplib.SMTPAuthenticationError as e:
        print(f"[ERROR] Authentication failed: {str(e)}")
        print("\nPossible issues:")
        print("1. Incorrect username or password")
        print("2. Gmail 'Less secure app access' not enabled")
        print("3. Need to use App Password instead of regular password")
        return False

    except Exception as e:
        print(f"[ERROR] Connection failed: {str(e)}")
        return False

def send_test_email():
    """Send a test email to mahery92@hotmail.fr"""
    print("="*60)
    print("SENDING TEST EMAIL")
    print("="*60 + "\n")

    smtp_server = os.getenv('SMTP_SERVER', 'smtp.gmail.com')
    smtp_port = int(os.getenv('SMTP_PORT', 587))
    smtp_username = os.getenv('SMTP_USERNAME')
    smtp_password = os.getenv('SMTP_PASSWORD')

    msg = MIMEMultipart()
    msg['From'] = smtp_username
    msg['To'] = TEST_EMAIL
    msg['Subject'] = 'Newcollab Email Test - Registration Flow'

    body = """
    <html>
        <body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
            <h2 style="color: #3B82F6;">Welcome to Newcollab!</h2>
            <p>This is a test email to verify the registration email flow is working correctly.</p>

            <div style="background: #F3F4F6; padding: 20px; border-radius: 8px; margin: 20px 0;">
                <h3 style="margin-top: 0;">Test Details:</h3>
                <ul>
                    <li>SMTP Server: smtp.gmail.com</li>
                    <li>Sender: team@newcollab.co</li>
                    <li>Test Recipient: mahery92@hotmail.fr</li>
                </ul>
            </div>

            <p style="background: #3B82F6; color: white; padding: 12px 20px; text-align: center; border-radius: 6px; display: inline-block;">
                <a href="https://newcollab.co/verify-email?token=TEST_TOKEN" style="color: white; text-decoration: none;">
                    Verify Email (Test Link)
                </a>
            </p>

            <p style="color: #6B7280; font-size: 14px; margin-top: 30px;">
                This is a test email sent from the Newcollab registration system.
            </p>
        </body>
    </html>
    """

    msg.attach(MIMEText(body, 'html'))

    try:
        print(f"Sending test email to {TEST_EMAIL}...")
        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls()
            server.login(smtp_username, smtp_password)
            server.sendmail(smtp_username, TEST_EMAIL, msg.as_string())

        print(f"[SUCCESS] Test email sent to {TEST_EMAIL}!")
        print("\nPlease check your inbox (and spam folder)")
        print("="*60 + "\n")
        return True

    except Exception as e:
        print(f"[ERROR] Failed to send email: {str(e)}")
        import traceback
        traceback.print_exc()
        return False

def send_verification_email_test():
    """Send a realistic verification email like the registration flow"""
    print("="*60)
    print("SENDING VERIFICATION EMAIL (LIKE REGISTRATION)")
    print("="*60 + "\n")

    smtp_server = os.getenv('SMTP_SERVER', 'smtp.gmail.com')
    smtp_port = int(os.getenv('SMTP_PORT', 587))
    smtp_username = os.getenv('SMTP_USERNAME')
    smtp_password = os.getenv('SMTP_PASSWORD')
    base_url = os.getenv('BASE_URL', 'https://newcollab.co')

    # Generate a test token
    import time
    import jwt as pyjwt
    secret_key = os.getenv('JWT_SECRET_KEY')
    test_token = pyjwt.encode({
        'sub': TEST_EMAIL,
        'iat': int(time.time()),
        'exp': int(time.time()) + 86400
    }, secret_key, algorithm='HS256')

    verification_url = f"{base_url}/verify-email?token={test_token}"

    msg = MIMEMultipart()
    msg['From'] = smtp_username
    msg['To'] = TEST_EMAIL
    msg['Subject'] = 'Verify your Newcollab account'

    body = f"""
    <html>
        <body>
            <h2>Welcome to Newcollab!</h2>
            <p>Please verify your email to complete your account setup:</p>
            <a href="{verification_url}">Verify Email</a>
            <p>Or copy this link: {verification_url}</p>
            <p>This link expires in 24 hours.</p>
        </body>
    </html>
    """

    msg.attach(MIMEText(body, 'html'))

    try:
        print(f"Sending verification email to {TEST_EMAIL}...")
        print(f"Verification URL: {verification_url}\n")

        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls()
            server.login(smtp_username, smtp_password)
            server.sendmail(smtp_username, TEST_EMAIL, msg.as_string())

        print(f"[SUCCESS] Verification email sent to {TEST_EMAIL}!")
        print("\nPlease check your inbox (and spam folder)")
        print("="*60 + "\n")
        return True

    except Exception as e:
        print(f"[ERROR] Failed to send email: {str(e)}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == '__main__':
    print("\n" + "="*60)
    print("NEWCOLLAB EMAIL TESTING SUITE")
    print("Target Email: mahery92@hotmail.fr")
    print("="*60 + "\n")

    # Test 1: SMTP Connection
    if not test_smtp_connection():
        print("\n[FAILED] SMTP connection test failed. Fix credentials before proceeding.\n")
        exit(1)

    # Test 2: Simple test email
    print("Press Enter to send a test email, or Ctrl+C to skip...")
    input()
    send_test_email()

    # Test 3: Realistic verification email
    print("\nPress Enter to send a verification email (like registration flow), or Ctrl+C to exit...")
    input()
    send_verification_email_test()

    print("\n[COMPLETE] All email tests finished!")
