"""
Fix Contact button loading state - use spinner instead of text
"""

import re

file_path = r'c:\Users\maher\Desktop\creator-dashboard\src\creator-portal\PRBrandDiscovery.js'

with open(file_path, 'r', encoding='utf-8') as f:
    content = f.read()

# Replace the button content with spinner animation
old_pattern = r'''<ActionButton
            variant="save"
            onClick={handleContactBrand}
            disabled={revealingContact}
            whileTap={{ scale: 0.9 }}
          >
            <FiCheck />
            <span style={{ fontSize: '13px', marginTop: '4px' }}>
              {revealingContact \? 'Loading\.\.\.' : 'Contact'}
            </span>
          </ActionButton>'''

new_pattern = '''<ActionButton
            variant="save"
            onClick={handleContactBrand}
            disabled={revealingContact}
            whileTap={{ scale: 0.9 }}
          >
            {revealingContact ? (
              <motion.div
                animate={{ rotate: 360 }}
                transition={{ duration: 1, repeat: Infinity, ease: 'linear' }}
                style={{ fontSize: '28px' }}
              >
                ⟳
              </motion.div>
            ) : (
              <FiCheck />
            )}
            <span style={{ fontSize: '13px', marginTop: '4px' }}>
              Contact
            </span>
          </ActionButton>'''

content = re.sub(old_pattern, new_pattern, content, flags=re.MULTILINE)

with open(file_path, 'w', encoding='utf-8') as f:
    f.write(content)

print('[OK] Fixed Contact button loading state!')
print('Now uses rotating spinner icon instead of "Loading..." text')
print('Button size remains consistent during loading')
