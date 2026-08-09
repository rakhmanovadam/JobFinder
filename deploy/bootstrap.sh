#!/usr/bin/env bash
# Run ON the EC2 instance (Ubuntu 24.04, arm64 or x86_64) as the `ubuntu` user:
#   bash ~/JobFinder/deploy/bootstrap.sh
# Idempotent — safe to re-run after a code pull.
set -euo pipefail

REPO_DIR="$HOME/JobFinder"
REPO_URL="https://github.com/rakhmanovadam/JobFinder.git"

echo "==> timezone (sweep slots are wall-clock; default UTC would shift them)"
sudo timedatectl set-timezone America/New_York

echo "==> system packages"
sudo apt-get update -qq
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
  git curl \
  python3-venv python3-pip \
  nodejs npm \
  libreoffice-writer fonts-liberation \
  xvfb x11vnc

echo "==> repo"
if [ ! -d "$REPO_DIR/.git" ]; then
  git clone "$REPO_URL" "$REPO_DIR"
fi
cd "$REPO_DIR"

echo "==> python env"
[ -d .venv ] || python3 -m venv .venv
./.venv/bin/pip install -q --upgrade pip
./.venv/bin/pip install -q -r requirements.txt

echo "==> browsers"
./.venv/bin/python -m camoufox fetch
./.venv/bin/python -m playwright install --with-deps chromium

echo "==> node deps for resume rendering"
npm install --silent --no-fund --no-audit   # package.json is at the repo root

echo "==> preflight"
missing=0
for f in .env tailor/resume_data.json; do
  if [ ! -f "$f" ]; then
    echo "   MISSING $f  (gitignored — scp it from the Mac)"
    missing=1
  fi
done
command -v soffice >/dev/null || { echo "   soffice not on PATH"; missing=1; }
command -v node    >/dev/null || { echo "   node not on PATH"; missing=1; }
if [ "$missing" -ne 0 ]; then
  echo "!! fix the above before installing services"
  exit 1
fi

echo "==> systemd units"
sudo cp deploy/systemd/jobfinder-*.service deploy/systemd/jobfinder-*.timer \
        deploy/systemd/xvfb.service deploy/systemd/x11vnc.service /etc/systemd/system/
sudo systemctl daemon-reload
# LinkedIn must never be browsed headless, so the display comes up first.
sudo systemctl enable --now xvfb.service
sudo systemctl enable --now x11vnc.service
sudo systemctl enable --now jobfinder-bot.service
sudo systemctl enable --now jobfinder-sweep.timer
sudo systemctl enable --now jobfinder-draft.timer
sudo systemctl enable --now jobfinder-digest.timer

echo
echo "done. check with:"
echo "  systemctl list-timers 'jobfinder*'"
echo "  systemctl status jobfinder-bot"
echo "  journalctl -u jobfinder-sweep -f"
