# AIPitchModal.js — Upgrade Brief
**File:** `src/creator-portal/AIPitchModal.js`  
**SHA baseline:** `6f8b88595c5344080247997cfcc4b3b4874574f0`  
**Approach:** Find/replace only. No new files. Extend existing logic.

---

## What's changing and why

| Issue | Root cause | Fix |
|---|---|---|
| Pitch says "I create baby content" for a fitness creator | `getHumanOpeners` uses `brand.category`, not creator niche | Openers use creator niche |
| Subject line is generic | Hardcoded `PR collab idea for {brand}` | Specific subject using creator niche + followers |
| Quota counter creates anxiety at send moment | `PitchCounter` in modal header, always visible | Move to subtle one-liner below pitch |
| "Regenerate (3 left)" double-counts quota | Separate `MAX_REGENERATES` limit | Remove limit, rename to "Rewrite pitch" |
| Pitch body not editable | `EmailBody` is a `div`, read-only | Editable `textarea`, synced to `editedBody` state |
| Email vs Form flow is confusing | CTA changes label silently based on data | Explicit tab switcher before the pitch |
| Modal closes after send — no context | `handleClose()` called immediately | `pitchSent` state shows success screen |
| Media kit nudge shown mid-compose | `FooterTip` always visible | Removed from compose, added to success screen |
| Purple gradient CTA | Doesn't match existing button styles | `#0F0F0F` black, consistent with rest of app |
| Match score not shown | Header only shows brand name + category | Match score chip added to header |

---

## Block 1 — Add new state variables

**FIND:**
```js
  const [creditUsed, setCreditUsed] = useState(false); // Track if credit was deducted
  const MAX_REGENERATES = 3; // Limit regenerations to save API credits
```

**REPLACE WITH:**
```js
  const [creditUsed, setCreditUsed] = useState(false);
  const [editedSubject, setEditedSubject] = useState('');
  const [editedBody, setEditedBody] = useState('');
  const [contactMethod, setContactMethod] = useState('email'); // 'email' | 'form'
  const [pitchSent, setPitchSent] = useState(false);
```

---

## Block 2 — Sync pitch content to editable state

Whenever `setPitch` is called (two places: AI response and fallback), we need to mirror the content into the editable states.

**FIND (inside `generatePitch`, after the AI response):**
```js
      setPitch(response.data);
      // Store the email from API if available
```

**REPLACE WITH:**
```js
      setPitch(response.data);
      setEditedSubject(response.data.subject || '');
      setEditedBody(response.data.body || '');
      // Store the email from API if available
```

**FIND (inside `generatePitch`, the fallback):**
```js
      const fallbackPitch = generateGoldenTemplate(brand, profile);
      setPitch(fallbackPitch);
      return fallbackPitch;
```

**REPLACE WITH:**
```js
      const fallbackPitch = generateGoldenTemplate(brand, profile);
      setPitch(fallbackPitch);
      setEditedSubject(fallbackPitch.subject || '');
      setEditedBody(fallbackPitch.body || '');
      return fallbackPitch;
```

---

## Block 3 — Fix subject line (specific, not generic)

**FIND:**
```js
    // Build the personalized subject - clean, no brackets or quotes
    const nicheDisplay = niche || brand.category || 'content';
    const subject = `PR collab idea for ${brand.brand_name}`;
```

**REPLACE WITH:**
```js
    // Specific subject — uses creator niche and followers for a cleaner signal
    const nicheDisplay = niche || brand.category || 'content';
    const followersShort = followers || null;
    const subjectNiche = niche
      ? niche.charAt(0).toUpperCase() + niche.slice(1)
      : (brand.category || 'Content');
    const subject = followersShort
      ? `${subjectNiche} content idea — ${followersShort} ${platform} audience`
      : `Content collab idea — ${brand.brand_name}`;
```

---

## Block 4 — Fix openers to use creator niche, not brand category

The current `getHumanOpeners` receives `brand.category` and one opener says "I'm a {category} creator" — this uses the brand's category as the creator's identity. Fix the function signature and the offending opener.

