/**
 * Fix mobile responsiveness for all gamification elements
 * - Center everything on mobile
 * - Fix progress bar disappearing glitch
 * - Adjust sizes for small screens
 */

const fs = require('fs');
const path = require('path');

const filePath = path.join(__dirname, '..', 'creator-dashboard', 'src', 'creator-portal', 'PRBrandDiscovery.js');

let content = fs.readFileSync(filePath, 'utf8');

// Fix ProgressStats - add mobile responsive styles
const progressStatsPattern = /const ProgressStats = styled\.div`[\s\S]*?justify-content: center;\s*`;/;
const newProgressStats = `const ProgressStats = styled.div\`
  display: flex;
  gap: 12px;
  margin-bottom: 16px;
  justify-content: center;
  flex-wrap: wrap;
  padding: 0 16px;

  @media (max-width: 768px) {
    gap: 8px;
    margin-bottom: 12px;
  }
\`;`;

content = content.replace(progressStatsPattern, newProgressStats);

// Fix StatBadge - smaller on mobile
const statBadgePattern = /const StatBadge = styled\(motion\.div\)`[\s\S]*?transition: all 0\.2s ease;\s*`;/;
const newStatBadge = `const StatBadge = styled(motion.div)\`
  background: white;
  border: 2px solid #E5E7EB;
  padding: 8px 16px;
  border-radius: 20px;
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  font-weight: 600;
  color: #374151;

  &:hover {
    border-color: \${primaryBlue};
    transform: translateY(-2px);
    box-shadow: 0 4px 12px rgba(59, 130, 246, 0.15);
  }

  transition: all 0.2s ease;

  @media (max-width: 768px) {
    padding: 6px 12px;
    font-size: 12px;
    gap: 4px;
  }
\`;`;

content = content.replace(statBadgePattern, newStatBadge);

// Fix GoalProgress - add padding and mobile styles, prevent disappearing
const goalProgressPattern = /const GoalProgress = styled\.div`[\s\S]*?border: 2px solid #E5E7EB;\s*`;/;
const newGoalProgress = `const GoalProgress = styled.div\`
  max-width: 500px;
  width: calc(100% - 32px);
  margin: 0 auto 20px;
  background: white;
  border-radius: 16px;
  padding: 16px;
  border: 2px solid #E5E7EB;

  @media (max-width: 768px) {
    width: calc(100% - 32px);
    margin: 0 16px 16px;
    padding: 12px;
    border-radius: 12px;
  }
\`;`;

content = content.replace(goalProgressPattern, newGoalProgress);

// Fix GoalHeader - smaller text on mobile
const goalHeaderPattern = /const GoalHeader = styled\.div`[\s\S]*?margin-bottom: 10px;\s*`;/;
const newGoalHeader = `const GoalHeader = styled.div\`
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 10px;

  @media (max-width: 768px) {
    margin-bottom: 8px;
  }
\`;`;

content = content.replace(goalHeaderPattern, newGoalHeader);

// Fix GoalTitle - smaller on mobile
const goalTitlePattern = /const GoalTitle = styled\.div`[\s\S]*?gap: 6px;\s*`;/;
const newGoalTitle = `const GoalTitle = styled.div\`
  font-weight: 600;
  font-size: 14px;
  color: #374151;
  display: flex;
  align-items: center;
  gap: 6px;

  @media (max-width: 768px) {
    font-size: 13px;
    gap: 4px;
  }
\`;`;

content = content.replace(goalTitlePattern, newGoalTitle);

// Remove StreakBadge - not tracking streaks yet
// Comment out StreakBadge styled component to hide it
const streakBadgePattern = /const StreakBadge = styled\(motion\.div\)`[\s\S]*?`;/;
const commentedStreakBadge = `// StreakBadge - disabled (not tracking yet)
// const StreakBadge = styled(motion.div)\`...\`;`;

content = content.replace(streakBadgePattern, commentedStreakBadge);

