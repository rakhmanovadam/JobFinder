"""Sweep discovery: one browser session runs ALL keywords like a human
job-hunting session — shuffled order, randomized waits and scrolls, occasional
keyword skipped. Meant to run 3-4x/day, not hourly.

Run:  python -m linkedin.discovery
Prints parsed cards as JSON lines. Hard-stops the whole sweep on any
checkpoint/authwall.
"""
import json
import os
import random
import re
import time

from camoufox.sync_api import Camoufox

from config import (
    PROXY,
    PROFILE_DIR,
    MAX_SPECS_PER_PASS,
    SEARCH_SPECS,
    SWEEP_TPR,
    GEO_US,
    WT_REMOTE,
    search_url,
)


def is_checkpoint(page, guest: bool = True) -> bool:
    """True when the page is a block/verification wall we must not retry into.

    guest mode: only a hard block counts (security check, or a signup wall with
    no job results behind it) — the public SERP is the expected surface.
    authenticated mode: any sign of being logged out also counts.
    """
    url = page.url.lower()
    if any(k in url for k in ("checkpoint", "authwall", "/uas/login")):
        return True
    try:
        if page.locator("text=/security check|verify you.?re a human/i").count() > 0:
            return True

        title = (page.title() or "").lower()
        signup_wall = (
            "sign up" in title
            or "join linkedin" in title
            or page.locator("h1:has-text('Join LinkedIn')").count() > 0
        )
        if guest:
            # A signup wall with zero cards behind it means LinkedIn refused to
            # serve results — that's the guest-mode block signal.
            return signup_wall and page.locator("div.base-card").count() == 0
        if signup_wall:
            return True
        # Authenticated chrome. #global-nav alone is stale in LinkedIn's
        # current DOM, so waiting on it declared a perfectly good session a
        # checkpoint and hard-stopped the sweep after two keywords. Use the
        # same marker _session_alive does, and only after giving it a moment
        # to render.
        for _ in range(10):
            if _session_alive(page):
                return False
            page.wait_for_timeout(1000)
        # Cards on the page mean LinkedIn served results, whatever the chrome
        # looks like — that is not a block.
        return page.locator(
            "li[data-occludable-job-id], div.job-card-container, div.base-card"
        ).count() == 0
    except Exception:
        return False


def human_pause(a=3.0, b=8.0):
    time.sleep(random.uniform(a, b))


# Authenticated links are /jobs/view/<id>; guest links are /jobs/view/<slug>-<id>.
JOB_ID_RE = re.compile(r"/jobs/view/(?:[^/?#]*?-)?(\d+)")
URN_RE = re.compile(r"jobPosting:(\d+)")


def extract_guest_cards(page) -> list[dict]:
    """Parse the logged-out public SERP (div.base-card). Same result set as the
    authenticated view for keyword+geo+filter searches, with zero account risk."""
    cards = []
    items = page.locator("div.base-card")
    total = items.count()
    for i in range(total):
        item = items.nth(i)
        try:
            # data-entity-urn is the most stable id source on the guest SERP.
            urn = item.get_attribute("data-entity-urn") or ""
            m = URN_RE.search(urn)
            if not m:
                href = (
                    item.locator("a[href*='/jobs/view/']").first.get_attribute("href")
                    or ""
                )
                m = JOB_ID_RE.search(href)
            if not m:
                continue

            def txt(sel):
                loc = item.locator(sel).first
                return " ".join(loc.inner_text().split()) if loc.count() else ""

            posted = ""
            t = item.locator("time").first
            if t.count():
                posted = t.get_attribute("datetime") or t.inner_text().strip()

            cards.append(
                {
                    "external_id": m.group(1),
                    "title": txt("h3"),
                    "company": txt("h4"),
                    "location": txt("span.job-search-card__location"),
                    "posted": posted,
                    "easy_apply": False,
                    "link": f"https://www.linkedin.com/jobs/view/{m.group(1)}/",
                }
            )
        except Exception:
            continue
    print(f"parsed {len(cards)} of {total} visible cards (guest)")
    return cards


def _profile_has_live_session(headless: bool = False) -> bool:
    """Cheap pre-flight: does the stored profile still hold a valid li_at?

    Reads the cookie jar first (no request at all), and only opens a page when
    the cookie is present — so a dead session costs zero LinkedIn traffic.
    """
    import sqlite3
    import shutil
    import tempfile

    src = os.path.join(PROFILE_DIR, "cookies.sqlite")
    if not os.path.exists(src):
        return False
    try:
        tmp = tempfile.mktemp()
        shutil.copy(src, tmp)
        for ext in ("-wal", "-shm"):
            if os.path.exists(src + ext):
                shutil.copy(src + ext, tmp + ext)
        names = {
            r[0]
            for r in sqlite3.connect(tmp).execute(
                "select name from moz_cookies where host like '%linkedin%'"
            )
        }
        return "li_at" in names
    except Exception as e:
        print("   session pre-flight failed:", e)
        return False


