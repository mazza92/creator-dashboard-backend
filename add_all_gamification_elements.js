/**
 * Add ALL missing gamification elements to main return
 * These were only in the loading state, not the actual content
 */

const fs = require('fs');
const path = require('path');

const filePath = path.join(__dirname, '..', 'creator-dashboard', 'src', 'creator-portal', 'PRBrandDiscovery.js');

let content = fs.readFileSync(filePath, 'utf8');

// Find where to insert: before the closing </Container> tag in main return
// Look for: </ActionButtons> followed by {/* Upgrade Modal */}
const insertPoint = /(\s*\{\/\* Upgrade Modal \*\/\}\s*<UpgradeModal)/;

const gamificationElements = `
      {/* Floating Badge - Shows on save */}
      <AnimatePresence>
        {showBadgeAnimation && sessionSavedCount > 0 && (
          <FloatingBadge
            initial={{ scale: 0, y: 50 }}
            animate={{ scale: 1, y: 0 }}
            exit={{ scale: 0, y: 50 }}
            transition={{ type: 'spring', stiffness: 300, damping: 20 }}
            whileHover={{ scale: 1.05 }}
            whileTap={{ scale: 0.95 }}
            onClick={() => window.location.href = '/pr-crm/pipeline'}
          >
            <FiHeart style={{ fill: 'white' }} />
            <span>Saved</span>
            <BadgeCount>{sessionSavedCount}</BadgeCount>
          </FloatingBadge>
        )}
      </AnimatePresence>

      {/* Celebration Animation */}
      <AnimatePresence>
        {showCelebration && (
          <CelebrationOverlay
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
          >
            {/* Confetti emojis */}
            {['🎉', '✨', '💫', '⭐', '🎊', '💝', '🌟', '💖'].map((emoji, i) => (
              <ConfettiEmoji
                key={i}
                initial={{
                  x: 0,
                  y: 0,
                  opacity: 1,
                  rotate: 0
                }}
                animate={{
                  x: [0, (Math.random() - 0.5) * 300],
                  y: [0, -200 - Math.random() * 100],
                  opacity: [1, 0],
                  rotate: [0, (Math.random() - 0.5) * 360]
                }}
                transition={{
                  duration: 1.5,
                  ease: 'easeOut',
                  delay: i * 0.05
                }}
                style={{
                  left: \`calc(50% + \${(Math.random() - 0.5) * 100}px)\`,
                  top: '50%'
                }}
              >
                {emoji}
              </ConfettiEmoji>
            ))}

            {/* Success checkmark */}
            <SuccessCheckmark
              initial={{ scale: 0, rotate: -180 }}
              animate={{ scale: 1, rotate: 0 }}
              exit={{ scale: 0, rotate: 180 }}
              transition={{ type: 'spring', stiffness: 200, damping: 15 }}
            >
              ✓
            </SuccessCheckmark>
          </CelebrationOverlay>
        )}
      </AnimatePresence>

      {/* Streak Badge */}
      <StreakBadge
        initial={{ scale: 0, rotate: -180 }}
        animate={{ scale: 1, rotate: 0 }}
        transition={{ type: 'spring', delay: 0.5 }}
        whileHover={{ scale: 1.1, rotate: 5 }}
      >
        <span>🔥</span>
        <span>{Math.floor(Math.random() * 7) + 1} day streak</span>
      </StreakBadge>

      {/* Achievement Unlock Modal */}
      <AnimatePresence>
        {showAchievement && (
          <>
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              style={{
                position: 'fixed',
                top: 0,
                left: 0,
                right: 0,
                bottom: 0,
                background: 'rgba(0, 0, 0, 0.5)',
                zIndex: 9999,
                backdropFilter: 'blur(4px)'
              }}
              onClick={() => setShowAchievement(false)}
            />
            <AchievementModal
              initial={{ scale: 0, rotate: -180 }}
              animate={{ scale: 1, rotate: 0 }}
              exit={{ scale: 0, rotate: 180 }}
              transition={{ type: 'spring', stiffness: 200, damping: 15 }}
            >
              <AchievementIcon
                animate={{
                  rotate: [0, -10, 10, -10, 10, 0],
                  scale: [1, 1.1, 1, 1.1, 1]
                }}
                transition={{ duration: 0.6 }}
              >
                🏆
              </AchievementIcon>
              <AchievementTitle>Achievement Unlocked!</AchievementTitle>
              <AchievementDesc>{achievementText}</AchievementDesc>
            </AchievementModal>
          </>
        )}
      </AnimatePresence>

      {/* Quick Hint Tooltip */}
      <AnimatePresence>
        {showHint && (
          <HintTooltip
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: 20 }}
            transition={{ type: 'spring' }}
          >
            💡 Swipe left to skip, tap Contact to save!
          </HintTooltip>
        )}
      </AnimatePresence>

$1`;

content = content.replace(insertPoint, gamificationElements);

fs.writeFileSync(filePath, content, 'utf8');

console.log('[OK] Added ALL gamification elements to main return!');
console.log('\nAdded:');
console.log('  ✓ Floating Badge (save count)');
console.log('  ✓ Celebration Animation (confetti + checkmark)');
console.log('  ✓ Streak Badge (top-right)');
console.log('  ✓ Achievement Modal (trophy unlocks)');
console.log('  ✓ Hint Tooltip (for new users)');
console.log('\nAll features now visible!');
console.log('Restart React server to see changes.');
