#!/usr/bin/env bash
# Builds the PixelCrew app bundle; the launch modes verify it and reopen it.
# Only --install installs and opens the /Applications copy; the other launch modes use dist/.
#
# Environment:
#   UNIVERSAL=1        build for Apple silicon and Intel (release builds)
#   SIGN_IDENTITY=...  codesign identity, e.g. "Developer ID Application: Name (TEAMID)";
#                      unset = ad-hoc signature for local use
set -euo pipefail

# Validate every argument before any side effect.
MODE="${1:-run}"
if [[ $# -gt 1 ]]; then
  echo "Usage: $0 [run|--build|--install|--verify|--logs|--debug]" >&2
  exit 2
fi
case "$MODE" in
  run|--build|build|--install|install|--verify|verify|--logs|logs|--debug|debug) ;;
  *) echo "Unknown option: $MODE" >&2; exit 2 ;;
esac
APP_NAME="PixelCrew"
BUNDLE_ID="app.pixelcrew.PixelCrew"
MIN_SYSTEM_VERSION="14.0"

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MAC_DIR="$ROOT_DIR/mac"
DIST_DIR="$ROOT_DIR/dist"
BUILD_DIR="$ROOT_DIR/.build-mac"
APP_BUNDLE="$DIST_DIR/$APP_NAME.app"
APP_CONTENTS="$APP_BUNDLE/Contents"
APP_RES="$APP_CONTENTS/Resources"
DATA_DIR="$HOME/Library/Application Support/PixelCrew"
LOG_FILE="$HOME/Library/Logs/PixelCrew/server.log"
VERSION="$(tr -d '[:space:]' < "$ROOT_DIR/VERSION")"
BUILD_NUMBER="$(date +%Y%m%d%H%M)"
UNIVERSAL="${UNIVERSAL:-0}"
SIGN_IDENTITY="${SIGN_IDENTITY:-}"

INSTALLED_BUNDLE="/Applications/$APP_NAME.app"
RUN_BUNDLE="$APP_BUNDLE"
if [[ "$MODE" == --install || "$MODE" == install ]]; then
  RUN_BUNDLE="$INSTALLED_BUNDLE"
fi

check_bundle() {
  (cd "$ROOT_DIR" && python3 "$ROOT_DIR/script/check_bundle.py" --bundle "$1")
}

# Walk up the process tree so we never quit the app this agent session runs inside.
inside_installed_app() {
  local pid=$$ command parent
  while [[ "$pid" -gt 1 ]]; do
    command="$(ps -p "$pid" -o command=)" || return 2
    case "$command" in
      "$INSTALLED_BUNDLE/Contents/MacOS/$APP_NAME"*)
        echo "This session runs inside the installed PixelCrew: PID $pid, bundle $INSTALLED_BUNDLE"
        return 0 ;;
    esac
    parent="$(ps -p "$pid" -o ppid= | tr -d ' ')" || return 2
    [[ "$parent" =~ ^[0-9]+$ && "$parent" != "$pid" ]] || break
    pid="$parent"
  done
  return 1
}

report_running() {
  ps -axo pid=,comm= | awk '/\/PixelCrew.app\/Contents\/MacOS\/PixelCrew$/ {pid=$1; $1=""; sub(/^ +/, ""); print "PID " pid ", executable " $0}'
}

quit_running() {
  local pid command bundle script_path
  while read -r pid command; do
    case "$command" in
      */PixelCrew.app/Contents/MacOS/PixelCrew)
        # Target the bundle path so copies in other places are left alone.
        bundle="${command%/Contents/MacOS/PixelCrew}"
        script_path="${bundle//\\/\\\\}"
        script_path="${script_path//\"/\\\"}"
        /usr/bin/osascript -e "tell application \"$script_path\" to quit" >/dev/null 2>&1 || true ;;
    esac
  done < <(ps -axo pid=,comm=)
  for ((i=0; i<40; i++)); do
    [[ -z "$(report_running)" ]] && return 0
    sleep 0.25
  done
  echo "PixelCrew did not quit politely; the installed bundle was left unchanged." >&2
  report_running >&2
  return 1
}

