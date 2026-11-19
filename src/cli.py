#!/usr/bin/env python3
"""CLI entry point for job scraping & Dossier draft application pipeline."""

import argparse
from datetime import datetime
import json
import os
import sys

try:
    from .dossier import sync_jobs_to_dossier
    from .scraper import scrape_linkedin_jobs
except ImportError:
    from dossier import sync_jobs_to_dossier
    from scraper import scrape_linkedin_jobs


def get_project_root() -> str:
    """Resolve the repository root directory from src/cli.py."""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Scrape LinkedIn jobs via Firefox and create AI-tailored Draft applications in Dossier via MCP."
    )
    parser.add_argument(
        "--url",
        default="https://www.linkedin.com/jobs/",
        help="Initial LinkedIn Jobs URL to open (default: https://www.linkedin.com/jobs/)",
    )
    parser.add_argument(
        "--profile",
        default=None,
        help="Path to Firefox profile directory containing cookies.sqlite (auto-detected if omitted)",
    )
    parser.add_argument(
        "--cookies",
        default=None,
        help="Optional path to a JSON cookies file (if not using Firefox profile cookies.sqlite directly)",
    )
    parser.add_argument(
        "--headless",
        dest="headless",
        action="store_true",
        default=True,
        help="Run Firefox in headless mode (default: True)",
    )
    parser.add_argument(
        "--no-headless",
        "--headed",
        dest="headless",
        action="store_false",
        help="Run Firefox with a visible GUI window",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Maximum number of job listings to scrape from the subsequent page",
    )
    parser.add_argument(
        "--show-all-index",
        type=int,
        default=0,
        help="Which 'Show all' section button to click (0 = first section, 1 = second section, etc.)",
    )
    parser.add_argument(
        "--output",
        "-o",
        default=None,
        help="Output JSON file path (default: scrapped/<YYYY-MM-DD_HH-MM-SS>.json)",
    )
    parser.add_argument(
        "--dossier-url",
        default="localhost:3000",
        help="Dossier base URL or host:port (default: localhost:3000)",
    )
    parser.add_argument(
        "--no-dossier",
        action="store_true",
        help="Skip syncing scraped jobs to Dossier",
    )
    parser.add_argument(
        "--force-dossier",
        action="store_true",
        help="Create applications in Dossier even if the job is already present as a draft or sent application",
    )
    parser.add_argument(
        "--no-wait-ai",
        action="store_true",
        help="Trigger Dossier AI draft_application without waiting for completion",
    )
    parser.add_argument(
        "--quiet",
        "-q",
        action="store_true",
        help="Suppress progress logs on stderr",
    )

    args = parser.parse_args()
    headless = args.headless or not bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))

    jobs = scrape_linkedin_jobs(
        url=args.url,
        profile_dir=args.profile,
        cookies_file=args.cookies,
        headless=headless,
        limit=args.limit,
        show_all_index=args.show_all_index,
        verbose=not args.quiet,
    )

    if not args.no_dossier:
        sync_jobs_to_dossier(
            jobs,
            dossier_url=args.dossier_url,
            force=args.force_dossier,
            wait_for_ai=not args.no_wait_ai,
            verbose=not args.quiet,
        )

    formatted = json.dumps(jobs, indent=2, ensure_ascii=False)
    project_root = get_project_root()
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    default_out = os.path.join(project_root, "scrapped", f"{timestamp}.json")
    output_path = args.output if args.output else default_out

    out_dir = os.path.dirname(os.path.abspath(output_path))
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(formatted + "\n")
    if not args.quiet:
        sys.stderr.write(f"[linkedin-scrapper] Saved {len(jobs)} jobs to {output_path}\n")

    print(formatted)
    return 0


if __name__ == "__main__":
    sys.exit(main())
