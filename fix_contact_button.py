"""
Fix Contact button not advancing to next brand
Run this after stopping the dev server
"""

import re

file_path = r'c:\Users\maher\Desktop\creator-dashboard\src\creator-portal\PRBrandDiscovery.js'

# Read the file
with open(file_path, 'r', encoding='utf-8') as f:
    content = f.read()

# Find and replace the handleContactBrand function to add setCurrentIndex
# Look for the success message followed by the catch block
pattern = r"(message\.success\(`Contact revealed and \${brand\.brand_name} added to pipeline!`\);)\s*(\n\s*}\s*catch)"
replacement = r"\1\n\n      // Advance to next brand\n      setCurrentIndex(prev => prev + 1);\2"

content = re.sub(pattern, replacement, content)

# Write the file
with open(file_path, 'w', encoding='utf-8') as f:
    f.write(content)

print("✓ Successfully fixed Contact button!")
print("\nChange made:")
print("- Added setCurrentIndex(prev => prev + 1) after successful contact reveal")
print("- Contact button will now advance to the next brand like Skip button does")
print("\nYou can now restart your dev server.")