install_bundle() {
  local stage backup
  stage="$(mktemp -d /Applications/.PixelCrew-install.XXXXXX)"
  backup="$stage/previous.app"
  if ! ditto "$APP_BUNDLE" "$stage/PixelCrew.app" || ! check_bundle "$stage/PixelCrew.app"; then
    rm -rf "$stage"
    return 1
  fi
  if [[ -e "$INSTALLED_BUNDLE" ]]; then
    if ! mv "$INSTALLED_BUNDLE" "$backup"; then rm -rf "$stage"; return 1; fi
  fi
  if ! mv "$stage/PixelCrew.app" "$INSTALLED_BUNDLE" || ! check_bundle "$INSTALLED_BUNDLE"; then
    rm -rf "$INSTALLED_BUNDLE"
    [[ ! -e "$backup" ]] || mv "$backup" "$INSTALLED_BUNDLE"
    rm -rf "$stage"
    echo "Could not verify the installed copy; the previous bundle was restored." >&2
    return 1
  fi
  rm -rf "$stage"
  echo "Installed and verified: $INSTALLED_BUNDLE"
}

build() {
  # bash 3.2 (macOS) treats an empty array as unset under `set -u`, hence the ${a[@]+...} form.
  local arch=()
  if [[ "$UNIVERSAL" == 1 ]]; then
    arch=(--arch arm64 --arch x86_64)
    echo "==> Compiling Swift (universal)"
  else
    echo "==> Compiling Swift"
  fi
  swift build --package-path "$MAC_DIR" --scratch-path "$BUILD_DIR" -c release ${arch[@]+"${arch[@]}"} --product "$APP_NAME"
  local bin
  bin="$(swift build --package-path "$MAC_DIR" --scratch-path "$BUILD_DIR" -c release ${arch[@]+"${arch[@]}"} --show-bin-path)/$APP_NAME"

  echo "==> Assembling $APP_BUNDLE"
  rm -rf "$APP_BUNDLE"
  mkdir -p "$APP_CONTENTS/MacOS" "$APP_RES/app"
  cp "$bin" "$APP_CONTENTS/MacOS/$APP_NAME"
  chmod +x "$APP_CONTENTS/MacOS/$APP_NAME"

  # The Python server and the web UI ship inside the bundle.
  rsync -a --delete --exclude '__pycache__' --exclude '*.pyc' \
    "$ROOT_DIR/server" "$ROOT_DIR/web" "$ROOT_DIR/pixelcrew.py" "$ROOT_DIR/LICENSE" "$APP_RES/app/"

  local icon_src="$MAC_DIR/tools/make_icon.swift" icns="$BUILD_DIR/AppIcon.icns"
  if [[ ! -f "$icns" || "$icon_src" -nt "$icns" ]]; then
    echo "==> Drawing the icon"
    local set="$BUILD_DIR/AppIcon.iconset"
    rm -rf "$set" && mkdir -p "$set"
    swift "$icon_src" "$BUILD_DIR/icon-1024.png"
    for s in 16 32 128 256 512; do
      sips -z $s $s "$BUILD_DIR/icon-1024.png" --out "$set/icon_${s}x${s}.png" >/dev/null
      sips -z $((s * 2)) $((s * 2)) "$BUILD_DIR/icon-1024.png" --out "$set/icon_${s}x${s}@2x.png" >/dev/null
    done
    iconutil -c icns "$set" -o "$icns"
  fi
  cp "$icns" "$APP_RES/AppIcon.icns"

  cat >"$APP_CONTENTS/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleExecutable</key><string>$APP_NAME</string>
  <key>CFBundleIdentifier</key><string>$BUNDLE_ID</string>
  <key>CFBundleName</key><string>$APP_NAME</string>
  <key>CFBundleDisplayName</key><string>$APP_NAME</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>$VERSION</string>
  <key>CFBundleVersion</key><string>$BUILD_NUMBER</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>CFBundleDevelopmentRegion</key><string>en</string>
  <key>CFBundleLocalizations</key><array><string>en</string><string>tr</string></array>
  <key>LSMinimumSystemVersion</key><string>$MIN_SYSTEM_VERSION</string>
  <key>LSApplicationCategoryType</key><string>public.app-category.developer-tools</string>
  <key>NSPrincipalClass</key><string>NSApplication</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>NSHumanReadableCopyright</key><string>© 2026 PixelCrew contributors. Apache License 2.0.</string>
  <key>NSAppTransportSecurity</key><dict><key>NSAllowsLocalNetworking</key><true/></dict>
  <key>NSDesktopFolderUsageDescription</key><string>The project folder the agents work in may be on your Desktop.</string>
  <key>NSDocumentsFolderUsageDescription</key><string>The project folder the agents work in may be in Documents.</string>
  <key>NSDownloadsFolderUsageDescription</key><string>The project folder the agents work in may be in Downloads.</string>
</dict>
</plist>
PLIST
  mkdir -p "$APP_RES/en.lproj" "$APP_RES/tr.lproj"
  cat >"$APP_RES/tr.lproj/InfoPlist.strings" <<'STRINGS'
"NSDesktopFolderUsageDescription" = "Ajanların çalıştığı proje klasörü Masaüstünde olabilir.";
"NSDocumentsFolderUsageDescription" = "Ajanların çalıştığı proje klasörü Belgeler'de olabilir.";
"NSDownloadsFolderUsageDescription" = "Ajanların çalıştığı proje klasörü İndirilenler'de olabilir.";
STRINGS

  if [[ -n "$SIGN_IDENTITY" ]]; then
    echo "==> Signing with $SIGN_IDENTITY (hardened runtime)"
    codesign --force --options runtime --timestamp --sign "$SIGN_IDENTITY" "$APP_BUNDLE"
    codesign --verify --strict --verbose=2 "$APP_BUNDLE"
  else
    codesign --force --sign - "$APP_BUNDLE" >/dev/null
  fi

}

