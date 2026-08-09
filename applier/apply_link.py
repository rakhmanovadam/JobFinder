"""Get the real application URL for a LinkedIn job.

LinkedIn hides the offsite apply URL from logged-out visitors, so this uses the
authenticated Camoufox profile — but ONLY for jobs you have already approved.
That keeps authenticated traffic at a few page views per day (vs. hundreds for
discovery, which stays in guest mode and carries no account risk).

If the session is dead, this returns None and the application routes to
needs_manual — discovery is unaffected either way.
"""
import re
from urllib.parse import parse_qs, unquote, urlparse

from camoufox.sync_api import Camoufox

from config import PROXY, PROFILE_DIR
from linkedin.discovery import _clear_stale_profile_lock

# re.I flag, never an inline (?i) — get_by_role serialises the pattern into a
# selector string and an inline flag makes it unparseable at runtime.
APPLY_BTN = re.compile(r"apply|continue", re.I)


def _dest_from_href(href: str | None) -> str | None:
    """LinkedIn wraps offsite applies as /safety/go/?url=<encoded real url>, so the
    destination can be read straight off the anchor — no click, no popup, no extra
    page load. Falls back to a plain external href."""
    if not href:
        return None
    if "/safety/go/" in href:
        q = parse_qs(urlparse(href).query).get("url", [])
        return unquote(q[0]) if q else None
    return href if "linkedin.com" not in href else None


def _logged_in(page) -> bool:
    """LinkedIn's authenticated chrome. #global-nav is stale — the current DOM
    exposes the 'Me' menu button instead."""
    return (
        page.locator("button:has-text('Me')").count() > 0
        or page.locator("#global-nav, .global-nav").count() > 0
    )


def resolve_apply_url(job_id: str, timeout_ms: int = 30000) -> str | None:
    """Returns the company's real application URL, or None."""
    url = f"https://www.linkedin.com/jobs/view/{job_id}/"
    _clear_stale_profile_lock()
    try:
        with Camoufox(
            headless=False, humanize=True, geoip=True, proxy=PROXY,
            persistent_context=True, user_data_dir=PROFILE_DIR,
        ) as browser:
            page = browser.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            page.wait_for_timeout(6000)

            # Logged out -> the apply control is never rendered.
            if not _logged_in(page):
                print(f"   [apply_link] session not authenticated for {job_id}")
                return None

            # Preferred path: read the destination off the anchor.
            anchors = page.locator("a:has-text('Apply')")
            for i in range(min(anchors.count(), 4)):
                a = anchors.nth(i)
                if (a.inner_text() or "").strip().lower().startswith("apply"):
                    dest = _dest_from_href(a.get_attribute("href"))
                    if dest:
                        return dest

            btn = page.get_by_role("button", name=APPLY_BTN)
            link = page.locator("a[href][class*='apply'], a[data-tracking-control-name*='apply']")

            # Offsite applies open a new tab; capture it.
            if btn.count():
                try:
                    with browser.expect_page(timeout=15000) as tab:
                        btn.first.click()
                    new_page = tab.value
                    new_page.wait_for_load_state("domcontentloaded", timeout=20000)
                    dest = new_page.url
                    new_page.close()
                    if dest and "linkedin.com" not in dest:
                        return dest
                except Exception:
                    # No new tab -> Easy Apply modal, which is not an offsite URL.
                    if page.locator("div[class*='jobs-easy-apply']").count():
                        return None

            if link.count():
                href = link.first.get_attribute("href")
                if href and "linkedin.com" not in href:
                    return href
    except Exception as e:
        print("apply-url resolution failed:", e)
    return None


if __name__ == "__main__":
    import sys

    print(resolve_apply_url(sys.argv[1]))
