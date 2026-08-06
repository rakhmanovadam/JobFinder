"""Fill and submit a real ATS application form with Playwright Chromium.

Hard rule (Layer 4): if any REQUIRED field cannot be resolved to a real value,
nothing is submitted — the application is routed to needs_manual instead.
Screenshots the filled form before submitting, always.
"""
import re
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright

from applier.fields import enumerate_fields, hydrate_combobox_options, resolve_form

ROOT = Path(__file__).resolve().parent.parent
SHOTS = ROOT / "screenshots"

SUBMIT_RE = re.compile(r"(?i)^(submit|submit application|apply|send application)$")


def _fill_field(page, name: str, entry: dict) -> bool:
    from applier.fields import _locate

    f, value = entry["field"], entry["value"]
    loc = _locate(page, f)
    if loc is None:
        return False
    try:
        if f["type"] == "select":
            loc.select_option(label=value)
        elif f["type"] == "combobox":
            # React widget: open it and click the matching option — typing into
            # it or setting .value directly leaves the component's state unset.
            loc.scroll_into_view_if_needed(timeout=3000)
            loc.click(timeout=3000)
            page.wait_for_timeout(300)
            opt = page.get_by_role("option", name=re.compile(f"^{re.escape(value)}$", re.I))
            if opt.count() == 0:
                opt = page.locator('[role="option"]').filter(has_text=value)
            if opt.count() == 0:
                page.keyboard.press("Escape")
                return False
            opt.first.click(timeout=3000)
            page.wait_for_timeout(200)
        elif f["type"] == "buttongroup":
            # Click the option button whose text matches; exact first so "No"
            # never matches "Not applicable".
            btns = loc.locator("button")
            target = None
            for i in range(btns.count()):
                if (btns.nth(i).inner_text() or "").strip().lower() == str(value).strip().lower():
                    target = btns.nth(i)
                    break
            if target is None:
                for i in range(btns.count()):
                    if str(value).strip().lower() in (btns.nth(i).inner_text() or "").strip().lower():
                        target = btns.nth(i)
                        break
            if target is None:
                return False
            target.scroll_into_view_if_needed(timeout=3000)
            target.click(timeout=3000)
            page.wait_for_timeout(200)
        elif f["type"] in ("radio", "checkbox"):
            page.locator(f'[name="{f["name"]}"][value="{value}"]').first.check()
        else:
            loc.fill(str(value))
        return True
    except Exception:
        return False


def apply_to_job(job: dict, app: dict, dry_run: bool = False) -> dict:
    """Returns {'status': applied|needs_manual|failed, 'detail', 'screenshot'}."""
    url = job.get("ats_url")
    if not url:
        # No board API match — ask LinkedIn (authenticated) for the real apply
        # URL. Only runs for approved jobs, so authenticated volume stays tiny.
        from applier.apply_link import resolve_apply_url

        url = resolve_apply_url(job["external_id"])
        if url:
            from db import get_db

            get_db().table("jobs").update(
                {"ats_url": url, "apply_lane": "ats"}
            ).eq("id", job["id"]).execute()
    if not url:
        return {
            "status": "needs_manual",
            "detail": "could not resolve an application URL — apply by hand",
        }

    resume = app.get("resume_pdf")
    SHOTS.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    shot_path = SHOTS / f"{job['external_id']}-{stamp}.png"

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(2500)

            # Some boards hide the form behind an Apply button.
            for label in ("Apply for this job", "Apply now", "Apply"):
                btn = page.get_by_role("button", name=re.compile(f"^{label}$", re.I))
                if btn.count():
                    btn.first.click()
                    page.wait_for_timeout(2000)
                    break

            # Upload the résumé FIRST: boards like Ashby run an "autofill from
            # resume" pass that overwrites whatever is already in the inputs, so
            # filling before uploading silently loses every answer.
            uploaded = False
            if resume and Path(resume).exists():
                fi = page.locator('input[type="file"]').first
                if fi.count():
                    fi.set_input_files(resume)
                    uploaded = True
                    page.wait_for_timeout(4000)

            fields = enumerate_fields(page)
            if not fields:
                return {"status": "needs_manual", "detail": "no form fields found"}
            hydrate_combobox_options(page, fields)

            answers, blocked = resolve_form(fields)

            if blocked:
                labels = ", ".join(b["label"][:40] for b in blocked[:4])
                page.screenshot(path=str(shot_path), full_page=True)
                return {
                    "status": "needs_manual",
                    "detail": f"unanswered required field(s): {labels}",
                    "screenshot": str(shot_path),
                }

            filled = sum(_fill_field(page, n, e) for n, e in answers.items())

            page.screenshot(path=str(shot_path), full_page=True)

            # What went into the form, for the confirmation card. Kept as data
            # so the Telegram layer can render it without re-deriving anything.
            summary = [
                {"label": e["field"]["label"][:70], "value": str(e["value"])[:70]}
                for e in answers.values()
            ]
            if dry_run:
                return {
                    "status": "preview",
                    "detail": f"filled {filled}/{len(answers)} field(s), "
                              f"résumé uploaded: {uploaded}",
                    "screenshot": str(shot_path),
                    "answers": summary,
                    "url": url,
                }

            submit = None
            for role in ("button", "link"):
                cand = page.get_by_role(role, name=SUBMIT_RE)
                if cand.count():
                    submit = cand.first
                    break
            if submit is None:
                return {
                    "status": "needs_manual",
                    "detail": "submit control not found",
                    "screenshot": str(shot_path),
                }

            submit.click()
            page.wait_for_timeout(6000)
            body = (page.inner_text("body") or "").lower()
            ok = any(
                k in body
                for k in ("thank you", "application received", "successfully",
                          "we received", "submitted")
            )
            after = SHOTS / f"{job['external_id']}-{stamp}-after.png"
            page.screenshot(path=str(after), full_page=True)
            return {
                "status": "applied" if ok else "needs_manual",
                "detail": "submitted" if ok else "no confirmation text — verify by hand",
                "screenshot": str(after),
            }
        except Exception as e:
            return {"status": "failed", "detail": f"{type(e).__name__}: {e}"}
        finally:
            browser.close()
