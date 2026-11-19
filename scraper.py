#!/usr/bin/env python3
"""
LinkedIn Jobs Scraper using Firefox (Marionette) + Firefox Profile Cookies.

Opens https://www.linkedin.com/jobs/ in Firefox using cookies from your local
Firefox profile (or a cookies JSON file), clicks "Show all" on the job listings
section, and scrapes detailed information from every job listing on the
subsequent page into JSON:

{
  "jobId": "...",
  "title": "...",
  "description": "...",
  "salaryRange": "... (or null)",
  "remote": true/false,
  "company": "...",
  "datePosted": "...",
  "contact": "https://www.linkedin.com/in/... (or null)"
}
"""

import argparse
import configparser
import glob
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from typing import Any, Dict, List, Optional


class MarionetteClient:
    """Minimal pure-Python client for Firefox's built-in Marionette protocol."""

    def __init__(self, host: str = "127.0.0.1", port: int = 2828, timeout: float = 60.0):
        self.host = host
        self.port = port
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.msg_id = 0
        self.buf = b""
        self.handshake = self._recv_packet()

    def _recv_packet(self) -> Any:
        while b":" not in self.buf:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise EOFError("Marionette socket closed while reading length prefix")
            self.buf += chunk
        len_str, rest = self.buf.split(b":", 1)
        length = int(len_str)
        while len(rest) < length:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise EOFError("Marionette socket closed mid-packet")
            rest += chunk
        payload = rest[:length]
        self.buf = rest[length:]
        return json.loads(payload.decode("utf-8"))

    def send(self, command: str, params: Optional[Dict[str, Any]] = None) -> Any:
        self.msg_id += 1
        msg = [0, self.msg_id, command, params or {}]
        raw = json.dumps(msg).encode("utf-8")
        packet = f"{len(raw)}:".encode("utf-8") + raw
        self.sock.sendall(packet)
        while True:
            resp = self._recv_packet()
            if isinstance(resp, list) and len(resp) == 4 and resp[0] == 1 and resp[1] == self.msg_id:
                if resp[2]:
                    raise RuntimeError(f"Marionette error on {command}: {resp[2]}")
                return resp[3]

    def execute_script(self, script: str, args: Optional[List[Any]] = None) -> Any:
        res = self.send("WebDriver:ExecuteScript", {"script": script, "args": args or []})
        return res.get("value") if isinstance(res, dict) and "value" in res else res

    def close(self) -> None:
        try:
            self.sock.close()
        except Exception:
            pass


def find_default_firefox_profile() -> Optional[str]:
    """Locate the user's default-release Firefox profile directory."""
    base_dirs = [
        os.path.expanduser("~/.config/mozilla/firefox"),
        os.path.expanduser("~/.mozilla/firefox"),
    ]
    for ff_dir in base_dirs:
        if not os.path.isdir(ff_dir):
            continue
        profiles_ini = os.path.join(ff_dir, "profiles.ini")
        if os.path.exists(profiles_ini):
            cp = configparser.ConfigParser()
            try:
                cp.read(profiles_ini)
                for section in cp.sections():
                    if cp.has_option(section, "Default") and "default-release" in cp.get(section, "Default"):
                        candidate = os.path.join(ff_dir, cp.get(section, "Default"))
                        if os.path.isdir(candidate):
                            return candidate
            except Exception:
                pass
        matches = glob.glob(os.path.join(ff_dir, "*.default-release"))
        if matches:
            return matches[0]
        matches = glob.glob(os.path.join(ff_dir, "*.default*"))
        if matches:
            return matches[0]
    return None