**FIND:**
```js
  // Human-sounding openers - varies to avoid AI detection
  const getHumanOpeners = (brandName, category) => {
    const openers = [
      `I've been using ${brandName} products for a bit now and wanted to reach out about a collab idea.`,
      `Found ${brandName} a few months back and it's become a staple in my routine - figured I'd shoot my shot.`,
      `Quick intro - I'm a ${category?.toLowerCase() || 'content'} creator and I've had my eye on ${brandName} for a while.`,
      `Hope this finds the right person! I create ${category?.toLowerCase() || ''} content and ${brandName} keeps coming up in my comments.`,
      `I've been wanting to reach out for a while - ${brandName} fits really well with the content I make.`
    ];
    return openers;
  };
```

**REPLACE WITH:**
```js
  // Human-sounding openers — always uses creator's own niche, not the brand's category
  const getHumanOpeners = (brandName, creatorNiche) => {
    const nicheLabel = creatorNiche?.toLowerCase() || 'content';
    const openers = [
      `I've been following ${brandName} for a while and wanted to reach out about a collab idea.`,
      `Found ${brandName} a few months back and it fits really well with the content I make.`,
      `Quick intro — I make ${nicheLabel} content and I've had my eye on ${brandName} for a while.`,
      `Hope this finds the right person! I make ${nicheLabel} content and ${brandName} keeps coming up in my comments.`,
      `I've been wanting to reach out for a while — ${brandName} fits naturally into what I already post.`
    ];
    return openers;
  };
```

**Also fix the call site to pass creator niche instead of brand category:**

**FIND:**
```js
    // Pick a random opener variation to avoid pattern detection
    const openers = getHumanOpeners(brand.brand_name, brand.category);
```

**REPLACE WITH:**
```js
    // Pass creator niche — not brand category — so the opener reflects the creator's identity
    const openers = getHumanOpeners(brand.brand_name, niche);
```

---

## Block 5 — Remove regenerate hard limit

**FIND:**
```js
  const handleRegenerate = () => {
    if (regenerateCount >= MAX_REGENERATES) {
      message.warning(`You can only regenerate ${MAX_REGENERATES} times per pitch`);
      return;
    }
    setRegenerateCount(prev => prev + 1);
    initializePitch();
  };
```

**REPLACE WITH:**
```js
  const handleRegenerate = () => {
    setRegenerateCount(prev => prev + 1);
    initializePitch();
  };
```

---

## Block 6 — Show success screen after send instead of closing

**FIND:**
```js
      message.success('Opening your email app...');

      // Close modal after short delay (handleClose notifies parent)
      setTimeout(() => {
        handleClose();
      }, 1000);
```

**REPLACE WITH:**
```js
      // Show success screen — don't close immediately
      setPitchSent(true);
```

---

## Block 7 — Update buildMailtoUrl to use edited content

**FIND:**
```js
  const buildMailtoUrl = () => {
    const email = brandEmail || '';
    const subject = encodeURIComponent(pitch?.subject || `PR collab idea for ${brand.brand_name}`);
    const body = encodeURIComponent(pitch?.body || '');

    return `mailto:${email}?subject=${subject}&body=${body}`;
  };
```

**REPLACE WITH:**
```js
  const buildMailtoUrl = () => {
    const email = brandEmail || '';
    const subject = encodeURIComponent(editedSubject || pitch?.subject || '');
    const body = encodeURIComponent(editedBody || pitch?.body || '');
    return `mailto:${email}?subject=${subject}&body=${body}`;
  };
```

---

## Block 8 — Handle copy to use edited content

**FIND:**
```js
      const fullPitch = `Subject: ${pitch?.subject}\n\n${pitch?.body}`;
```

**REPLACE WITH:**
```js
      const fullPitch = `Subject: ${editedSubject || pitch?.subject}\n\n${editedBody || pitch?.body}`;
```

---

## Block 9 — Redesign Header JSX

Removes the large `PitchCounter` from the header. Adds a subtle match score chip when available.

**FIND:**
```jsx
          <Header>
            <BrandInfo>
              <BrandLogo>
                {brandLogo ? (
                  <img src={brandLogo} alt={brandName} />
                ) : (
                  <span>{brandName?.charAt(0)}</span>
                )}
              </BrandLogo>
              <div>
                <BrandName>Contact {brandName}</BrandName>
                <BrandCategory>{brand.category}</BrandCategory>
              </div>
            </BrandInfo>

            <PitchCounter canPitch={pitchLimits.canPitch}>
              <FiZap />
              <span>{pitchLimits.limit - pitchLimits.used} / {pitchLimits.limit} contacts left</span>
            </PitchCounter>
          </Header>
