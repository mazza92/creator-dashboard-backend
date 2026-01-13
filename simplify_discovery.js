/**
 * Simplify Discovery - Remove all gamification
 * Keep only what's essential for MVP/validation
 */

const fs = require('fs');
const path = require('path');

const filePath = path.join(__dirname, '..', 'creator-dashboard', 'src', 'creator-portal', 'PRBrandDiscovery.js');

let content = fs.readFileSync(filePath, 'utf8');

// Remove ProgressStats from main return
const removeProgressStats = /\{\/\* Progress Stats \*\/\}\s*<ProgressStats>[\s\S]*?<\/ProgressStats>/g;
content = content.replace(removeProgressStats, '');

// Remove GoalProgress from main return
const removeGoalProgress = /\{\/\* Daily Goal Progress \*\/\}\s*<GoalProgress>[\s\S]*?<\/GoalProgress>/g;
content = content.replace(removeGoalProgress, '');

// Remove FloatingBadge
const removeFloatingBadge = /\{\/\* Floating Badge.*?\*\/\}\s*<AnimatePresence>[\s\S]*?<\/FloatingBadge>[\s\S]*?<\/AnimatePresence>/g;
content = content.replace(removeFloatingBadge, '');

// Remove Celebration Animation
const removeCelebration = /\{\/\* Celebration Animation \*\/\}\s*<AnimatePresence>[\s\S]*?<\/CelebrationOverlay>[\s\S]*?<\/AnimatePresence>/g;
content = content.replace(removeCelebration, '');

// Remove Achievement Modal
const removeAchievement = /\{\/\* Achievement Unlock Modal \*\/\}\s*<AnimatePresence>[\s\S]*?<\/AchievementModal>[\s\S]*?<\/AnimatePresence>/g;
content = content.replace(removeAchievement, '');

// Remove Hint Tooltip
const removeHint = /\{\/\* Quick Hint Tooltip \*\/\}\s*<AnimatePresence>[\s\S]*?<\/HintTooltip>[\s\S]*?<\/AnimatePresence>/g;
content = content.replace(removeHint, '');

// Remove Streak Badge (already removed but make sure)
const removeStreak = /\{\/\* Streak Badge.*?\*\/\}[\s\S]*?<StreakBadge[\s\S]*?<\/StreakBadge>/g;
content = content.replace(removeStreak, '');

// Remove swipe indicators from card
const removeSwipeIndicators = /\{\/\* Swipe Indicators \*\/\}[\s\S]*?<\/SwipeLabel>/g;
content = content.replace(removeSwipeIndicators, '');

// Remove next card peek
const removeNextCardPeek = /\{\/\* Next Card Peek \*\/\}[\s\S]*?<\/NextCardPeek>[\s\S]*?\}/g;
content = content.replace(removeNextCardPeek, '');

// Simplify BrandCard - remove double tap, remove fancy drag
const simplifyBrandCard = /(<BrandCard[\s\S]*?)onClick=\{handleDoubleTap\}([\s\S]*?)dragConstraints=\{\{ left: -300, right: 300 \}\}\s*dragElastic=\{0\.7\}/;
content = content.replace(simplifyBrandCard, '$1$2dragConstraints={{ left: 0, right: 0 }}');

// Remove gamification state handlers
const removeCheckAchievements = /setSessionSavedCount\(prev => \{[\s\S]*?checkAchievements\(newCount\);[\s\S]*?return newCount;[\s\S]*?\}\);/g;
content = content.replace(removeCheckAchievements, 'setSessionSavedCount(prev => prev + 1);');

// Remove celebration triggers from handleSave
const removeCelebrationTriggers = /\/\/ Trigger celebration animation[\s\S]*?setShowBadgeAnimation\(false\);[\s\S]*?\}, 2000\);/g;
content = content.replace(removeCelebrationTriggers, '');

// Revert button to simple version
const simplifyContactButton = /(variant="save"[\s\S]*?)whileHover=\{[^}]*\}[\s\S]*?whileTap=\{[^}]*\}[\s\S]*?transition=\{[^}]*\}/;
content = content.replace(simplifyContactButton, '$1whileTap={{ scale: 0.9 }}');

fs.writeFileSync(filePath, content, 'utf8');

console.log('[OK] Simplified Discovery - removed gamification!');
console.log('\nRemoved:');
console.log('  ✗ Progress stats (Today/Pipeline/Viewed)');
console.log('  ✗ Daily goal progress bar');
console.log('  ✗ Floating save badge');
console.log('  ✗ Confetti celebration animations');
console.log('  ✗ Achievement unlock modals');
console.log('  ✗ Streak tracking');
console.log('  ✗ Hint tooltips');
console.log('  ✗ Swipe indicators');
console.log('  ✗ Next card peek');
console.log('  ✗ Double-tap functionality');
console.log('\nKept:');
console.log('  ✓ Basic brand cards');
console.log('  ✓ Skip/Contact buttons');
console.log('  ✓ Simple save functionality');
console.log('  ✓ Core discovery flow');
console.log('\nRestart React server for clean, simple version.');
