"""Fire-and-forget Telegram sends (used by the batch process). Plain HTTP —
the always-on bot (tg/callbacks.py) owns polling and button handling."""
import html

import httpx

from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"


def send(text: str, reply_markup: dict | None = None) -> dict | None:
    if not TELEGRAM_CHAT_ID:
        print("TELEGRAM_CHAT_ID not set — skipping send:", text[:80])
        return None
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    r = httpx.post(f"{API}/sendMessage", json=payload, timeout=20)
    r.raise_for_status()
    return r.json()


def send_document(path: str, caption: str = "") -> dict | None:
    if not TELEGRAM_CHAT_ID:
        return None
    with open(path, "rb") as f:
        r = httpx.post(
            f"{API}/sendDocument",
            data={"chat_id": TELEGRAM_CHAT_ID, "caption": caption[:1000]},
            files={"document": f},
            timeout=60,
        )
    return r.json() if r.status_code == 200 else None


def alert(text: str):
    try:
        send(text)
    except Exception as e:
        print("telegram alert failed:", e)


LANE_LABEL = {
    "ats": "🟢 auto-apply available",
    "easy_apply": "🟡 LinkedIn Easy Apply",
    "unresolved": "🔵 manual apply",
}


def send_job_card(job: dict, app_row: dict, note: str) -> dict | None:
    """The approval card: job summary + tailored note + action buttons."""
    e = html.escape
    lane = job.get("apply_lane") or "unresolved"
    apply_target = job.get("ats_url") or (
        f"https://www.linkedin.com/jobs/view/{job['external_id']}/"
    )

    text = (
        f"📌 <b>{e(job['title'])}</b>\n"
        f"{e(job['company'])} · {e(job.get('location') or '')}\n"
        f"persona: {job.get('persona')} · match {int(job.get('match_score') or 0)}\n"
        f"{LANE_LABEL.get(lane, lane)}"
        + (f" ({job.get('ats_type')})" if job.get("ats_type") else "")
        + f"\n\n<i>{e(note)}</i>\n\n"
        f'<a href="{apply_target}">open posting</a>'
    )

    app_id = app_row["id"]
    buttons = [
        [
            {"text": "✅ Apply", "callback_data": f"ok:{app_id}"},
            {"text": "❌ Skip", "callback_data": f"no:{app_id}"},
        ],
        [
            {"text": "📄 Resume", "callback_data": f"cv:{app_id}"},
            {"text": "👁 JD", "callback_data": f"jd:{app_id}"},
        ],
    ]
    return send(text, reply_markup={"inline_keyboard": buttons})
