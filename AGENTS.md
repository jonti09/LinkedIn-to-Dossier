# AGENTS.md

Guidelines and technical reference for AI agents working in this repository (`job-dossier-pipeline`).

## Project Overview

This repository automates scraping job listings via Firefox (using the user's logged-in cookies) and creating AI-tailored **Draft** applications in **Dossier** (`http://localhost:3000/api/mcp`) via MCP.

## Project Structure & Module Layout

All source modules live directly under `src/` (flat module layout configured in `pyproject.toml`):

```text
.
├── AGENTS.md
├── pyproject.toml
├── README.md
├── uv.lock
└── src/
    ├── __init__.py
    ├── __main__.py     # Entry point (`python3 src` or `python3 -m src`)
    ├── cli.py          # CLI argument parsing and workflow orchestration
    ├── cookies.py      # Firefox profile discovery and SQLite cookie loader
    ├── db.py           # SQLite job store (data/jobs.sqlite) with Dossier sync metadata
    ├── dossier.py      # Dossier MCP client, duplicate checking, and AI draft creation
    ├── marionette.py   # Pure-Python Firefox Marionette client
    └── scraper.py      # Job board browser navigation and DOM extraction
```

### Module Responsibilities

- `src/cli.py`: CLI entry point, argument parsing, orchestration, and persisting results to SQLite via `JobStore`.
- `src/db.py`: `JobStore` over `data/jobs.sqlite` (override with `--db`). Table `jobs` is keyed by the LinkedIn numeric job ID (primary key) and stores scraped fields plus Dossier metadata (`dossier_draft_id`, `dossier_application_id`, `dossier_url`, `dossier_status`, `dossier_error`, `dossier_synced_at`). New rows use `INSERT OR IGNORE`; existing rows are never overwritten by scrapes.
- `src/cookies.py`: Auto-detects the default Firefox profile (`~/.config/mozilla/firefox` or `~/.mozilla/firefox`) and reads `cookies.sqlite` (including WAL/SHM files) in a temporary directory to avoid SQLite database locks while Firefox is open.
- `src/marionette.py`: Zero-dependency pure-Python client for Firefox's built-in Marionette protocol (`--marionette`).
- `src/scraper.py`: Launches headless Firefox with profile cookies, opens `https://www.linkedin.com/jobs/`, clicks `"Show all"`, scrolls and selects each job card (`[role="button"][componentkey^="job-card-component-ref-"]`) on the subsequent page, expands truncated descriptions, extracts all job fields, and optionally filters listings via `--min-salary <X>` (requiring lower bound `P >= X` when a salary range `P - Q` is posted, while keeping listings with no posted salary).
- `src/dossier.py`: Connects to Dossier's MCP server (`http://localhost:3000/api/mcp`) using OAuth tokens stored in `~/.mcp-auth/mcp-remote-v1` (with automatic token refresh via `http://localhost:3000/api/oauth/token`):
  - Checks existing applications (`list_applications`) by normalized `apply`/`postingLink` URL, LinkedIn numeric Job ID, and `(company, role)` pair so duplicate drafts are never created.
  - Calls Dossier's built-in AI tool (`draft_application` followed by polling `check_draft`) rather than `save_application`, ensuring Dossier's configured local/remote AI model tailors the resume, cover letter, and requirement match breakdown.
  - Passes `apply` as `posting_url` so the Dossier application link points directly to the actual application portal.
  - Uses `--dossier-url` (defaulting to `localhost:3000`, normalized to `http://localhost:3000`) for both MCP/OAuth requests and `dossierUrl` links in the JSON output.

## Output JSON Schema

Jobs are stored in SQLite (`data/jobs.sqlite`). Jobs processed in a run are also printed to stdout as JSON, each item following this schema (`jobId` is the LinkedIn job ID / primary key):

```json
{
  "jobId": "1234567890",
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

- `salaryRange` and `contact` are `null` when unavailable.
- When a job is already tracked in Dossier, `dossierStatus` is set to `"already_exists (<Status>)"` and `dossierUrl` / `dossierApplicationId` point to the existing record.

## Commands

Use `uv` to run and manage the project:

```bash
# Run full pipeline (headless by default)
uv run scrape
# or
uv run scrape-to-dossier
# or
uv run src/cli.py

# Test with a small limit
uv run scrape --limit 3

# Sync jobs already in the DB that aren't in Dossier yet (no scraping; retries failed entries)
uv run scrape --from-db

# Import a legacy scraped JSON file into the DB
uv run scrape --from-json path/to/old.json

# Scrape only (skip Dossier MCP sync)
uv run scrape --no-dossier

# Queue Dossier AI drafts without waiting for completion
uv run scrape --no-wait-ai

# Debug with a visible Firefox GUI window
uv run scrape --no-headless
```

## Coding Conventions & Rules

1. **Zero External Runtime Dependencies**: Keep the package self-contained using Python's standard library (`urllib.request`, `sqlite3`, `socket`, `subprocess`, `json`, `argparse`) and system `firefox`. Do not add heavy browser wrappers unless explicitly requested.
2. **Dossier AI Drafting**: Always use Dossier's `draft_application` + `check_draft` MCP tools when creating new drafts so Dossier's own AI model generates the tailored resume and cover letter.
3. **Deduplication First**: The LinkedIn job ID (SQLite primary key) is the source of truth: the scraper skips IDs already in the DB and only jobs without a Dossier draft/application ID are synced. Additionally, always check `find_existing_application()` against `list_applications` (including existing `Draft` items) before triggering `draft_application`, unless `--force-dossier` is explicitly passed.
4. **Privacy & Security**:
   - Never commit cookies (`cookies.json`, `*.sqlite*`), OAuth tokens, or the scraped database (`data/`, `*.sqlite*`).
   - Never put personal user information, real recruiter profile links, or private tokens in `README.md` or committed documentation.
   - Always use `http://localhost:3000` (not `127.0.0.1:3000`) for user-facing `dossierUrl` fields.
