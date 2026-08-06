"""PROCESS 2 — always-on Telegram bot: approval buttons, controls, and the
worker that runs the applier when you tap ✅.

Run:  python -m tg.callbacks
"""
import asyncio
import html
from datetime import datetime, timezone

from telegram import Update, InputFile
from telegram.ext import (
    ApplicationBuilder, CallbackQueryHandler, CommandHandler, ContextTypes,
)

from config import TELEGRAM_BOT_TOKEN, MAX_APPLIES_PER_DAY, MAX_APPLIES_PER_HOUR
from db import get_db


def set_control(key: str, value):
    get_db().table("control").upsert(
        {"key": key, "value": value, "updated_at": datetime.now(timezone.utc).isoformat()}
    ).execute()


def get_control(key: str, default=None):
    r = get_db().table("control").select("value").eq("key", key).execute()
    return r.data[0]["value"] if r.data else default


def get_app(app_id: str) -> dict | None:
    r = get_db().table("applications").select("*").eq("id", app_id).execute()
    return r.data[0] if r.data else None


def get_job(job_id: str) -> dict | None:
    r = get_db().table("jobs").select("*").eq("id", job_id).execute()
    return r.data[0] if r.data else None


def applies_today() -> int:
    start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    r = (
        get_db().table("applications").select("id", count="exact")
        .eq("status", "applied").gte("applied_at", start.isoformat()).execute()
    )
    return r.count or 0


async def on_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    # A tap made while the bot was down arrives with an expired query id.
    # Acknowledging fails, but the action itself is still valid — keep going and
    # reply with a fresh message instead of editing the original card.
    stale = False
    try:
        await q.answer()
    except Exception:
        stale = True

    action, _, app_id = (q.data or "").partition(":")
    async def respond(text: str):
        """Edit the card when possible; fall back to a new message if stale."""
        if not stale:
            try:
                await q.edit_message_text(text, parse_mode="HTML")
                return
            except Exception:
                pass
        await q.message.reply_text(text, parse_mode="HTML")

    app = get_app(app_id)
    if not app:
        await respond("⚠️ application row not found")
        return
    job = get_job(app["job_id"]) or {}

    if action == "no":
        get_db().table("applications").update({"status": "skipped"}).eq(
            "id", app_id
        ).execute()
        await respond(
            f"❌ Skipped — {html.escape(job.get('title', ''))} @ "
            f"{html.escape(job.get('company', ''))}"
        )
        return

    if action == "cv":
        path = app.get("resume_pdf")
        if path:
            with open(path, "rb") as f:
                await q.message.reply_document(
                    InputFile(f), caption=f"Tailored resume — {job.get('company', '')}"
                )
        else:
            await q.message.reply_text("no resume on file")
        return

    if action == "jd":
        jd = (job.get("jd_text") or "")[:3500] or "no JD stored"
        await q.message.reply_text(jd)
        return

    if action == "ok":
        if get_control("paused") is True:
            await q.message.reply_text("⏸ paused — /resume first")
            return
        if applies_today() >= MAX_APPLIES_PER_DAY:
            await q.message.reply_text(f"🚦 daily cap reached ({MAX_APPLIES_PER_DAY})")
            return

        get_db().table("applications").update({"status": "approved"}).eq(
            "id", app_id
        ).execute()
        await respond(
            f"✅ Approved — {html.escape(job.get('title', ''))} @ "
            f"{html.escape(job.get('company', ''))}\n<i>filling the form…</i>"
        )
        asyncio.create_task(run_preview(app_id, q))
        return

    if action == "nosub":
        get_db().table("applications").update({"status": "skipped"}).eq(
            "id", app_id
        ).execute()
        await respond("❌ Cancelled — nothing was submitted.")
        return

    if action == "sub":
        if get_control("paused") is True:
            await q.message.reply_text("⏸ paused — /resume first")
            return
        if applies_today() >= MAX_APPLIES_PER_DAY:
            await q.message.reply_text(f"🚦 daily cap reached ({MAX_APPLIES_PER_DAY})")
            return
        await q.message.reply_text(
            f"📤 Submitting — {html.escape(job.get('company', ''))} · "
            f"{html.escape(job.get('title', ''))}"
        )
        asyncio.create_task(run_applier(app_id, q))
        return


