"""One-time manual LinkedIn login. Saves the full Firefox profile to PROFILE_DIR.

Run:  python -m linkedin.session
Then: cp -r li_profile li_profile.bak
"""
from camoufox.sync_api import Camoufox

from config import PROXY, PROFILE_DIR


def login_once():
    with Camoufox(
        headless=False,
        humanize=True,
        geoip=True,
        proxy=PROXY,
        persistent_context=True,
        user_data_dir=PROFILE_DIR,
    ) as browser:
        page = browser.new_page()
        page.goto("https://www.linkedin.com/login")
        input(
            "Log in by hand (2FA/CAPTCHA if asked), reach your feed, "
            "then press Enter here... "
        )
        print(f"Profile saved to {PROFILE_DIR}")
        print("Back it up now:  cp -r li_profile li_profile.bak")


if __name__ == "__main__":
    login_once()
