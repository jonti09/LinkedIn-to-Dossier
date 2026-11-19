# Job Scraper & Dossier Pipeline

A zero-dependency Python 3 project that scrapes job listings via Firefox and automatically creates AI-tailored **Draft** applications in **Dossier** via MCP with job links pointing to the actual application URLs.

## Project Structure

```text
.
├── pyproject.toml
├── README.md
└── src/
    ├── __init__.py
    ├── __main__.py     # Entry point (`python3 src` or `python3 -m src`)
    ├── cli.py          # CLI argument parsing and workflow orchestration
    ├── cookies.py      # Firefox profile discovery and SQLite cookie loader
    ├── dossier.py      # Dossier MCP client, duplicate checking, and AI draft creation
    ├── marionette.py   # Pure-Python Firefox Marionette client
    └── scraper.py      # Job board browser navigation and DOM extraction
```

## Workflow

1. **Firefox Automation** ([`src/cookies.py`](src/cookies.py), [`src/marionette.py`](src/marionette.py)): Opens `https://www.linkedin.com/jobs/` using cookies from your local Firefox profile (`cookies.sqlite`) or a cookies JSON file.
2. **Find Jobs** ([`src/scraper.py`](src/scraper.py)): Clicks **"Show all"** on your recommended job collection.
3. **Scrape Detail** ([`src/scraper.py`](src/scraper.py)): Extracts the listing title, company, clean description, salary range, remote flag, posting date, recruiter contact URL, and direct application link (resolving external ATS redirects like Ashby, Greenhouse, Lever, etc.).
4. **Save Scraped Data** ([`src/cli.py`](src/cli.py)): Writes JSON output to `scrapped/<YYYY-MM-DD_HH-MM-SS>.json`.
5. **Dossier MCP Integration** ([`src/dossier.py`](src/dossier.py)):
   - Connects to Dossier (`http://localhost:3000/api/mcp`) via its authorized MCP tokens (`~/.mcp-auth/mcp-remote-v1`).
   - Checks existing Dossier applications (including existing **Draft** entries) by URL, LinkedIn job ID, and company + role to avoid duplicates.
   - Calls `draft_application` + `check_draft` so **Dossier's own built-in AI** generates the tailored resume, cover letter, and requirement match breakdown.
   - Sets the application's job link directly to the actual application URL (`apply`).

## Output Schema

Each scraped job produces an object structured as follows:

```json
{
  "postingLink": "https://www.linkedin.com/jobs/view/1234567890",
  "title": "Senior Software Engineer",
  "description": "Full job description text...",
  "salaryRange": "100K GBP/yr - 130K GBP/yr",
  "remote": true,
  "company": "Acme Corp",
  "datePosted": "1 week ago",
  "contact": "https://www.linkedin.com/in/example-recruiter/",
  "apply": "https://jobs.example.com/acme/senior-software-engineer",
  "dossierDraftId": "draft_example_123",
  "dossierApplicationId": "app_example_456",
  "dossierUrl": "http://localhost:3000/applications/app_example_456",
  "dossierStatus": "Draft"
}
```

## Usage

```bash
# Run via uv (either command alias or script path)
uv run scrape-to-dossier
# or
uv run scrape
# or
uv run src/cli.py

# Scrape only first 3 listings
uv run scrape-to-dossier --limit 3

# Run with visible Firefox GUI window
uv run scrape-to-dossier --no-headless

# Only scrape to scrapped/<date-time>.json without creating applications in Dossier
uv run scrape-to-dossier --no-dossier
```
