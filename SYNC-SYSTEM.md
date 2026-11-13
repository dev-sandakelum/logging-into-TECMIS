# TECMIS Redis Sync System

This document explains how the TECMIS notice scraper syncs with Redis for real-time notifications.

## Overview

The system consists of two workflows:
1. **Scrape Workflow** - Scrapes TECMIS notices every 6 hours
2. **Sync Workflow** - Syncs notices to Redis with duplicate prevention

## How It Works

### 1. Scraping (`scrape-notices.yml`)
```
Every 6 hours:
├── Login to TECMIS
├── Scrape notices page
├── Extract all notices (Recent + Previous)
├── Save to data/notices.json
└── Commit if changed
```

### 2. Syncing to Redis (`sync-redis.yml`)
```
After scrape completes:
├── Fetch existing TECMIS notices from Redis
├── Compare local notices with Redis
├── Download PDFs for new notices
├── Upload PDFs to GitHub
├── Sync only NEW notices to Redis
└── Use GitHub URLs for attachments
```

## Key Features

### ✅ Duplicate Prevention
- Checks if notice already exists in Redis before syncing
- Uses notice ID as unique identifier
- Never overwrites existing notices

### ✅ All Notices Stored
- Syncs BOTH "Recent" and "Previous" sections
- Not just the latest 5 notices
- Complete historical archive

### ✅ Smart Attachment Handling
- Downloads PDFs/documents only for new notices
- Stores in `data/attachments/` folder
- Commits to GitHub for CDN hosting
- Uses GitHub raw URLs in Redis (faster, no TECMIS login needed)

### ✅ Type Safety
- TypeScript definitions in `db-monitor-api/types/index.d.ts`
- Python type hints in `types/notices.py`
- Ensures API compatibility

## Data Flow

```
TECMIS Website
    ↓
Scraper (scrape.py)
    ↓
data/notices.json (Git commit)
    ↓
Sync Script (sync-redis.yml)
    ↓
├── Check existing notices in Redis
├── Download new PDFs → data/attachments/
├── Commit PDFs to GitHub
└── POST new notices to Redis API
        ↓
    Redis (Upstash)
        ↓
    Mobile App / DB Monitor
```

## Notice Structure

### In notices.json:
```json
{
  "id": "notice-3190",
  "section": "recent",
  "no": 1,
  "posted_at": "2026-11-07/04:16",
  "title": "CA ELIGIBILITY OF TCS 2212",
  "url": "https://paravi.ruh.ac.lk/tecmis/forms/form_53_a.php?taskf=incl&noticeid=3190",
  "file": null
}
```

### In Redis:
```json
{
  "id": "tecmis-notice-3190",
  "title": "TECMIS: CA ELIGIBILITY OF TCS 2212",
  "body": "## CA ELIGIBILITY OF TCS 2212\n\n**Posted:** 2026-11-07/04:16\n\n**Section:** Recent\n\n[View/Download](https://raw.githubusercontent.com/...)",
  "type": "tecmis",
  "createdAt": "2026-11-13T10:30:00Z",
  "updatedAt": "2026-11-13T10:30:00Z",
  "done": false,
  "seen": false,
  "source": "tecmis-scraper"
}
```

## API Endpoints

### Core Endpoints
- `GET /api/items` - Get all items (including TECMIS notices)
- `GET /api/items/:id` - Get specific item
- `PUT /api/items/:id` - Create/update item
- `DELETE /api/items/:id` - Delete item

### TECMIS-Specific Endpoints
- `GET /api/tecmis-last-date` - Get latest notice date
- `DELETE /api/tecmis-delete?id=xxx` - Delete specific notice
- `DELETE /api/tecmis-delete-old` - Bulk delete old notices

## Configuration

### Required Secrets (GitHub Actions)
```
TECMIS_USER          - Your TECMIS username
TECMIS_PASS          - Your TECMIS password
DB_MONITOR_API_URL   - Redis API URL (e.g., https://xxx.vercel.app/api/items)
DB_MONITOR_API_TOKEN - API authentication token
```

### Local Development
Create `.env` file:
```env
TECMIS_USER=your_username
TECMIS_PASS=your_password
```

## Sync Statistics

Example output:
```
✓ Found 50 existing TECMIS notice(s) in Redis
Total notices in local file: 58

Found 8 new notice(s) to sync...
  Downloading: exam-timetable.pdf...
  ✓ Saved: exam-timetable.pdf

✓ 📌 Synced: AMENDED ICT EXAM TIME TABLE
✓ 📌 Synced: VERIFICATION RESULTS OF BICT
✓ 📁 Synced: NOTICE REGARDING REPEAT EXAMINATION
...

============================================================
Sync complete!
  New:     8
  Skipped: 0
  Failed:  0
  Total in Redis: 58
============================================================
```

## Troubleshooting

### "All notices already in Redis"
- Normal if no new notices since last sync
- System is working correctly

### "Failed to download PDF"
- TECMIS server may be slow/down
- File will be retried on next sync
- Notice still synced with TECMIS URL

### "HTTP 401 Unauthorized"
- Check `DB_MONITOR_API_TOKEN` secret
- Verify API URL is correct

### "Notice already exists (skipped)"
- Duplicate prevention working correctly
- Notice was synced in a previous run

## Maintenance

### Clear All TECMIS Notices from Redis
```bash
curl -X DELETE https://your-api.vercel.app/api/tecmis-delete-old \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"currentIds": []}'
```

### Manually Trigger Sync
1. Go to Actions tab on GitHub
2. Select "Sync TECMIS Notices to Redis"
3. Click "Run workflow"

### View Sync Logs
1. Go to Actions tab
2. Click on latest "Sync TECMIS Notices to Redis" run
3. Expand "Sync new notices to Redis" step

## Benefits

✅ **No Duplicates** - Each notice synced only once  
✅ **Complete Archive** - All notices stored, not just recent  
✅ **Fast Delivery** - GitHub CDN for attachments  
✅ **Reliable** - No TECMIS login required to view PDFs  
✅ **Efficient** - Only syncs new notices  
✅ **Type Safe** - Full type definitions for API  
✅ **Automatic** - Runs every 6 hours unattended  