def load_cookies_from_sqlite(profile_dir: str, domain_filter: str = "linkedin.com") -> List[Dict[str, Any]]:
    """Read cookies from Firefox's cookies.sqlite (including WAL state)."""
    cookies_db = os.path.join(profile_dir, "cookies.sqlite")
    if not os.path.exists(cookies_db):
        raise FileNotFoundError(f"cookies.sqlite not found in {profile_dir}")

    cookies: List[Dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="ff_cookies_") as tmpdir:
        for ext in ["", "-wal", "-shm"]:
            src = cookies_db + ext
            if os.path.exists(src):
                shutil.copy2(src, os.path.join(tmpdir, "cookies.sqlite" + ext))

        tmp_db = os.path.join(tmpdir, "cookies.sqlite")
        conn = sqlite3.connect(tmp_db)
        cur = conn.cursor()
        cur.execute(
            "SELECT name, value, host, path, expiry, isSecure, isHttpOnly, sameSite "
            "FROM moz_cookies WHERE host LIKE ?",
            (f"%{domain_filter}%",),
        )
        same_site_map = {0: "None", 1: "Lax", 2: "Strict"}
        for name, value, host, path, expiry, is_sec, is_http, same_site in cur.fetchall():
            ss = same_site_map.get(same_site, "None")
            if ss == "None" and not is_sec:
                ss = "Lax"
            cookie: Dict[str, Any] = {
                "name": name,
                "value": value,
                "domain": host,
                "path": path or "/",
                "secure": bool(is_sec),
                "httpOnly": bool(is_http),
                "sameSite": ss,
            }
            if expiry and int(expiry) > 0:
                cookie["expiry"] = int(expiry)
            cookies.append(cookie)
        conn.close()
    return cookies


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


