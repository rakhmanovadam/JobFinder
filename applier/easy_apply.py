"""LinkedIn Easy Apply.

Easy Apply never leaves LinkedIn: the form is a multi-step modal on the job
page, so it needs the authenticated profile rather than a fresh Chromium the
way an external ATS does. Same resolver as every other form — the questions
are answered from profile.yaml, the learned cache, and the models — but the
walking is different, because the modal reveals its questions a page at a
time and only shows Submit on the last one.

Headful, always. Headless gets the logged-out page and burns li_at.
"""
import re
from datetime import datetime, timezone
from pathlib import Path

from camoufox.sync_api import Camoufox

from config import PROXY, PROFILE_DIR
from applier.fields import enumerate_fields, hydrate_combobox_options, resolve_form

ROOT = Path(__file__).resolve().parent.parent
SHOTS = ROOT / "screenshots"

MODAL = "div.jobs-easy-apply-modal, div[data-test-modal][role='dialog']"
EASY_BTN = re.compile(r"easy apply", re.I)
NEXT_BTN = re.compile(r"^(next|continue|review)", re.I)
SUBMIT_BTN = re.compile(r"^(submit application|submit)$", re.I)
MAX_STEPS = 8


def is_easy_apply(page) -> bool:
    return page.get_by_role("button", name=EASY_BTN).count() > 0


def _click(page, pattern, timeout=6000) -> bool:
    btn = page.get_by_role("button", name=pattern)
    if not btn.count():
        return False
    try:
        btn.first.click(timeout=timeout)
        page.wait_for_timeout(1800)
        return True
    except Exception:
        return False


def apply_easy(job: dict, app: dict, dry_run: bool = True) -> dict:
    """Returns the same shape as applier.ats.apply_to_job."""
    url = f"https://www.linkedin.com/jobs/view/{job['external_id']}/"
    resume = app.get("resume_pdf")
    SHOTS.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    shot = SHOTS / f"easy-{job['external_id']}-{stamp}.png"

    answers_all: dict[str, dict] = {}
    missing_all: list[dict] = []
    from linkedin.discovery import _clear_stale_profile_lock

    _clear_stale_profile_lock()
    with Camoufox(
        headless=False, humanize=True, geoip=True, proxy=PROXY,
        persistent_context=True, user_data_dir=PROFILE_DIR,
    ) as browser:
        page = browser.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(4000)

            from linkedin.discovery import _session_alive

            if not _session_alive(page):
                return {"status": "failed",
                        "detail": "LinkedIn session is not live — re-run the login"}

            if not _click(page, EASY_BTN, timeout=10000):
                return {"status": "needs_manual",
                        "detail": "no Easy Apply button on this posting"}

            for step in range(MAX_STEPS):
                page.wait_for_timeout(1200)

                # Résumé: pick the already-uploaded one if offered, else upload.
                fi = page.locator(f"{MODAL} input[type='file']")
                if fi.count() and resume and Path(resume).exists():
                    try:
                        fi.first.set_input_files(resume)
                        page.wait_for_timeout(3000)
                    except Exception:
                        pass

                fields = enumerate_fields(page, root=MODAL)
                if fields:
                    hydrate_combobox_options(page, fields)
                    answers, blocked, unanswered = resolve_form(
                        fields,
                        context={"company": job.get("company", ""),
                                 "title": job.get("title", ""),
                                 "jd": job.get("jd_text", "")},
                    )
                    if blocked:
                        page.screenshot(path=str(shot))
                        return {
                            "status": "needs_manual",
                            "detail": "unanswered required field(s): "
                                      + ", ".join(b["label"][:40] for b in blocked[:4]),
                            "screenshot": str(shot),
                            "blocked": blocked,
                        }
                    from applier.ats import _fill_field

                    for name, entry in answers.items():
                        if not _fill_field(page, name, entry):
                            missing_all.append(entry["field"])
                        answers_all[f"{step}:{name}"] = entry
                    missing_all.extend(unanswered)

                page.screenshot(path=str(shot))

                # Last step is the only one with Submit.
                if page.get_by_role("button", name=SUBMIT_BTN).count():
                    summary = [
                        {"label": e["field"]["label"], "value": str(e["value"])}
                        for e in answers_all.values()
                    ]
                    if dry_run:
                        return {"status": "preview", "missing": missing_all,
                                "detail": f"Easy Apply ready — {len(summary)} field(s) "
                                          f"filled over {step + 1} step(s)",
                                "screenshot": str(shot), "answers": summary, "url": url}
                    if not _click(page, SUBMIT_BTN, timeout=10000):
                        return {"status": "needs_manual",
                                "detail": "submit control would not click",
                                "screenshot": str(shot)}
                    page.wait_for_timeout(4000)
                    after = SHOTS / f"easy-{job['external_id']}-{stamp}-after.png"
                    page.screenshot(path=str(after))
                    body = (page.inner_text("body") or "").lower()
                    ok = any(k in body for k in
                             ("application sent", "your application was sent",
                              "applied", "thank you"))
                    return {
                        "status": "applied" if ok else "needs_manual",
                        "detail": "submitted" if ok
                                  else "no confirmation text — verify by hand",
                        "screenshot": str(after), "answers": summary, "url": url,
                    }

                if not _click(page, NEXT_BTN):
                    page.screenshot(path=str(shot))
                    return {"status": "needs_manual",
                            "detail": f"stuck at step {step + 1}: no Next or Submit",
                            "screenshot": str(shot)}

            return {"status": "needs_manual",
                    "detail": f"more than {MAX_STEPS} steps — finish by hand",
                    "screenshot": str(shot)}
        except Exception as e:
            return {"status": "failed", "detail": f"{type(e).__name__}: {e}"}
        finally:
            browser.close()
