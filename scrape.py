#!/usr/bin/env python3
"""
TECMIS Notices scraper
======================

Logs into TECMIS (Faculty of Technology, University of Ruhuna), opens the
NOTICE page (forms/form_53_a.php), extracts every notice from the
"Most Recent Notices" and "Previous Notices" tables, and writes:

  * data/notices.json  - canonical machine-readable data (deterministic:
                         byte-identical when the notices did not change, so
                         the GitHub Action only commits real updates)
  * NOTICES.md         - human-readable rendering for browsing on GitHub

Optionally (DOWNLOAD_PDFS=1) it also downloads every attachment into
data/attachments/.

Environment variables / .env file:
  TECMIS_USER         student/staff index number  (required for live mode)
  TECMIS_PASS         TECMIS password            (required for live mode)
  IGNORE_SSL_ERRORS   1 = skip TLS verification (fallback only; the current
                      certificate is valid)
  DOWNLOAD_PDFS       1 = download attachments to data/attachments/

Testing without credentials:
  python scrape.py --parse-file "notices.html"
  (parses a previously saved NOTICE page and writes the same outputs)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import parse_qs, unquote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://paravi.ruh.ac.lk/tecmis/"
LOGIN_URL = BASE_URL + "login.php"
NOTICES_URL = BASE_URL + "forms/form_53_a.php"

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

# The two section headings on the NOTICE page, mapped to stable keys.
SECTION_MAP = {
    "most recent notices": "recent",
    "previous notices": "previous",
}
SECTION_TITLES = {"recent": "Most Recent Notices", "previous": "Previous Notices"}

DATE_TIME_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})/(\d{2}:\d{2})$")

# Content types that mean "this is a document, not an HTML page".
DOC_CONTENT_TYPES = {
    "application/pdf": ".pdf",
    "application/msword": ".doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.ms-excel": ".xls",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/octet-stream": ".bin",
    "image/jpeg": ".jpg",
    "image/png": ".png",
}


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #
def load_dotenv(path: Path = Path(__file__).with_name(".env")) -> None:
    """Tiny .env loader (KEY=VALUE lines) - environment variables win."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip("'\"")
        if key and key not in __import__("os").environ:
            __import__("os").environ[key] = value


def retry(fn, tries: int = 3, backoff: float = 5.0):
    """Run a request callable with retries on transient network errors."""
    last_exc = None
    for attempt in range(1, tries + 1):
        try:
            return fn()
        except requests.RequestException as exc:
            last_exc = exc
            if attempt == tries:
                raise
            print(f"[warn] network error ({exc}); retry {attempt}/{tries - 1} "
                  f"in {backoff * attempt:.0f}s", file=sys.stderr)
            time.sleep(backoff * attempt)
    raise last_exc  # unreachable, keeps type-checkers happy


# --------------------------------------------------------------------------- #
# Live mode: login + fetch the NOTICE page
# --------------------------------------------------------------------------- #
def make_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({
        "User-Agent": USER_AGENT,
        "Referer": BASE_URL + "index.php",
    })
    if __import__("os").environ.get("IGNORE_SSL_ERRORS") == "1":
        session.verify = False
        requests.packages.urllib3.disable_warnings()  # type: ignore[attr-defined]
    return session


def login_and_fetch(user: str, password: str) -> str:
    """POST the login form, then fetch the NOTICE page and validate it."""
    session = make_session()

    print("Logging in to TECMIS ...")
    login_page = retry(lambda: session.get(BASE_URL + "index.php", timeout=60))
    login_page.raise_for_status()

    response = retry(lambda: session.post(
        LOGIN_URL,
        data={"uname": user, "upwd": password},
        headers={"Referer": BASE_URL + "index.php"},
        timeout=60,
    ))
    response.raise_for_status()

    if "mpcng.php" in response.text:
        raise SystemExit(
            "TECMIS is forcing a periodic password change for this account.\n"
            "Log in once in a browser, change the password, update the "
            "TECMIS_PASS secret, then re-run."
        )

    print("Fetching the NOTICE page ...")
    notices_page = retry(lambda: session.get(NOTICES_URL, timeout=60))
    notices_page.raise_for_status()
    html = notices_page.text

    if "You Have Not Permission" in html or "name='uname'" in html:
        raise SystemExit(
            "Login failed: TECMIS did not accept the credentials.\n"
            "Check the TECMIS_USER / TECMIS_PASS secrets "
            "(or your .env file when running locally)."
        )
    return html


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #
def notice_id(url: str) -> str:
    """Stable id: the noticeid query param when present, else the filename."""
    query = parse_qs(urlparse(url).query)
    if "noticeid" in query:
        return f"notice-{query['noticeid'][0]}"
    filename = Path(unquote(urlparse(url).path)).name
    return f"file-{filename}"


