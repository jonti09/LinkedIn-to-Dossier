"""Dossier MCP client and AI draft application creation."""

import glob
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional


def _normalize_base_url(base_url: str) -> str:
    base_url = (base_url or "localhost:3000").strip().rstrip("/")
    if not base_url.startswith(("http://", "https://")):
        base_url = f"http://{base_url}"
    return base_url


class DossierClient:
    """Interacts with Dossier's MCP endpoint."""

    def __init__(
        self,
        base_url: str = "localhost:3000",
        mcp_url: Optional[str] = None,
        token_url: Optional[str] = None,
    ):
        self.base_url = _normalize_base_url(base_url)
        self.mcp_url = mcp_url or f"{self.base_url}/api/mcp"
        self.token_url = token_url or f"{self.base_url}/api/oauth/token"
        self.token = self._load_or_refresh_token()
        self.profile = None

    def format_app_link(self, link: Optional[str], app_id: Optional[str] = None) -> Optional[str]:
        if not link and app_id:
            return f"{self.base_url}/applications/{app_id}"
        if not isinstance(link, str) or not link:
            return link
        try:
            parsed = urllib.parse.urlparse(link)
            if parsed.path:
                return f"{self.base_url}{parsed.path}"
        except Exception:
            pass
        return link.replace("http://127.0.0.1:3000", self.base_url).replace("http://localhost:3000", self.base_url)

    def _load_or_refresh_token(self) -> str:
        auth_dir = os.path.expanduser("~/.mcp-auth/mcp-remote-v1")
        token_files = glob.glob(os.path.join(auth_dir, "*_tokens.json"))
        client_files = glob.glob(os.path.join(auth_dir, "*_client_info.json"))

        if not token_files or not client_files:
            raise FileNotFoundError(
                f"Dossier MCP tokens not found in {auth_dir}. "
                "Ensure 'npx -y mcp-remote http://localhost:3000/api/mcp 9696' was authorized."
            )

        token_file = token_files[0]
        client_file = client_files[0]

        with open(token_file, "r", encoding="utf-8") as f:
            tokens = json.load(f)
        with open(client_file, "r", encoding="utf-8") as f:
            client_info = json.load(f)

        expires_at = tokens.get("expires_at", 0) / 1000.0
        if time.time() > (expires_at - 60) and "refresh_token" in tokens:
            try:
                data = urllib.parse.urlencode({
                    "grant_type": "refresh_token",
                    "refresh_token": tokens["refresh_token"],
                    "client_id": client_info.get("client_id", ""),
                }).encode("utf-8")
                req = urllib.request.Request(
                    self.token_url,
                    data=data,
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
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
            "Authorization": f"Bearer {self.token}",
        }
        body = json.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments},
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
                app_id = chk.get("application_id")
                link = self.format_app_link(chk.get("link"), app_id)
                return {
                    "draft_id": draft_id,
                    "application_id": app_id,
                    "link": link,
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
        return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}{parsed.path.rstrip('/')}"
    except Exception:
        return u.split("?")[0].rstrip("/").lower()


def _extract_linkedin_job_id(u: str) -> Optional[str]:
    if not u:
        return None
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

        if norm_app_url and (norm_app_url == norm_apply or norm_app_url == norm_posting):
            return app

        if li_id and app_li_id and li_id == app_li_id:
            return app

        app_company = (app.get("company") or "").strip().lower()
        app_role = (app.get("role") or "").strip().lower()
        if job_company and app_company and job_company == app_company:
            if job_title and app_role and (job_title == app_role or job_title in app_role or app_role in job_title):
                return app

    return None


def sync_jobs_to_dossier(
    jobs: List[Dict[str, Any]],
    dossier_url: str = "localhost:3000",
    force: bool = False,
    wait_for_ai: bool = True,
    verbose: bool = True,
) -> None:
    def log(msg: str) -> None:
        if verbose:
            sys.stderr.write(f"[dossier-sync] {msg}\n")
            sys.stderr.flush()

    try:
        dossier = DossierClient(base_url=dossier_url)
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
            existing_link = dossier.format_app_link(matched_app.get("link"), existing_id)
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
