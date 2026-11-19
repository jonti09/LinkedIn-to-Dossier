"""Firefox LinkedIn Jobs scraping logic."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Any, Dict, List, Optional

from .cookies import find_default_firefox_profile, load_cookies_from_sqlite
from .marionette import MarionetteClient, find_free_port


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

// 10. Apply link (external job portal link if available, or internal LinkedIn apply/job view link)
let apply = `https://www.linkedin.com/jobs/view/${jobId}/`;
const applyAnchors = Array.from(detailCol.querySelectorAll('a[href]'));
const extAnchor = applyAnchors.find(a => {
  const txt = (a.innerText || a.textContent || '').trim();
  const aria = a.getAttribute('aria-label') || '';
  const href = a.getAttribute('href') || '';
  return /^apply$/i.test(txt) || /apply/i.test(aria) || href.includes('/safety/go/');
});
if (extAnchor && extAnchor.href) {
  let rawHref = extAnchor.href;
  try {
    const parsed = new URL(rawHref, window.location.origin);
    if (parsed.pathname.includes('/safety/go') && parsed.searchParams.get('url')) {
      apply = parsed.searchParams.get('url');
    } else if (!parsed.pathname.startsWith('/jobs/search-results')) {
      apply = parsed.href;
    }
  } catch (e) {
    apply = rawHref;
  }
} else {
  const easyApplyAnchor = applyAnchors.find(a => {
    const txt = (a.innerText || a.textContent || '').trim();
    const href = a.getAttribute('href') || '';
    return /easy apply/i.test(txt) || href.includes('/apply/');
  });
  if (easyApplyAnchor && easyApplyAnchor.href && !easyApplyAnchor.href.includes('/jobs/search-results')) {
    apply = easyApplyAnchor.href;
  }
}

return {
  postingLink: `https://www.linkedin.com/jobs/view/${jobId}`,
  title,
  description,
  salaryRange,
  remote,
  company,
  datePosted,
  contact,
  apply
};
"""


def scrape_linkedin_jobs(
    url: str = "https://www.linkedin.com/jobs/",
    profile_dir: Optional[str] = None,
    cookies_file: Optional[str] = None,
    headless: bool = True,
    limit: Optional[int] = None,
    show_all_index: int = 0,
    verbose: bool = True,
) -> List[Dict[str, Any]]:
    """Open Firefox with cookies, click 'Show all', and scrape each listing on the subsequent page."""

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
