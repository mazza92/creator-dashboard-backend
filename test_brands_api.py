#!/usr/bin/env python3
"""
Test the PR CRM brands API endpoint
"""
import requests

# Test the API endpoint
url = "http://localhost:5000/api/pr-crm/brands?limit=5"

print("=" * 60)
print("Testing PR CRM Brands API")
print("=" * 60)
print(f"URL: {url}\n")

try:
    response = requests.get(url)

    print(f"Status Code: {response.status_code}")
    print(f"Response Headers: {dict(response.headers)}\n")

    if response.status_code == 200:
        data = response.json()

        if data.get('success'):
            brands = data.get('brands', [])
            total = data.get('pagination', {}).get('total', 0)

            print(f"✓ SUCCESS!")
            print(f"Total brands in database: {total}")
            print(f"Brands returned: {len(brands)}\n")

            if brands:
                print("First 3 brands:")
                for i, brand in enumerate(brands[:3], 1):
                    print(f"\n{i}. {brand.get('brand_name')}")
                    print(f"   Category: {brand.get('category')}")
                    print(f"   Instagram: {brand.get('instagram_handle')}")
                    print(f"   Email: {brand.get('contact_email')}")
            else:
                print("No brands returned (but total shows brands exist)")
                print("This might be a filtering issue.")
        else:
            print(f"✗ API returned success=false")
            print(f"Error: {data.get('error')}")
    else:
        print(f"✗ API returned error status")
        print(f"Response: {response.text}")

except requests.exceptions.ConnectionError:
    print("✗ ERROR: Could not connect to Flask server")
    print("\nIs Flask running? Start it with: python app.py")
except Exception as e:
    print(f"✗ ERROR: {str(e)}")
    import traceback
    traceback.print_exc()

print("\n" + "=" * 60)
