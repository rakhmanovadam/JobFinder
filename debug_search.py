"""One-off debug: load one search URL, wait, dump diagnostics + screenshot.
Run: python debug_search.py
"""
import time

from camoufox.sync_api import Camoufox

from config import PROXY, PROFILE_DIR, search_url

URL = search_url("data analyst")
SHOT = "debug_search.png"

with Camoufox(headless=True, humanize=True, geoip=True, proxy=PROXY,
              persistent_context=True, user_data_dir=PROFILE_DIR) as browser:
    page = browser.new_page()
    page.goto(URL, wait_until="domcontentloaded")
    print("url after load:", page.url)
    time.sleep(10)
    print("url after 10s :", page.url)
    print("title:", page.title())
    for sel in [
        "li[data-occludable-job-id]",
        "div.job-card-container",
        "ul.jobs-search__results-list li",
        "div.scaffold-layout__list li",
        "a[href*='/jobs/view/']",
        "text=/no matching jobs/i",
        "text=/sign in/i",
    ]:
        try:
            print(f"{sel:45s} -> {page.locator(sel).count()}")
        except Exception as e:
            print(f"{sel:45s} -> ERR {e}")
    page.screenshot(path=SHOT, full_page=False)
    print("screenshot:", SHOT)