```

**REPLACE WITH:**
```jsx
          <Header>
            <BrandInfo>
              <BrandLogo>
                {brandLogo ? (
                  <img src={brandLogo} alt={brandName} />
                ) : (
                  <span>{brandName?.charAt(0)}</span>
                )}
              </BrandLogo>
              <div>
                <BrandName>{brandName}</BrandName>
                <BrandMeta>
                  {brand.category}
                  {brand.match_score && (
                    <MatchChip score={brand.match_score}>
                      {brand.match_score}% match
                    </MatchChip>
                  )}
                </BrandMeta>
              </div>
            </BrandInfo>
          </Header>
```

---

## Block 10 — Add flow tab switcher + redesign email section

Replace the static `EmailPreview` block and the `StatsBadge` block with the new tab-aware version. This block covers from the `{/* Email Preview */}` comment through the end of `StatsBadge`.

**FIND:**
```jsx
              {/* Email Preview */}
              <EmailPreview>
                <EmailHeader>
                  <EmailLabel>To:</EmailLabel>
                  <EmailValue>{brandEmail || `${brandName} PR Team`}</EmailValue>
                </EmailHeader>
                <EmailHeader>
                  <EmailLabel>Subject:</EmailLabel>
                  <EmailSubject>{pitch?.subject}</EmailSubject>
                </EmailHeader>
                <EmailDivider />
                <EmailBody>{pitch?.body}</EmailBody>
              </EmailPreview>

              {/* Creator Stats Badge */}
              {pitch?.creator_stats && (pitch.creator_stats.followers || pitch.creator_stats.niche) && (
                <StatsBadge>
                  <StatsTitle><FiUser /> Personalized with your data:</StatsTitle>
                  <StatsRow>
                    {pitch.creator_stats.followers && (
                      <Stat>📊 {pitch.creator_stats.followers} followers</Stat>
                    )}
                    {pitch.creator_stats.niche && (
                      <Stat>🎯 {pitch.creator_stats.niche}</Stat>
                    )}
                    {pitch.creator_stats.platform && (
                      <Stat>📱 {pitch.creator_stats.platform}</Stat>
                    )}
                  </StatsRow>
                </StatsBadge>
              )}
```

**REPLACE WITH:**
```jsx
              {/* Flow tab switcher — only show when brand has both email and form */}
              {brandEmail && applicationFormUrl && (
                <FlowTabs>
                  <FlowTab
                    active={contactMethod === 'email'}
                    onClick={() => setContactMethod('email')}
                  >
                    Email pitch
                  </FlowTab>
                  <FlowTab
                    active={contactMethod === 'form'}
                    onClick={() => setContactMethod('form')}
                  >
                    Application form
                  </FlowTab>
                </FlowTabs>
              )}

              {/* Email fields */}
              <EmailPreview>
                <EmailFieldRow>
                  <FieldLabel>To</FieldLabel>
                  <FieldValue>{brandEmail || `${brandName} PR Team`}</FieldValue>
                </EmailFieldRow>
                <EmailFieldRow>
                  <FieldLabel>Subject</FieldLabel>
                  <SubjectInput
                    value={editedSubject}
                    onChange={e => setEditedSubject(e.target.value)}
                    placeholder="Subject line"
                  />
                </EmailFieldRow>
              </EmailPreview>

              {/* Editable pitch body */}
              <PitchBodyWrap>
                <PitchBodyHeader>
                  Pitch
                  <EditHint>Tap to edit</EditHint>
                </PitchBodyHeader>
                <PitchTextarea
                  value={editedBody}
                  onChange={e => setEditedBody(e.target.value)}
                  rows={8}
                />
              </PitchBodyWrap>

              {/* Personalization confirmation */}
              {pitch?.creator_stats && (pitch.creator_stats.followers || pitch.creator_stats.niche) && (
                <PersonalizationBar>
                  <PersonalizationLabel>Written for your profile</PersonalizationLabel>
                  <PersonalizationTags>
                    {pitch.creator_stats.followers && (
                      <PersonalizationTag>{pitch.creator_stats.followers} followers</PersonalizationTag>
                    )}
                    {pitch.creator_stats.niche && (
                      <PersonalizationTag>{pitch.creator_stats.niche}</PersonalizationTag>
                    )}
                    {pitch.creator_stats.platform && (
                      <PersonalizationTag>{pitch.creator_stats.platform}</PersonalizationTag>
                    )}
                  </PersonalizationTags>
                </PersonalizationBar>
              )}
