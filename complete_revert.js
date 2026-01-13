/**
 * Complete revert to clean working state - removes ALL UX enhancement remnants
 */

const fs = require('fs');
const path = require('path');

const filePath = path.join(__dirname, '..', 'creator-dashboard', 'src', 'creator-portal', 'PRBrandDiscovery.js');

let content = fs.readFileSync(filePath, 'utf8');

// Remove whileDrag prop from BrandCard
content = content.replace(/\s*whileDrag=\{\{[\s\S]*?\}\}/g, '');

// Remove style prop with x and rotate from BrandCard
content = content.replace(/\s*style=\{\{\s*x: 0,\s*rotate: 0\s*\}\}/g, '');

// Remove any SwipeLabel JSX
content = content.replace(/<SwipeLabel[\s\S]*?<\/SwipeLabel>\s*/g, '');

// Clean up extra whitespace
content = content.replace(/\n\s*\n\s*\n/g, '\n\n');

fs.writeFileSync(filePath, content, 'utf8');

console.log('✅ Complete revert finished!');
console.log('\nCleaned up:');
console.log('  - whileDrag props');
console.log('  - style props with x/rotate');
console.log('  - SwipeLabel components');
console.log('  - Extra whitespace');
console.log('\nComponent is now fully clean!');