def parse_notices(html: str, base_url: str = NOTICES_URL) -> list[dict]:
    """Extract every notice row from the NOTICE page.

    Page structure (verified against the live site):
      <h3>Most Recent Notices</h3><table>... <tr class="trbgc"> 4 <td>s
      <h3>Previous Notices</h3><table>... same shape
    Columns: No | Add Date / Time | Title | Download Link
    """
    soup = BeautifulSoup(html, "html.parser")
    by_id: dict[str, dict] = {}

    for h3 in soup.find_all("h3"):
        heading = h3.get_text(strip=True).lower()
        section = next(
            (key for text, key in SECTION_MAP.items() if text in heading), None
        )
        if section is None:
            continue
        table = h3.find_next("table")
        if table is None:
            continue

        for tr in table.find_all("tr", class_="trbgc"):
            cells = tr.find_all("td")
            if len(cells) < 4:
                continue

            raw_no = cells[0].get_text(strip=True)
            raw_datetime = cells[1].get_text(strip=True)
            title = " ".join(cells[2].get_text().split())

            anchor = cells[3].find("a")
            href = anchor.get("href", "").strip() if anchor else ""
            url = urljoin(base_url, href) if href else ""

            match = DATE_TIME_RE.match(raw_datetime)
            posted_at = f"{match.group(1)} {match.group(2)}" if match else raw_datetime

            path_file = unquote(urlparse(url).path) if url else ""
            entry = {
                "id": notice_id(url) if url else f"row-{raw_no}-{posted_at}",
                "section": section,
                "no": int(raw_no) if raw_no.isdigit() else raw_no,
                "posted_at": posted_at,
                "title": title,
                "url": url,
                "file": Path(path_file).name if path_file and path_file != "/tecmis/forms/form_53_a.php" else None,
            }
            # If the same notice somehow appears in both sections, keep the
            # "recent" occurrence.
            existing = by_id.get(entry["id"])
            if existing is None or (existing["section"] != "recent" and section == "recent"):
                by_id[entry["id"]] = entry

    notices = sorted(
        by_id.values(),
        key=lambda n: (n["posted_at"], str(n["title"])),
        reverse=True,
    )
    return notices


# --------------------------------------------------------------------------- #
# Output writing
# --------------------------------------------------------------------------- #
def md_escape(text: str) -> str:
    return text.replace("|", "\\|").replace("[", "\\[").replace("]", "\\]")


def render_markdown(notices: list[dict]) -> str:
    counts = {key: sum(1 for n in notices if n["section"] == key)
              for key in ("recent", "previous")}
    newest = notices[0]["posted_at"] if notices else "n/a"
    # NOTE: "latest notice" time is derived from the data (not the run time),
    # so an unchanged scrape produces a byte-identical file.
    lines = [
        "# TECMIS Notices",
        "",
        "Notices scraped from the [TECMIS NOTICE page]"
        f"({NOTICES_URL}) — Faculty of Technology, University of Ruhuna.",
        "",
        f"Latest notice: **{newest}** — {len(notices)} notices total "
        f"({counts['recent']} recent · {counts['previous']} previous).",
        "",
    ]
    for key in ("recent", "previous"):
        rows = [n for n in notices if n["section"] == key]
        if not rows:
            continue
        lines += [f"## {SECTION_TITLES[key]}", "",
                  "| # | Posted | Title | Attachment |",
                  "|---|--------|-------|------------|"]
        for n in rows:
            link = f"[Download]({n['url']})" if n["url"] else "—"
            lines.append(
                f"| {n['no']} | {n['posted_at']} | {md_escape(n['title'])} | {link} |"
            )
        lines.append("")
    lines.append("")
    return "\n".join(lines)