async def run_preview(app_id: str, q):
    """Fill the form and send it back for confirmation. Submits nothing."""
    from applier.ats import apply_to_job
    from tg.notify import send_apply_preview

    app = get_app(app_id)
    job = get_job(app["job_id"])
    try:
        result = await asyncio.to_thread(apply_to_job, job, app, True)
    except Exception as e:
        result = {"status": "failed", "detail": f"{type(e).__name__}: {e}"}

    if result.get("status") == "preview":
        await asyncio.to_thread(send_apply_preview, job, app, result)
        return

    # Couldn't fill it — hand the user the link rather than guessing.
    get_db().table("applications").update(
        {"status": "needs_manual", "error": result.get("detail")}
    ).eq("id", app_id).execute()
    link = job.get("ats_url") or f"https://www.linkedin.com/jobs/view/{job['external_id']}/"
    await q.message.reply_text(
        f"⚠️ Needs you — {job['company']} · {job['title']}\n"
        f"{result.get('detail', '')}\n{link}"
    )


async def run_applier(app_id: str, q):
    """Runs the ATS applier off the event loop; reports the outcome back."""
    from applier.ats import apply_to_job

    app = get_app(app_id)
    job = get_job(app["job_id"])
    try:
        result = await asyncio.to_thread(apply_to_job, job, app)
    except Exception as e:
        result = {"status": "failed", "detail": f"{type(e).__name__}: {e}"}

    db = get_db()
    if result["status"] == "applied":
        db.table("applications").update(
            {"status": "applied", "applied_at": datetime.now(timezone.utc).isoformat()}
        ).eq("id", app_id).execute()
        await q.message.reply_text(f"✅ Applied — {job['company']} · {job['title']}")
        shot = result.get("screenshot")
        if shot:
            with open(shot, "rb") as f:
                await q.message.reply_document(InputFile(f), caption="submitted form")
    elif result["status"] == "needs_manual":
        db.table("applications").update(
            {"status": "needs_manual", "error": result.get("detail")}
        ).eq("id", app_id).execute()
        link = job.get("ats_url") or f"https://www.linkedin.com/jobs/view/{job['external_id']}/"
        await q.message.reply_text(
            f"⚠️ Needs you — {job['company']} · {job['title']}\n"
            f"{result.get('detail', '')}\n{link}"
        )
    else:
        attempts = (app.get("attempts") or 0) + 1
        db.table("applications").update(
            {
                "status": "approved" if attempts < 2 else "failed",
                "attempts": attempts,
                "error": result.get("detail"),
            }
        ).eq("id", app_id).execute()
        await q.message.reply_text(
            f"❌ Apply failed ({attempts}/2) — {job['company']}\n{result.get('detail', '')}"
        )


async def cmd_pause(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    set_control("paused", True)
    await update.message.reply_text("⏸ paused — no sweeps, no applies")


async def cmd_resume(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    set_control("paused", False)
    await update.message.reply_text("▶️ resumed")


async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    db = get_db()
    rows = db.table("applications").select("status").execute().data
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    jobs = db.table("jobs").select("id", count="exact").execute().count
    matched = (
        db.table("jobs").select("id", count="exact").eq("matched", True).execute().count
    )
    paused = get_control("paused")
    await update.message.reply_text(
        f"{'⏸ PAUSED' if paused else '▶️ running'}\n"
        f"jobs seen {jobs} · matched {matched}\n"
        f"applications: {counts or 'none yet'}\n"
        f"applied today {applies_today()}/{MAX_APPLIES_PER_DAY} "
        f"(cap {MAX_APPLIES_PER_HOUR}/hr)"
    )


def main():
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(CommandHandler("pause", cmd_pause))
    app.add_handler(CommandHandler("resume", cmd_resume))
    app.add_handler(CommandHandler("status", cmd_status))
    print("bot running — ctrl-c to stop")
    app.run_polling()


if __name__ == "__main__":
    main()