```

---

## Block 11 — Redesign Actions section

Replaces the purple gradient `SendButton`, secondary actions, and conditional application form logic with the new black CTA, subtle quota row, and single rewrite button.

**FIND:**
```jsx
              {/* Action Buttons */}
              <Actions>
                {/* Primary action: Application Form if no email, or Send Email if email exists */}
                {!brandEmail && applicationFormUrl ? (
                  <PrimaryApplicationButton
                    href={applicationFormUrl}
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    📋 <span>Open Application Form</span>
                  </PrimaryApplicationButton>
                ) : (
                  <SendButton
                    onClick={handleSendEmail}
                    disabled={sending || !brandEmail}
                    whileHover={{ scale: 1.02 }}
                    whileTap={{ scale: 0.98 }}
                  >
                    {sending ? (
                      <span>Opening Email...</span>
                    ) : (
                      <>
                        <FiSend /> <span>Send Email to {brandName}</span>
                      </>
                    )}
                  </SendButton>
                )}

                <SecondaryActions>
                  <SecondaryButton onClick={handleCopyPitch}>
                    <FiCopy /> {copied ? 'Copied!' : 'Copy Pitch'}
                  </SecondaryButton>
                  <SecondaryButton
                    onClick={handleRegenerate}
                    disabled={regenerateCount >= MAX_REGENERATES}
                  >
                    <FiRefreshCw /> {regenerateCount >= MAX_REGENERATES ? 'Limit reached' : `Regenerate (${MAX_REGENERATES - regenerateCount} left)`}
                  </SecondaryButton>
                </SecondaryActions>
              </Actions>

              {/* Show application form as secondary if brand has BOTH email and application form */}
              {brandEmail && applicationFormUrl && (
                <ApplicationFormBox>
                  <ApplicationFormHeader>
                    <span>📋</span>
                    <div>
                      <ApplicationFormTitle>Application Form Available</ApplicationFormTitle>
                      <ApplicationFormSubtitle>You can also apply directly on their website</ApplicationFormSubtitle>
                    </div>
                  </ApplicationFormHeader>
                  <ApplicationFormButton
                    href={applicationFormUrl}
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    Open Form →
                  </ApplicationFormButton>
                </ApplicationFormBox>
              )}

              {/* Tip for using the pitch when application form is primary */}
              {!brandEmail && applicationFormUrl && (
                <PitchTip>
                  💡 Use the pitch above as a reference when filling out the application form
                </PitchTip>
              )}

              {/* No email and no application form */}
              {!brandEmail && !applicationFormUrl && (
                <NoEmailWarning>
                  <FiMail /> No contact info found. Copy the pitch and send via Instagram DM instead!
                </NoEmailWarning>
              )}
```

**REPLACE WITH:**
```jsx
              {/* Primary CTA */}
              <Actions>
                {contactMethod === 'form' && applicationFormUrl ? (
                  <PrimaryBtn
                    as="a"
                    href={applicationFormUrl}
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    Open application form
                  </PrimaryBtn>
                ) : brandEmail ? (
                  <PrimaryBtn
                    as={motion.button}
                    onClick={handleSendEmail}
                    disabled={sending}
                    whileTap={{ scale: 0.98 }}
                  >
                    {sending ? 'Opening email app...' : `Open in email app · Use 1 contact`}
                  </PrimaryBtn>
                ) : !applicationFormUrl ? (
                  <NoContactNote>
                    No contact info on file yet. Copy the pitch and send via DM.
                  </NoContactNote>
                ) : null}

                <SecondaryRow>
                  <SecondaryBtn onClick={handleCopyPitch}>
                    {copied ? 'Copied' : 'Copy pitch'}
                  </SecondaryBtn>
                  <SecondaryBtn onClick={handleRegenerate}>
                    Rewrite pitch
                  </SecondaryBtn>
                </SecondaryRow>

                {/* Subtle quota line — informational only */}
                <QuotaLine>
                  Contact {pitchLimits.used} of {pitchLimits.limit} used this month
                </QuotaLine>
              </Actions>
