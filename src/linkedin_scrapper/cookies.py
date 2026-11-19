"""Firefox profile discovery and SQLite cookie extraction."""

import configparser
import glob
import os
import shutil
import sqlite3
import tempfile
from typing import Any, Dict, List, Optional


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
