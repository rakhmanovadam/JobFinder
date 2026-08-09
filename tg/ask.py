"""Ask about a field the agent can't answer, then never ask again.

A blocked required field used to end the application: it went to needs_manual
and the same question blocked the next form too. Now the bot asks, the answer
goes into field_answers, and cached_answer picks it up from then on.

Pending questions live in `control` (key/value jsonb) keyed by the Telegram
message id, so this needs no schema change. Choice fields get buttons; free
text gets a force-reply.
"""
import uuid

import httpx

from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from db import get_db

API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
KEY = "ask:{}"


def _post(method: str, payload: dict) -> dict | None:
    try:
        r = httpx.post(f"{API}/{method}", json=payload, timeout=25)
        return r.json() if r.status_code == 200 else None
    except Exception:
        return None


def _pending_key(message_id) -> str:
    return KEY.format(message_id)


def save_pending(message_id, field: dict, job: dict, app_id: str):
    get_db().table("control").upsert(
        {
            "key": _pending_key(message_id),
            "value": {
                "label": field["label"],
                "type": field["type"],
                "options": field.get("options") or [],
                "app_id": app_id,
                "company": job.get("company", ""),
                "title": job.get("title", ""),
            },
        }
    ).execute()


def load_pending(message_id) -> dict | None:
    r = (
        get_db().table("control").select("value")
        .eq("key", _pending_key(message_id)).execute()
    )
    return r.data[0]["value"] if r.data else None


def clear_pending(message_id):
    get_db().table("control").delete().eq("key", _pending_key(message_id)).execute()


def already_asked(field: dict) -> bool:
    """Don't re-ask a question that's already waiting for an answer."""
    rows = get_db().table("control").select("key,value").like("key", "ask:%").execute().data
    return any(
        (r["value"] or {}).get("label") == field["label"]
        and (r["value"] or {}).get("type") == field["type"]
        for r in rows
    )


def ask_field(field: dict, job: dict, app_id: str) -> bool:
    """Send one question. Returns True if it went out."""
    if already_asked(field):
        return False

    head = (
        f"❓ <b>Need an answer</b>\n"
        f"{job.get('company', '')} · {job.get('title', '')}\n\n"
        f"<b>{field['label']}</b>"
    )
    options = [o for o in (field.get("options") or []) if o][:8]

    if options:
        # Short ids: callback_data is capped at 64 bytes, and option text is not.
        qid = uuid.uuid4().hex[:8]
        rows = [[{"text": o[:60], "callback_data": f"ans:{qid}:{i}"}]
                for i, o in enumerate(options)]
        res = _post("sendMessage", {
            "chat_id": TELEGRAM_CHAT_ID, "text": head, "parse_mode": "HTML",
            "reply_markup": {"inline_keyboard": rows},
        })
        if not (res and res.get("ok")):
            return False
        mid = res["result"]["message_id"]
        save_pending(mid, field, job, app_id)
        # Indexed a second time by qid, because a button tap reports the id of
        # the message it is attached to, but a tap on an older card can arrive
        # after that message has been edited.
        get_db().table("control").upsert(
            {"key": _pending_key(qid), "value": {"message_id": mid}}
        ).execute()
        return True

    res = _post("sendMessage", {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": head + "\n\n<i>Reply to this message with your answer.</i>",
        "parse_mode": "HTML",
        "reply_markup": {"force_reply": True},
    })
    if not (res and res.get("ok")):
        return False
    save_pending(res["result"]["message_id"], field, job, app_id)
    return True


def ask_blocked(blocked: list[dict], job: dict, app_id: str) -> int:
    """Ask about every field that stopped this application. Returns how many."""
    return sum(ask_field(f, job, app_id) for f in (blocked or [])[:6])


def store_answer(pending: dict, answer: str):
    """Remember it so the question is never asked again."""
    get_db().table("field_answers").upsert(
        {
            "field_label": pending["label"],
            "field_type": pending["type"],
            "answer": answer,
            "source": "asked",
        },
        on_conflict="field_label,field_type",
    ).execute()
