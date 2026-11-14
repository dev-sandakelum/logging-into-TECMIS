"""
AI notice enhancement using Google Gemini (Google AI Studio, free tier).

Turns a raw scraped TECMIS notice (messy title, optional PDF attachment)
into a clean title + well-structured markdown body before it is pushed
to Redis. PDFs are sent directly to Gemini (it reads them natively,
including scanned documents), so no PDF library is needed.

- Stdlib only (urllib).
- Never raises: on any failure, `enhance_notice` returns None and the caller
  falls back to the plain (non-AI) body.

Env vars:
    GEMINI_API_KEY   required to enable AI enhancement
    GEMINI_MODEL     optional, default "gemini-3.5-flash-lite" (free tier)
"""

from __future__ import annotations

import base64
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
DEFAULT_MODEL = "gemini-3.5-flash-lite"
MAX_PDF_BYTES = 15 * 1024 * 1024  # inline request limit is ~20 MB total
MIN_INTERVAL = 7.0                # seconds between calls (free tier: ~10 req/min)

_last_call = 0.0

SYSTEM_PROMPT = """You rewrite university notices for students of the Faculty of Technology, University of Ruhuna (TECMIS system).
You receive a raw scraped notice title (may contain junk like "Download" or merged dates) and, when available, the notice text and/or the attached PDF document.

Return ONLY a JSON object with these keys:
{
  "title": "clean, concise, human-friendly title (max 80 chars, Title Case, no 'Download')",
  "category": one of ["Exam", "Timetable", "Results", "Registration", "Event", "Payment", "Academic", "Administrative", "Other"],
  "summary": "2-3 sentence plain-English summary of what the notice is about and who it affects",
  "key_points": ["short bullet", "..."],
  "important_dates": ["YYYY-MM-DD - what happens", "..."],
  "action_required": "what students must do, or empty string if nothing",
  "audience": "who this applies to, e.g. 'BICT Level 2 students', or 'All students'"
}

Rules:
- key_points: 0-6 items; important_dates: 0-6 items. Only facts present in the input.
- Never invent facts, dates, venues, or deadlines. If neither notice text nor a document is provided, base everything on the title only and keep key_points/important_dates empty unless obvious from the title.
- Expand abbreviations where clear: ICT, ET (Engineering Technology), BST (Biosystems Technology).
- For timetables/result sheets, summarise the scope (levels, semesters, date range) instead of listing every row.
- Be concise. No markdown inside JSON values."""


def _clean_posted_at(raw: str) -> str:
    """Normalise posted_at ('YYYY-MM-DD HH:MM', or old 'YYYY-MM-DD/HH:MMjunk')."""
    m = re.search(r"(\d{4}-\d{2}-\d{2})[/ ](\d{2}:\d{2})", raw or "")
    return f"{m.group(1)} {m.group(2)}" if m else (raw or "").strip()


def _pdf_part(file_path: Path | None) -> dict | None:
    """Inline PDF part for Gemini, or None if not a usable PDF."""
    if not file_path or not file_path.exists() or file_path.suffix.lower() != ".pdf":
        return None
    try:
        data = file_path.read_bytes()
    except OSError:
        return None
    if not data.startswith(b"%PDF") or len(data) > MAX_PDF_BYTES:
        return None
    return {"inline_data": {"mime_type": "application/pdf",
                            "data": base64.b64encode(data).decode("ascii")}}


def _gemini_call(api_key: str, model: str, parts: list[dict]) -> dict | None:
    global _last_call
    wait = MIN_INTERVAL - (time.time() - _last_call)
    if wait > 0:
        time.sleep(wait)

    # Gemini 3.x: keep default temperature and thinking settings (lowering
    # temperature is discouraged); leave headroom since thinking uses output tokens.
    generation_config = {
        "maxOutputTokens": 4096,
        "responseMimeType": "application/json",
    }

    payload = {
        "system_instruction": {"parts": [{"text": SYSTEM_PROMPT}]},
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": generation_config,
    }
    req = urllib.request.Request(
        GEMINI_URL.format(model=model),
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "x-goog-api-key": api_key,
            "Content-Type": "application/json",
            "User-Agent": "tecmis-notice-sync/1.0",
        },
        method="POST",
    )

    for attempt in range(3):
        _last_call = time.time()
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            candidates = data.get("candidates") or []
            if not candidates:
                print(f"    (Gemini returned no candidates on {model}: {str(data)[:200]})")
                return None
            text = "".join(p.get("text", "") for p in candidates[0].get("content", {}).get("parts", []))
            text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
            return json.loads(text)
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")[:300]
            if e.code in (429, 500, 503) and attempt < 2:
                delay = 30.0 if e.code == 429 else 10.0
                print(f"    (Gemini HTTP {e.code} on {model}, retrying in {delay:.0f}s)")
                time.sleep(delay)
                continue
            print(f"    (Gemini HTTP {e.code} on {model}: {body})")
            return None
        except Exception as e:
            print(f"    (Gemini request failed on {model}: {e})")
            return None
    return None


