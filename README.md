# LinkedIn Jobs Scraper & Dossier Pipeline (`scrape_to_dossier.py`)

A zero-dependency Python 3 tool that scrapes job listings from LinkedIn via Firefox and automatically creates **Draft** applications in **Dossier** via MCP with job links pointing to the actual application URLs.

## Workflow

1. **Firefox Automation**: Opens `https://www.linkedin.com/jobs/` using cookies from your local Firefox profile (`cookies.sqlite`) or a cookies JSON file.
2. **Find Jobs**: Automatically clicks **"Show all"** on your recommended job collection.
3. **Scrape Detail**: Extracts the listing title, company, clean description, salary range, remote flag, posting date, recruiter contact URL, and direct application link (resolving external ATS redirects like Ashby, Greenhouse, Lever, etc.).
4. **Save Scraped Data**: Writes JSON output to `scrapped/<YYYY-MM-DD_HH-MM-SS>.json`.
5. **Dossier MCP Integration**: Connects to Dossier (`http://localhost:3000/api/mcp`) via its authorized MCP tokens (`~/.mcp-auth/mcp-remote-v1`), fetches your candidate profile, and calls `save_application` to create a **Draft** application in Dossier. The application's link is set directly to the actual application URL (`apply`).

## Output Schema

Each scraped job produces an object structured as follows:

```json
{
  "postingLink": "https://www.linkedin.com/jobs/view/4489115739",
  "title": "Senior Software Engineer – Agentic Development",
  "description": "Full job description text...",
  "salaryRange": "115K GBP/yr - 145K GBP/yr",
  "remote": true,
  "company": "The Upstarts Club",
  "datePosted": "1 week ago",
  "contact": "https://www.linkedin.com/in/steve-di-piazza-0bb55b6b/",
  "apply": "https://jobs.ashbyhq.com/example/job",
  "dossierApplicationId": "cmwnkcc0s007201qtblbap47k",
  "dossierUrl": "http://127.0.0.1:3000/applications/cmwnkcc0s007201qtblbap47k",
  "dossierStatus": "Draft"
}
```

## Usage

```bash
cd /home/saber/Projects/Job/linkedin-scrapper

# Scrape and create draft applications in Dossier (headless by default)
./scrape_to_dossier.py

# Scrape only first 3 listings
./scrape_to_dossier.py --limit 3

# Run with visible Firefox GUI window
./scrape_to_dossier.py --no-headless

# Only scrape to scrapped/<date-time>.json without creating applications in Dossier
./scrape_to_dossier.py --no-dossier

# Custom output file path
./scrape_to_dossier.py -o scrapped/custom_run.json
```
