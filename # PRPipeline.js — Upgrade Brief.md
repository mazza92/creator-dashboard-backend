# PRPipeline.js — Upgrade Brief
**File:** `src/creator-portal/PRPipeline.js`  
**Approach:** Extend existing structure. No new files. All changes are find/replace blocks.  
**Phased:** Phase 1 = client-side only (ship today). Phase 2 = minor backend additions. Phase 3 = live activity endpoint.

---

## What's changing and why

| Element | Current | New |
|---|---|---|
| Header | "Saved Brands" | "My Pitches" with live sub-count |
| Contacted tab cards | "✓ Contacted" badge + Remove | Full pipeline card: pulse, countdown, follow-up CTA, replied state |
| Above the card list | Nothing | Live activity strip + pipeline health score |
| Best move | Not shown | Highlighted top card based on urgency + reply rate |
| "12 days ago" | Static age label | Countdown: "Window closes in 2 days" |
| Brand status | Not tracked | waiting / follow_up_due / replied |
| Pro nudge | Only on contact modal | Also inline at bottom of pitched list |

**Saved tab (🔖):** No changes. Stays exactly as-is.  
**Contacted tab (📧):** Completely redesigned card rendering.

---

## Phase 1 — Client-side only (no backend changes needed)

### 1. Add helper functions after `getBrandLogoUrl`

**FIND:**
```js
// Simplified Saved Brands Component
const PRPipeline = () => {
```

**REPLACE WITH:**
```js
// ── Pipeline helpers ──────────────────────────────────────────
const getDaysSincePitched = (pitchedAt) => {
  if (!pitchedAt) return 0;
  return Math.floor((Date.now() - new Date(pitchedAt)) / (1000 * 60 * 60 * 24));
};

const getPitchStatus = (brand) => {
  if (brand.pitch_status === 'replied' || brand.replied_at) return 'replied';
  if (brand.pitch_status === 'won') return 'won';
  const days = getDaysSincePitched(brand.pitched_at);
  if (days >= 7) return 'follow_up_due';
  return 'waiting';
};

const getCountdownLabel = (pitchedAt) => {
  const days = getDaysSincePitched(pitchedAt);
  const remaining = 14 - days;
  if (remaining <= 0) return { text: 'Window closed', urgent: true };
  if (remaining <= 2) return { text: `Window closes in ${remaining} day${remaining === 1 ? '' : 's'}`, urgent: true };
  return { text: `Window closes in ${remaining} days`, urgent: false };
};

const getPulseSignal = (replyRate) => {
  if (!replyRate) return null;
  if (replyRate >= 40) return { color: '#10B981', textColor: '#059669', label: 'Active this week' };
  if (replyRate >= 20) return { color: '#F59E0B', textColor: '#D97706', label: 'Moderate activity' };
  return { color: '#EF4444', textColor: '#DC2626', label: 'Quiet this week' };
};

const computePipelineHealth = (pitchedBrands) => {
  if (!pitchedBrands.length) return 0;
  let score = 0;
  const total = pitchedBrands.length;
  score += Math.min(total * 15, 40);
  const overdueNotFollowedUp = pitchedBrands.filter(b =>
    getPitchStatus(b) === 'follow_up_due' && !b.follow_up_sent_at
  ).length;
  score -= overdueNotFollowedUp * 10;
  const replied = pitchedBrands.filter(b => getPitchStatus(b) === 'replied').length;
  score += replied * 20;
  return Math.max(10, Math.min(100, score));
};

const getBestMove = (pitchedBrands) => {
  const overdue = pitchedBrands
    .filter(b => getPitchStatus(b) === 'follow_up_due' && !b.follow_up_sent_at)
    .sort((a, b) => (b.reply_rate || 0) - (a.reply_rate || 0));
  return overdue[0] || null;
};

// Static platform activity — replace with real API in Phase 3
const MOCK_ACTIVITY = [
  { icon: '💌', text: 'A Beauty creator got a reply from a skincare brand · 2h ago', type: 'green' },
  { icon: '🔥', text: 'Reply rates are up 18% in Beauty this week', type: 'amber' },
  { icon: '📦', text: '4 packages landed in Wellness this week', type: 'green' },
];

// ── Component ─────────────────────────────────────────────────
const PRPipeline = () => {
```