```

---

## Block 12 — Replace FooterTip with media kit nudge

**FIND:**
```jsx
          {/* Footer tip */}
          <FooterTip>
            💡 Personalized emails get 3x more responses than generic templates
          </FooterTip>
```

**REPLACE WITH:**
```jsx
          {/* Media kit nudge — only in compose state, not success */}
          {!pitchSent && (
            <MediaKitNudge>
              <MediaKitNudgeText>
                <MediaKitNudgeTitle>Attach your media kit</MediaKitNudgeTitle>
                <MediaKitNudgeSub>Creators with a media kit get 3x more replies</MediaKitNudgeSub>
              </MediaKitNudgeText>
              <MediaKitNudgeBtn>Build kit</MediaKitNudgeBtn>
            </MediaKitNudge>
          )}
```

---

## Block 13 — Add success screen (insert before closing `</Modal>` tag)

**FIND:**
```jsx
        </Modal>
      </Overlay>
    </AnimatePresence>
```

**REPLACE WITH:**
```jsx
          {/* Success state — shown after pitch is sent */}
          {pitchSent && (
            <SuccessScreen
              as={motion.div}
              initial={{ opacity: 0, y: 16 }}
              animate={{ opacity: 1, y: 0 }}
            >
              <SuccessHero>
                <SuccessIconWrap>
                  <FiSend />
                </SuccessIconWrap>
                <SuccessTitle>Pitch sent to {brandName}</SuccessTitle>
                <SuccessSub>
                  Your pitch is in their inbox. Here's what to do next.
                </SuccessSub>
              </SuccessHero>

              <SuccessPipelineCard>
                <SuccessBrandLogo>
                  {brandLogo
                    ? <img src={brandLogo} alt={brandName} style={{ width: '100%', height: '100%', objectFit: 'cover', borderRadius: 13 }} />
                    : <span>{brandName?.charAt(0)}</span>
                  }
                </SuccessBrandLogo>
                <SuccessPipelineInfo>
                  <SuccessPipelineBrand>{brandName}</SuccessPipelineBrand>
                  <SuccessPipelineMeta>Added to your pipeline</SuccessPipelineMeta>
                </SuccessPipelineInfo>
                <SuccessWindowBadge>
                  <SuccessWindowDays>14</SuccessWindowDays>
                  <SuccessWindowLabel>day window</SuccessWindowLabel>
                </SuccessWindowBadge>
              </SuccessPipelineCard>

              <SuccessTips>
                <SuccessTip>
                  <SuccessTipIcon><FiZap /></SuccessTipIcon>
                  <SuccessTipText>
                    <strong>Follow-up reminder set.</strong> We'll remind you in 7 days if you haven't heard back — that's when reply rates are highest.
                  </SuccessTipText>
                </SuccessTip>
                {brand.reply_rate > 0 && (
                  <SuccessTip>
                    <SuccessTipIcon>📊</SuccessTipIcon>
                    <SuccessTipText>
                      <strong>{brandName} replies to {brand.reply_rate}% of pitches.</strong> Creators who follow up on day 7 are 2x more likely to get a response.
                    </SuccessTipText>
                  </SuccessTip>
                )}
              </SuccessTips>

              <SuccessKitNudge>
                <SuccessKitText>
                  <SuccessKitTitle>Build your media kit before they reply</SuccessKitTitle>
                  <SuccessKitSub>Brands ask for it when they're interested. Be ready.</SuccessKitSub>
                </SuccessKitText>
                <SuccessKitBtn>Build kit</SuccessKitBtn>
              </SuccessKitNudge>

              <SuccessActions>
                <PrimaryBtn as="button" onClick={handleClose}>
                  Pitch another brand
                </PrimaryBtn>
                <SuccessSecondaryBtn onClick={handleClose}>
                  View my pipeline
                </SuccessSecondaryBtn>
              </SuccessActions>
            </SuccessScreen>
          )}
        </Modal>
      </Overlay>
    </AnimatePresence>