// Fix FloatingBadge - better mobile position
const floatingBadgePattern = /const FloatingBadge = styled\(motion\.div\)`[\s\S]*?user-select: none;\s*`;/;
const newFloatingBadge = `const FloatingBadge = styled(motion.div)\`
  position: fixed;
  bottom: 80px;
  right: 20px;
  background: linear-gradient(135deg, \${successGreen}, #059669);
  color: white;
  padding: 12px 20px;
  border-radius: 50px;
  display: flex;
  align-items: center;
  gap: 8px;
  font-weight: 600;
  font-size: 14px;
  box-shadow: 0 8px 24px rgba(16, 185, 129, 0.3);
  z-index: 1000;
  cursor: pointer;
  user-select: none;

  @media (max-width: 768px) {
    bottom: 70px;
    right: 16px;
    left: auto;
    padding: 10px 16px;
    font-size: 13px;
    gap: 6px;
  }
\`;`;

content = content.replace(floatingBadgePattern, newFloatingBadge);

// Fix AchievementModal - smaller on mobile
const achievementModalPattern = /const AchievementModal = styled\(motion\.div\)`[\s\S]*?min-width: 300px;\s*`;/;
const newAchievementModal = `const AchievementModal = styled(motion.div)\`
  position: fixed;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  background: linear-gradient(135deg, #FFD700, #FFA500);
  padding: 32px;
  border-radius: 24px;
  box-shadow: 0 20px 60px rgba(255, 215, 0, 0.4);
  z-index: 10000;
  text-align: center;
  min-width: 300px;

  @media (max-width: 768px) {
    min-width: auto;
    width: calc(100% - 48px);
    max-width: 320px;
    padding: 24px;
    border-radius: 20px;
  }
\`;`;

content = content.replace(achievementModalPattern, newAchievementModal);

// Fix HintTooltip - better mobile position
const hintTooltipPattern = /const HintTooltip = styled\(motion\.div\)`[\s\S]*?border-top: 8px solid rgba\(0, 0, 0, 0\.85\);\s*\}\s*`;/;
const newHintTooltip = `const HintTooltip = styled(motion.div)\`
  position: fixed;
  bottom: 200px;
  left: 50%;
  transform: translateX(-50%);
  background: rgba(0, 0, 0, 0.85);
  color: white;
  padding: 12px 20px;
  border-radius: 12px;
  font-size: 14px;
  max-width: 300px;
  text-align: center;
  z-index: 999;
  backdrop-filter: blur(10px);

  &::after {
    content: '';
    position: absolute;
    bottom: -8px;
    left: 50%;
    transform: translateX(-50%);
    width: 0;
    height: 0;
    border-left: 8px solid transparent;
    border-right: 8px solid transparent;
    border-top: 8px solid rgba(0, 0, 0, 0.85);
  }

  @media (max-width: 768px) {
    bottom: 180px;
    max-width: calc(100% - 48px);
    padding: 10px 16px;
    font-size: 13px;
  }
\`;`;

content = content.replace(hintTooltipPattern, newHintTooltip);

// Remove StreakBadge from JSX (both in loading and main return)
const streakBadgeJSX = /\{\/\* Streak Badge \*\/\}\s*<StreakBadge[\s\S]*?<\/StreakBadge>/g;
content = content.replace(streakBadgeJSX, '{/* Streak Badge - removed (not tracking yet) */}');

// Fix Container to be centered properly
const containerPattern = /const Container = styled\.div`[\s\S]*?padding: 0;\s*`;/;
const newContainer = `const Container = styled.div\`
  width: 100%;
  max-width: 100%;
  background: #FAFAFA;
  padding: 0;
  min-height: 100vh;
  display: flex;
  flex-direction: column;
  align-items: center;

  @media (max-width: 768px) {
    padding: 0;
  }
\`;`;

content = content.replace(containerPattern, newContainer);

fs.writeFileSync(filePath, content, 'utf8');

console.log('[OK] Fixed mobile responsiveness and removed streak!');
console.log('\nChanges:');
console.log('  ✓ Container centered with flexbox');
console.log('  ✓ Progress stats wrap and center on mobile');
console.log('  ✓ Stat badges smaller on mobile');
console.log('  ✓ Goal progress bar width fixed (no disappearing)');
console.log('  ✓ Streak badge REMOVED (not tracking yet)');
console.log('  ✓ Floating badge stays visible on mobile');
console.log('  ✓ Achievement modal responsive width');
console.log('  ✓ Hint tooltip fits small screens');
console.log('  ✓ All elements properly padded and centered');
console.log('\nRestart React server to see responsive changes.');