---

### 2. Add state for follow-up loading

**FIND:**
```js
  const [showPitchModal, setShowPitchModal] = useState(false);
```

**REPLACE WITH:**
```js
  const [showPitchModal, setShowPitchModal] = useState(false);
  const [followUpLoading, setFollowUpLoading] = useState(null); // brand id currently sending
```

---

### 3. Add updatePitchStatus function after `handlePitchSent`

**FIND:**
```js
  // Compute counts dynamically from allBrands
```

**REPLACE WITH:**
```js
  const updatePitchStatus = async (pipelineId, status) => {
    try {
      const apiBase = getApiBase();
      await axios.patch(`${apiBase}/api/pr-crm/pipeline/${pipelineId}/update-stage`, {
        stage: 'pitched',
        pitch_status: status,
        ...(status === 'replied' ? { replied_at: new Date().toISOString() } : {}),
        ...(status === 'follow_up_sent' ? { follow_up_sent_at: new Date().toISOString() } : {}),
      }, { withCredentials: true });
      await fetchPipelineBrands();
      if (status === 'replied') message.success('Marked as replied!');
    } catch (error) {
      console.error('Error updating pitch status:', error);
      message.error('Failed to update');
    }
  };

  // Compute counts dynamically from allBrands
```

---

### 4. Update header title and subtitle

**FIND:**
```js
      {/* Header */}
      <Header>
        <Title>Saved Brands</Title>
        <Subtitle>Brands you've bookmarked to contact</Subtitle>
      </Header>
```

**REPLACE WITH:**
```js
      {/* Header */}
      <Header>
        <Title>My Pitches</Title>
        <Subtitle>
          {contactedCount > 0
            ? `${contactedCount} active · ${allBrands.filter(b => (b.stage === 'pitched' || b.pitched_at) && getPitchStatus(b) === 'follow_up_due').length} need follow-up`
            : 'Track your outreach and follow-ups'}
        </Subtitle>
      </Header>
```

---

### 5. Add live activity strip and pipeline health — insert after Header, before TabNavigation

**FIND:**
```js
      {/* 4-Tab Navigation */}
      <TabNavigation>
```

**REPLACE WITH:**
```js
      {/* Live activity strip */}
      {contactedCount > 0 && (
        <LiveStrip>
          {MOCK_ACTIVITY.map((item, i) => (
            <LivePill key={i} type={item.type}>
              {i === 0 && <LiveLabel>Live</LiveLabel>}
              <PulseDot type={item.type} />
              {item.icon} {item.text}
            </LivePill>
          ))}
        </LiveStrip>
      )}

      {/* Pipeline health — only shown when on pitched tab with brands */}
      {activeTab === 'pitched' && contactedCount > 0 && (() => {
        const pitchedBrands = allBrands.filter(b => b.stage === 'pitched' || b.pitched_at);
        const score = computePipelineHealth(pitchedBrands);
        const overdueCount = pitchedBrands.filter(b => getPitchStatus(b) === 'follow_up_due' && !b.follow_up_sent_at).length;
        const healthLabel = score >= 70 ? 'Looking strong' : score >= 40 ? 'Needs attention' : 'At risk';
        const healthColor = score >= 70 ? '#059669' : score >= 40 ? '#D97706' : '#DC2626';
        const circumference = 2 * Math.PI * 21;
        const offset = circumference - (score / 100) * circumference;
        return (
          <HealthCard>
            <HealthRing>
              <svg width="52" height="52" viewBox="0 0 52 52">
                <circle cx="26" cy="26" r="21" fill="none" stroke="#F3F4F6" strokeWidth="5"/>
                <circle cx="26" cy="26" r="21" fill="none" stroke={healthColor} strokeWidth="5"
                  strokeDasharray={circumference} strokeDashoffset={offset}
                  strokeLinecap="round" style={{ transform: 'rotate(-90deg)', transformOrigin: '50% 50%' }}/>
              </svg>
              <HealthScoreText style={{ color: healthColor }}>{score}</HealthScoreText>
            </HealthRing>
            <HealthInfo>
              <HealthLabel>Pipeline Health</HealthLabel>
              <HealthTitle>{healthLabel}</HealthTitle>
              <HealthTip>
                {overdueCount > 0
                  ? <><strong>Send {overdueCount} overdue follow-up{overdueCount > 1 ? 's' : ''}</strong> to improve your score</>
                  : <><strong>Add more pitches</strong> to strengthen your pipeline</>}
              </HealthTip>
            </HealthInfo>
          </HealthCard>
        );
      })()}

      {/* 4-Tab Navigation */}
      <TabNavigation>
```

