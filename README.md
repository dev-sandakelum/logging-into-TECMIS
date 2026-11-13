# TECMIS Notices Auto-Collector

Automatically logs into [TECMIS](https://paravi.ruh.ac.lk/tecmis) (Faculty of
Technology, University of Ruhuna), opens the **NOTICE** page, scrapes every
notice, and commits the result back to this repository on a schedule.

**Latest scraped notices: see [`NOTICES.md`](NOTICES.md)** (raw data:
[`data/notices.json`](data/notices.json)).

## What it does

Every 6 hours (and on demand via **Actions → Scrape TECMIS Notices → Run
workflow**), a GitHub Actions workflow:

1. Logs into TECMIS with your credentials (stored as encrypted repository
   secrets, never in the code).
2. Opens the NOTICE page (`forms/form_53_a.php`).
3. Extracts every notice from both tables — *Most Recent Notices* and
   *Previous Notices* — with number, posted date/time, title and download link.
4. Writes `data/notices.json` (canonical data) and `NOTICES.md` (readable
   table) and commits them only if something actually changed.
5. **Redis Sync (optional):** If notices changed and Redis API secrets are configured:
   - Downloads PDF/document attachments for new notices
   - Commits attachments to `data/attachments/` folder in GitHub
   - Pushes the 5 most recent notices to your DB Monitor API
   - Uses GitHub raw URLs for attachments (fast, reliable CDN delivery)

The scraper uses plain HTTPS requests + BeautifulSoup (no headless browser):
the TECMIS pages are server-rendered PHP, so login is a simple form POST and
the whole run finishes in well under a minute.

## Setup (2 minutes)

### 1. Add the secrets

Repository **Settings → Secrets and variables → Actions → New repository secret**:

| Secret        | Value                          |
|---------------|--------------------------------|
| `TECMIS_USER` | your TECMIS index number       |
| `TECMIS_PASS` | your TECMIS password           |
| `DB_MONITOR_API_URL` | your DB Monitor API URL (flexible format - see below) |
| `DB_MONITOR_API_TOKEN` | your API authentication token |

**Note:** The `DB_MONITOR_API_*` secrets are optional. If provided, the workflow will automatically push recent TECMIS notices to your Redis-backed notification API whenever notices change.

**API URL Format:** The sync script is smart and accepts any of these formats:
- `https://your-project.vercel.app` (domain only)
- `https://your-project.vercel.app/api` (API base path)
- `https://your-project.vercel.app/api/items` (full items endpoint)

It will automatically detect and construct the correct endpoints.

### 2. Run it

**Actions → Scrape TECMIS Notices → Run workflow** (or just wait for the
schedule). The first successful run creates `data/notices.json` and
`NOTICES.md`.

### 3. Watch it keep itself updated

Each run commits only when the scraped notices differ from the committed
state, so the commit history is a clean changelog of notice-board changes.

## Files

| File                                | Purpose                                            |
|-------------------------------------|----------------------------------------------------|
| `scrape.py`                         | The scraper (login → parse → write outputs)         |
| `.github/workflows/scrape-notices.yml` | The GitHub Actions schedule + auto-commit        |
| `requirements.txt`                  | Python dependencies                                 |
| `data/notices.json`                 | Scraped notices, machine-readable                  |
| `NOTICES.md`                        | Scraped notices, human-readable table              |

## Local usage

```bash
pip install -r requirements.txt

# with a local .env file (TECMIS_USER=... / TECMIS_PASS=...)
python scrape.py

# or with environment variables
TECMIS_USER=xxxxx TECMIS_PASS=yyyyy python scrape.py

# offline test against a saved NOTICE page
python scrape.py --parse-file "notices.html"

# also download every attachment into data/attachments/
TECMIS_USER=... TECMIS_PASS=... DOWNLOAD_PDFS=1 python scrape.py
```

## Configuration knobs

- **Schedule** — edit the `cron:` line in
  `.github/workflows/scrape-notices.yml` (UTC times).
- **Attachments** — the workflow comments out `DOWNLOAD_PDFS: '1'` by default;
  uncomment it to also archive every notice PDF/document into
  `data/attachments/` (PDFs are served publicly, no login needed for those).
- **SSL fallback** — if the university certificate ever expires again, set a
  repository variable `IGNORE_SSL_ERRORS=1`
  (Settings → Secrets and variables → Actions → *Variables* tab) and
  uncomment that line in the workflow. Not needed today: the current
  certificate is valid and verified.

## Caveats

- If TECMIS starts forcing a periodic password change for your account, the
  run fails with a clear message — log in once in a browser, change the
  password, update the `TECMIS_PASS` secret.
- The site is sometimes slow from outside Sri Lanka; the scraper retries each
  request up to 3 times with backoff before failing.
- Notices are public documents published by the faculty, and your credentials
  live only in encrypted repository secrets — so a public repo is fine. If you
  prefer the data not to be publicly browsable, flip the repository to
  private in **Settings** at any time; the workflow keeps working either way
  (private repos just consume Actions minutes).