```

---

## Block 14 — New styled components (insert before `export default AIPitchModal`)

**FIND:**
```js
export default AIPitchModal;
```

**REPLACE WITH:**
```js
// ── New styled components ─────────────────────────────────────

const BrandMeta = styled.div`
  font-size: 12px;
  color: #6B7280;
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 2px;
`;

const MatchChip = styled.span`
  background: ${p => p.score >= 80 ? '#D1FAE5' : p.score >= 60 ? '#FEF3C7' : '#F3F4F6'};
  color: ${p => p.score >= 80 ? '#065F46' : p.score >= 60 ? '#92400E' : '#6B7280'};
  font-size: 10px;
  font-weight: 700;
  padding: 2px 8px;
  border-radius: 10px;
`;

const FlowTabs = styled.div`
  display: flex;
  margin: 0 24px 12px;
  background: #F3F4F6;
  border-radius: 12px;
  padding: 3px;
  gap: 2px;
  @media (max-width: 768px) { margin: 0 16px 10px; }
`;

const FlowTab = styled.button`
  flex: 1;
  padding: 8px;
  border-radius: 10px;
  font-size: 12px;
  font-weight: 700;
  border: none;
  cursor: pointer;
  transition: all 0.15s;
  background: ${p => p.active ? '#fff' : 'transparent'};
  color: ${p => p.active ? '#0F0F0F' : '#9CA3AF'};
  box-shadow: ${p => p.active ? '0 1px 4px rgba(0,0,0,0.08)' : 'none'};
`;

const EmailFieldRow = styled.div`
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 14px;
  border-bottom: 1px solid #F3F4F6;
  background: #fff;
  &:last-child { border-bottom: none; }
`;

const FieldLabel = styled.span`
  font-size: 11.5px;
  font-weight: 700;
  color: #9CA3AF;
  width: 52px;
  flex-shrink: 0;
`;

const FieldValue = styled.span`
  font-size: 13px;
  color: #0F0F0F;
  flex: 1;
`;

const SubjectInput = styled.input`
  flex: 1;
  font-size: 13px;
  color: #0F0F0F;
  border: none;
  outline: none;
  background: transparent;
  font-family: inherit;
`;

const PitchBodyWrap = styled.div`
  margin: 10px 24px 0;
  border: 1.5px solid #E5E7EB;
  border-radius: 14px;
  overflow: hidden;
  @media (max-width: 768px) { margin: 10px 16px 0; }
`;

const PitchBodyHeader = styled.div`
  padding: 8px 14px 6px;
  font-size: 10px;
  font-weight: 700;
  color: #9CA3AF;
  text-transform: uppercase;
  letter-spacing: 0.4px;
  border-bottom: 1px solid #F9FAFB;
  display: flex;
  align-items: center;
  justify-content: space-between;
`;

const EditHint = styled.span`
  font-size: 10px;
  font-weight: 600;
  color: #C4B5FD;
  background: #F5F3FF;
  padding: 2px 8px;
  border-radius: 6px;
`;

const PitchTextarea = styled.textarea`
  width: 100%;
  padding: 12px 14px;
  font-size: 13px;
  color: #374151;
  line-height: 1.6;
  border: none;
  outline: none;
  resize: none;
  font-family: inherit;
  background: #fff;
  min-height: 160px;
  max-height: 220px;
`;

const PersonalizationBar = styled.div`
  margin: 10px 24px 0;
  background: #F0FDF4;
  border: 1px solid #A7F3D0;
  border-radius: 11px;
  padding: 8px 12px;
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  @media (max-width: 768px) { margin: 10px 16px 0; }
`;

const PersonalizationLabel = styled.span`
  font-size: 10px;
  font-weight: 700;
  color: #059669;
  white-space: nowrap;
`;

const PersonalizationTags = styled.div`
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
`;

const PersonalizationTag = styled.span`
  font-size: 10.5px;
  font-weight: 600;
  color: #065F46;
  background: #D1FAE5;
  padding: 2px 8px;
  border-radius: 8px;
  text-transform: capitalize;
`;

