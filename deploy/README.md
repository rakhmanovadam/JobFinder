# Deploy

Two targets. macOS uses launchd, Linux/EC2 uses systemd. Same three jobs either way:

| job | schedule | what |
|-----|----------|------|
| bot | always on, restarts | Telegram approve/skip loop |
| sweep | 8x/day, ~3h apart | LinkedIn guest discovery pass (~75-110 min) |
| draft | every 30 min | matched jobs -> tailor -> render -> Telegram card |

## macOS (current)

`*.plist` -> `~/Library/LaunchAgents/`. Sweep is wrapped in `caffeinate -i` so the
Mac stays awake for the pass. Requires the Mac to be on.

## AWS EC2

1. On the Mac: `bash deploy/provision-aws.sh` (needs `aws configure` done).
   Launches Ubuntu 24.04 arm64 `t4g.medium`, 30 GB gp3, SSH locked to your IP,
   Elastic IP attached.
2. SSH in, then `bash ~/JobFinder/deploy/bootstrap.sh` — installs deps, builds
   the venv, fetches Camoufox + Chromium, installs and starts the systemd units.
3. `scp` the two gitignored files across: `.env` and `tailor/resume_data.json`.
   **Rotate every key first** — the originals were pasted in chat.

No `caffeinate` equivalent needed; the box never sleeps. Host timezone is set to
`America/New_York` by bootstrap because the sweep slots are wall-clock times.

### Proxy

A bare AWS IP gets challenged by LinkedIn even for logged-out browsing. Add a
**static residential ISP proxy** (dedicated IP, unmetered, ~$5-10/mo) — not a
metered rotating one, which runs $40-80/mo at ~11 GB of page loads. Then in `.env`:

```
PROXY_SERVER=http://host:port
PROXY_USERNAME=...
PROXY_PASSWORD=...
```

`config.PROXY` picks these up and every Camoufox launch inherits them. Leave
`PROXY_SERVER` unset on the Mac to keep using the home IP. Allowlist the
instance's Elastic IP with the proxy provider.

### Checks

```bash
systemctl list-timers 'jobfinder*'
systemctl status jobfinder-bot
journalctl -u jobfinder-sweep -f
```

### Notes

- Discovery is guest mode (`headless=True`), so no display is needed. `xvfb` is
  installed only for `linkedin/session.py`, which is headful and is the one path
  that would need a virtual display if you ever re-do an authenticated login on
  the server.
- Run the bot in exactly one place. Two pollers on one token fight over updates —
  unload the macOS `com.jobfinder.bot` plist before starting the EC2 one.
