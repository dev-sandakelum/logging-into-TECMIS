"""
AI notice enhancement using Groq (free tier).

Turns a raw scraped TECMIS notice (messy title, optional PDF attachment)
into a clean title + well-structured markdown body before it is pushed
to Redis.

- Stdlib only (urllib). `pypdf` is optional and only used to read PDF text.
- Never raises: on any failure, `enhance_notice` returns None and the caller
  falls back to the plain (non-AI) body.

Env vars:
    GROQ_API_KEY   required to enable AI enhancement
    GROQ_MODEL     optional, default "llama-3.3-70b-versatile" (free tier)
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
DEFAULT_MODEL = "llama-3.3-70b-versatile"
FALLBACK_MODEL = "llama-3.1-8b-instant"
MAX_DOC_CHARS = 6000  # keep prompts small for free-tier token limits
MIN_INTERVAL = 2.5    # seconds between calls (free tier: 30 req/min)

_last_call = 0.0

SYSTEM_PROMPT = """You rewrite university notices for students of the Faculty of Technology, University of Ruhuna (TECMIS system).
You receive a raw scraped notice title (may contain junk like "Download" or merged dates) and, sometimes, text extracted from the attached document.

Return ONLY a JSON object with these keys:
{
  "title": "clean, concise, human-friendly title (max 80 chars, Title Case, no 'Download')",
  "category": one of ["Exam", "Timetable", "Results", "Registration", "Event", "Payment", "Academic", "Administrative", "Other"],
  "summary": "2-3 sentence plain-English summary of what the notice is about and who it affects",
  "key_points": ["short bullet", "..."],          // 0-6 items, only facts present in the input
  "important_dates": ["YYYY-MM-DD - what happens", "..."],  // 0-6 items, only dates found in the input
  "action_required": "what students must do, or empty string if nothing",
  "audience": "who this applies to, e.g. 'BICT Level 2 students', or 'All students'"
}

Rules:
- Never invent facts, dates, venues, or deadlines. If the document text is missing, base everything on the title only and keep key_points/important_dates empty unless obvious from the title.
- Expand abbreviations where clear: ICT, ET (Engineering Technology), BST (Biosystems Technology).
- Be concise. No markdown inside JSON values."""


def _clean_posted_at(raw: str) -> str:
    """Scraped posted_at is often 'YYYY-MM-DD/HH:MMTITLE...'. Extract the timestamp."""
    m = re.search(r"(\d{4}-\d{2}-\d{2})/(\d{2}:\d{2})", raw or "")
    return f"{m.group(1)} {m.group(2)}" if m else (raw or "").strip()


def extract_document_text(file_path: Path | None) -> str:
    """Best-effort text extraction from a PDF attachment (first pages only)."""
    if not file_path or not file_path.exists() or file_path.suffix.lower() != ".pdf":
        return ""
    try:
        from pypdf import PdfReader  # optional dependency
    except ImportError:
        return ""
    try:
        reader = PdfReader(str(file_path))
        chunks: list[str] = []
        total = 0
        for page in reader.pages[:4]:
            text = page.extract_text() or ""
            chunks.append(text)
            total += len(text)
            if total >= MAX_DOC_CHARS:
                break
        text = re.sub(r"[ \t]+", " ", "\n".join(chunks))
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
        return text[:MAX_DOC_CHARS]
    except Exception as e:  # corrupt/scanned PDFs etc.
        print(f"    (pdf text extraction failed: {e})")
        return ""


def _groq_chat(api_key: str, model: str, user_content: str) -> dict | None:
    global _last_call
    wait = MIN_INTERVAL - (time.time() - _last_call)
    if wait > 0:
        time.sleep(wait)

    payload = {
        "model": model,
        "temperature": 0.2,
        "max_tokens": 700,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
    }
    req = urllib.request.Request(
        GROQ_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            # Groq's edge rejects the default Python-urllib User-Agent (HTTP 403)
            "User-Agent": "tecmis-notice-sync/1.0",
        },
        method="POST",
    )

    for attempt in range(3):
        _last_call = time.time()
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            content = data["choices"][0]["message"]["content"]
            return json.loads(content)
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")[:300]
            if e.code == 429 and attempt < 2:
                retry_after = e.headers.get("retry-after")
                delay = min(float(retry_after) if retry_after else 20.0, 60.0)
                print(f"    (Groq rate limited, retrying in {delay:.0f}s)")
                time.sleep(delay)
                continue
            print(f"    (Groq HTTP {e.code} on {model}: {body})")
            return None
        except Exception as e:
            print(f"    (Groq request failed on {model}: {e})")
            return None
    return None


def enhance_notice(notice: dict, file_path: Path | None = None) -> dict | None:
    """Return the AI-structured dict for a notice, or None if unavailable."""
    api_key = os.environ.get("GROQ_API_KEY", "").strip()
    if not api_key:
        return None

    raw_title = notice.get("title", "").replace("Download", "").strip()
    doc_text = extract_document_text(file_path)

    user_content = (
        f"Raw title: {raw_title}\n"
        f"Posted: {_clean_posted_at(notice.get('posted_at', ''))}\n"
        f"Section: {notice.get('section', '')}\n"
        f"Attachment filename: {notice.get('file') or 'none'}\n\n"
        f"Document text:\n{doc_text if doc_text else '(not available)'}"
    )

    model = os.environ.get("GROQ_MODEL", "").strip() or DEFAULT_MODEL
    result = _groq_chat(api_key, model, user_content)
    if result is None and model != FALLBACK_MODEL:
        result = _groq_chat(api_key, FALLBACK_MODEL, user_content)
    if not isinstance(result, dict) or not result.get("title"):
        return None
    return result


def _as_list(value) -> list[str]:
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def build_body(notice: dict, document_url: str, ai: dict | None) -> tuple[str, str]:
    """Return (title, markdown_body). Uses AI output when available."""
    raw_title = notice.get("title", "").replace("Download", "").strip()
    posted = _clean_posted_at(notice.get("posted_at", ""))
    section = str(notice.get("section", "")).title()

    if not ai:
        body = f"## {raw_title}\n\n"
        body += f"**Posted:** {posted}\n\n"
        body += f"**Section:** {section}\n\n"
        if document_url:
            body += f"[View/Download]({document_url})"
        return raw_title, body

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
    if document_url:
        lines += [f"[📄 View/Download Original]({document_url})", ""]
    lines += [f"<sub>Original title: {raw_title} · AI-enhanced summary, verify with the original notice.</sub>"]
    return title, "\n".join(lines).strip()


if __name__ == "__main__":
    # Local test: python ai_enhance.py  (reads GROQ_API_KEY from env or .env)
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
