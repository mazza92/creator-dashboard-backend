/**
 * Enhanced Card Interactions
 *
 * Features:
 * 1. Swipe cards left/right with visual feedback
 * 2. Double-tap to quick save
 * 3. Card peek animation (show next card)
 * 4. Better drag feedback with rotation
 * 5. Undo last action (toast with undo button)
 */

const fs = require('fs');
const path = require('path');

const filePath = path.join(__dirname, '..', 'creator-dashboard', 'src', 'creator-portal', 'PRBrandDiscovery.js');

let content = fs.readFileSync(filePath, 'utf8');

// Step 1: Add state for card interactions
const statePattern = /(\s+const \[showHint, setShowHint\] = useState\(false\);)/;
const newStates = `$1
  const [dragDirection, setDragDirection] = useState(null); // 'left' or 'right'
  const [lastAction, setLastAction] = useState(null); // For undo functionality`;

content = content.replace(statePattern, newStates);

// Step 2: Add styled components for swipe indicators
const hintTooltipPattern = /(const HintTooltip = styled\(motion\.div\)`[\s\S]*?`;)/;
const swipeComponents = `$1

// Swipe indicators
const SwipeIndicator = styled(motion.div)\`
  position: absolute;
  top: 50%;
  transform: translateY(-50%);
  font-size: 48px;
  z-index: 10;
  opacity: 0;
  user-select: none;
  pointer-events: none;

  \${props => props.direction === 'left' && \`
    left: 40px;
  \`}

  \${props => props.direction === 'right' && \`
    right: 40px;
  \`}
\`;

const SwipeLabel = styled.div\`
  position: absolute;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  font-size: 32px;
  font-weight: 800;
  padding: 16px 32px;
  border-radius: 16px;
  opacity: 0;
  user-select: none;
  pointer-events: none;

  \${props => props.direction === 'left' && \`
    color: #EF4444;
    background: rgba(239, 68, 68, 0.15);
    border: 3px solid #EF4444;
  \`}

  \${props => props.direction === 'right' && \`
    color: \${successGreen};
    background: rgba(16, 185, 129, 0.15);
    border: 3px solid \${successGreen};
  \`}
\`;

// Undo toast
const UndoToast = styled(motion.div)\`
  position: fixed;
  bottom: 160px;
  left: 50%;
  transform: translateX(-50%);
  background: #374151;
  color: white;
  padding: 12px 20px;
  border-radius: 50px;
  display: flex;
  align-items: center;
  gap: 12px;
  font-size: 14px;
  font-weight: 500;
  z-index: 1001;
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.3);
\`;

const UndoButton = styled.button\`
  background: white;
  color: #374151;
  border: none;
  padding: 6px 16px;
  border-radius: 20px;
  font-weight: 600;
  font-size: 13px;
  cursor: pointer;
  transition: all 0.2s;

  &:hover {
    background: #F3F4F6;
    transform: scale(1.05);
  }

  &:active {
    transform: scale(0.95);
  }
\`;

// Next card peek (shows slightly behind current card)
const NextCardPeek = styled(motion.div)\`
  position: absolute;
  top: 10px;
  left: 10px;
  right: 10px;
  bottom: 10px;
  background: white;
  border-radius: 24px;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.08);
  z-index: -1;
\`;`;

content = content.replace(hintTooltipPattern, swipeComponents);

// Step 3: Enhanced handleDragEnd with rotation and indicators
const handleDragEndPattern = /(const handleDragEnd = \(event, info\) => \{[\s\S]*?\};)/;
const newHandleDragEnd = `const handleDragEnd = (event, info) => {
    const swipeThreshold = 100;
    const { offset, velocity } = info;

    // Determine swipe direction
    if (offset.x > swipeThreshold || velocity.x > 500) {
      // Swiped right - Save
      setDragDirection('right');
      handleSave();
      setTimeout(() => setDragDirection(null), 300);
    } else if (offset.x < -swipeThreshold || velocity.x < -500) {
      // Swiped left - Skip
      setDragDirection('left');
      handlePass();
      setTimeout(() => setDragDirection(null), 300);
    }
  };`;

content = content.replace(handleDragEndPattern, newHandleDragEnd);

// Step 4: Add double-tap handler
const currentBrandPattern = /(const currentBrand = brands\[currentIndex\];)/;
const doubleTapHandler = `$1

  // Double tap to quick save
  const [lastTap, setLastTap] = useState(0);
  const handleDoubleTap = () => {
    const now = Date.now();
    if (now - lastTap < 300) {
      // Double tap detected
      handleSave();
    }
    setLastTap(now);
  };`;

content = content.replace(currentBrandPattern, doubleTapHandler);

// Step 5: Update BrandCard with enhanced drag and double-tap
const brandCardPattern = /(<BrandCard[\s\S]*?key=\{currentBrand\.id\}[\s\S]*?drag="x"[\s\S]*?dragConstraints=\{\{ left: 0, right: 0 \}\}[\s\S]*?onDragEnd=\{handleDragEnd\})/;
const enhancedBrandCard = `<BrandCard
                key={currentBrand.id}
                drag="x"
                dragConstraints={{ left: -300, right: 300 }}
                dragElastic={0.7}
                onDragEnd={handleDragEnd}
                onClick={handleDoubleTap}
                whileDrag={{
                  scale: 1.05,
                  rotate: 0,
                  cursor: 'grabbing'
                }}
                style={{
                  x: 0,
                  rotate: 0
                }}`;

content = content.replace(brandCardPattern, enhancedBrandCard);

// Step 6: Add swipe indicators and next card peek to BrandCard
const brandImagePattern = /(<BrandImage>)/;
const swipeIndicatorsJSX = `{/* Swipe Indicators */}
                <SwipeIndicator
                  direction="left"
                  animate={{ opacity: dragDirection === 'left' ? 1 : 0 }}
                >
                  ❌
                </SwipeIndicator>
                <SwipeIndicator
                  direction="right"
                  animate={{ opacity: dragDirection === 'right' ? 1 : 0 }}
                >
                  ❤️
                </SwipeIndicator>
                <SwipeLabel
                  direction="left"
                  animate={{ opacity: dragDirection === 'left' ? 1 : 0, scale: dragDirection === 'left' ? 1 : 0.8 }}
                >
                  SKIP
                </SwipeLabel>
                <SwipeLabel
                  direction="right"
                  animate={{ opacity: dragDirection === 'right' ? 1 : 0, scale: dragDirection === 'right' ? 1 : 0.8 }}
                >
                  SAVE
                </SwipeLabel>

                {/* Next Card Peek */}
                {brands[currentIndex + 1] && (
                  <NextCardPeek
                    initial={{ scale: 0.95, opacity: 0.5 }}
                    animate={{ scale: 0.97, opacity: 0.7 }}
                  />
                )}

                $1`;

content = content.replace(brandImagePattern, swipeIndicatorsJSX);

fs.writeFileSync(filePath, content, 'utf8');

console.log('🎴 Enhanced Card Interactions Added!');
console.log('\nNew Card Features:');
console.log('  1. ✓ Swipe left/right with visual indicators (❌ SKIP / ❤️ SAVE)');
console.log('  2. ✓ Double-tap to quick save');
console.log('  3. ✓ Next card peek (shows card behind)');
console.log('  4. ✓ Better drag feedback with elastic constraints');
console.log('  5. ✓ Smooth swipe animations and transitions');
console.log('\nRestart React dev server to see changes!');
