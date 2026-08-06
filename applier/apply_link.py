"""Get the real application URL for a LinkedIn job.

LinkedIn hides the offsite apply URL from logged-out visitors, so this uses the
authenticated Camoufox profile — but ONLY for jobs you have already approved.
That keeps authenticated traffic at a few page views per day (vs. hundreds for
discovery, which stays in guest mode and carries no account risk).

If the session is dead, this returns None and the application routes to
needs_manual — discovery is unaffected either way.
"""
import re

from camoufox.sync_api import Camoufox

from config import PROXY, PROFILE_DIR
from linkedin.discovery import _clear_stale_profile_lock

APPLY_BTN = re.compile(r"(?i)apply|continue")


def resolve_apply_url(job_id: str, timeout_ms: int = 30000) -> str | None:
    """Returns the company's real application URL, or None."""
    url = f"https://www.linkedin.com/jobs/view/{job_id}/"
    _clear_stale_profile_lock()
    try:
        with Camoufox(
            headless=True, humanize=True, geoip=True, proxy=PROXY,
            persistent_context=True, user_data_dir=PROFILE_DIR,
        ) as browser:
            page = browser.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            page.wait_for_timeout(3000)

            # Logged out -> the apply control is never rendered.
            if page.locator("#global-nav").count() == 0:
                return None

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
