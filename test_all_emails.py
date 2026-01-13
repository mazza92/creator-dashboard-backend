#!/usr/bin/env python3
"""
Quick script to test all 5 onboarding email sequences.
Run this after restarting your Flask server.
"""
import requests
import json
import time

TEST_EMAIL = 'mahery92@hotmail.fr'
BASE_URL = 'http://localhost:5000'

def send_test_email(email_number):
    """Send a test email by number"""
    url = f"{BASE_URL}/api/test-onboarding-reminder"
    data = {
        'email': TEST_EMAIL,
        'email_number': email_number
    }
    
    try:
        response = requests.post(url, json=data, timeout=30)
        if response.status_code == 200:
            result = response.json()
            print(f"✅ Email #{email_number}: {result.get('message', 'Sent')}")
            return True
        else:
            print(f"❌ Email #{email_number}: Status {response.status_code} - {response.text}")
            return False
    except Exception as e:
        print(f"❌ Email #{email_number}: Error - {str(e)}")
        return False

if __name__ == '__main__':
    print(f"📧 Sending all 5 onboarding emails to {TEST_EMAIL}...")
    print("=" * 60)
    
    results = []
    for email_num in range(1, 6):
        success = send_test_email(email_num)
        results.append((email_num, success))
        if email_num < 5:  # Don't wait after the last email
            time.sleep(2)  # Small delay between emails
    
    print("=" * 60)
    success_count = sum(1 for _, success in results if success)
    print(f"\n📊 Results: {success_count}/5 emails sent successfully")
    
    if success_count == 5:
        print("✅ All emails sent! Check your inbox.")
    else:
        print("⚠️  Some emails failed. Check the server logs for details.")