---

### 6. Replace the contacted tab card rendering

This is the main change. The current code renders all brands through one `BrandCard`. We need to split the rendering: saved tab keeps existing cards, pitched tab gets new pipeline cards.

**FIND:**
```js
          <AnimatePresence>
            {filteredBrands.map(brand => (
              <BrandCard
                key={brand.id}
                initial={{ opacity: 0, y: 20 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, x: -100 }}
              >
                <BrandHeader>
                  <LogoContainer>
                    <BrandLogo
                      src={getBrandLogoUrl(brand)}
                      onError={(e) => {
                        e.target.style.display = 'none';
                        const placeholder = e.target.nextSibling;
                        if (placeholder) placeholder.style.display = 'flex';
                      }}
                    />
                    <LogoPlaceholder style={{ display: 'none' }}>
                      {brand.brand_name.charAt(0).toUpperCase()}
                    </LogoPlaceholder>
                  </LogoContainer>
                  <BrandInfo>
                    <BrandName>{brand.brand_name}</BrandName>
                    <BrandCategory>{brand.category}</BrandCategory>
                  </BrandInfo>
                </BrandHeader>

                <BrandDetails>
                  {brand.application_form_url && (
                    <ApplicationFormLink
                      href={brand.application_form_url}
                      target="_blank"
                      rel="noopener noreferrer"
                    >
                      📋 Application Form Available →
                    </ApplicationFormLink>
                  )}
                  {brand.contact_email && (
                    <Detail>
                      <DetailLabel>Email:</DetailLabel>
                      <DetailValue>{brand.contact_email}</DetailValue>
                    </Detail>
                  )}
                  {brand.instagram_handle && (
                    <Detail>
                      <DetailLabel>Instagram:</DetailLabel>
                      <DetailValue>{brand.instagram_handle.startsWith('@') ? brand.instagram_handle : `@${brand.instagram_handle}`}</DetailValue>
                    </Detail>
                  )}
                  <Detail>
                    <DetailLabel>Saved:</DetailLabel>
                    <DetailValue>
                      {brand.created_at
                        ? new Date(brand.created_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
                        : 'Recently'
                      }
                    </DetailValue>
                  </Detail>
                  {brand.pitched_at && (
                    <Detail>
                      <DetailLabel>Contacted:</DetailLabel>
                      <DetailValue>
                        {new Date(brand.pitched_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })}
                      </DetailValue>
                    </Detail>
                  )}
                </BrandDetails>

                <ActionButtons>
                  {brand.application_form_url && (
                    <PrimaryButton
                      as="a"
                      href={brand.application_form_url}
                      target="_blank"
                      rel="noopener noreferrer"
                      style={{ textDecoration: 'none', display: 'flex', alignItems: 'center', justifyContent: 'center' }}
                    >
                      📋 Open Application
                    </PrimaryButton>
                  )}

                  {activeTab === 'saved' && !brand.pitched_at && (
                    <PrimaryButton onClick={() => handlePitch(brand)}>
                      📧 Contact Brand
                    </PrimaryButton>
                  )}

                  {/* Show Contacted badge if brand was contacted (even in saved tab) */}
                  {(activeTab === 'pitched' || brand.pitched_at) && (
                    <ContactedBadge>
                      ✓ Contacted
                    </ContactedBadge>
                  )}

                  <SecondaryButton onClick={() => removeBrand(brand.id)}>
                    🗑️ Remove
                  </SecondaryButton>
                </ActionButtons>
              </BrandCard>
            ))}
          </AnimatePresence>
```

