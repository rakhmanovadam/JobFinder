"""Fire-and-forget Telegram sends (used by the batch process). Plain HTTP —
the always-on bot (tg/callbacks.py) owns polling and button handling."""
import html
import os

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
    """Never raises. A slow upload used to abort the whole approval card, so
    the résumé attachment failing meant losing the Submit/Cancel buttons with
    it — the least important part of the message taking down the rest."""
    if not TELEGRAM_CHAT_ID:
        return None
    try:
        with open(path, "rb") as f:
            r = httpx.post(
                f"{API}/sendDocument",
                data={"chat_id": TELEGRAM_CHAT_ID, "caption": caption[:1000]},
                files={"document": f},
                timeout=httpx.Timeout(connect=15, read=180, write=180, pool=15),
            )
        return r.json() if r.status_code == 200 else None
    except Exception as e:
        print("sendDocument failed:", type(e).__name__, e)
        return None


def send_photo(path: str, caption: str = "", reply_markup: dict | None = None) -> dict | None:
    if not TELEGRAM_CHAT_ID:
        return None
    data = {"chat_id": TELEGRAM_CHAT_ID, "caption": caption[:1024], "parse_mode": "HTML"}
    if reply_markup:
        import json as _json
        data["reply_markup"] = _json.dumps(reply_markup)
    try:
        with open(path, "rb") as f:
            r = httpx.post(
                f"{API}/sendPhoto", data=data, files={"photo": f},
                timeout=httpx.Timeout(connect=15, read=180, write=180, pool=15),
            )
    except Exception as e:
        print("sendPhoto failed:", type(e).__name__, e)
        return None
    if r.status_code != 200:
        print("sendPhoto failed:", r.text[:200])
        return None
    return r.json()


def send_apply_preview(job: dict, app_row: dict, result: dict) -> dict | None:
    """Filled-form screenshot + what was entered + Submit/Cancel buttons.

    Nothing is submitted until the ✅ comes back, so a wrong answer costs a tap
    rather than a real application under the user's name.
    """
    e = html.escape
    lines = [
        f"📝 <b>Ready to submit</b>",
        f"<b>{e(job.get('company') or '?')}</b> — {e(job.get('title') or '?')}",
        "",
        e(result.get("detail", "")),
        "",
    ]
    for a in (result.get("answers") or [])[:14]:
        # Answers now arrive whole (the email needs the full text), so the
        # shortening for a chat card happens here instead.
        label = a["label"][:70]
        value = a["value"] if len(a["value"]) <= 220 else a["value"][:217] + "..."
        lines.append(f"• <b>{e(label)}</b>: {e(value)}")
    extra = len(result.get("answers") or []) - 14
    if extra > 0:
        lines.append(f"• …and {extra} more")

    # Say plainly what is going in blank, rather than leaving it to be inferred
    # from a "filled 4/6" count.
    missing = result.get("missing") or []
    if missing:
        lines.append("")
        lines.append(f"⚠️ <b>{len(missing)} field(s) left blank</b> — asking you now:")
        for m in missing[:6]:
            lines.append(f"   · {e(m['label'][:60])}")
        if len(missing) > 6:
            lines.append(f"   · …and {len(missing) - 6} more")

    lines.append("")
    lines.append("Submit this application?")

    kb = {"inline_keyboard": [[
        {"text": "✅ Submit", "callback_data": f"sub:{app_row['id']}"},
        {"text": "❌ Cancel", "callback_data": f"nosub:{app_row['id']}"},
    ]]}

    # Send the exact PDF that will be attached, first — so the résumé is on
    # screen above the Submit button rather than described in text.
    pdf = app_row.get("resume_pdf")
    if pdf and os.path.exists(pdf):
        send_document(
            pdf,
            f"Résumé that will be submitted — {job.get('company') or '?'} · "
            f"{job.get('title') or '?'}",
        )
    else:
        lines.insert(4, "⚠️ <b>No tailored résumé on file</b> — nothing will be attached.")

    shot = result.get("screenshot")
    caption = "\n".join(lines)
    if shot:
        # Telegram caps captions at 1024 chars; long field lists go as a
        # follow-up message so nothing is silently truncated away.
        if len(caption) <= 1024:
            return send_photo(shot, caption, kb)
        send_photo(shot, f"📝 <b>Ready to submit</b>\n{e(job.get('company') or '?')} — {e(job.get('title') or '?')}")
        return send(caption, kb)
    return send(caption, kb)


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