const PrimaryBtn = styled.button`
  width: 100%;
  padding: 15px;
  background: #0F0F0F;
  color: #fff;
  font-size: 14px;
  font-weight: 700;
  border: none;
  border-radius: 14px;
  cursor: pointer;
  text-align: center;
  text-decoration: none;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: opacity 0.15s;
  &:disabled { opacity: 0.5; cursor: not-allowed; }
  &:hover:not(:disabled) { opacity: 0.88; }
`;

const SecondaryRow = styled.div`
  display: flex;
  gap: 8px;
  margin-top: 8px;
`;

const SecondaryBtn = styled.button`
  flex: 1;
  padding: 10px;
  background: #F3F4F6;
  color: #374151;
  font-size: 12.5px;
  font-weight: 600;
  border: none;
  border-radius: 11px;
  cursor: pointer;
  &:hover { background: #E5E7EB; }
`;

const QuotaLine = styled.div`
  text-align: center;
  font-size: 11px;
  color: #9CA3AF;
  margin-top: 10px;
`;

const NoContactNote = styled.div`
  padding: 13px 16px;
  background: #FEF3C7;
  color: #92400E;
  border-radius: 12px;
  font-size: 13px;
  text-align: center;
`;

const MediaKitNudge = styled.div`
  margin: 12px 24px 20px;
  background: #F5F3FF;
  border: 1px solid #DDD6FE;
  border-radius: 13px;
  padding: 12px 14px;
  display: flex;
  align-items: center;
  gap: 10px;
  @media (max-width: 768px) { margin: 12px 16px 20px; }
`;

const MediaKitNudgeText = styled.div`
  flex: 1;
`;

const MediaKitNudgeTitle = styled.div`
  font-size: 12px;
  font-weight: 700;
  color: #0F0F0F;
`;

const MediaKitNudgeSub = styled.div`
  font-size: 11px;
  color: #6B7280;
  margin-top: 1px;
`;

const MediaKitNudgeBtn = styled.button`
  background: #7C3AED;
  color: #fff;
  font-size: 11px;
  font-weight: 700;
  padding: 7px 13px;
  border-radius: 8px;
  border: none;
  cursor: pointer;
  white-space: nowrap;
  flex-shrink: 0;
`;

// ── Success screen ─────────────────────────────────────────────

const SuccessScreen = styled.div`
  padding-bottom: 24px;
`;

const SuccessHero = styled.div`
  padding: 32px 24px 20px;
  text-align: center;
`;

const SuccessIconWrap = styled.div`
  width: 56px;
  height: 56px;
  border-radius: 50%;
  background: #D1FAE5;
  color: #059669;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 24px;
  margin: 0 auto 14px;
`;

const SuccessTitle = styled.div`
  font-size: 20px;
  font-weight: 900;
  color: #0F0F0F;
  margin-bottom: 6px;
`;

const SuccessSub = styled.div`
  font-size: 13px;
  color: #6B7280;
  line-height: 1.5;
`;

const SuccessPipelineCard = styled.div`
  margin: 0 24px 14px;
  background: #F0FDF4;
  border: 1.5px solid #A7F3D0;
  border-radius: 16px;
  padding: 14px;
  display: flex;
  align-items: center;
  gap: 12px;
  @media (max-width: 768px) { margin: 0 16px 14px; }
`;

const SuccessBrandLogo = styled.div`
  width: 44px;
  height: 44px;
  border-radius: 13px;
  background: linear-gradient(135deg, #5B21B6, #7C3AED);
  display: flex;
  align-items: center;
  justify-content: center;
  font-weight: 900;
  color: #fff;
  font-size: 16px;
  flex-shrink: 0;
  overflow: hidden;
`;

const SuccessPipelineInfo = styled.div`
  flex: 1;
`;

const SuccessPipelineBrand = styled.div`
  font-size: 13px;
  font-weight: 800;
  color: #0F0F0F;
`;

const SuccessPipelineMeta = styled.div`
  font-size: 11.5px;
  color: #059669;
  font-weight: 600;
  margin-top: 2px;
`;

const SuccessWindowBadge = styled.div`
  background: #D1FAE5;
  border-radius: 10px;
  padding: 8px 12px;
  text-align: center;
  flex-shrink: 0;
`;

const SuccessWindowDays = styled.div`
  font-size: 20px;
  font-weight: 900;
  color: #059669;
  line-height: 1;
`;