verify_server() {
  python3 - "$ROOT_DIR/web/office.js" "$1" <<'PYTHON'
import hashlib
from pathlib import Path
import shlex
import subprocess
import sys
import time
import urllib.request

source, bundle = sys.argv[1:]
expected = Path(source).read_bytes()
script = bundle + "/Contents/Resources/app/pixelcrew.py"
app = bundle + "/Contents/MacOS/PixelCrew"
last = "No Python process found for the chosen bundle: " + bundle
for _ in range(60):
    rows = subprocess.check_output(["ps", "-axo", "pid=,ppid=,command="], text=True).splitlines()
    processes = {}
    for row in rows:
        parts = row.strip().split(None, 2)
        if len(parts) == 3:
            processes[parts[0]] = (parts[1], parts[2])
    for pid, (parent, command) in processes.items():
        try:
            args = shlex.split(command)
            if script not in args or processes.get(parent, (None, None))[1] != app:
                continue
            port = args[args.index("--port") + 1]
            url = "http://127.0.0.1:" + str(int(port)) + "/office.js"
            with urllib.request.urlopen(url, timeout=1) as response:
                body = response.read()
            if body != expected:
                last = "The server's /office.js does not match the source: " + url
                continue
            print("Python PID " + pid + ": " + script)
            print("Server verified: " + url)
            print("office.js SHA-256: " + hashlib.sha256(body).hexdigest())
            print("Check the window itself separately.")
            sys.exit(0)
        except (ValueError, OSError) as error:
            last = str(error)
    time.sleep(0.5)
print("Could not verify the launch: " + last, file=sys.stderr)
sys.exit(1)
PYTHON
}

build
if [[ "$MODE" == --build || "$MODE" == build ]]; then
  echo "Ready: $APP_BUNDLE"
  exit 0
fi
check_bundle "$APP_BUNDLE"

SELF_SESSION=0
if inside_installed_app; then
  SELF_SESSION=1
else
  ancestry_status=$?
  if [[ "$ancestry_status" != 1 ]]; then
    echo "Could not read the parent processes; a safe relaunch cannot be verified." >&2
    exit 1
  fi
  quit_running
fi
if [[ "$MODE" == --install || "$MODE" == install ]]; then
  install_bundle
fi

if [[ "$SELF_SESSION" == 1 ]]; then
  echo "Bundle verified: $RUN_BUNDLE; not relaunched so this session keeps running." >&2
  echo "Next step: when this task is done, quit PixelCrew from its menu and run in Terminal:" >&2
  if [[ "$MODE" == --install || "$MODE" == install ]]; then
    printf '  %q --install\n' "$ROOT_DIR/script/build_and_run.sh" >&2
  else
    printf '  %q --verify\n' "$ROOT_DIR/script/build_and_run.sh" >&2
  fi
  echo "The window and the relaunched server are not verified yet." >&2
  exit 1
fi

# History from a checkout moves over only in the launch flow; --build writes nothing outside dist/.
if [[ ! -d "$DATA_DIR" && -d "$ROOT_DIR/data" ]]; then
  mkdir -p "$DATA_DIR"
  cp -R "$ROOT_DIR/data/." "$DATA_DIR/"
fi
case "$MODE" in
  --debug|debug) lldb -- "$RUN_BUNDLE/Contents/MacOS/$APP_NAME" ;;
  *)
    /usr/bin/open -n "$RUN_BUNDLE"
    verify_server "$RUN_BUNDLE"
    if [[ "$MODE" == --logs || "$MODE" == logs ]]; then
      tail -n 20 -f "$LOG_FILE"
    fi ;;
esac