def _session_alive(page) -> bool:
    """Authenticated chrome. #global-nav is stale in LinkedIn's current DOM —
    the 'Me' menu is the reliable marker."""
    try:
        return (
            page.locator("button:has-text('Me')").count() > 0
            or page.locator("#global-nav, .global-nav").count() > 0
        )
    except Exception:
        return False


def _browse_like_a_human(page) -> None:
    """Reading behaviour, not a fetch loop: variable scroll depth and speed,
    occasional scroll-back, sometimes a hover over a card. Cheap, and it makes
    the request cadence look less like a script."""
    for _ in range(random.randint(1, 4)):
        page.mouse.wheel(0, random.randint(300, 1400))
        human_pause(1.2, 4.5)

    # People scroll back up to re-read something ~30% of the time.
    if random.random() < 0.3:
        page.mouse.wheel(0, -random.randint(200, 700))
        human_pause(1.0, 3.0)

    # And sometimes rest the pointer on a listing before moving on.
    if random.random() < 0.45:
        try:
            cards = page.locator("li[data-occludable-job-id], div.base-card")
            n = cards.count()
            if n:
                cards.nth(random.randrange(min(n, 8))).hover(timeout=3000)
                human_pause(0.8, 2.5)
        except Exception:
            pass


def extract_job_cards(page) -> list[dict]:
    """Parse visible job cards. Tolerant of DOM drift: each card wrapped in
    try/except, cards without a jobId are skipped, returns parsed subset."""
    cards = []
    # The authenticated list is virtualised and renders after the shell, so
    # counting immediately returned 0 and silently fell through to the guest
    # parser — which then also returned 0, making a working search look empty.
    # Wait for the list before deciding it isn't there.
    AUTH_SEL = ("li[data-occludable-job-id], div.job-card-container, "
                "li.scaffold-layout__list-item")
    try:
        page.wait_for_selector(AUTH_SEL, timeout=12000)
    except Exception:
        pass

    items = page.locator("li[data-occludable-job-id]")
    if items.count() == 0:
        items = page.locator("div.job-card-container")
    if items.count() == 0:
        items = page.locator("li.scaffold-layout__list-item")
    total = items.count()
    if total == 0:
        # Genuinely no authenticated list. Either the guest SERP was served, or
        # the search really has no results — say which, because they mean very
        # different things.
        body = ""
        try:
            body = page.inner_text("body") or ""
        except Exception:
            pass
        if re.search(r"\bNo matching jobs found\b|\b0 results\b", body, re.I):
            print("search returned no results (authenticated)")
            return []
        return extract_guest_cards(page)

    for i in range(total):
        item = items.nth(i)
        try:
            job_id = item.get_attribute("data-occludable-job-id") or ""
            if not job_id:
                link = item.locator("a[href*='/jobs/view/']").first
                href = link.get_attribute("href") or ""
                m = JOB_ID_RE.search(href)
                job_id = m.group(1) if m else ""
            if not job_id:
                continue

            def txt(sel):
                loc = item.locator(sel).first
                return loc.inner_text().strip() if loc.count() else ""

            title = txt("a[href*='/jobs/view/'] strong") or txt(
                ".job-card-list__title--link"
            ) or txt("a[href*='/jobs/view/']")
            company = txt(".artdeco-entity-lockup__subtitle") or txt(
                ".job-card-container__primary-description"
            )
            location = txt(".job-card-container__metadata-wrapper li") or txt(
                ".artdeco-entity-lockup__caption"
            )
            posted = txt("time")
            easy_apply = "Easy Apply" in (item.inner_text() or "")

            cards.append(
                {
                    "external_id": job_id,
                    "title": " ".join(title.split()),
                    "company": " ".join(company.split()),
                    "location": " ".join(location.split()),
                    "posted": posted,
                    "easy_apply": easy_apply,
                    "link": f"https://www.linkedin.com/jobs/view/{job_id}/",
                }
            )
        except Exception:
            continue

    print(f"parsed {len(cards)} of {total} visible cards")
    return cards


def _clear_stale_profile_lock():
    """Firefox leaves .parentlock behind after a crash/kill, which blocks the
    next launch with 'A copy of Camoufox is already open'. Safe to remove when
    no camoufox process is actually running."""
    import subprocess
    from pathlib import Path

    if subprocess.run(["pgrep", "-if", "camoufox"], capture_output=True).returncode != 0:
        for name in (".parentlock", "parent.lock", "lock"):
            Path(PROFILE_DIR, name).unlink(missing_ok=True)


