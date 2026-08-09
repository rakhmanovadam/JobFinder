"""Email receipt for every submitted application.

Telegram is the live channel, but it's a chat log — it scrolls away and it
isn't searchable a month later. This puts a permanent, searchable record in
the inbox, with the résumé that was actually sent attached.

Needs RESEND_API_KEY in .env. Without it nothing is sent and nothing breaks;
the caller is told so it can say so on Telegram.
"""
import base64
import os
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

import config  # noqa: F401  — imported for its load_dotenv side effect

RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "").strip()
EMAIL_TO = os.environ.get("EMAIL_TO", "rakhabduazim@gmail.com").strip()
# resend.dev works with no domain setup. Swap for an address on a verified
# domain when there is one — mail from resend.dev is likelier to be filtered.
EMAIL_FROM = os.environ.get("EMAIL_FROM", "JobFinder <onboarding@resend.dev>").strip()
LOCAL_TZ = ZoneInfo(os.environ.get("LOCAL_TZ", "America/New_York"))

# Ordered: an hourly rate is more specific than a bare range, so it wins.
PAY_PATTERNS = [
    r"\$\s?[\d,]+(?:\.\d+)?\s*(?:-|–|to)\s*\$?\s?[\d,]+(?:\.\d+)?\s*"
    r"(?:per\s+hour|/\s?hour|/\s?hr|an hour|hourly)",
    r"\$\s?[\d,]+(?:\.\d+)?\s*(?:per\s+hour|/\s?hour|/\s?hr|an hour|hourly)",
    r"\$\s?[\d,]+(?:\.\d+)?\s*(?:-|–|to)\s*\$?\s?[\d,]+(?:\.\d+)?\s*"
    r"(?:per\s+year|/\s?year|/\s?yr|annually|a year)?",
]


def extract_pay(jd_text: str) -> str:
    """Best-effort. LinkedIn has no pay field, so this reads the JD prose."""
    text = " ".join((jd_text or "").split())
    for pat in PAY_PATTERNS:
        m = re.search(pat, text, re.I)
        if m:
            return m.group(0).strip()
    return "not listed in the posting"


def _attachment(path: str | None) -> list[dict]:
    if not path:
        return []
    p = Path(path)
    if not p.exists() or p.stat().st_size > 8_000_000:
        return []
    return [{
        "filename": p.name,
        "content": base64.b64encode(p.read_bytes()).decode(),
    }]


def _row(label: str, value: str) -> str:
    return (
        f'<tr><td style="padding:6px 14px 6px 0;color:#666;vertical-align:top;'
        f'white-space:nowrap">{label}</td>'
        f'<td style="padding:6px 0"><b>{value}</b></td></tr>'
    )


def build_body(job: dict, app: dict, result: dict, when: datetime) -> str:
    tailored = app.get("tailored_json") or {}
    links = []
    if job.get("ats_url"):
        links.append(f'<a href="{job["ats_url"]}">Application page</a>')
    if job.get("external_id"):
        links.append(
            f'<a href="https://www.linkedin.com/jobs/view/{job["external_id"]}/">'
            "LinkedIn posting</a>"
        )

    rows = [
        _row("Company", job.get("company") or "?"),
        _row("Role", job.get("title") or "?"),
        _row("Location", job.get("location") or "not stated"),
        _row("Workplace", tailored.get("workplace_type") or "unstated"),
        _row("Pay", extract_pay(job.get("jd_text"))),
        _row("Applied at", when.strftime("%A %d %B %Y, %-I:%M %p %Z")),
        _row("Applied via", job.get("ats_type") or job.get("apply_lane") or "?"),
        _row("Match score", str(job.get("match_score") or "?")),
        _row("Persona", job.get("persona") or "?"),
        _row("Links", " · ".join(links) or "none"),
        _row("Outcome", result.get("detail") or result.get("status") or "?"),
    ]

    answers = result.get("answers") or []
    if answers:
        items = "".join(
            f'<li><span style="color:#666">{a["label"]}</span>: {a["value"]}</li>'
            for a in answers
        )
        answers_html = (
            '<h3 style="margin:24px 0 8px">What was filled in</h3>'
            f'<ul style="margin:0;padding-left:18px;line-height:1.6">{items}</ul>'
        )
    else:
        answers_html = ""

    note = tailored.get("note") or app.get("cover_note") or ""
    note_html = (
        f'<h3 style="margin:24px 0 8px">Note sent with it</h3>'
        f'<p style="margin:0;line-height:1.6">{note}</p>' if note else ""
    )

    return (
        '<div style="font-family:-apple-system,Segoe UI,Roboto,sans-serif;'
        'font-size:15px;color:#111;max-width:640px">'
        f'<h2 style="margin:0 0 4px">Applied to {job.get("title", "")}</h2>'
        f'<p style="margin:0 0 18px;color:#666">{job.get("company", "")}</p>'
        f'<table style="border-collapse:collapse">{"".join(rows)}</table>'
        f"{answers_html}{note_html}"
        '<p style="margin:24px 0 0;color:#888;font-size:13px">'
        "The résumé attached is the exact file that was submitted.</p>"
        "</div>"
    )


def send_application_email(job: dict, app: dict, result: dict) -> tuple[bool, str]:
    """Returns (sent, reason). Never raises — a mail problem must not undo a
    submission that already happened."""
    if not RESEND_API_KEY:
        return False, "RESEND_API_KEY not set"

    when = datetime.now(LOCAL_TZ)
    payload = {
        "from": EMAIL_FROM,
        "to": [EMAIL_TO],
        "subject": f"Applied: {job.get('title', '')} at {job.get('company', '')}",
        "html": build_body(job, app, result, when),
    }
    att = _attachment(app.get("resume_pdf"))
    if att:
        payload["attachments"] = att

    try:
        r = httpx.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {RESEND_API_KEY}"},
            json=payload, timeout=30,
        )
        if r.status_code in (200, 201):
            return True, "sent"
        return False, f"resend {r.status_code}: {r.text[:120]}"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"
