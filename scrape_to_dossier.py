#!/usr/bin/env python3
"""
LinkedIn Jobs Scraper -> Dossier Integration (`scrape_to_dossier.py`)

1. Opens https://www.linkedin.com/jobs/ in Firefox using cookies from your
   local Firefox profile (or a JSON cookies file).
2. Clicks "Show all" on the jobs section.
3. Scrapes detailed information for each listing on the subsequent page:
   - postingLink: LinkedIn job view URL (https://www.linkedin.com/jobs/view/<id>)
   - title
   - description
   - salaryRange (if available)
   - remote (boolean)
   - company
   - datePosted
   - contact (recruiter profile link if available)
   - apply: direct application link (external job portal ATS link or internal LinkedIn link)
4. Saves the scraped listings to `scrapped/<YYYY-MM-DD_HH-MM-SS>.json`.
5. Connects to Dossier via MCP (http://localhost:3000/api/mcp) using authorized tokens,
   and creates a **Draft** application in Dossier for each listing, setting the job link
   directly to the actual application URL.
"""

import argparse
import configparser
from datetime import datetime
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
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Tuple


# ==============================================================================
# Firefox Marionette Client
# ==============================================================================

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


# ==============================================================================
# Dossier MCP / API Integration
# ==============================================================================

class DossierClient:
    """Interacts with Dossier's MCP endpoint (http://localhost:3000/api/mcp)."""

    def __init__(self, mcp_url: str = "http://127.0.0.1:3000/api/mcp", token_url: str = "http://127.0.0.1:3000/api/oauth/token"):
        self.mcp_url = mcp_url
        self.token_url = token_url
        self.token = self._load_or_refresh_token()
        self.profile = None

    def _load_or_refresh_token(self) -> str:
        auth_dir = os.path.expanduser("~/.mcp-auth/mcp-remote-v1")
        token_files = glob.glob(os.path.join(auth_dir, "*_tokens.json"))
        client_files = glob.glob(os.path.join(auth_dir, "*_client_info.json"))

        if not token_files or not client_files:
            raise FileNotFoundError(
                f"Dossier MCP tokens not found in {auth_dir}. Ensure 'npx -y mcp-remote http://localhost:3000/api/mcp 9696' was authorized."
            )

        token_file = token_files[0]
        client_file = client_files[0]

        with open(token_file, "r", encoding="utf-8") as f:
            tokens = json.load(f)
        with open(client_file, "r", encoding="utf-8") as f:
            client_info = json.load(f)

        expires_at = tokens.get("expires_at", 0) / 1000.0
        # Refresh if expired or expiring within 60s
        if time.time() > (expires_at - 60) and "refresh_token" in tokens:
            try:
                data = urllib.parse.urlencode({
                    "grant_type": "refresh_token",
                    "refresh_token": tokens["refresh_token"],
                    "client_id": client_info.get("client_id", "")
                }).encode("utf-8")
                req = urllib.request.Request(
                    self.token_url,
                    data=data,
                    headers={"Content-Type": "application/x-www-form-urlencoded"}
                )
                with urllib.request.urlopen(req, timeout=15) as resp:
                    refreshed = json.loads(resp.read().decode("utf-8"))
                    tokens.update(refreshed)
                    tokens["expires_at"] = (time.time() + refreshed.get("expires_in", 3600)) * 1000
                    with open(token_file, "w", encoding="utf-8") as out:
                        json.dump(tokens, out, indent=2)
            except Exception as e:
                sys.stderr.write(f"[linkedin-scrapper] Warning refreshing Dossier OAuth token: {e}\n")

        return tokens["access_token"]

    def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Any:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream, */*",
            "Authorization": f"Bearer {self.token}"
        }
        body = json.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments}
        }).encode("utf-8")

        req = urllib.request.Request(self.mcp_url, data=body, headers=headers)
        with urllib.request.urlopen(req, timeout=45) as resp:
            content = resp.read().decode("utf-8")
            for line in content.split("\n"):
                if line.startswith("data: "):
                    data = json.loads(line[6:])
                    res = data.get("result", {})
                    if res.get("isError"):
                        err_text = res.get("content", [{}])[0].get("text", "Unknown MCP tool error")
                        raise RuntimeError(f"Dossier MCP Error ({tool_name}): {err_text}")
                    text = res.get("content", [{}])[0].get("text", "{}")
                    try:
                        return json.loads(text)
                    except Exception:
                        return text
        return None

    def get_candidate_profile(self) -> Dict[str, Any]:
        if self.profile is None:
            self.profile = self.call_tool("get_profile", {})
        return self.profile

    def list_existing_applications(self) -> List[Dict[str, Any]]:
        try:
            apps = self.call_tool("list_applications", {})
            return apps if isinstance(apps, list) else []
        except Exception as e:
            sys.stderr.write(f"[linkedin-scrapper] Warning fetching applications list: {e}\n")
            return []

    def create_draft_application(
        self,
        job: Dict[str, Any],
        wait_for_ai: bool = True,
        log_fn: Any = None,
    ) -> Dict[str, Any]:
        # Target job link is actual application link (e.g. Ashby/Greenhouse URL), fallback to posting link
        target_link = job.get("apply") or job.get("postingLink") or ""
        company = job.get("company") or "Unknown Company"
        role = job.get("title") or "Software Engineer"
        mode = "Remote" if job.get("remote") else "On-site / Hybrid"
        salary = job.get("salaryRange") or ""
        desc = job.get("description") or ""
        posting_link = job.get("postingLink") or ""

        header_lines = [
            f"Company: {company}",
            f"Role: {role}",
            f"Work mode: {mode}",
        ]
        if salary:
            header_lines.append(f"Salary: {salary}")
        if posting_link:
            header_lines.append(f"LinkedIn Posting: {posting_link}")
        if job.get("contact"):
            header_lines.append(f"Recruiter Contact: {job['contact']}")

        full_posting_text = "\n".join(header_lines) + "\n\n" + desc

        # Use Dossier's built-in AI model (draft_application) so Dossier tailors the resume & cover letter
        draft_res = self.call_tool(
            "draft_application",
            {
                "posting_url": target_link,
                "posting_text": full_posting_text,
            },
        )
        if not isinstance(draft_res, dict) or not draft_res.get("draft_id"):
            return draft_res if isinstance(draft_res, dict) else {"raw": draft_res}

        draft_id = draft_res["draft_id"]
        if not wait_for_ai:
            return {
                "draft_id": draft_id,
                "status": draft_res.get("status", "started"),
            }

        last_step = None
        for _ in range(90):
            time.sleep(3.0)
            chk = self.call_tool("check_draft", {"draft_id": draft_id})
            if not isinstance(chk, dict):
                continue
            status = chk.get("status")
            step = chk.get("step")
            if step and step != last_step and log_fn:
                log_fn(f"      [Dossier AI] {step}...")
                last_step = step
            if status == "done":
                return {
                    "draft_id": draft_id,
                    "application_id": chk.get("application_id"),
                    "link": chk.get("link"),
                    "status": "Draft",
                }
            if status in ("failed", "error"):
                raise RuntimeError(chk.get("error") or f"Dossier AI draft failed: {chk}")

        return {
            "draft_id": draft_id,
            "status": "working",
        }


def _normalize_url(u: str) -> str:
    if not u:
        return ""
    u = u.strip()
    try:
        parsed = urllib.parse.urlparse(u)
        # Keep scheme, netloc, and path without trailing slash
        return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}{parsed.path.rstrip('/')}"
    except Exception:
        return u.split("?")[0].rstrip("/").lower()


def _extract_linkedin_job_id(u: str) -> Optional[str]:
    if not u:
        return None
    import re
    m = re.search(r"linkedin\.com/jobs/view/(?:[^/?#]*-)?(\d{6,})", u, re.I)
    return m.group(1) if m else None


def find_existing_application(
    job: Dict[str, Any],
    existing_apps: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """
    Check if a job listing is already in Dossier (including as a Draft) by matching:
    1. Exact or normalized `apply` URL or `postingLink` URL
    2. LinkedIn numeric job ID in the posting URL
    3. Exact `(company, role)` pair (case-insensitive)
    """
    apply_url = (job.get("apply") or "").strip()
    posting_url = (job.get("postingLink") or "").strip()
    norm_apply = _normalize_url(apply_url)
    norm_posting = _normalize_url(posting_url)
    li_id = _extract_linkedin_job_id(posting_url) or _extract_linkedin_job_id(apply_url)

    job_company = (job.get("company") or "").strip().lower()
    job_title = (job.get("title") or "").strip().lower()

    for app in existing_apps:
        app_url = (app.get("posting") or app.get("posting_url") or "").strip()
        norm_app_url = _normalize_url(app_url)
        app_li_id = _extract_linkedin_job_id(app_url)

        # 1. URL match
        if norm_app_url and (norm_app_url == norm_apply or norm_app_url == norm_posting):
            return app

        # 2. LinkedIn job ID match
        if li_id and app_li_id and li_id == app_li_id:
            return app

        # 3. Company + Role match (especially useful when one entry has the LinkedIn URL and another has the external ATS URL)
        app_company = (app.get("company") or "").strip().lower()
        app_role = (app.get("role") or "").strip().lower()
        if job_company and app_company and job_company == app_company:
            if job_title and app_role and (job_title == app_role or job_title in app_role or app_role in job_title):
                return app

    return None


def sync_jobs_to_dossier(
    jobs: List[Dict[str, Any]],
    force: bool = False,
    wait_for_ai: bool = True,
    verbose: bool = True,
) -> None:
    def log(msg: str) -> None:
        if verbose:
            sys.stderr.write(f"[dossier-sync] {msg}\n")
            sys.stderr.flush()

    try:
        dossier = DossierClient()
    except Exception as e:
        log(f"Skipping Dossier sync (MCP client init failed: {e})")
        return

    existing_apps = dossier.list_existing_applications()
    draft_count = sum(1 for a in existing_apps if (a.get("status") or "").lower() == "draft")
    log(f"Found {len(existing_apps)} existing applications in Dossier ({draft_count} currently in Draft).")

    for idx, job in enumerate(jobs, start=1):
        target_link = (job.get("apply") or job.get("postingLink") or "").strip()
        matched_app = find_existing_application(job, existing_apps)

        if not force and matched_app is not None:
            existing_status = matched_app.get("status", "Draft")
            existing_id = matched_app.get("id")
            existing_link = matched_app.get("link")
            log(
                f"[{idx}/{len(jobs)}] Skipping '{job.get('title')} · {job.get('company')}' "
                f"(already in Dossier with status '{existing_status}': {existing_link})"
            )
            job["dossierApplicationId"] = existing_id
            job["dossierUrl"] = existing_link
            job["dossierStatus"] = f"already_exists ({existing_status})"
            continue

        log(f"[{idx}/{len(jobs)}] Requesting Dossier AI to draft application for '{job.get('title')} · {job.get('company')}' -> {target_link}")
        try:
            res = dossier.create_draft_application(job, wait_for_ai=wait_for_ai, log_fn=log)
            app_id = res.get("application_id")
            link = res.get("link")
            job["dossierDraftId"] = res.get("draft_id")
            job["dossierApplicationId"] = app_id
            job["dossierUrl"] = link
            job["dossierStatus"] = res.get("status", "Draft")
            if link:
                log(f"      -> AI Draft completed: {link}")
            else:
                log(f"      -> AI Draft queued (draft_id={res.get('draft_id')}, status={res.get('status')})")

            # Add to in-memory existing_apps list so duplicate listings within the same scrape batch aren't drafted twice
            existing_apps.append({
                "id": app_id,
                "company": job.get("company"),
                "role": job.get("title"),
                "status": "Draft",
                "posting": target_link,
                "link": link,
            })
        except Exception as e:
            log(f"      -> Error creating draft in Dossier: {e}")
            job["dossierError"] = str(e)


# ==============================================================================
# Main Entry Point
# ==============================================================================

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Scrape LinkedIn jobs and create Draft applications in Dossier via MCP."
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

    # Sync to Dossier
    if not args.no_dossier:
        sync_jobs_to_dossier(
            jobs,
            force=args.force_dossier,
            wait_for_ai=not args.no_wait_ai,
            verbose=not args.quiet,
        )

    formatted = json.dumps(jobs, indent=2, ensure_ascii=False)
    script_dir = os.path.dirname(os.path.abspath(__file__))
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    # Output to scrapped/<timestamp>.json rather than linked-in sub dir
    default_out = os.path.join(script_dir, "scrapped", f"{timestamp}.json")
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
