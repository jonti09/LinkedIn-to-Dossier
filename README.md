# Job Scraper & Dossier Pipeline

Automatically pull your recommended job listings from LinkedIn and prepare AI-tailored **Draft** applications in **Dossier** — complete with tailored resumes, cover letters, and direct links to the application portal.

## How It Works

1. **Uses Your Existing Firefox Login**: Reads your LinkedIn session directly from your local Firefox profile so you don't have to manage credentials or log in manually.
2. **Collects Recommended Listings**: Opens LinkedIn Jobs in headless Firefox, clicks **"Show all"**, and extracts full job descriptions, salary ranges, remote status, recruiter profiles, and direct application links (unwrapping external portals like Greenhouse, Ashby, Lever, and Workday).
3. **Drafts Applications in Dossier**: Connects to your local Dossier instance (`http://localhost:3000`), skips jobs you've already drafted or applied to, and triggers Dossier's AI to prepare a tailored resume and cover letter for each new role.
4. **Saves a Local Snapshot**: Writes the scraped listings and their Dossier links to `scrapped/<YYYY-MM-DD_HH-MM-SS>.json`.

## Prerequisites

- **Firefox** installed locally with an active LinkedIn login session.
- **Dossier** running locally at `http://localhost:3000` with MCP authorized (`npx -y mcp-remote http://localhost:3000/api/mcp 9696`).
- **[uv](https://docs.astral.sh/uv/)** (or Python 3.9+).

## Quick Start

```bash
# Scrape recommended jobs and draft new applications in Dossier
uv run scrape

# Test with the first 3 listings only
uv run scrape --limit 3
```

## Common Options

| Flag | Description |
| :--- | :--- |
| `--limit <N>` | Only process the first `N` listings on the page. |
| `--show-all-index <N>` | Choose which "Show all" section on LinkedIn Jobs to open (`0` = first section, `1` = second, etc.). |
| `--no-dossier` | Only scrape listings to `scrapped/<date-time>.json` without creating drafts in Dossier. |
| `--no-wait-ai` | Queue AI drafts in Dossier and exit immediately without waiting for generation to finish. |
| `--force-dossier` | Create a new draft in Dossier even if the job already exists. |
| `--no-headless` | Open a visible Firefox window to watch the browser while it scrapes. |
| `-o, --output <path>` | Save the JSON output to a custom file path. |
| `-q, --quiet` | Hide progress logs and only print the final JSON output. |
