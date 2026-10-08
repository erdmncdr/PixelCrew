#!/usr/bin/env bash
# Builds a release DMG: universal app, Developer ID signature with the hardened runtime,
# notarized and stapled (app first, then the DMG), plus a SHA-256 checksum. With PUBLISH=1
# it also tags the commit, publishes the GitHub release with the DMG and checks the download.
#
#   script/release.sh                 sign + DMG, skip notarization
#   NOTARY_PROFILE=pixelcrew script/release.sh
#                                     ...and notarize with a profile stored once via
#                                     xcrun notarytool store-credentials pixelcrew
#   NOTARY_PROFILE=pixelcrew PUBLISH=1 script/release.sh
#                                     ...and publish: needs a clean, committed tree, the GitHub
#                                     CLI signed in once (gh auth login) and release-notes/<version>.md
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
PUBLISH="${PUBLISH:-0}"
NOTES="$ROOT_DIR/release-notes/$VERSION.md"
TAG="v$VERSION"

if [[ "$PUBLISH" == 1 ]]; then
  # Refuse early, before a long build, when publishing could not finish or would ship the wrong thing.
  [[ -n "$NOTARY_PROFILE" ]] || { echo "PUBLISH=1 needs NOTARY_PROFILE: releases are always notarized." >&2; exit 1; }
  command -v gh >/dev/null || { echo "PUBLISH=1 needs the GitHub CLI (https://cli.github.com), then: gh auth login" >&2; exit 1; }
  gh auth status >/dev/null 2>&1 || { echo "The GitHub CLI is not signed in. Run: gh auth login" >&2; exit 1; }
  [[ -s "$NOTES" ]] || { echo "Write the release notes first: release-notes/$VERSION.md" >&2; exit 1; }
  if ! git -C "$ROOT_DIR" diff --quiet || ! git -C "$ROOT_DIR" diff --cached --quiet; then
    echo "Commit your changes first; the DMG must match a commit." >&2
    exit 1
  fi
  REPO="$(git -C "$ROOT_DIR" remote get-url origin | sed -E 's#^(git@github\.com:|https://github\.com/)##; s#\.git$##')"
fi

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
SHA="$(cut -d' ' -f1 < "$DMG.sha256")"

if [[ "$PUBLISH" == 1 ]]; then
  echo "==> Publishing $TAG to $REPO"
  git -C "$ROOT_DIR" rev-parse -q --verify "refs/tags/$TAG" >/dev/null || git -C "$ROOT_DIR" tag -a "$TAG" -m "PixelCrew $VERSION"
  git -C "$ROOT_DIR" push origin HEAD:main
  git -C "$ROOT_DIR" push origin "$TAG"
  BODY="$STAGE/notes.md"
  { cat "$NOTES"; printf '\n\n**SHA-256** (%s): `%s`\n' "$(basename "$DMG")" "$SHA"; } > "$BODY"
  if gh release view "$TAG" --repo "$REPO" >/dev/null 2>&1; then
    gh release upload "$TAG" "$DMG" --repo "$REPO" --clobber
    gh release edit "$TAG" --repo "$REPO" --title "PixelCrew $VERSION" --notes-file "$BODY" --draft=false --latest
  else
    gh release create "$TAG" "$DMG" --repo "$REPO" --title "PixelCrew $VERSION" --notes-file "$BODY" --verify-tag --latest
  fi
  echo "==> Checking the published download"
  GOT="$(curl -fsSL "https://github.com/$REPO/releases/download/$TAG/$(basename "$DMG")" | shasum -a 256 | cut -d' ' -f1)"
  [[ "$GOT" == "$SHA" ]] || { echo "The downloaded DMG does not match ($GOT)." >&2; exit 1; }
  LATEST="$(gh release view --repo "$REPO" --json tagName -q .tagName)"
  [[ "$LATEST" == "$TAG" ]] || { echo "The latest release is $LATEST, not $TAG." >&2; exit 1; }
  echo "  https://github.com/$REPO/releases/tag/$TAG"
fi

echo "==> Done"
echo "  $DMG"
echo "  $SHA  $(basename "$DMG")"
