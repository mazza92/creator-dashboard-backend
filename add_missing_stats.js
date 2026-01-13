/**
 * Add missing Progress Stats and Daily Goal to main return
 * They were only in the loading state, not the main content
 */

const fs = require('fs');
const path = require('path');

const filePath = path.join(__dirname, '..', 'creator-dashboard', 'src', 'creator-portal', 'PRBrandDiscovery.js');

let content = fs.readFileSync(filePath, 'utf8');

// Find the main return statement (after the loading check)
// Look for: return ( <Container> <PROnboarding... /> <PageHeader>
const mainReturnPattern = /return \(\s*<Container>\s*<PROnboarding visible=\{showOnboarding\}[^>]*\/>\s*<PageHeader>/;

// Add ProgressStats and GoalProgress before PageHeader
const replacement = `return (
    <Container>
      <PROnboarding visible={showOnboarding} onClose={() => setShowOnboarding(false)} />

      {/* Progress Stats */}
      <ProgressStats>
        <StatBadge
          whileHover={{ scale: 1.05 }}
          whileTap={{ scale: 0.95 }}
        >
          <span>🎯</span>
          <span>Today:</span>
          <StatNumber>{sessionSavedCount}</StatNumber>
        </StatBadge>
        <StatBadge
          whileHover={{ scale: 1.05 }}
          whileTap={{ scale: 0.95 }}
        >
          <span>📊</span>
          <span>Pipeline:</span>
          <StatNumber>{brandsSavedCount}</StatNumber>
        </StatBadge>
        <StatBadge
          whileHover={{ scale: 1.05 }}
          whileTap={{ scale: 0.95 }}
        >
          <span>⚡</span>
          <span>Viewed:</span>
          <StatNumber>{currentIndex}</StatNumber>
        </StatBadge>
      </ProgressStats>

      {/* Daily Goal Progress */}
      <GoalProgress>
        <GoalHeader>
          <GoalTitle>
            <span>🎯</span>
            <span>Daily Goal</span>
          </GoalTitle>
          <GoalCount>
            {sessionSavedCount}/{dailyGoal}
          </GoalCount>
        </GoalHeader>
        <ProgressBar>
          <ProgressFill
            initial={{ width: 0 }}
            animate={{ width: \`\${Math.min((sessionSavedCount / dailyGoal) * 100, 100)}%\` }}
            transition={{ type: 'spring', stiffness: 100, damping: 20 }}
          />
        </ProgressBar>
      </GoalProgress>

      <PageHeader>`;

content = content.replace(mainReturnPattern, replacement);

fs.writeFileSync(filePath, content, 'utf8');

console.log('[OK] Added missing progress stats and daily goal!');
console.log('\nAdded to main return:');
console.log('  - Progress Stats (Today/Pipeline/Viewed)');
console.log('  - Daily Goal Progress Bar');
console.log('\nRestart React server to see changes.');