EXTRACT_JOB_JS = r"""
const jobId = arguments[0];

// 1. Locate job card in left list
const card = document.querySelector(`[role="button"][componentkey="job-card-component-ref-${jobId}"]`) ||
             document.querySelector(`[componentkey="job-card-component-ref-${jobId}"]`);
const cardText = card ? card.innerText : '';
const cardLines = cardText.split('\n').map(s => s.trim()).filter(Boolean);

// 2. Locate right-hand detail column
const detailCols = Array.from(document.querySelectorAll('[data-testid="lazy-column"]'))
  .filter(el => el.getAttribute('componentkey') !== 'SearchResultsMainContent');
const detailCol = detailCols[detailCols.length - 1] || document.body;
const detailText = detailCol.innerText || '';

// 3. Title
let title = null;
const detailTitleLink = document.querySelector(`a[href*="/jobs/view/${jobId}"]`);
if (detailTitleLink && detailTitleLink.innerText.trim()) {
  title = detailTitleLink.innerText.trim();
} else if (card) {
  const hiddenSpan = card.querySelector('p span[aria-hidden="true"]');
  if (hiddenSpan && hiddenSpan.innerText.trim()) {
    title = hiddenSpan.innerText.trim();
  }
}
if (!title && cardLines.length > 0) {
  title = cardLines[0].replace(/\s*\(Verified job\)\s*$/i, '').trim();
}

// 4. Company
let company = null;
const compLink = detailCol.querySelector(
  `a[componentkey$="-${jobId}"], a[href*="/company/"], a[href*="/school/"]`
);
if (compLink && compLink.innerText.trim()) {
  company = compLink.innerText.trim().split('\n')[0].trim();
}
if (!company && card) {
  const ps = Array.from(card.querySelectorAll('p')).map(p => p.innerText.trim()).filter(Boolean);
  if (ps.length >= 2) {
    company = ps[1].split('\n')[0].trim();
  }
}

// 5. Description
let description = null;
const aboutEl = document.querySelector(`[componentkey="JobDetails_AboutTheJob_${jobId}"]`) ||
                document.querySelector('[componentkey^="JobDetails_AboutTheJob_"]');
if (aboutEl && aboutEl.innerText.trim()) {
  description = aboutEl.innerText.replace(/^About the job\s*/i, '').trim();
}

// 6. Contact (recruiter / hiring team LinkedIn profile URL if available)
let contact = null;
const hiringEl = document.querySelector(`[componentkey="JobDetailsPeopleWhoCanHelpSlot_${jobId}"]`) ||
                 document.querySelector('[componentkey^="JobDetailsPeopleWhoCanHelpSlot_"]') ||
                 detailCol;
if (hiringEl) {
  const inLink = hiringEl.querySelector('a[href*="/in/"]');
  if (inLink && inLink.href) {
    contact = inLink.href.split('?')[0];
  }
}

// 7. Salary Range (if available)
const salaryRegex = /(?:[£$€]\s?\d+[\d,.]*\s*[kKmM]?(?:\s*(?:GBP|USD|EUR|CAD|AUD))?(?:\/(?:yr|year|hr|hour|mo|month|day))?(?:\s*[-–—to]+\s*[£$€]?\s?\d+[\d,.]*\s*[kKmM]?(?:\s*(?:GBP|USD|EUR|CAD|AUD))?(?:\/(?:yr|year|hr|hour|mo|month|day))?)?|\d+[\d,.]*\s*[kKmM]?\s*(?:GBP|USD|EUR|CAD|AUD)(?:\/(?:yr|year|hr|hour|mo|month|day))?(?:\s*[-–—to]+\s*\d+[\d,.]*\s*[kKmM]?\s*(?:GBP|USD|EUR|CAD|AUD)(?:\/(?:yr|year|hr|hour|mo|month|day))?)?)/i;

let salaryRange = null;
for (const line of cardLines) {
  if (/Try Premium for/i.test(line)) continue;
  if (/\/(yr|year|hr|hour|mo|month|day)\b/i.test(line) || /[£$€]\s?\d/i.test(line) || /\b(GBP|USD|EUR|CAD|AUD)\b/i.test(line)) {
    const m = line.match(salaryRegex);
    if (m && (/\d/.test(m[0]))) {
      salaryRange = m[0].trim();
      break;
    }
  }
}

const headerText = detailText.split('About the job')[0] || '';
if (!salaryRange) {
  for (const line of headerText.split('\n').map(s => s.trim()).filter(Boolean)) {
    if (/Try Premium for/i.test(line)) continue;
    if (/\/(yr|year|hr|hour|mo|month|day)\b/i.test(line) || /[£$€]\s?\d+\s*[kKmM,]/i.test(line) || /\b(GBP|USD|EUR|CAD|AUD)\/(yr|hr|mo)/i.test(line)) {
      const m = line.match(salaryRegex);
      if (m) {
        salaryRange = m[0].trim();
        break;
      }
    }
  }
}

if (!salaryRange && title) {
  const m = title.match(/(?:up to\s*)?[£$€]\s?\d+[\d,.]*\s*[kKmM]?(?:\s*[-–—to]+\s*[£$€]?\s?\d+[\d,.]*\s*[kKmM]?)?/i);
  if (m) salaryRange = m[0].trim();
}

if (!salaryRange && description) {
  const m = description.match(/(?:[£$€]\s?\d+[\d,.]*\s*[kK]?\s*[-–—to]+\s*[£$€]?\s?\d+[\d,.]*\s*[kK]?|\d+[\d,.]*\s*[kK]?\s*(?:GBP|USD|EUR)\/(?:yr|year)\s*[-–—to]+\s*\d+[\d,.]*\s*[kK]?\s*(?:GBP|USD|EUR)\/(?:yr|year))/i);
  if (m) salaryRange = m[0].trim();
}

// 8. Remote?
let remote = false;
if (/\bremote\b/i.test(cardText) || /\bremote\b/i.test(headerText)) {
  remote = true;
}

// 9. Date Posted
let datePosted = null;
const dateRe = /(?:Reposted\s+|Posted\s+)?(\d+\s+(?:second|minute|hour|day|week|month|year)s?\s+ago|just now|today|yesterday)/i;
const dmDetail = headerText.match(dateRe);
if (dmDetail) {
  datePosted = dmDetail[1].trim();
} else {
  const dmCard = cardText.match(dateRe);
  if (dmCard) datePosted = dmCard[1].trim();
}

return {
  jobId,
  title,
  description,
  salaryRange,
  remote,
  company,
  datePosted,
  contact
};
"""


