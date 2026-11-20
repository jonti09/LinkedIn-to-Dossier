# Job Scraper & Dossier Pipeline

> [!WARNING]
> **Disclaimer**: This project is provided strictly for **educational and personal research purposes**. Automated scraping or data extraction is against [LinkedIn's Terms of Service](https://www.linkedin.com/legal/user-agreement). Use this tool responsibly and entirely at your own risk; the authors assume no liability for account restrictions or misuse.

Automatically pull your recommended job listings from LinkedIn and prepare AI-tailored **Draft** applications in **Dossier** — complete with tailored resumes, cover letters, and direct links to the application portal.

## How It Works

1. **Uses Your Existing Firefox Login**: Reads your LinkedIn session directly from your local Firefox profile so you don't have to manage credentials or log in manually.
2. **Collects Recommended Listings**: Opens LinkedIn Jobs in headless Firefox, clicks **"Show all"**, and extracts full job descriptions, salary ranges, remote status, recruiter profiles, and direct application links (unwrapping external portals like Greenhouse, Ashby, Lever, and Workday).
3. **Drafts Applications in Dossier**: Connects to your local Dossier instance (`http://localhost:3000`), skips jobs you've already drafted or applied to, and triggers Dossier's AI to prepare a tailored resume and cover letter for each new role.
4. **Saves a Local Snapshot**: Stores listings and Dossier metadata in a SQLite DB at `data/jobs.sqlite`, keyed by LinkedIn job ID (duplicates are never re-scraped or re-sent to Dossier).

## Prerequisites

- **Firefox** installed locally with an active LinkedIn login session.
- **Dossier** running locally (default: `http://localhost:3000`).
- **Node.js / `npx`** (used once to perform the initial OAuth handshake with Dossier via `mcp-remote`).
- **[uv](https://docs.astral.sh/uv/)** (or Python 3.9+).

## Setting Up the Dossier MCP Connection (First-Time Setup)

Dossier uses OAuth 2.0 to authorize MCP clients. On a brand-new setup, follow these steps once to link this tool to your Dossier instance:

1. **Start Dossier & Sign In**:
   Make sure Dossier is running (e.g. `docker compose up -d`) and sign in to the Dossier web UI at `http://localhost:3000` in your browser.
2. **Configure Your Profile & AI Provider in Dossier**:
   - Ensure you have created at least one candidate profile in Dossier (your experience, skills, and summary are used when tailoring resumes).
   - In Dossier's **Settings → AI**, configure an AI provider (such as a local Ollama model or an API key) so Dossier can automatically draft tailored resumes and cover letters.
3. **Enable the MCP Connector in Dossier**:
   - In Dossier, go to **Settings → Connector / MCP** (or open the connector prompt) and enable MCP access.
   - If prompted for an allowed callback/redirect port, allow `http://localhost:9696/oauth/callback` (port `9696`).
4. **Authorize via `mcp-remote`**:
   Run the following command in your terminal:
   ```bash
   npx -y mcp-remote http://localhost:3000/api/mcp 9696
   ```
   - This opens a browser tab asking you to approve the connection in Dossier.
   - Click **Approve / Connect**.
   - `mcp-remote` will save the OAuth client metadata and tokens inside `~/.mcp-auth/mcp-remote-v1/`. Once authorized, you can press `Ctrl+C` to exit `mcp-remote`.
5. **Ready to Run**:
   From now on, this pipeline automatically reads your tokens from `~/.mcp-auth/mcp-remote-v1/` and refreshes them via `http://localhost:3000/api/oauth/token` whenever they expire — no manual re-authentication needed.

## Quick Start

```bash
# Scrape recommended jobs and draft new applications in Dossier
uv run scrape

# Test with the first 3 listings only
uv run scrape --limit 3

# Retry jobs in the DB that are not yet in Dossier (no scraping)
uv run scrape --from-db

# Import a legacy scraped JSON file into the DB
uv run scrape --from-json path/to/old.json
```

## Common Options

| Flag | Description |
| :--- | :--- |
| `--from-db` | Skip scraping; sync DB jobs not yet in Dossier. |
| `-i, --from-json <path>` | Import a legacy JSON file into the DB. |
| `--db <path>` | SQLite path (default `data/jobs.sqlite`). |
| `--limit <N>` | Only process the first `N` listings on the page. |
| `--min-salary <X>` | Only include postings where the lower bound `P` of the posted salary range `P - Q` is `>= X` (e.g. `100000` or `100k`). Postings without a listed salary are kept. |
| `--show-all-index <N>` | Choose which "Show all" section on LinkedIn Jobs to open (`0` = first section, `1` = second, etc.). |
| `--dossier-url <url>` | Override the Dossier base URL (default: `localhost:3000`). |
| `--no-dossier` | Only scrape listings to the database without creating drafts in Dossier. |
| `--no-wait-ai` | Queue AI drafts in Dossier and exit immediately without waiting for generation to finish. |
| `--force-dossier` | Create a new draft in Dossier even if the job already exists. |
| `--no-headless` | Open a visible Firefox window to watch the browser while it scrapes. |
| `-q, --quiet` | Hide progress logs and only print the final JSON output. |

## Acknowledgements

Special thanks to [**Dossier**](https://github.com/AienTech/dossier/) for providing a fantastic self-hosted job search and AI application management platform with built-in MCP support.

## License

This project is licensed under the [MIT License](LICENSE).