def write_outputs(notices: list[dict], out_dir: Path) -> tuple[Path, Path]:
    data_dir = out_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    json_path = data_dir / "notices.json"
    json_path.write_text(
        json.dumps(notices, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    md_path = out_dir / "NOTICES.md"
    md_path.write_text(render_markdown(notices), encoding="utf-8")
    return json_path, md_path


# --------------------------------------------------------------------------- #
# Optional attachment download
# --------------------------------------------------------------------------- #
def download_attachments(session: requests.Session, notices: list[dict],
                          out_dir: Path) -> None:
    att_dir = out_dir / "data" / "attachments"
    att_dir.mkdir(parents=True, exist_ok=True)
    downloaded = skipped = failed = 0

    for notice in notices:
        url = notice["url"]
        if not url:
            continue
        try:
            response = retry(lambda u=url: session.get(u, timeout=120))
            response.raise_for_status()

            if notice["file"]:
                filename = notice["file"]
            else:
                content_type = response.headers.get("Content-Type", "").split(";")[0].strip().lower()
                if content_type == "text/html" or "text/html" in content_type:
                    filename = f"{notice['id']}.html"
                else:
                    suffix = DOC_CONTENT_TYPES.get(content_type)
                    if suffix is None:
                        suffix = Path(urlparse(response.url).path).suffix or ".bin"
                    filename = f"{notice['id']}{suffix}"

            target = att_dir / re.sub(r"[<>:\"/\\|?*]", "_", filename)
            if target.exists() and target.stat().st_size > 0:
                skipped += 1
                continue
            target.write_bytes(response.content)
            downloaded += 1
            print(f"  saved {target.name}")
        except Exception as exc:  # noqa: BLE001 - keep going notice by notice
            failed += 1
            print(f"[warn] could not download {notice['id']}: {exc}", file=sys.stderr)

    print(f"Attachments: {downloaded} downloaded, {skipped} already present, {failed} failed.")


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main() -> None:
    parser = argparse.ArgumentParser(description="Scrape TECMIS notices.")
    parser.add_argument(
        "--parse-file",
        help="Parse a saved NOTICE-page HTML file instead of logging in (testing).",
    )
    parser.add_argument(
        "--out-dir",
        default=".",
        help="Directory in which data/ and NOTICES.md are written (default: cwd).",
    )
    args = parser.parse_args()

    load_dotenv()
    out_dir = Path(args.out_dir).resolve()

    if args.parse_file:
        html = Path(args.parse_file).read_text(encoding="utf-8", errors="replace")
        print(f"Parsing saved page: {args.parse_file}")
    else:
        user = __import__("os").environ.get("TECMIS_USER", "")
        password = __import__("os").environ.get("TECMIS_PASS", "")
        if not user or not password:
            raise SystemExit(
                "TECMIS_USER / TECMIS_PASS are not set.\n"
                "Add them as repository secrets (GitHub Actions) or to a local "
                ".env file, or use --parse-file for offline testing."
            )
        html = login_and_fetch(user, password)

    notices = parse_notices(html)
    if not notices:
        raise SystemExit(
            "No notices were parsed - the NOTICE page layout may have changed."
        )

    json_path, md_path = write_outputs(notices, out_dir)
    recent = sum(1 for n in notices if n["section"] == "recent")
    previous = len(notices) - recent
    print(f"Parsed {len(notices)} notices ({recent} recent, {previous} previous).")
    print(f"Newest: {notices[0]['posted_at']} - {notices[0]['title']}")
    print(f"Wrote {json_path}")
    print(f"Wrote {md_path}")

    if __import__("os").environ.get("DOWNLOAD_PDFS") == "1" and not args.parse_file:
        session = make_session()
        print("DOWNLOAD_PDFS=1 - downloading attachments ...")
        download_attachments(session, notices, out_dir)


if __name__ == "__main__":
    main()