def enhance_notice(notice: dict, file_path: Path | None = None) -> dict | None:
    """Return the AI-structured dict for a notice, or None if unavailable."""
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        return None

    raw_title = notice.get("title", "").replace("Download", "").strip()
    pdf = _pdf_part(file_path)

    prompt = (
        f"Raw title: {raw_title}\n"
        f"Posted: {_clean_posted_at(notice.get('posted_at', ''))}\n"
        f"Section: {notice.get('section', '')}\n"
        f"Attachment filename: {notice.get('file') or 'none'}\n"
        f"Document: {'attached PDF' if pdf else '(not available)'}\n\n"
        f"Notice text:\n{(notice.get('content') or '(not available)')[:8000]}"
    )
    parts = ([pdf] if pdf else []) + [{"text": prompt}]

    model = os.environ.get("GEMINI_MODEL", "").strip() or DEFAULT_MODEL
    result = _gemini_call(api_key, model, parts)
    if result is None and model != DEFAULT_MODEL:
        # A custom GEMINI_MODEL failed (e.g. retired) - retry on the default
        result = _gemini_call(api_key, DEFAULT_MODEL, parts)
    if not isinstance(result, dict) or not result.get("title"):
        return None
    return result


def _as_list(value) -> list[str]:
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _links_md(notice: dict, document_url: str) -> str:
    """Markdown links: main document / TECMIS page, plus links inside the notice."""
    out = []
    if document_url:
        label = "View on TECMIS (login required)" if "noticeid=" in document_url else "View/Download Original"
        out.append(f"[📄 {label}]({document_url})")
    for link in notice.get("links") or []:
        if link != document_url:
            name = link.rstrip("/").rsplit("/", 1)[-1] or link
            out.append(f"[🔗 {urllib.parse.unquote(name)}]({link.replace(' ', '%20')})")
    return "\n\n".join(out)


def build_body(notice: dict, document_url: str, ai: dict | None) -> tuple[str, str]:
    """Return (title, markdown_body). Uses AI output when available."""
    raw_title = notice.get("title", "").replace("Download", "").strip()
    posted = _clean_posted_at(notice.get("posted_at", ""))
    section = str(notice.get("section", "")).title()

    if not ai:
        body = f"## {raw_title}\n\n"
        body += f"**Posted:** {posted}\n\n"
        body += f"**Section:** {section}\n\n"
        if notice.get("content"):
            body += notice["content"].strip() + "\n\n"
        body += _links_md(notice, document_url)
        return raw_title, body.strip()

    title = str(ai.get("title") or raw_title).strip()
    category = str(ai.get("category") or "").strip()
    summary = str(ai.get("summary") or "").strip()
    audience = str(ai.get("audience") or "").strip()
    action = str(ai.get("action_required") or "").strip()
    key_points = _as_list(ai.get("key_points"))
    dates = _as_list(ai.get("important_dates"))

    lines = [f"## {title}", ""]
    meta = [f"**Posted:** {posted}"]
    if category:
        meta.append(f"**Category:** {category}")
    meta.append(f"**Section:** {section}")
    lines += [" · ".join(meta), ""]
    if audience:
        lines += [f"**For:** {audience}", ""]
    if summary:
        lines += [summary, ""]
    if action:
        lines += [f"> **Action required:** {action}", ""]
    if key_points:
        lines += ["### Key Points"] + [f"- {p}" for p in key_points] + [""]
    if dates:
        lines += ["### Important Dates"] + [f"- {d}" for d in dates] + [""]
    links = _links_md(notice, document_url)
    if links:
        lines += [links, ""]
    lines += [f"<sub>Original title: {raw_title} · AI-enhanced summary, verify with the original notice.</sub>"]
    return title, "\n".join(lines).strip()


if __name__ == "__main__":
    # Local test: python ai_enhance.py  (reads GEMINI_API_KEY from env or .env)
    env_file = Path(__file__).with_name(".env")
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    notices = json.loads(Path("data/notices.json").read_text(encoding="utf-8"))
    n = notices[0]
    fp = Path("data/attachments") / n["file"] if n.get("file") else None
    ai = enhance_notice(n, fp)
    print(json.dumps(ai, indent=2, ensure_ascii=False))
    print("\n" + build_body(n, n.get("url", ""), ai)[1])
