/**
 * Properly fix React Hooks error
 */

const fs = require('fs');
const path = require('path');

const filePath = path.join(__dirname, '..', 'creator-dashboard', 'src', 'creator-portal', 'PRBrandDiscovery.js');

let content = fs.readFileSync(filePath, 'utf8');

// Step 1: Add lastTap state after lastAction state
const addStatePattern = /const \[lastAction, setLastAction\] = useState\(null\); \/\/ For undo functionality/;
const replacement = `const [lastAction, setLastAction] = useState(null); // For undo functionality
  const [lastTap, setLastTap] = useState(0); // For double-tap detection`;

content = content.replace(addStatePattern, replacement);

// Step 2: Find and move handleDoubleTap function from after currentBrand to before it
// First, find the function wherever it is
const findDoubleTapPattern = /const handleDoubleTap = \(\) => \{[\s\S]*?setLastTap\(now\);\s*\};/;
const doubleTapMatch = content.match(findDoubleTapPattern);

if (doubleTapMatch) {
  const doubleTapFunction = doubleTapMatch[0];

  // Remove it from wherever it is
  content = content.replace(findDoubleTapPattern, '');

  // Add it before handlePass function (which should be before return statement)
  const handlePassPattern = /(const handlePass = \(\) => \{)/;
  const withDoubleTap = `// Double tap to quick save
  const handleDoubleTap = () => {
    const now = Date.now();
    if (now - lastTap < 300) {
      // Double tap detected
      handleSave();
    }
    setLastTap(now);
  };

  $1`;

  content = content.replace(handlePassPattern, withDoubleTap);
}

fs.writeFileSync(filePath, content, 'utf8');

console.log('[OK] Fixed React Hooks error properly!');
console.log('Changes:');
console.log('  1. Added lastTap state with other useState hooks');
console.log('  2. Moved handleDoubleTap function before return statement');
console.log('  3. Function now has access to lastTap and setLastTap');
console.log('\nRestart React dev server to verify.');
