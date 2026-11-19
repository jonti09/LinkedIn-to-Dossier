# AGENTS.md

Guidelines and context for AI agents working in this repository (`job-dossier-pipeline`).

## Project Overview

This repository automates scraping job listings via Firefox (using the user's logged-in cookies) and creating AI-tailored **Draft** applications in **Dossier** (`http://localhost:3000/api/mcp`) via MCP.

## Architecture & Module Layout

All source modules live directly under `src/` (flat module layout configured in `pyproject.toml`):

- `src/cli.py`: CLI entry point, argument parsing, orchestration, and saving output to `scrapped/<YYYY-MM-DD_HH-MM-SS>.json`.
- `src/cookies.py`: Auto-detects the default Firefox profile (`~/.config/mozilla/firefox` or `~/.mozilla/firefox`) and reads `cookies.sqlite` (including WAL/SHM files) in a temporary directory to avoid database locks.
- `src/marionette.py`: Zero-dependency pure-Python client for Firefox's built-in Marionette protocol (`--marionette`).
- `src/scraper.py`: Launches headless Firefox with profile cookies, opens `https://www.linkedin.com/jobs/`, clicks `"Show all"`, scrolls and selects each job card on the subsequent page, expands truncated descriptions, and extracts:
  - `postingLink` (`https://www.linkedin.com/jobs/view/<id>`)
  - `title`, `company`, `description`, `salaryRange`, `remote`, `datePosted`, `contact`
  - `apply` (unwrapped external ATS portal URL from `/safety/go/?url=...` or internal LinkedIn job link)
- `src/dossier.py`: Connects to Dossier's MCP server (`http://localhost:3000/api/mcp`) using OAuth tokens stored in `~/.mcp-auth/mcp-remote-v1` (with automatic token refresh via `http://localhost:3000/api/oauth/token`):
  - Checks existing applications (`list_applications`) by normalized `apply`/`postingLink` URL, LinkedIn numeric Job ID, and `(company, role)` pair so duplicate drafts are never created.
  - Uses Dossier's built-in AI tool (`draft_application` followed by polling `check_draft`) rather than `save_application`, ensuring Dossier's configured local/remote AI model tailors the resume, cover letter, and requirement match.
  - Passes `apply` as `posting_url` so the Dossier application link points directly to the actual application portal.
  - Normalizes Dossier links in JSON output to `http://localhost:3000/applications/<id>`.

## Commands

Use `uv` to run and manage the project:

```bash
# Run full pipeline (headless by default)
uv run scrape-to-dossier

# Or via short alias / direct script
uv run scrape
uv run src/cli.py

# Test with a small limit
uv run scrape-to-dossier --limit 3

# Scrape only (skip Dossier MCP sync)
uv run scrape-to-dossier --no-dossier

# Queue Dossier AI drafts without waiting for completion
uv run scrape-to-dossier --no-wait-ai

# Debug with a visible Firefox GUI window
uv run scrape-to-dossier --no-headless
```

## Coding Conventions & Rules

1. **Zero External Runtime Dependencies**: Keep the package self-contained using Python's standard library (`urllib.request`, `sqlite3`, `socket`, `subprocess`, `json`, `argparse`) and system `firefox`. Do not add heavy browser wrappers unless explicitly requested.
2. **Dossier AI Drafting**: Always use Dossier's `draft_application` + `check_draft` MCP tools when creating new drafts so Dossier's own AI model generates the tailored resume and cover letter.
3. **Deduplication First**: Always check `find_existing_application()` against `list_applications` (including existing `Draft` items) before triggering `draft_application`, unless `--force-dossier` is explicitly passed.
4. **Privacy & Security**:
   - Never commit cookies (`cookies.json`, `*.sqlite*`), OAuth tokens, or scraped outputs (`scrapped/`).
   - Never put personal user information, real recruiter profile links, or private tokens in `README.md` or committed documentation.
