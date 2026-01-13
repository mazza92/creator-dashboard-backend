# Bio URL Reminder System

## Overview
The bio URL reminder system automatically sends email notifications to creators every time they publish a new ad slot opportunity. This helps increase brand interest and bid amounts by encouraging creators to add their public profile URL to their social media bio links.

## How It Works

### 1. Automatic Trigger
- **When**: Every time a creator publishes a new ad slot opportunity (sponsor draft)
- **Where**: Both `/creator/first-ad-slot` and `/sponsor-draft` endpoints
- **Action**: Automatically sends bio URL reminder email

### 2. Email Notification
Sends a personalized email with:
- Creator's public profile URL
- Instructions for adding to social media bios
- Benefits of having bio links
- Direct link to update profile

## Email Template

### HTML Template: `templates/bio_url_reminder.html`
- Beautiful, responsive design
- Gradient header with Newcollab branding
- Step-by-step instructions for each platform
- Statistics showing benefits of bio links
- Call-to-action button to update profile

### Text Template: `templates/bio_url_reminder.txt`
- Plain text version for email clients
- Same content as HTML version
- Clean, readable format

## API Endpoints

### Test Endpoint
```
POST /test-bio-url-reminder
Content-Type: application/json

{
  "creator_id": 123,
  "test_email": "creator@example.com"
}
```

**Response:**
```json
{
  "success": true,
  "creator_id": 123,
  "username": "creator_username",
  "public_profile_url": "https://newcollab.co/c/creator_username",
  "message": "Bio URL reminder test completed"
}
```

## Database Schema

### Creators Table
- `username`: Creator's unique username for public profile URL generation
- `user_id`: Links to users table for email and name information

### Public Profile URL Format
```
https://newcollab.co/c/{username}
```

## Implementation Details

### Function: `send_bio_url_reminder(creator_id, public_profile_url)`
1. Fetches creator's user information from database
2. Prepares personalized email data
3. Sends reminder email using custom template
4. Logs success/failure for monitoring

### Integration Points
- **First Ad Slot Creation**: `/creator/first-ad-slot` (POST)
- **Regular Ad Slot Creation**: `/sponsor-draft` (POST)
- Both trigger bio URL check after successful creation

## Email Content

### Subject Line
```
🚀 {first_name}, boost your brand bids with your bio link!
```

### Key Benefits Highlighted
- 3x More Brand Interest
- 40% Higher Bids  
- 2x Faster Responses
- Increased professionalism and credibility

### Platform Instructions
- **Instagram**: Profile → Edit Profile → Website field
- **TikTok**: Profile → Edit Profile → Website field  
- **YouTube**: Channel → Customize Channel → Links section

## Monitoring & Logs

### Success Logs
```
✅ Bio URL reminder sent successfully to creator@example.com
✅ Creator 123 already has bio URL in social links
```

### Error Logs
```
❌ Failed to send bio URL reminder to creator@example.com
🔥 Error in check_and_send_bio_url_reminder: [error details]
```

### Non-Critical Errors
```
Bio URL check failed (non-critical): [error details]
```

## Testing

### Manual Test
```bash
curl -X POST https://api.newcollab.co/test-bio-url-reminder \
  -H "Content-Type: application/json" \
  -d '{"test_email": "creator@example.com"}'
```

### Production Test
```bash
curl -X POST https://api.newcollab.co/test-bio-url-reminder \
  -H "Content-Type: application/json" \
  -d '{"creator_id": 123}'
```

## Benefits

### For Creators
- Increased brand interest and higher bids
- Professional appearance
- Easy profile discovery by brands
- Step-by-step guidance

### For Platform
- Higher conversion rates
- Better creator-brand matching
- Improved user engagement
- Professional platform image

## Future Enhancements

1. **Analytics**: Track email open rates and bio link adoption
2. **A/B Testing**: Test different email templates and subject lines
3. **Frequency Control**: Prevent duplicate reminders within time window
4. **Platform-Specific**: Different instructions for each social platform
5. **Success Tracking**: Monitor when creators add bio links after reminder

## Troubleshooting

### Common Issues
1. **No emails sent**: Check SMTP configuration and creator email validity
2. **Template errors**: Verify template files exist and are properly formatted
3. **Database errors**: Check creator_id exists and social_links field is valid JSON
4. **URL detection**: Ensure public profile URL format matches exactly

### Debug Steps
1. Check application logs for error messages
2. Test with `/test-bio-url-reminder` endpoint
3. Verify creator's social_links JSON structure
4. Confirm SMTP settings and email delivery
