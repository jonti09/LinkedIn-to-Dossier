# LinkedIn Jobs Scraper (`linkedin-scrapper`)

A zero-dependency Python 3 script that opens Firefox via its built-in Marionette protocol, reuses your logged-in LinkedIn cookies from your local Firefox profile (`cookies.sqlite`) or an exported cookies JSON file, navigates to `https://www.linkedin.com/jobs/`, clicks **"Show all"**, and extracts full details for each job listing on the subsequent page.

## Output Schema

The script outputs a JSON array where each element has the following shape:

```json
{
  "jobId": "4489115739",
  "title": "Senior Software Engineer – Agentic Development",
  "description": "Full job description text...",
  "salaryRange": "115K GBP/yr - 145K GBP/yr",
  "remote": true,
  "company": "The Upstarts Club",
  "datePosted": "1 week ago",
  "contact": "https://www.linkedin.com/in/steve-di-piazza-0bb55b6b/"
}
```

*(Fields like `salaryRange` and `contact` are set to `null` if not provided on the listing.)*

## Usage

```bash
# Run with visible Firefox window (auto-detects your default Firefox profile cookies)
./scraper.py

# Run in headless mode and save output to jobs.json
./scraper.py --headless -o jobs.json

# Scrape only the first 5 listings on the subsequent page
./scraper.py --limit 5

# Click the second "Show all" section on https://www.linkedin.com/jobs/
./scraper.py --show-all-index 1

# Explicitly specify a Firefox profile directory or a cookies.json file
./scraper.py --profile ~/.config/mozilla/firefox/0csfnlo2.default-release
./scraper.py --cookies cookies.json
```
