#!/usr/bin/env bash
# Builds a release DMG: universal app, Developer ID signature with the hardened runtime,
# notarized and stapled (app first, then the DMG), plus a SHA-256 checksum.
#
#   script/release.sh                 sign + DMG, skip notarization
#   NOTARY_PROFILE=pixelcrew script/release.sh
#                                     ...and notarize with a profile stored once via
#                                     xcrun notarytool store-credentials pixelcrew
#
# SIGN_IDENTITY defaults to the first "Developer ID Application" identity in the keychain.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_NAME="PixelCrew"
VERSION="$(tr -d '[:space:]' < "$ROOT_DIR/VERSION")"
DIST_DIR="$ROOT_DIR/dist"
APP_BUNDLE="$DIST_DIR/$APP_NAME.app"
DMG="$DIST_DIR/$APP_NAME-$VERSION.dmg"
NOTARY_PROFILE="${NOTARY_PROFILE:-}"

if [[ -z "${SIGN_IDENTITY:-}" ]]; then
  SIGN_IDENTITY="$(security find-identity -v -p codesigning | sed -n 's/.*"\(Developer ID Application: [^"]*\)".*/\1/p' | head -1)"
fi
if [[ -z "$SIGN_IDENTITY" ]]; then
  echo "No 'Developer ID Application' identity found. Set SIGN_IDENTITY or install the certificate." >&2
  exit 1
fi
export SIGN_IDENTITY
export UNIVERSAL=1

notarize() {
  local file="$1"
  echo "==> Notarizing $(basename "$file") (this can take a few minutes)"
  xcrun notarytool submit "$file" --keychain-profile "$NOTARY_PROFILE" --wait
}

"$ROOT_DIR/script/build_and_run.sh" --build

echo "==> Checking the app"
lipo -info "$APP_BUNDLE/Contents/MacOS/$APP_NAME"
codesign --verify --deep --strict --verbose=2 "$APP_BUNDLE"
SIGNATURE="$(codesign -dvv "$APP_BUNDLE" 2>&1)"
if ! grep -q 'Authority=Developer ID Application' <<<"$SIGNATURE" || ! grep -q 'flags=.*(runtime)' <<<"$SIGNATURE"; then
  echo "The app is not signed with Developer ID and the hardened runtime." >&2
  exit 1
fi
(cd "$ROOT_DIR" && python3 script/check_bundle.py --bundle "$APP_BUNDLE" >/dev/null)

if [[ -n "$NOTARY_PROFILE" ]]; then
  ZIP="$DIST_DIR/$APP_NAME-notarize.zip"
  rm -f "$ZIP"
  ditto -c -k --keepParent "$APP_BUNDLE" "$ZIP"
  notarize "$ZIP"
  rm -f "$ZIP"
  xcrun stapler staple "$APP_BUNDLE"
fi

echo "==> Building $DMG"
STAGE="$(mktemp -d "$DIST_DIR/.dmg.XXXXXX")"
trap 'rm -rf "$STAGE"' EXIT
ditto "$APP_BUNDLE" "$STAGE/$APP_NAME.app"
ln -s /Applications "$STAGE/Applications"
rm -f "$DMG"
hdiutil create -volname "$APP_NAME $VERSION" -srcfolder "$STAGE" -fs HFS+ -format UDZO -imagekey zlib-level=9 -ov "$DMG" >/dev/null
codesign --force --timestamp --sign "$SIGN_IDENTITY" "$DMG"

if [[ -n "$NOTARY_PROFILE" ]]; then
  notarize "$DMG"
  xcrun stapler staple "$DMG"
  echo "==> Gatekeeper"
  spctl -a -vv -t install "$DMG"
  spctl -a -vv "$APP_BUNDLE"
else
  echo "Notarization skipped (NOTARY_PROFILE not set): Gatekeeper will warn on other Macs." >&2
fi

(cd "$DIST_DIR" && shasum -a 256 "$(basename "$DMG")" > "$(basename "$DMG").sha256")
echo "==> Done"
echo "  $DMG"
echo "  $(cat "$DMG.sha256")"