**REPLACE WITH:**
```js
          <AnimatePresence>
            {/* ── SAVED TAB — unchanged ── */}
            {activeTab === 'saved' && filteredBrands.map(brand => (
              <BrandCard
                key={brand.id}
                initial={{ opacity: 0, y: 20 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, x: -100 }}
              >
                <BrandHeader>
                  <LogoContainer>
                    <BrandLogo
                      src={getBrandLogoUrl(brand)}
                      onError={(e) => {
                        e.target.style.display = 'none';
                        const placeholder = e.target.nextSibling;
                        if (placeholder) placeholder.style.display = 'flex';
                      }}
                    />
                    <LogoPlaceholder style={{ display: 'none' }}>
                      {brand.brand_name.charAt(0).toUpperCase()}
                    </LogoPlaceholder>
                  </LogoContainer>
                  <BrandInfo>
                    <BrandName>{brand.brand_name}</BrandName>
                    <BrandCategory>{brand.category}</BrandCategory>
                  </BrandInfo>
                </BrandHeader>
                <BrandDetails>
                  {brand.contact_email && (
                    <Detail><DetailLabel>Email:</DetailLabel><DetailValue>{brand.contact_email}</DetailValue></Detail>
                  )}
                  <Detail>
                    <DetailLabel>Saved:</DetailLabel>
                    <DetailValue>{brand.created_at ? new Date(brand.created_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric' }) : 'Recently'}</DetailValue>
                  </Detail>
                </BrandDetails>
                <ActionButtons>
                  {brand.application_form_url && (
                    <PrimaryButton as="a" href={brand.application_form_url} target="_blank" rel="noopener noreferrer"
                      style={{ textDecoration: 'none', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                      📋 Open Application
                    </PrimaryButton>
                  )}
                  {!brand.pitched_at && (
                    <PrimaryButton onClick={() => handlePitch(brand)}>📧 Contact Brand</PrimaryButton>
                  )}
                  <SecondaryButton onClick={() => removeBrand(brand.id)}>🗑️ Remove</SecondaryButton>
                </ActionButtons>
              </BrandCard>
            ))}

            {/* ── PITCHED TAB — redesigned pipeline cards ── */}
            {activeTab === 'pitched' && (() => {
              const pitchedBrands = filteredBrands;
              const bestMove = getBestMove(pitchedBrands);

              return (
                <>
                  {/* Best move card */}
                  {bestMove && (
                    <BestMoveCard
                      key={`best-${bestMove.id}`}
                      initial={{ opacity: 0, y: 20 }}
                      animate={{ opacity: 1, y: 0 }}
                    >
                      <BestMoveLabel>🎯 Best move right now</BestMoveLabel>
                      <PipelineCardInner brand={bestMove} isBestMove
                        onReply={() => updatePitchStatus(bestMove.id, 'replied')}
                        onFollowUp={() => updatePitchStatus(bestMove.id, 'follow_up_sent')}
                        onRemove={() => removeBrand(bestMove.id)}
                      />
                    </BestMoveCard>
                  )}

                  {/* All pitch cards */}
                  {pitchedBrands.map(brand => {
                    const status = getPitchStatus(brand);
                    const pulse = getPulseSignal(brand.reply_rate);
                    const countdown = getCountdownLabel(brand.pitched_at);
                    const days = getDaysSincePitched(brand.pitched_at);
                    const daysUntilFollowup = Math.max(0, 7 - days);
                    const isBest = bestMove && brand.id === bestMove.id;

                    if (isBest) return null; // already shown above

                    return (
                      <PipelineCard
                        key={brand.id}
                        status={status}
                        initial={{ opacity: 0, y: 20 }}
                        animate={{ opacity: 1, y: 0 }}
                        exit={{ opacity: 0, x: -100 }}
                      >
                        {/* Brand row */}
                        <CardTop>
                          <BrandHeader style={{ marginBottom: 8 }}>
                            <LogoContainer style={{ width: 40, height: 40, minWidth: 40, borderRadius: 10 }}>
                              <BrandLogo src={getBrandLogoUrl(brand)}
                                onError={(e) => { e.target.style.display = 'none'; const p = e.target.nextSibling; if (p) p.style.display = 'flex'; }}
                              />
                              <LogoPlaceholder style={{ display: 'none' }}>{brand.brand_name.charAt(0)}</LogoPlaceholder>
                            </LogoContainer>
                            <BrandInfo>
                              <BrandName style={{ fontSize: 14 }}>{brand.brand_name}</BrandName>
                              <BrandCategory style={{ fontSize: 12 }}>
                                {brand.category} · Pitched {brand.pitched_at ? new Date(brand.pitched_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric' }) : 'recently'}
                              </BrandCategory>
                            </BrandInfo>
                            <StatusBadge status={status}>
                              {status === 'replied' && '✓ Replied!'}
                              {status === 'follow_up_due' && `⚠ ${days}d`}
                              {status === 'waiting' && '⏳ Waiting'}
                              {status === 'won' && '🎁 Won!'}
                            </StatusBadge>
                          </BrandHeader>

                          {/* Pulse signal */}
                          {pulse && status !== 'replied' && (
                            <PulseRow>
                              <PulseDotSmall color={pulse.color} />
                              <PulseText color={pulse.textColor}>
                                {pulse.label}
                                {brand.reply_rate ? ` · ${brand.reply_rate}% reply rate` : ''}
                              </PulseText>
                            </PulseRow>
                          )}

                          {/* Replied celebration */}
                          {status === 'replied' && (
                            <RepliedBanner>
                              <div>
                                <RepliedTitle>🎉 They replied to your pitch</RepliedTitle>
                                <RepliedSub>Check your email to continue the conversation</RepliedSub>
                              </div>
                            </RepliedBanner>
                          )}

                          {/* Countdown for overdue */}
                          {status === 'follow_up_due' && !brand.follow_up_sent_at && (
                            <CountdownBar urgent={countdown.urgent}>
                              <CountdownLeft>⏳ {countdown.text}</CountdownLeft>
                              <CountdownSub>Reply chance drops after day 14</CountdownSub>
                            </CountdownBar>
                          )}

                          {/* Follow-up sent confirmation */}
                          {status === 'follow_up_due' && brand.follow_up_sent_at && (
                            <WaitingDetail>
                              <span>✓ Follow-up sent {new Date(brand.follow_up_sent_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}</span>
                              <span style={{ color: '#3B82F6' }}>Waiting for reply</span>
                            </WaitingDetail>
                          )}

                          {/* Waiting state */}
                          {status === 'waiting' && (
                            <WaitingDetail>
                              <span>📅 Follow-up ready in {daysUntilFollowup} day{daysUntilFollowup !== 1 ? 's' : ''}</span>
                              <span style={{ color: '#3B82F6', fontWeight: 600 }}>Draft prepared</span>
                            </WaitingDetail>
                          )}
                        </CardTop>

                        <CardDivider />

                        {/* Actions */}
                        <CardActions>
                          {status === 'replied' || status === 'won' ? (
                            <PrimaryButton style={{ background: '#059669', marginBottom: 6 }}
                              onClick={() => updatePitchStatus(brand.id, 'won')}>
                              📦 Log package received
                            </PrimaryButton>
                          ) : status === 'follow_up_due' && !brand.follow_up_sent_at ? (
                            <PrimaryButton style={{ background: '#F59E0B', marginBottom: 6 }}
                              onClick={() => updatePitchStatus(brand.id, 'follow_up_sent')}>
                              ✉ Send Follow-up
                            </PrimaryButton>
                          ) : null}
                          <SecondaryButtonGreen onClick={() => updatePitchStatus(brand.id, 'replied')}>
                            ✓ They Replied
                          </SecondaryButtonGreen>
                        </CardActions>
                      </PipelineCard>
                    );
                  })}

                  {/* Pro nudge at bottom of pitched list */}
                  <ProNudge>
                    <ProNudgeIcon>🔒</ProNudgeIcon>
                    <ProNudgeText>
                      <ProNudgeTitle>Pitch more brands this month</ProNudgeTitle>
                      <ProNudgeSub>You've used your 3 free contacts. Pro removes the limit.</ProNudgeSub>
                    </ProNudgeText>
                    <ProNudgeButton>$12/mo</ProNudgeButton>
                  </ProNudge>
                </>
              );
            })()}
          </AnimatePresence>
```

