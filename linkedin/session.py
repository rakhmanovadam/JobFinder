"""One-time manual LinkedIn login. Saves the full Firefox profile to PROFILE_DIR.

Run:  python -m linkedin.session

Takes the writer lease, so it reaps any stuck browser first and cannot race a
sweep for the cookie jar. Backs the profile up itself once the login is
verified — there is no separate cp step to forget.
"""
from camoufox.sync_api import Camoufox

from config import PROXY
from linkedin import profile_lease
from linkedin.discovery import _session_alive, human_pause


def login_once() -> bool:
    """True only when LinkedIn actually serves an authenticated page afterwards.

    The old version printed 'Profile saved' unconditionally, so a login that
    stopped at a checkpoint, or a window closed instead of Enter pressed, read
    as success — and the next sweep authwalled with no clue why.

    Cookie presence is not the check. A profile parked on /uas/login still has
    li_at, plus li_rm and 18 others; it just is not logged in. Only a real page
    load proves it.
    """
    with profile_lease.writer() as profile_dir:
        with Camoufox(
            headless=False,
            humanize=True,
            geoip=True,
            proxy=PROXY,
            persistent_context=True,
            user_data_dir=profile_dir,
        ) as browser:
            page = browser.new_page()
            page.goto("https://www.linkedin.com/login")
            input(
                "Log in by hand (2FA/CAPTCHA if asked), reach your feed, "
                "then press Enter HERE in the terminal — not just close the "
                "window, or Firefox never writes the cookies out... "
            )

            page.goto("https://www.linkedin.com/feed/",
                      wait_until="domcontentloaded")
            human_pause(5, 8)
            ok = _session_alive(page)
            print(f"   verify: {page.url[:90]}")

    if ok:
        print("Logged in. Profile saved and backed up.")
    else:
        print("NOT logged in — LinkedIn is still serving the login/authwall "
              "page. Nothing was saved as good. Run this again and make sure "
              "the feed is on screen before pressing Enter.")
    return ok


if __name__ == "__main__":
    import sys

    sys.exit(0 if login_once() else 1)