def scrape_linkedin_jobs(
    url: str = "https://www.linkedin.com/jobs/",
    profile_dir: Optional[str] = None,
    cookies_file: Optional[str] = None,
    headless: bool = False,
    limit: Optional[int] = None,
    show_all_index: int = 0,
    verbose: bool = True,
) -> List[Dict[str, Any]]:
    def log(msg: str) -> None:
        if verbose:
            sys.stderr.write(f"[linkedin-scrapper] {msg}\n")
            sys.stderr.flush()

    if not profile_dir and not cookies_file:
        profile_dir = find_default_firefox_profile()

    tmpdir = tempfile.mkdtemp(prefix="linkedin_ff_profile_")
    port = find_free_port()

    with open(os.path.join(tmpdir, "user.js"), "w", encoding="utf-8") as f:
        f.write(f'user_pref("marionette.port", {port});\n')
        f.write('user_pref("datareporting.policy.dataSubmissionEnabled", false);\n')
        f.write('user_pref("toolkit.telemetry.reportingpolicy.firstRun", false);\n')
        f.write('user_pref("browser.shell.checkDefaultBrowser", false);\n')
        f.write('user_pref("dom.webdriver.enabled", false);\n')

    external_cookies: List[Dict[str, Any]] = []
    if profile_dir and os.path.isdir(profile_dir):
        log(f"Loading cookies from Firefox profile: {profile_dir}")
        for fname in ["cookies.sqlite", "cookies.sqlite-wal", "cookies.sqlite-shm"]:
            src = os.path.join(profile_dir, fname)
            if os.path.exists(src):
                shutil.copy2(src, os.path.join(tmpdir, fname))
        try:
            external_cookies = load_cookies_from_sqlite(profile_dir)
            has_li_at = any(c["name"] == "li_at" for c in external_cookies)
            log(f"Loaded {len(external_cookies)} LinkedIn cookies (li_at present: {has_li_at})")
        except Exception as e:
            log(f"Warning reading cookies via sqlite3: {e}")
    elif cookies_file and os.path.exists(cookies_file):
        log(f"Loading cookies from JSON file: {cookies_file}")
        with open(cookies_file, "r", encoding="utf-8") as cf:
            external_cookies = json.load(cf)
    else:
        log("Warning: No Firefox profile or cookies file found; proceeding unauthenticated.")

    cmd = ["firefox", "--marionette", "--no-remote", "--profile", tmpdir]
    if headless:
        cmd.insert(1, "--headless")

    log(f"Launching Firefox ({'headless' if headless else 'GUI'}) on Marionette port {port}...")
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    try:
        client: Optional[MarionetteClient] = None
        for _ in range(40):
            try:
                client = MarionetteClient(port=port)
                break
            except Exception:
                time.sleep(0.5)
        if not client:
            raise RuntimeError("Timed out waiting for Firefox Marionette server to start.")

        client.send("WebDriver:NewSession", {"capabilities": {}})
        client.send("WebDriver:SetWindowRect", {"width": 1600, "height": 1000})

        # If cookies were supplied only via JSON file (not profile sqlite copy), inject them after domain load
        if cookies_file and not (profile_dir and os.path.isdir(profile_dir)):
            client.send("WebDriver:Navigate", {"url": "https://www.linkedin.com/robots.txt"})
            time.sleep(1.5)
            for c in external_cookies:
                try:
                    client.send("WebDriver:AddCookie", {"cookie": c})
                except Exception:
                    pass

        log(f"Opening {url} ...")
        client.send("WebDriver:Navigate", {"url": url})

        # Wait until "Show all" link or job cards appear
        show_all_info = None
        for _ in range(30):
            time.sleep(1.0)
            show_all_info = client.execute_script(
                """
                const els = Array.from(document.querySelectorAll('a, button'));
                const matches = els.filter(el => {
                  const txt = (el.innerText || el.textContent || '').trim();
                  const aria = el.getAttribute('aria-label') || '';
                  const href = el.getAttribute('href') || '';
                  return (/^show all$/i.test(txt) || /show all/i.test(aria) || /show all/i.test(txt)) &&
                         (href.includes('/jobs/') || el.tagName === 'BUTTON' || el.tagName === 'A');
                });
                if (matches.length === 0) return null;
                const idx = Math.min(arguments[0], matches.length - 1);
                const target = matches[idx];
                const href = target.getAttribute('href');
                target.scrollIntoView({ block: 'center' });
                target.click();
                return {
                  clicked: true,
                  count: matches.length,
                  selectedText: (target.innerText || target.textContent || '').trim(),
                  ariaLabel: target.getAttribute('aria-label'),
                  href: href
                };
                """,
                [show_all_index],
            )
            if show_all_info and show_all_info.get("clicked"):
                break

        if not show_all_info:
            curr_url = client.send("WebDriver:GetCurrentURL").get("value", "")
            raise RuntimeError(
                f"Could not find 'Show all' link on {curr_url}. Make sure your Firefox cookies have an active LinkedIn login session."
            )

        log(f"Clicked 'Show all' ({show_all_info.get('ariaLabel') or show_all_info.get('selectedText')}) -> {show_all_info.get('href')}")

        # Wait for the subsequent search-results / collection page to render job cards
        job_ids: List[str] = []
        for _ in range(30):
            time.sleep(1.0)
            job_ids = client.execute_script(
                """
                const cards = Array.from(document.querySelectorAll('[role="button"][componentkey^="job-card-component-ref-"]'));
                return cards.map(el => el.getAttribute('componentkey').replace('job-card-component-ref-', ''));
                """
            ) or []
            if len(job_ids) > 0:
                break

        # Scroll the left list container to ensure all lazy-loaded cards on the page are mounted
        client.execute_script(
            """
            const listCol = document.querySelector('[componentkey="SearchResultsMainContent"]');
            if (listCol) {
              listCol.scrollTo({ top: listCol.scrollHeight, behavior: 'smooth' });
            }
            const cards = Array.from(document.querySelectorAll('[role="button"][componentkey^="job-card-component-ref-"]'));
            if (cards.length > 0) {
              cards[cards.length - 1].scrollIntoView({ block: 'center' });
            }
            """
        )
        time.sleep(1.5)
        job_ids = client.execute_script(
            """
            const cards = Array.from(document.querySelectorAll('[role="button"][componentkey^="job-card-component-ref-"]'));
            const seen = new Set();
            const ids = [];
            for (const el of cards) {
              const id = el.getAttribute('componentkey').replace('job-card-component-ref-', '');
              if (id && !seen.has(id)) {
                seen.add(id);
                ids.push(id);
              }
            }
            return ids;
            """
        ) or []

        if limit and limit > 0:
            job_ids = job_ids[:limit]

        log(f"Found {len(job_ids)} job listings on the subsequent page.")

        results: List[Dict[str, Any]] = []
        for idx, jid in enumerate(job_ids, start=1):
            log(f"[{idx}/{len(job_ids)}] Selecting job {jid}...")
            client.execute_script(
                """
                const jid = arguments[0];
                const btn = document.querySelector(`[role="button"][componentkey="job-card-component-ref-${jid}"]`);
                if (btn) {
                  btn.scrollIntoView({ block: 'center' });
                  btn.click();
                }
                """,
                [jid],
            )

            # Wait up to 8 seconds for the job's description component to load in the detail pane
            for _ in range(25):
                ready = client.execute_script(
                    """
                    const jid = arguments[0];
                    const aboutEl = document.querySelector(`[componentkey="JobDetails_AboutTheJob_${jid}"]`);
                    return !!(aboutEl && aboutEl.innerText && aboutEl.innerText.trim().length > 20);
                    """,
                    [jid],
                )
                if ready:
                    break
                time.sleep(0.35)

            time.sleep(0.2)
            # Click "… more" inside the About the job block if present so description isn't truncated
            client.execute_script(
                """
                const jid = arguments[0];
                const aboutEl = document.querySelector(`[componentkey="JobDetails_AboutTheJob_${jid}"]`) ||
                                document.querySelector('[componentkey^="JobDetails_AboutTheJob_"]');
                if (aboutEl) {
                  const moreBtn = Array.from(aboutEl.querySelectorAll('button, [role="button"], span'))
                    .find(el => /^(\u2026|\\.{3})\\s*more$/i.test((el.innerText || '').trim()));
                  if (moreBtn) {
                    try { moreBtn.click(); } catch (e) {}
                  }
                }
                """,
                [jid],
            )
            time.sleep(0.15)
            job_data = client.execute_script(EXTRACT_JOB_JS, [jid])
            results.append(job_data)

        client.close()
        return results

    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()
        shutil.rmtree(tmpdir, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Open Firefox on https://www.linkedin.com/jobs/, click 'Show all', and output job details as JSON."
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
        action="store_true",
        help="Run Firefox in headless mode (default: opens visible Firefox window if DISPLAY is available)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Maximum number of job listings to scrape from the subsequent page (default: all listings on the page)",
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
        help="Write JSON output to file instead of only stdout",
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

    formatted = json.dumps(jobs, indent=2, ensure_ascii=False)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(formatted + "\n")
        if not args.quiet:
            sys.stderr.write(f"[linkedin-scrapper] Saved {len(jobs)} jobs to {args.output}\n")
    print(formatted)
    return 0


if __name__ == "__main__":
    sys.exit(main())
