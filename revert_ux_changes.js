/**
 * Revert PRBrandDiscovery to working state before UX improvements
 * Removes all gamification styled components and related state
 */

const fs = require('fs');
const path = require('path');

const filePath = path.join(__dirname, '..', 'creator-dashboard', 'src', 'creator-portal', 'PRBrandDiscovery.js');

let content = fs.readFileSync(filePath, 'utf8');

// Remove all gamification styled components
const componentsToRemove = [
  /const FloatingBadge = styled\(motion\.div\)`[\s\S]*?`;/,
  /const BadgeCount = styled\.span`[\s\S]*?`;/,
  /const CelebrationOverlay = styled\(motion\.div\)`[\s\S]*?`;/,
  /const ConfettiEmoji = styled\(motion\.div\)`[\s\S]*?`;/,
  /const SuccessCheckmark = styled\(motion\.div\)`[\s\S]*?`;/,
  /const ProgressStats = styled\.div`[\s\S]*?`;/,
  /const StatBadge = styled\(motion\.div\)`[\s\S]*?`;/,
  /const StatNumber = styled\.span`[\s\S]*?`;/,
  /const GoalProgress = styled\.div`[\s\S]*?`;/,
  /const GoalHeader = styled\.div`[\s\S]*?`;/,
  /const GoalTitle = styled\.div`[\s\S]*?`;/,
  /const GoalCount = styled\.span`[\s\S]*?`;/,
  /const ProgressBar = styled\.div`[\s\S]*?`;/,
  /const ProgressFill = styled\(motion\.div\)`[\s\S]*?`;/,
  /\/\/ StreakBadge - disabled[\s\S]*?\/\/ const StreakBadge.*?;/,
  /const AchievementModal = styled\(motion\.div\)`[\s\S]*?`;/,
  /const AchievementIcon = styled\(motion\.div\)`[\s\S]*?`;/,
  /const AchievementTitle = styled\.h3`[\s\S]*?`;/,
  /const AchievementDesc = styled\.p`[\s\S]*?`;/,
  /const HintTooltip = styled\(motion\.div\)`[\s\S]*?`;/,
  /const SwipeLabel = styled\(motion\.div\)`[\s\S]*?`;/,
  /const NextCardPeek = styled\(motion\.div\)`[\s\S]*?`;/
];

componentsToRemove.forEach(pattern => {
  content = content.replace(pattern, '');
});

// Remove gamification state variables
const stateToRemove = [
  /const \[sessionSavedCount, setSessionSavedCount\] = useState\(0\);[\s\S]*?\n/,
  /const \[dailyGoal\] = useState\(5\);[\s\S]*?\n/,
  /const \[showBadgeAnimation, setShowBadgeAnimation\] = useState\(false\);[\s\S]*?\n/,
  /const \[showCelebration, setShowCelebration\] = useState\(false\);[\s\S]*?\n/,
  /const \[showAchievement, setShowAchievement\] = useState\(false\);[\s\S]*?\n/,
  /const \[achievementText, setAchievementText\] = useState\(''\);[\s\S]*?\n/,
  /const \[showHint, setShowHint\] = useState\(false\);[\s\S]*?\n/,
  /const \[dragDirection, setDragDirection\] = useState\(null\);[\s\S]*?\n/,
  /const \[lastAction, setLastAction\] = useState\(null\);[\s\S]*?\n/,
  /const \[lastTap, setLastTap\] = useState\(0\);.*?\n/
];

stateToRemove.forEach(pattern => {
  content = content.replace(pattern, '');
});

// Remove checkAchievements function
content = content.replace(/const checkAchievements = \(count\) => \{[\s\S]*?\};[\s\S]*?\n/m, '');

// Remove handleDoubleTap function
content = content.replace(/const handleDoubleTap = \(\) => \{[\s\S]*?\};[\s\S]*?\n/m, '');