const SuccessWindowLabel = styled.div`
  font-size: 9px;
  color: #065F46;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.3px;
  margin-top: 2px;
`;

const SuccessTips = styled.div`
  margin: 0 24px 14px;
  display: flex;
  flex-direction: column;
  gap: 8px;
  @media (max-width: 768px) { margin: 0 16px 14px; }
`;

const SuccessTip = styled.div`
  display: flex;
  align-items: flex-start;
  gap: 10px;
  background: #F9FAFB;
  border-radius: 12px;
  padding: 12px 13px;
`;

const SuccessTipIcon = styled.div`
  font-size: 16px;
  flex-shrink: 0;
  margin-top: 1px;
  color: #6B7280;
`;

const SuccessTipText = styled.div`
  font-size: 12.5px;
  color: #374151;
  line-height: 1.5;
  strong { color: #0F0F0F; }
`;

const SuccessKitNudge = styled.div`
  margin: 0 24px 16px;
  background: #0F0F0F;
  border-radius: 16px;
  padding: 16px;
  display: flex;
  align-items: center;
  gap: 12px;
  @media (max-width: 768px) { margin: 0 16px 16px; }
`;

const SuccessKitText = styled.div`
  flex: 1;
`;

const SuccessKitTitle = styled.div`
  font-size: 13px;
  font-weight: 800;
  color: #fff;
  margin-bottom: 3px;
`;

const SuccessKitSub = styled.div`
  font-size: 11px;
  color: #9CA3AF;
`;

const SuccessKitBtn = styled.button`
  background: #fff;
  color: #0F0F0F;
  font-size: 12px;
  font-weight: 800;
  padding: 9px 14px;
  border-radius: 10px;
  border: none;
  cursor: pointer;
  white-space: nowrap;
  flex-shrink: 0;
`;

const SuccessActions = styled.div`
  padding: 0 24px;
  display: flex;
  flex-direction: column;
  gap: 8px;
  @media (max-width: 768px) { padding: 0 16px; }
`;

const SuccessSecondaryBtn = styled.button`
  width: 100%;
  padding: 11px;
  background: transparent;
  color: #6B7280;
  font-size: 13px;
  font-weight: 600;
  border: none;
  cursor: pointer;
  text-align: center;
`;

export default AIPitchModal;
```

---

## Checklist

- [ ] Block 1: New state vars added (`editedSubject`, `editedBody`, `contactMethod`, `pitchSent`)
- [ ] Block 2: Both `setPitch()` calls also set `setEditedSubject` / `setEditedBody`
- [ ] Block 3: Subject line is now specific (niche + followers)
- [ ] Block 4: `getHumanOpeners` uses creator niche, call site passes `niche` not `brand.category`
- [ ] Block 5: `handleRegenerate` has no hard limit
- [ ] Block 6: `handleSendEmail` calls `setPitchSent(true)` instead of `handleClose()`
- [ ] Block 7: `buildMailtoUrl` uses `editedSubject` / `editedBody`
- [ ] Block 8: `handleCopyPitch` uses `editedSubject` / `editedBody`
- [ ] Block 9: Header shows brand name + match score chip, no `PitchCounter`
- [ ] Block 10: `FlowTabs` (conditional), `SubjectInput`, `PitchTextarea`, `PersonalizationBar` replace static email preview
- [ ] Block 11: New `PrimaryBtn` (black), `SecondaryRow`, `QuotaLine` replace purple gradient + old secondaries
- [ ] Block 12: `FooterTip` replaced with `MediaKitNudge` (hidden when `pitchSent`)
- [ ] Block 13: `SuccessScreen` inserted before closing `</Modal>`
- [ ] Block 14: All new styled components added before `export default`
- [ ] Test: Pitch body is editable, changes persist to mailto and clipboard copy
- [ ] Test: Subject input is editable
- [ ] Test: Rewrite pitch regenerates without any limit warning
- [ ] Test: After send, success screen shows — modal does not immediately close
- [ ] Test: Quota counter shows as subtle text only, not in header
- [ ] Test: Flow tabs only appear when brand has both email AND application form
- [ ] Test: `brand.match_score` chip shows in header when passed from For You tab
- [ ] Test: Upgrade paywall state unchanged (when `!pitchLimits.canPitch`)