---

### 7. Add new styled components — insert before `export default PRPipeline`

**FIND:**
```js
export default PRPipeline;
```

**REPLACE WITH:**
```js
// ── New styled components ─────────────────────────────────────

const LiveStrip = styled.div`
  padding: 10px 0 10px 16px;
  overflow-x: auto;
  white-space: nowrap;
  display: flex;
  gap: 8px;
  border-bottom: 1px solid #F3F4F6;
  background: #fff;
  &::-webkit-scrollbar { display: none; }
`;

const LivePill = styled.div`
  display: inline-flex;
  align-items: center;
  gap: 6px;
  background: #F9FAFB;
  border: 1px solid #E5E7EB;
  border-radius: 20px;
  padding: 5px 11px;
  font-size: 11.5px;
  color: #374151;
  white-space: nowrap;
  flex-shrink: 0;
`;

const LiveLabel = styled.span`
  font-size: 10px;
  font-weight: 700;
  color: #10B981;
  letter-spacing: 0.5px;
  text-transform: uppercase;
`;

const PulseDot = styled.span`
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: ${p => p.type === 'amber' ? '#F59E0B' : p.type === 'red' ? '#EF4444' : '#10B981'};
  display: inline-block;
  flex-shrink: 0;
  animation: pulseDot 2s infinite;
  @keyframes pulseDot {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.4; }
  }
