#!/bin/bash
# Install or update lakeFinder on a Mac without Docker: caddy and the api service under launchd.
# Run it on the target Mac from the checkout (deploy/push.sh does that over ssh). Safe to rerun.
#
#   deploy/install.sh                 LaunchAgents: runs while this user is logged in (needs auto-login when headless)
#   deploy/install.sh --daemon        LaunchDaemons as this user: runs from boot, no login needed (asks for sudo)
#   deploy/install.sh --serve         also put `tailscale serve` in front of caddy (HTTPS on the tailnet)
#   deploy/install.sh --dry-run DIR   write the plists into DIR and print what would happen; changes nothing
#   deploy/install.sh --uninstall     stop and remove the jobs (add --daemon if that is how they were installed)
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
MODE=agent
SERVE=0
DRY=""
UNINSTALL=0
PORT="${LAKEFINDER_PORT:-8080}"
API_PORT="${LAKEFINDER_API_PORT:-8000}"
BIND="${LAKEFINDER_BIND:-127.0.0.1}"

while [ $# -gt 0 ]; do
  case "$1" in
    --daemon) MODE=daemon ;;
    --serve) SERVE=1 ;;
    --dry-run) DRY="${2:?--dry-run needs a directory}"; shift ;;
    --uninstall) UNINSTALL=1 ;;
    -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done

# ssh and launchd both start with a bare PATH; brew lives in one of two places.
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
say() { printf '%s\n' "$*"; }
die() { printf 'install: %s\n' "$*" >&2; exit 1; }
need() { command -v "$1" >/dev/null 2>&1 || die "$1 not found. Install it with: $2"; }

ME="$(id -un)"
LOGS="$HOME/Library/Logs"
LABELS=(com.lakefinder.api com.lakefinder.web)
if [ "$MODE" = daemon ]; then
  PLIST_DIR=/Library/LaunchDaemons; DOMAIN=system; SUDO=sudo
else
  PLIST_DIR="$HOME/Library/LaunchAgents"; DOMAIN="gui/$(id -u)"; SUDO=""
fi

if [ "$UNINSTALL" = 1 ]; then
  for label in "${LABELS[@]}"; do
    $SUDO launchctl bootout "$DOMAIN/$label" 2>/dev/null || true
    $SUDO rm -f "$PLIST_DIR/$label.plist"
    say "removed $label"
  done
  exit 0
fi

need uv "brew install uv"
need caddy "brew install caddy"
UV="$(command -v uv)"
CADDY="$(command -v caddy)"

[ -f "$REPO/data/out/pack.json" ] || die "no data pack at $REPO/data/out (run deploy/push.sh from the dev Mac, or the pipeline here)"
[ -f "$REPO/web/dist/index.html" ] || die "no web build at $REPO/web/dist (run deploy/push.sh from the dev Mac, or 'cd web && npm ci && npm run build' here)"

# In daemon mode launchd starts as root and drops to this user; an agent already is the user.
user_key() { [ "$MODE" = daemon ] && printf '  <key>UserName</key>          <string>%s</string>\n' "$ME" || true; }

# The api runs under caffeinate so the Mac does not idle-sleep through the 18:00/20:00/22:00 outlook runs.
api_plist() {
  cat <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>             <string>com.lakefinder.api</string>
$(user_key)
  <key>WorkingDirectory</key>  <string>$REPO/api</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/caffeinate</string>
    <string>-is</string>
    <string>$UV</string>
    <string>run</string>
    <string>--frozen</string>
    <string>--no-dev</string>
    <string>seaplane-api</string>
    <string>--host</string>
    <string>127.0.0.1</string>
    <string>--port</string>
    <string>$API_PORT</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>HOME</key>                  <string>$HOME</string>
    <key>PATH</key>                  <string>$(dirname "$UV"):/usr/bin:/bin</string>
    <key>SEAPLANE_DATA_OUT</key>     <string>$REPO/data/out</string>
    <key>SEAPLANE_DATA_MANUAL</key>  <string>$REPO/data/manual</string>
  </dict>
  <key>RunAtLoad</key>         <true/>
  <key>KeepAlive</key>         <true/>
  <key>ThrottleInterval</key>  <integer>30</integer>
  <key>StandardOutPath</key>   <string>$LOGS/lakefinder-api.log</string>
  <key>StandardErrorPath</key> <string>$LOGS/lakefinder-api.log</string>
</dict>
</plist>
EOF
}

