"""One stable browser identity for the LinkedIn profile.

Camoufox invents a fresh fingerprint on every launch — new screen size, new
hardware, sometimes a new language. Measured on the box: two launches minutes
apart claimed a 2560x1440 English machine and then a 1680x1050 Spanish one.
LinkedIn watched the same li_at cookie hop between those "devices" and revoked
the session within half an hour of a manual login. Same cookie + changing
device is the signature of a stolen cookie, and LinkedIn treats it as one.

So the identity is generated once, saved beside the profile, and passed back
into every launch. Camoufox's config parameter overrides its own randomisation
property-for-property (verified: a full round-trip pins all 52), which makes
every launch the same machine — matching how a person actually uses one
laptop.

The file must outlive everything except a deliberate re-identity. It is NOT
part of the profile directory, so profile heals/restores do not touch it.
Delete it only alongside a fresh login (e.g. after a camoufox upgrade changes
the real Firefox version underneath a pinned userAgent).
"""
import json
import os

from config import PROFILE_DIR, PROXY

IDENTITY_PATH = PROFILE_DIR + ".identity.json"


def _generate() -> dict:
    """Ask camoufox for one full config the same way a launch would, and keep
    it. locale is forced to en-US rather than letting the dice pick a language
    the account has never used."""
    from camoufox.utils import launch_options

    opts = launch_options(
        headless=False, humanize=True, geoip=True, proxy=PROXY, locale="en-US",
    )
    cfg, _ = json.JSONDecoder().raw_decode(opts["env"]["CAMOU_CONFIG_1"])
    return cfg


def stable_config() -> dict:
    if os.path.exists(IDENTITY_PATH):
        with open(IDENTITY_PATH) as fh:
            return json.load(fh)
    cfg = _generate()
    tmp = IDENTITY_PATH + ".new"
    with open(tmp, "w") as fh:
        json.dump(cfg, fh, indent=1)
    os.rename(tmp, IDENTITY_PATH)
    print(f"generated a new browser identity -> {IDENTITY_PATH}")
    return cfg


def camoufox_kwargs(user_data_dir: str, headless: bool = False) -> dict:
    """Launch arguments every LinkedIn browser must share. geoip stays on —
    the proxy exit is static, so the derived geolocation is stable and always
    matches the IP LinkedIn sees. i_know_what_im_doing silences camoufox's
    warning about supplying device properties manually; pinning them is the
    entire point here."""
    return dict(
        headless=headless,
        humanize=True,
        geoip=True,
        proxy=PROXY,
        persistent_context=True,
        user_data_dir=user_data_dir,
        config=stable_config(),
        i_know_what_im_doing=True,
    )


if __name__ == "__main__":
    cfg = stable_config()
    for k in ("navigator.userAgent", "screen.width", "screen.height",
              "locale:language", "timezone"):
        print(f"  {k} = {cfg.get(k)}")
