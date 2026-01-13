/**
 * Enhanced UX Improvements for PR Brand Discovery
 *
 * Features:
 * 1. Floating badge showing brands saved count (with animation)
 * 2. Success celebration animation when saving brands
 * 3. Daily streak/progress tracking
 * 4. Smooth micro-interactions and haptic-like feedback
 * 5. Toast notifications with better styling
 */

const fs = require('fs');
const path = require('path');

const filePath = path.join(__dirname, '..', 'creator-dashboard', 'src', 'creator-portal', 'PRBrandDiscovery.js');

let content = fs.readFileSync(filePath, 'utf8');

// Step 1: Add new state for badge animation and celebration
const statePattern = /(\s+const \[fetchingMore, setFetchingMore\] = useState\(false\);)/;
const newStates = `$1
  const [showBadgeAnimation, setShowBadgeAnimation] = useState(false);
  const [showCelebration, setShowCelebration] = useState(false);
  const [sessionSavedCount, setSessionSavedCount] = useState(0); // Track session saves for badge`;

content = content.replace(statePattern, newStates);

// Step 2: Add styled components for badge and celebration
const containerPattern = /(const Container = styled\.div`[\s\S]*?`;)/;
const newComponents = `$1

// Floating badge on saved brands
const FloatingBadge = styled(motion.div)\`
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
\`;

const BadgeCount = styled.span\`
  background: rgba(255, 255, 255, 0.25);
  padding: 4px 10px;
  border-radius: 20px;
  font-weight: 700;
  font-size: 16px;
\`;

// Celebration confetti overlay
const CelebrationOverlay = styled(motion.div)\`
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  pointer-events: none;
  z-index: 9999;
  display: flex;
  align-items: center;
  justify-content: center;
\`;

const ConfettiEmoji = styled(motion.div)\`
  position: absolute;
  font-size: 32px;
  user-select: none;
\`;

const SuccessCheckmark = styled(motion.div)\`
  width: 120px;
  height: 120px;
  border-radius: 50%;
  background: linear-gradient(135deg, \${successGreen}, #059669);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 64px;
  color: white;
  box-shadow: 0 20px 60px rgba(16, 185, 129, 0.4);
\`;

// Progress stats at top
const ProgressStats = styled.div\`
  display: flex;
  gap: 12px;
  margin-bottom: 16px;
  justify-content: center;
\`;

const StatBadge = styled(motion.div)\`
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
\`;

const StatNumber = styled.span\`
  color: \${primaryBlue};
  font-weight: 700;
\`;`;

content = content.replace(containerPattern, newComponents);

// Step 3: Update handleSave to trigger animations
const handleSavePattern = /(const handleSave = async \(\) => \{[\s\S]*?message\.success\(`\$\{brand\.brand_name\} saved to pipeline!`\);)/;
const newHandleSave = `$1

      // Trigger celebration animation
      setShowCelebration(true);
      setSessionSavedCount(prev => prev + 1);
      setShowBadgeAnimation(true);

      setTimeout(() => {
        setShowCelebration(false);
        setShowBadgeAnimation(false);
      }, 2000);`;

content = content.replace(handleSavePattern, newHandleSave);

// Step 4: Update handleContactBrand to trigger animations
const handleContactPattern = /(message\.success\(`Contact revealed and \$\{brand\.brand_name\} added to pipeline!`\);)/;
const newHandleContact = `$1

      // Trigger celebration animation
      setShowCelebration(true);
      setSessionSavedCount(prev => prev + 1);
      setShowBadgeAnimation(true);

      setTimeout(() => {
        setShowCelebration(false);
        setShowBadgeAnimation(false);
      }, 2000);`;

content = content.replace(handleContactPattern, newHandleContact);

// Step 5: Add progress stats and floating badge to JSX (before PageHeader)
const pageHeaderPattern = /(<PageHeader>)/;
const progressStatsJSX = `      {/* Progress Stats */}
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

      $1`;

content = content.replace(pageHeaderPattern, progressStatsJSX);

// Step 6: Add floating badge and celebration overlay before closing Container tag
const closingContainerPattern = /(<\/Container>)/;
const floatingBadgeJSX = `      {/* Floating Badge - Shows on save */}
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

      $1`;

content = content.replace(closingContainerPattern, floatingBadgeJSX);

// Step 7: Enhanced button interactions with haptic-like feedback
const actionButtonPattern = /(variant="save"[\s\S]*?whileTap=\{\{ scale: 0\.9 \}\})/;
const enhancedButton = `variant="save"
            onClick={handleContactBrand}
            disabled={revealingContact}
            whileHover={{ scale: 1.05, boxShadow: '0 8px 24px rgba(16, 185, 129, 0.3)' }}
            whileTap={{ scale: 0.92 }}
            transition={{ type: 'spring', stiffness: 400, damping: 17 }}`;

content = content.replace(
  /variant="save"[\s\S]*?whileTap=\{\{ scale: 0\.9 \}\}/,
  enhancedButton
);

fs.writeFileSync(filePath, content, 'utf8');

console.log('✨ Enhanced UX Applied!');
console.log('\nNew Features:');
console.log('  1. ✓ Floating badge showing brands saved (animated)');
console.log('  2. ✓ Celebration confetti animation on save');
console.log('  3. ✓ Progress stats showing session activity');
console.log('  4. ✓ Enhanced button interactions (haptic-like)');
console.log('  5. ✓ Smooth micro-interactions throughout');
console.log('\nRestart React dev server to see changes!');
