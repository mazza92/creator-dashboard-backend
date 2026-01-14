"""
Comprehensive email testing for all Newcollab email flows
IMPORTANT: Only sends to mahery92@hotmail.fr for testing
"""
import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from dotenv import load_dotenv
from jinja2 import Environment, FileSystemLoader

load_dotenv()

TEST_EMAIL = "mahery92@hotmail.fr"
SMTP_SERVER = os.getenv('SMTP_SERVER', 'smtp.gmail.com')
SMTP_PORT = int(os.getenv('SMTP_PORT', 587))
SMTP_USERNAME = os.getenv('SMTP_USERNAME')
SMTP_PASSWORD = os.getenv('SMTP_PASSWORD')
SENDER_NAME = os.getenv('EMAIL_SENDER_NAME', 'Newcollab team')

def test_smtp_connection():
    """Test SMTP connection"""
    print("\n" + "="*60)
    print("TESTING SMTP CONNECTION")
    print("="*60 + "\n")

    print(f"SMTP Server: {SMTP_SERVER}:{SMTP_PORT}")
    print(f"Username: {SMTP_USERNAME}")
    print(f"Test Email: {TEST_EMAIL}\n")

    try:
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=10) as server:
            server.starttls()
            server.login(SMTP_USERNAME, SMTP_PASSWORD)
        print("[SUCCESS] SMTP connection working!\n")
        return True
    except Exception as e:
        print(f"[ERROR] SMTP connection failed: {str(e)}\n")
        return False

def send_test_email(subject, body_html, body_text=None):
    """Generic function to send test emails"""
    try:
        msg = MIMEMultipart('alternative')
        msg['From'] = f"{SENDER_NAME} <{SMTP_USERNAME}>"
        msg['To'] = TEST_EMAIL
        msg['Subject'] = subject

        if body_text:
            msg.attach(MIMEText(body_text, 'plain'))
        msg.attach(MIMEText(body_html, 'html'))

        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=10) as server:
            server.starttls()
            server.login(SMTP_USERNAME, SMTP_PASSWORD)
            server.sendmail(SMTP_USERNAME, TEST_EMAIL, msg.as_string())

        print(f"[SUCCESS] Email sent: {subject}")
        return True
    except Exception as e:
        print(f"[ERROR] Failed to send: {str(e)}")
        return False

def test_welcome_email():
    """Test welcome email with template"""
    print("\n" + "="*60)
    print("TEST 1: WELCOME EMAIL")
    print("="*60 + "\n")

    try:
        # Load template
        template_dir = 'templates'
        env = Environment(loader=FileSystemLoader(template_dir))
        html_template = env.get_template('welcome_email.html')
        text_template = env.get_template('welcome_email.txt')

        # Render with test data
        test_data = {
            'email': TEST_EMAIL,
            'first_name': 'Maher',
            'username': 'maher_test'
        }

        message = f"""
        <h2>Welcome to Newcollab, {test_data['first_name']}! </h2>
        <p>You're all set to start discovering and pitching to PR brands.</p>
        <p>We've added 229+ brands to our directory, and we're adding more every week!</p>
        """

        html_content = html_template.render(
            message=message,
            data=test_data,
            action_url="https://newcollab.co/directory",
            action_text="Browse PR Brands",
            secondary_action_url="https://newcollab.co/creator/dashboard/profile",
            secondary_action_text="Complete Your Profile",
            user_id=999
        )

        text_content = text_template.render(
            message="Welcome to Newcollab! You're all set to start discovering PR brands.",
            data=test_data,
            action_url="https://newcollab.co/directory",
            action_text="Browse PR Brands",
            secondary_action_url=None,
            secondary_action_text=None,
            user_id=999
        )

        return send_test_email(
            "Welcome to Newcollab!",
            html_content,
            text_content
        )

    except Exception as e:
        print(f"[ERROR] Template error: {str(e)}")
        return False

def test_simple_html_email():
    """Test simple HTML email without templates"""
    print("\n" + "="*60)
    print("TEST 2: SIMPLE HTML EMAIL (NO TEMPLATE)")
    print("="*60 + "\n")

    html = """
    <!DOCTYPE html>
    <html>
        <body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px;">
            <h2 style="color: #3B82F6;">Test Email - No Template</h2>
            <p>This is a simple test email sent directly without using Jinja2 templates.</p>
            <p>If you receive this, it means the basic SMTP sending works, but template rendering might have issues.</p>
        </body>
    </html>
    """

    return send_test_email(
        "Newcollab Email Test - Simple HTML",
        html
    )

if __name__ == '__main__':
    print("\n" + "="*60)
    print("NEWCOLLAB EMAIL TESTING SUITE")
    print("Target: mahery92@hotmail.fr")
    print("="*60)

    # Test SMTP connection first
    if not test_smtp_connection():
        print("\n[FAILED] Cannot proceed - SMTP not working\n")
        exit(1)

    results = {
        'SMTP Connection': True,
        'Simple HTML Email': False,
        'Welcome Email': False
    }

    # Run tests
    results['Simple HTML Email'] = test_simple_html_email()
    results['Welcome Email'] = test_welcome_email()

    # Summary
    print("\n" + "="*60)
    print("TEST RESULTS SUMMARY")
    print("="*60 + "\n")

    for test_name, passed in results.items():
        status = "[PASS]" if passed else "[FAIL]"
        print(f"{status} {test_name}")

    print("\nCheck mahery92@hotmail.fr inbox (and spam folder)!")
    print("="*60 + "\n")