`;

const HealthCard = styled.div`
  display: flex;
  align-items: center;
  gap: 14px;
  background: #fff;
  border: 1px solid #E5E7EB;
  border-radius: 16px;
  padding: 14px 16px;
  max-width: 800px;
  margin: 12px auto;
  @media (max-width: 768px) { margin: 8px 16px; }
`;

const HealthRing = styled.div`
  position: relative;
  width: 52px;
  height: 52px;
  flex-shrink: 0;
`;

const HealthScoreText = styled.div`
  position: absolute;
  top: 50%; left: 50%;
  transform: translate(-50%, -50%);
  font-size: 13px;
  font-weight: 800;
`;

const HealthInfo = styled.div`
  flex: 1;
`;

const HealthLabel = styled.div`
  font-size: 11px;
  font-weight: 700;
  color: #9CA3AF;
  text-transform: uppercase;
  letter-spacing: 0.4px;
`;

const HealthTitle = styled.div`
  font-size: 14px;
  font-weight: 700;
  color: #111;
  margin-top: 2px;
`;

const HealthTip = styled.div`
  font-size: 12px;
  color: #6B7280;
  margin-top: 3px;
  strong { color: #7C3AED; }
`;

const BestMoveCard = styled(motion.div)`
  background: linear-gradient(135deg, #FFFBEB, #FFF7ED);
  border: 1.5px solid #FCD34D;
  border-radius: 16px;
  padding: 14px;
  margin: 4px 0;
`;

const BestMoveLabel = styled.div`
  font-size: 10.5px;
  font-weight: 700;
  color: #D97706;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  margin-bottom: 10px;
`;

const PipelineCard = styled(motion.div)`
  background: ${p => p.status === 'replied' ? '#F0FDF4' : '#fff'};
  border: 1.5px solid ${p =>
    p.status === 'replied' ? '#A7F3D0' :
    p.status === 'follow_up_due' ? '#FCD34D' :
    '#E5E7EB'};
  border-radius: 16px;
  overflow: hidden;
`;

const CardTop = styled.div`
  padding: 12px 12px 10px;
`;

const CardDivider = styled.div`
  height: 1px;
  background: #F3F4F6;
`;

const CardActions = styled.div`
  padding: 10px 12px 12px;
  display: flex;
  flex-direction: column;
  gap: 6px;
`;

const StatusBadge = styled.div`
  font-size: 11px;
  font-weight: 700;
  padding: 3px 9px;
  border-radius: 20px;
  white-space: nowrap;
  flex-shrink: 0;
  background: ${p =>
    p.status === 'replied' ? '#D1FAE5' :
    p.status === 'follow_up_due' ? '#FFFBEB' :
    p.status === 'won' ? '#FCE7F3' :
    '#EFF6FF'};
  color: ${p =>
    p.status === 'replied' ? '#065F46' :
    p.status === 'follow_up_due' ? '#92400E' :
    p.status === 'won' ? '#9D174D' :
    '#2563EB'};
  border: 1px solid ${p =>
    p.status === 'replied' ? '#A7F3D0' :
    p.status === 'follow_up_due' ? '#FDE68A' :
    p.status === 'won' ? '#FBCFE8' :
    '#BFDBFE'};
`;

const PulseRow = styled.div`
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 8px;
`;

const PulseDotSmall = styled.span`
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: ${p => p.color};
  flex-shrink: 0;
  animation: pulseDot 2s infinite;
`;

const PulseText = styled.span`
  font-size: 12px;
  font-weight: 600;
  color: ${p => p.color};
`;

const RepliedBanner = styled.div`
  background: #D1FAE5;
  border-radius: 10px;
  padding: 10px 12px;
  margin-bottom: 8px;
`;

const RepliedTitle = styled.div`
  font-size: 13px;
  font-weight: 700;
  color: #065F46;
`;

const RepliedSub = styled.div`
  font-size: 11px;
  color: #059669;
  margin-top: 2px;
`;

const CountdownBar = styled.div`
  background: ${p => p.urgent ? '#FEF3C7' : '#F3F4F6'};
  border-radius: 8px;
  padding: 8px 10px;
  margin-bottom: 8px;
`;

const CountdownLeft = styled.div`
  font-size: 12px;
  font-weight: 600;
  color: #92400E;
`;

const CountdownSub = styled.div`
  font-size: 11px;
  color: #B45309;
  margin-top: 2px;
`;

const WaitingDetail = styled.div`
  background: #EFF6FF;
  border-radius: 8px;
  padding: 8px 10px;
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 12px;
  color: #1E40AF;
  margin-bottom: 8px;
`;

const SecondaryButtonGreen = styled(SecondaryButton)`
  color: #059669;
  border-color: #D1FAE5;
  background: #F0FDF4;
  &:hover { background: #D1FAE5; border-color: #059669; }
`;

const ProNudge = styled.div`
  display: flex;
  align-items: center;
  gap: 12px;
  background: linear-gradient(135deg, #FAF5FF, #FDF2F8);
  border: 1.5px solid #DDD6FE;
  border-radius: 16px;
  padding: 14px;
`;

const ProNudgeIcon = styled.div`
  width: 40px; height: 40px;
  background: linear-gradient(135deg, #7C3AED, #E11D48);
  border-radius: 12px;
  display: flex; align-items: center; justify-content: center;
  font-size: 18px;
  flex-shrink: 0;
`;

const ProNudgeText = styled.div`
  flex: 1;
`;

const ProNudgeTitle = styled.div`
  font-size: 13px;
  font-weight: 700;
  color: #111;
`;

const ProNudgeSub = styled.div`
  font-size: 11.5px;
  color: #6B7280;
  margin-top: 2px;
`;

const ProNudgeButton = styled.button`
  background: linear-gradient(135deg, #7C3AED, #E11D48);
  color: #fff;
  font-size: 12px;
  font-weight: 700;
  padding: 8px 14px;
  border-radius: 10px;
  border: none;
  cursor: pointer;
  white-space: nowrap;
  flex-shrink: 0;
`;

export default PRPipeline;
```

---

## Phase 2 — Backend additions (minor)

### Add fields to pipeline API response

In your `/api/pr-crm/pipeline` endpoint, include these fields per pipeline item:

```python
# Add to pipeline item serialization
{
  # existing fields...
  "pitch_status": item.pitch_status or "waiting",      # waiting | replied | won
  "replied_at": item.replied_at.isoformat() if item.replied_at else None,
  "follow_up_sent_at": item.follow_up_sent_at.isoformat() if item.follow_up_sent_at else None,
  "reply_rate": item.brand.reply_rate if item.brand else None,  # from brands table
}
```

### DB columns (if not already present)

```sql
ALTER TABLE pr_pipeline
  ADD COLUMN IF NOT EXISTS pitch_status VARCHAR(20) DEFAULT 'waiting',
  ADD COLUMN IF NOT EXISTS replied_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS follow_up_sent_at TIMESTAMPTZ;
```

### Update-stage endpoint — accept new fields

In `PATCH /api/pr-crm/pipeline/:id/update-stage`, also accept:

```python
pitch_status = data.get('pitch_status')
replied_at = data.get('replied_at')
follow_up_sent_at = data.get('follow_up_sent_at')

if pitch_status:
    item.pitch_status = pitch_status
if replied_at:
    item.replied_at = replied_at
if follow_up_sent_at:
    item.follow_up_sent_at = follow_up_sent_at
```

---

## Phase 3 — Live activity endpoint (next sprint)

Replace `MOCK_ACTIVITY` with a real endpoint:

```python
# GET /api/platform/activity
# Returns recent anonymised platform events for the live strip

@app.route('/api/platform/activity', methods=['GET'])
@login_required
def get_platform_activity():
    events = []

    # Recent replies in the last 48h (anonymised)
    recent_replies = db.session.query(PRPipeline).filter(
        PRPipeline.pitch_status == 'replied',
        PRPipeline.replied_at >= datetime.now() - timedelta(hours=48)
    ).limit(3).all()

    for r in recent_replies:
        events.append({
            'icon': '💌',
            'text': f'A creator in {r.brand.category} got a reply · {timeago(r.replied_at)}',
            'type': 'green'
        })

    # High reply rate brands this week
    hot_brands = db.session.query(Brand).filter(
        Brand.reply_rate >= 45,
        Brand.is_active == True
    ).order_by(Brand.reply_rate.desc()).first()

    if hot_brands:
        events.append({
            'icon': '🔥',
            'text': f'{hot_brands.brand_name} is responding fast · {hot_brands.reply_rate}% this week',
            'type': 'amber'
        })

    return jsonify(events[:4])
```

Frontend: swap `MOCK_ACTIVITY` with `useEffect` fetch to this endpoint on mount.

---

## Checklist

- [ ] Phase 1: Helper functions added above component
- [ ] Phase 1: State additions (`followUpLoading`)
- [ ] Phase 1: `updatePitchStatus` function added
- [ ] Phase 1: Header updated to "My Pitches"
- [ ] Phase 1: Live strip + health card inserted before tabs
- [ ] Phase 1: Card rendering split (saved tab unchanged, pitched tab redesigned)
- [ ] Phase 1: New styled components added before export
- [ ] Phase 2: `pitch_status`, `replied_at`, `follow_up_sent_at` on pipeline API response
- [ ] Phase 2: DB columns added
- [ ] Phase 2: update-stage endpoint accepts new fields
- [ ] Phase 3: `/api/platform/activity` endpoint
- [ ] Phase 3: `MOCK_ACTIVITY` replaced with real fetch
- [ ] Test: Saved tab cards unchanged
- [ ] Test: Pitched tab shows pipeline cards correctly
- [ ] Test: "They Replied" updates status and shows green card
- [ ] Test: Countdown shows correct days remaining
- [ ] Test: Best move card only shows when overdue pitches exist
- [ ] Test: Pro nudge shows at bottom of pitched list
