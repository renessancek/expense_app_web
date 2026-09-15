#!/usr/bin/env bash
# Pull main and rebuild expense-web only when the repo actually changed.
# Intended for Raspberry Pi / LAN host via cron or systemd timer.
set -euo pipefail

REPO_DIR="${EXPENSE_APP_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
BRANCH="${EXPENSE_GIT_BRANCH:-main}"
LOG_TAG="expense-auto-update"

cd "$REPO_DIR"

if [[ ! -d .git ]]; then
  echo "$LOG_TAG: not a git repo: $REPO_DIR" >&2
  exit 1
fi

if [[ ! -f docker-compose.yml ]]; then
  echo "$LOG_TAG: docker-compose.yml missing in $REPO_DIR" >&2
  exit 1
fi

echo "$LOG_TAG: checking $REPO_DIR ($BRANCH)"
git fetch --quiet origin "$BRANCH"

LOCAL=$(git rev-parse HEAD)
REMOTE=$(git rev-parse "origin/$BRANCH")

if [[ "$LOCAL" == "$REMOTE" ]]; then
  echo "$LOG_TAG: already up to date ($LOCAL)"
  exit 0
fi

echo "$LOG_TAG: updating $LOCAL -> $REMOTE"
git pull --ff-only origin "$BRANCH"

echo "$LOG_TAG: rebuilding containers"
docker compose up -d --build

echo "$LOG_TAG: done"