def _alert_checkpoint(page):
    try:
        from tg.notify import alert

        alert(
            "⚠️ LinkedIn checkpoint — aborting sweep. "
            "May need re-auth (run python -m linkedin.session)."
        )
    except Exception:
        pass
    print("CHECKPOINT DETECTED — hard stop. Current url:", page.url)


# Drip pacing: gap between keyword searches, in seconds (5-10 minutes).
# Full 13-keyword pass takes ~75-110 min of a 3h cycle.
KEYWORD_GAP = (300, 600)


class SessionDead(RuntimeError):
    """No usable LinkedIn session — discovery must not run guest-mode."""


def sweep(
    headless: bool = False,
    keywords: list[str] | None = None,
    tpr: str | None = None,
    gap: tuple = KEYWORD_GAP,
    guest: bool = True,
):
    """Generator: one browser session, yields (keyword, cards) per keyword so
    the caller can store/notify immediately instead of waiting for the pass to
    finish. 5-10 min between searches.

    guest=False is the real path: only the authenticated SERP honours f_WT, so
    only it can tell a remote job from an on-site one. Without a live session
    this raises SessionDead rather than falling back to guest — an unfiltered
    pass looks identical to a filtered one downstream.

    guest=True remains for manual probing (debug_search.py, doctor).
    """
    if keywords:
        specs = [
            k if isinstance(k, dict) else {"kw": k, "geo": GEO_US, "wt": WT_REMOTE}
            for k in keywords
        ]
    else:
        specs = list(SEARCH_SPECS)
    random.shuffle(specs)
    # Cap the pass. Gaps are 5-10 min, so every spec every time would overrun
    # the 3-hour cadence and start overlapping itself. Shuffling first means
    # the full list is still covered across the day's slots, and a shorter,
    # variable pass looks less like a scheduled job than an exhaustive one.
    if len(specs) > MAX_SPECS_PER_PASS:
        specs = specs[:random.randint(MAX_SPECS_PER_PASS - 2, MAX_SPECS_PER_PASS)]

    # Authenticated-only by request. The guest SERP accepts f_WT and ignores
    # it, so a guest pass returns on-site jobs labelled as if they had been
    # filtered for remote — worse than no pass at all. A dead session is now a
    # hard stop, not a downgrade.
    if not guest and not _profile_has_live_session(headless=headless):
        raise SessionDead(
            "no live li_at — discovery is authenticated-only. "
            "Re-run: python -m linkedin.session"
        )

    seen = set()
    cf_kwargs = dict(headless=headless, humanize=True, geoip=True, proxy=PROXY)
    if not guest:
        _clear_stale_profile_lock()
        cf_kwargs.update(persistent_context=True, user_data_dir=PROFILE_DIR)

    with Camoufox(**cf_kwargs) as browser:
        page = browser.new_page()
        for i, spec in enumerate(specs):
            kw, geo, wt = spec["kw"], spec["geo"], spec["wt"]
            scope = "durham" if geo != GEO_US else "remote-us"
            print(f"[{i + 1}/{len(specs)}] searching: {kw} ({scope})")
            page.goto(
                search_url(kw, tpr=tpr or SWEEP_TPR, geo=geo, wt=wt),
                wait_until="domcontentloaded",
            )
            human_pause(4, 9)

            # In guest mode the signup wall means "blocked", not "logged out";
            # in authenticated mode any checkpoint aborts the sweep.
            if is_checkpoint(page, guest=guest):
                _alert_checkpoint(page)
                return

            # Authenticated mode is the only one where f_WT (remote/on-site) is
            # honoured — the guest endpoint accepts the parameter and ignores
            # it. Losing the session mid-pass therefore changes what every
            # later result means, so the pass stops there instead of quietly
            # continuing with unfiltered cards.
            wt_applied = not guest
            if not guest and not _session_alive(page):
                raise SessionDead(
                    f"session dropped mid-pass (after {i} of {len(specs)} "
                    "keywords) — remaining results would not be "
                    "workplace-filtered. Re-run: python -m linkedin.session"
                )

            _browse_like_a_human(page)

            fresh = []
            for c in extract_job_cards(page):
                if c["external_id"] not in seen:
                    seen.add(c["external_id"])
                    c["wt_filtered"] = wt_applied
                    fresh.append(c)
            yield kw, fresh

            # Drip gap before the next search (none after the last).
            if i < len(specs) - 1:
                human_pause(*gap)


def discover(headless: bool = False, **kwargs) -> list[dict]:
    """Flat-list wrapper around sweep() for quick CLI runs."""
    all_cards = []
    for _, cards in sweep(headless=headless, **kwargs):
        all_cards.extend(cards)
    print(f"sweep done: {len(all_cards)} unique cards")
    return all_cards


if __name__ == "__main__":
    # CLI test run uses short gaps so a manual spike doesn't take 2 hours.
    for card in discover(headless=False, gap=(12, 35)):
        print(json.dumps(card, ensure_ascii=False))
