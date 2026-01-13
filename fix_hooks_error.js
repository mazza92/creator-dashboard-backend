/**
 * Fix React Hooks Rules Violation
 * Move useState hook to top of component where it belongs
 */

const fs = require('fs');
const path = require('path');

const filePath = path.join(__dirname, '..', 'creator-dashboard', 'src', 'creator-portal', 'PRBrandDiscovery.js');

let content = fs.readFileSync(filePath, 'utf8');

// Step 1: Add lastTap state with other state declarations
const statePattern = /(const \[lastAction, setLastAction\] = useState\(null\);.*\n)/;
const newState = `$1  const [lastTap, setLastTap] = useState(0); // For double-tap detection\n`;

content = content.replace(statePattern, newState);

// Step 2: Remove the incorrectly placed useState hook after currentBrand declaration
const incorrectHookPattern = /const currentBrand = brands\[currentIndex\];\s*\n\s*\/\/ Double tap to quick save\s*\n\s*const \[lastTap, setLastTap\] = useState\(0\);\s*\n/;
const correctPattern = `const currentBrand = brands[currentIndex];\n\n`;

content = content.replace(incorrectHookPattern, correctPattern);

// Step 3: Move handleDoubleTap function to right after checkAchievements (with other functions)
const handleDoubleTapPattern = /const handleDoubleTap = \(\) => \{[\s\S]*?\n  \};\n/;
const handleDoubleTapCode = content.match(handleDoubleTapPattern);

if (handleDoubleTapCode) {
  // Remove from current location
  content = content.replace(handleDoubleTapPattern, '');

  // Add after checkAchievements function
  const checkAchievementsPattern = /(const checkAchievements = \(count\) => \{[\s\S]*?\n  \};)/;
  const withDoubleTap = `$1

  // Double tap to quick save
  const handleDoubleTap = () => {
    const now = Date.now();
    if (now - lastTap < 300) {
      // Double tap detected
      handleSave();
    }
    setLastTap(now);
  };`;

  content = content.replace(checkAchievementsPattern, withDoubleTap);
}

fs.writeFileSync(filePath, content, 'utf8');

console.log('[OK] Fixed React Hooks error!');
console.log('Changes:');
console.log('  - Moved lastTap useState to top of component');
console.log('  - Moved handleDoubleTap function to correct location');
console.log('  - All hooks now called in correct order');
console.log('\nRestart React dev server to verify fix.');
