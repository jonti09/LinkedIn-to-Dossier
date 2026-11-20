"""SQLite storage for scraped LinkedIn jobs and their Dossier sync metadata."""

import os
import re
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Set

SCRAPE_COLUMNS = {
    "postingLink": "posting_link",
    "title": "title",
    "description": "description",
    "salaryRange": "salary_range",
    "remote": "remote",
    "company": "company",
    "datePosted": "date_posted",
    "contact": "contact",
    "apply": "apply",
}
DOSSIER_COLUMNS = {
    "dossierDraftId": "dossier_draft_id",
    "dossierApplicationId": "dossier_application_id",
    "dossierUrl": "dossier_url",
    "dossierStatus": "dossier_status",
    "dossierError": "dossier_error",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    job_id TEXT PRIMARY KEY,            -- LinkedIn numeric job ID
    posting_link TEXT NOT NULL,
    title TEXT,
    description TEXT,
    salary_range TEXT,
    remote INTEGER,
    company TEXT,
    date_posted TEXT,
    contact TEXT,
    apply TEXT,
    scraped_at TEXT NOT NULL,
    dossier_draft_id TEXT,
    dossier_application_id TEXT,
    dossier_url TEXT,
    dossier_status TEXT,
    dossier_error TEXT,
    dossier_synced_at TEXT,
    updated_at TEXT NOT NULL
);
"""


def default_db_path(project_root: str) -> str:
    return os.path.join(project_root, "data", "jobs.sqlite")


def extract_job_id(job: Dict[str, Any]) -> Optional[str]:
    """LinkedIn numeric job ID from an explicit `jobId` or the posting link."""
    if job.get("jobId"):
        return str(job["jobId"])
    m = re.search(r"linkedin\.com/jobs/view/(?:[^/?#]*-)?(\d+)", job.get("postingLink") or "", re.I)
    return m.group(1) if m else None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class JobStore:
    def __init__(self, path: str):
        self.path = path
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def known_ids(self) -> Set[str]:
        return {r[0] for r in self.conn.execute("SELECT job_id FROM jobs")}

    def has(self, job_id: str) -> bool:
        return self.conn.execute("SELECT 1 FROM jobs WHERE job_id=?", (job_id,)).fetchone() is not None

    def add_scraped(self, jobs: Iterable[Dict[str, Any]]) -> List[str]:
        """Insert jobs not yet in the DB (never overwrites). Returns IDs actually inserted."""
        inserted = []
        for job in jobs:
            jid = extract_job_id(job)
            if not jid:
                continue
            cols = ["job_id", "scraped_at", "updated_at"] + list(SCRAPE_COLUMNS.values())
            vals = [jid, _now(), _now()]
            for key in SCRAPE_COLUMNS:
                v = job.get(key)
                vals.append(int(v) if key == "remote" and v is not None else v)
            cur = self.conn.execute(
                f"INSERT OR IGNORE INTO jobs ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", vals
            )
            if cur.rowcount:
                inserted.append(jid)
        self.conn.commit()
        return inserted

    def save_dossier(self, job: Dict[str, Any]) -> None:
        """Persist Dossier metadata from a job dict onto its row."""
        jid = extract_job_id(job)
        if not jid:
            return
        sets = [f"{col}=?" for col in DOSSIER_COLUMNS.values()]
        vals = [job.get(k) for k in DOSSIER_COLUMNS]
        synced = bool(job.get("dossierApplicationId") or job.get("dossierDraftId"))
        self.conn.execute(
            f"UPDATE jobs SET {','.join(sets)}, "
            "dossier_synced_at=CASE WHEN ? THEN COALESCE(dossier_synced_at, ?) ELSE dossier_synced_at END, "
            "updated_at=? WHERE job_id=?",
            vals + [int(synced), _now(), _now(), jid],
        )
        self.conn.commit()

    @staticmethod
    def is_synced(job: Dict[str, Any]) -> bool:
        return bool(job.get("dossierApplicationId") or job.get("dossierDraftId"))

    @staticmethod
    def _row_to_job(row: sqlite3.Row) -> Dict[str, Any]:
        job: Dict[str, Any] = {"jobId": row["job_id"]}
        for key, col in SCRAPE_COLUMNS.items():
            v = row[col]
            job[key] = bool(v) if key == "remote" and v is not None else v
        for key, col in DOSSIER_COLUMNS.items():
            if row[col] is not None:
                job[key] = row[col]
        return job

    def get_jobs(self, job_ids: Optional[Iterable[str]] = None) -> List[Dict[str, Any]]:
        rows = self.conn.execute("SELECT * FROM jobs ORDER BY scraped_at, rowid").fetchall()
        wanted = set(job_ids) if job_ids is not None else None
        return [self._row_to_job(r) for r in rows if wanted is None or r["job_id"] in wanted]

    def pending_jobs(self) -> List[Dict[str, Any]]:
        """Jobs stored but not yet pushed to Dossier (includes previously failed ones)."""
        return [j for j in self.get_jobs() if not self.is_synced(j)]