// Remove celebration triggers from handleSave
const handleSavePattern = /const handleSave = async \(\) => \{[\s\S]*?\};/;
const match = content.match(handleSavePattern);
if (match) {
  let handleSaveContent = match[0];

  // Remove gamification code from handleSave
  handleSaveContent = handleSaveContent.replace(/\/\/ Celebration[\s\S]*?setShowBadgeAnimation\(true\);[\s\S]*?\n/g, '');
  handleSaveContent = handleSaveContent.replace(/setSessionSavedCount\(prev => prev \+ 1\);[\s\S]*?\n/g, '');
  handleSaveContent = handleSaveContent.replace(/setShowCelebration\(true\);[\s\S]*?\n/g, '');
  handleSaveContent = handleSaveContent.replace(/checkAchievements\(.*?\);[\s\S]*?\n/g, '');
  handleSaveContent = handleSaveContent.replace(/setTimeout\(\(\) => setShowCelebration\(false\), 2000\);[\s\S]*?\n/g, '');

  content = content.replace(handleSavePattern, handleSaveContent);
}

// Remove hint trigger useEffect
content = content.replace(/\/\/ Show hint after first brand[\s\S]*?useEffect\(\(\) => \{[\s\S]*?currentIndex === 1[\s\S]*?\}, \[currentIndex\]\);[\s\S]*?\n/m, '');

// Remove all gamification JSX from loading state
content = content.replace(/\{\/\* Progress Stats \*\/\}[\s\S]*?<\/ProgressStats>[\s\S]*?\n/g, '');
content = content.replace(/\{\/\* Daily Goal Progress \*\/\}[\s\S]*?<\/GoalProgress>[\s\S]*?\n/g, '');
content = content.replace(/\{\/\* Floating Badge.*?\*\/\}[\s\S]*?<\/AnimatePresence>[\s\S]*?\n/g, '');
content = content.replace(/\{\/\* Celebration Animation \*\/\}[\s\S]*?<\/AnimatePresence>[\s\S]*?\n/g, '');
content = content.replace(/\{\/\* Streak Badge.*?\*\/\}[\s\S]*?\n/g, '');
content = content.replace(/\{\/\* Achievement.*?\*\/\}[\s\S]*?<\/AnimatePresence>[\s\S]*?\n/g, '');
content = content.replace(/\{\/\* Quick Hint.*?\*\/\}[\s\S]*?<\/AnimatePresence>[\s\S]*?\n/g, '');
content = content.replace(/\{\/\* Swipe Indicators.*?\*\/\}[\s\S]*?<\/SwipeLabel>[\s\S]*?\n/g, '');
content = content.replace(/\{\/\* Next Card Peek.*?\*\/\}[\s\S]*?<\/NextCardPeek>[\s\S]*?\n/g, '');

// Simplify Container back to original
const containerPattern = /const Container = styled\.div`[\s\S]*?@media \(max-width: 768px\) \{[\s\S]*?\}[\s\S]*?`;/;
const originalContainer = `const Container = styled.div\`
  width: 100%;
  max-width: 100%;
  background: #FAFAFA;
  padding: 0;
  min-height: 100vh;
\`;`;

content = content.replace(containerPattern, originalContainer);

// Remove drag/swipe from BrandCard
content = content.replace(/drag="x"[\s\S]*?\n/g, '');
content = content.replace(/dragConstraints=\{\{[\s\S]*?\}\}[\s\S]*?\n/g, '');
content = content.replace(/dragElastic=\{[\s\S]*?\}[\s\S]*?\n/g, '');
content = content.replace(/onDragEnd=\{handleDragEnd\}[\s\S]*?\n/g, '');
content = content.replace(/onClick=\{handleDoubleTap\}[\s\S]*?\n/g, '');

// Remove handleDragEnd function
content = content.replace(/const handleDragEnd = \(event, info\) => \{[\s\S]*?\};[\s\S]*?\n/m, '');

fs.writeFileSync(filePath, content, 'utf8');

console.log('✅ Reverted PRBrandDiscovery to working state!');
console.log('\nRemoved:');
console.log('  - All gamification styled components');
console.log('  - Progress stats, goals, celebrations');
console.log('  - Achievements, streaks, hints');
console.log('  - Swipe interactions and animations');
console.log('  - Related state and functions');
console.log('\nThe component is now back to basic working state.');
console.log('Restart React server to see changes.');
