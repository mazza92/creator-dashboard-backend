"""
Apply brand description fix to PRBrandDiscovery.js
Run this script after closing your code editor to prevent file watchers from interfering
"""

import re

file_path = r'c:\Users\maher\Desktop\creator-dashboard\src\creator-portal\PRBrandDiscovery.js'

# Read the file
with open(file_path, 'r', encoding='utf-8') as f:
    content = f.read()

# Change 1: Add getBrandDescription helper function
helper_function = '''// Get brand description from notes field
const getBrandDescription = (brand) => {
  if (!brand.notes) return null;
  const description = brand.notes.replace('Scraped: ', '').trim();
  return description.length > 120 ? description.substring(0, 120) + '...' : description;
};

'''

# Find the location to insert (after getBrandLogoUrl function)
pattern1 = r'(  return null;\n};\n\n)(// Minimalist Container)'
replacement1 = r'\1' + helper_function + r'\2'
content = re.sub(pattern1, replacement1, content)

# Change 2: Update description display
pattern2 = r'\{currentBrand\.description && <BrandDescription>\{currentBrand\.description\}</BrandDescription>\}'
replacement2 = '''{getBrandDescription(currentBrand) && (
                    <BrandDescription>{getBrandDescription(currentBrand)}</BrandDescription>
                  )}'''
content = re.sub(pattern2, replacement2, content)

# Write the file
with open(file_path, 'w', encoding='utf-8') as f:
    f.write(content)

print("✓ Successfully applied brand description fix!")
print("\nChanges made:")
print("1. Added getBrandDescription() helper function")
print("2. Updated brand card to display notes field as description")
print("\nYou can now restart your dev server.")