web_plist() {
  cat <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>             <string>com.lakefinder.web</string>
$(user_key)
  <key>WorkingDirectory</key>  <string>$REPO</string>
  <key>ProgramArguments</key>
  <array>
    <string>$CADDY</string>
    <string>run</string>
    <string>--config</string>
    <string>$REPO/Caddyfile</string>
    <string>--adapter</string>
    <string>caddyfile</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>HOME</key>          <string>$HOME</string>
    <key>BIND_ADDR</key>     <string>$BIND</string>
    <key>LISTEN_PORT</key>   <string>$PORT</string>
    <key>API_UPSTREAM</key>  <string>127.0.0.1:$API_PORT</string>
    <key>DATA_ROOT</key>     <string>$REPO/data/out</string>
    <key>WEB_ROOT</key>      <string>$REPO/web/dist</string>
  </dict>
  <key>RunAtLoad</key>         <true/>
  <key>KeepAlive</key>         <true/>
  <key>ThrottleInterval</key>  <integer>30</integer>
  <key>StandardOutPath</key>   <string>$LOGS/lakefinder-web.log</string>
  <key>StandardErrorPath</key> <string>$LOGS/lakefinder-web.log</string>
</dict>
</plist>
EOF
}

if [ -n "$DRY" ]; then
  mkdir -p "$DRY"
  api_plist > "$DRY/com.lakefinder.api.plist"
  web_plist > "$DRY/com.lakefinder.web.plist"
  plutil -lint "$DRY"/com.lakefinder.*.plist
  say "dry run: would install into $PLIST_DIR and bootstrap into launchd domain '$DOMAIN'"
  exit 0
fi

mkdir -p "$LOGS"
say "syncing the api environment"
(cd "$REPO/api" && "$UV" sync --frozen --no-dev)

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
api_plist > "$STAGE/com.lakefinder.api.plist"
web_plist > "$STAGE/com.lakefinder.web.plist"
plutil -lint "$STAGE"/com.lakefinder.*.plist >/dev/null

[ "$MODE" = daemon ] || mkdir -p "$PLIST_DIR"
for label in "${LABELS[@]}"; do
  $SUDO launchctl bootout "$DOMAIN/$label" 2>/dev/null || true
  # bootout returns before the job is gone; bootstrapping too early fails with "Input/output error".
  for _ in $(seq 1 20); do
    $SUDO launchctl print "$DOMAIN/$label" >/dev/null 2>&1 || break
    sleep 0.5
  done
  $SUDO install -m 644 "$STAGE/$label.plist" "$PLIST_DIR/$label.plist"
  if [ "$MODE" = daemon ]; then sudo chown root:wheel "$PLIST_DIR/$label.plist"; fi
  $SUDO launchctl bootstrap "$DOMAIN" "$PLIST_DIR/$label.plist" \
    || die "launchctl bootstrap $DOMAIN failed for $label. Over ssh with nobody logged in at the console there is no gui domain: rerun with --daemon."
  say "loaded $label"
done

# The first api start builds nothing (uv sync ran above) but still takes a few seconds.
ok=0
for _ in $(seq 1 30); do
  if curl -fsS "http://127.0.0.1:$PORT/api/health" >/dev/null 2>&1; then ok=1; break; fi
  sleep 1
done
[ "$ok" = 1 ] || die "no answer from http://127.0.0.1:$PORT/api/health after 30 s; see $LOGS/lakefinder-api.log and lakefinder-web.log"
curl -fsS "http://127.0.0.1:$PORT/data/pack.json" >/dev/null || die "caddy is up but /data/pack.json is not served"
curl -fsS "http://127.0.0.1:$PORT/" >/dev/null || die "caddy is up but the app shell is not served"
say "up: $(curl -fsS "http://127.0.0.1:$PORT/api/health")"

if [ "$SERVE" = 1 ]; then
  need tailscale "install Tailscale, or symlink its CLI into /usr/local/bin"
  tailscale serve --bg "$PORT"
  tailscale serve status
else
  say "next: tailscale serve --bg $PORT   (HTTPS on the tailnet; the PWA needs it to install)"
fi

# caffeinate covers idle sleep on mains power. A closed lid still sleeps the Mac unless disablesleep is set.
if ! pmset -g | grep -Eq 'disablesleep[[:space:]]+1|SleepDisabled[[:space:]]+1'; then
  say "note: lid-closed sleep is still on. For a closed-lid Mac: sudo pmset -a disablesleep 1"
fi
