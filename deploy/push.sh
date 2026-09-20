#!/bin/bash
# Build the client here and copy the checkout, the web build and the data pack to the serving Mac, then run
# deploy/install.sh there. The dev Mac has the pipeline toolchain (GDAL, tippecanoe, pmtiles); the server needs
# only uv and caddy.
#
#   deploy/push.sh <ssh-host> [remote-dir] [-- install.sh options]
#   deploy/push.sh labmac                       # remote-dir defaults to lakeFinder (under the remote home)
#   deploy/push.sh labmac lakeFinder -- --daemon --serve
#   SKIP_BUILD=1 deploy/push.sh labmac          # reuse web/dist as it is
#   NO_INSTALL=1 deploy/push.sh labmac          # copy only (a data refresh needs no restart)
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
HOST="${1:?usage: deploy/push.sh <ssh-host> [remote-dir] [-- install.sh options]}"
shift
DEST=lakeFinder
if [ $# -gt 0 ] && [ "$1" != "--" ]; then DEST="$1"; shift; fi
if [ $# -gt 0 ] && [ "$1" = "--" ]; then shift; fi

[ -f "$REPO/data/out/pack.json" ] || { echo "push: no data pack in data/out; run the pipeline first" >&2; exit 1; }

if [ "${SKIP_BUILD:-0}" != 1 ]; then
  (cd "$REPO/web" && npm ci --no-audit --no-fund && npm run build)
fi
[ -f "$REPO/web/dist/index.html" ] || { echo "push: web/dist is missing" >&2; exit 1; }

# Never sent, never deleted on the far side: what the server writes for itself (its settings, its briefing, its
# venvs) and what only the pipeline needs. --delete only touches files that are not excluded.
rsync -az --delete --human-readable --stats \
  --exclude '.git/' --exclude '.DS_Store' --exclude '__pycache__/' --exclude '*.pyc' \
  --exclude '.venv/' --exclude 'node_modules/' --exclude '.pytest_cache/' --exclude '.ruff_cache/' \
  --exclude 'web/dev-dist/' --exclude '*.log' --exclude '.env' --exclude '.env.*' \
  --exclude 'data/raw/' --exclude 'data/work/' --exclude 'data/cache/' \
  --exclude 'data/manual/settings.json' --exclude 'data/out/briefing.json' --exclude 'deploy/local.env' \
  "$REPO/" "$HOST:$DEST/"

# First deploy only: start the server from this Mac's briefing settings rather than the KPTK defaults. After that
# the server's file is its own.
if [ -f "$REPO/data/manual/settings.json" ]; then
  rsync -az --ignore-existing "$REPO/data/manual/settings.json" "$HOST:$DEST/data/manual/settings.json"
fi

if [ "${NO_INSTALL:-0}" = 1 ]; then
  echo "copied; install skipped"
  exit 0
fi
# -t so a --daemon install can ask for the sudo password.
ssh -t "$HOST" "cd '$DEST' && deploy/install.sh $*"
