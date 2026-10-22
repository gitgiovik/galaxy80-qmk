#!/usr/bin/env bash
# Fetch the pinned SonixQMK tree (with the submodules the build needs) and copy the Galaxy80
# keyboard directory into it.
#
#   tools/fetch_qmk.sh
#
# Environment overrides:
#   G80_QMK_DIR   where the QMK tree lives (default: <repo>/third_party/qmk_firmware, gitignored)
#   G80_QMK_URL   clone URL (default: https://github.com/SonixQMK/qmk_firmware.git)
#   G80_KBD_SRC   keyboard directory to copy in (default: <repo>/keyboards/epomaker/galaxy80)
#
# The tree is pinned to one commit; the submodule gitlinks of that commit are verified against the
# table below. Re-running refreshes the keyboard copy only.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
QMK_DIR="${G80_QMK_DIR:-$REPO/third_party/qmk_firmware}"
QMK_URL="${G80_QMK_URL:-https://github.com/SonixQMK/qmk_firmware.git}"
QMK_SHA=62973ee48a976c6f7cc6d4f7b198bd48a2fdfe60
KB_REL=keyboards/epomaker/galaxy80
KBD_SRC="${G80_KBD_SRC:-$REPO/$KB_REL}"

# Submodules the build needs, with their expected commits (googletest, lvgl, pico-sdk are not needed).
SUBMODULES="
lib/chibios 6170ddf92d55be54c89b708d1882eea229edeb2c
lib/chibios-contrib 4b5588b511c0e98b4ae8c3b81357e4468ec9d1cb
lib/lufa 549b97320d515bfca2f95c145a67bd13be968faa
lib/printf c2e3b4e10d281e7f0f694d3ecbd9f320977288cc
lib/vusb 819dbc1e5d5926b17e27e00ca6d3d2988adae04e
"

if [ ! -d "$QMK_DIR/.git" ]; then
  mkdir -p "$QMK_DIR"
  git -C "$QMK_DIR" init -q
  git -C "$QMK_DIR" remote add origin "$QMK_URL"
fi
cd "$QMK_DIR"

if [ "$(git rev-parse -q --verify HEAD 2>/dev/null || true)" != "$QMK_SHA" ]; then
  echo "fetching $QMK_SHA from $QMK_URL"
  git fetch -q --depth 1 origin "$QMK_SHA"
  git -c advice.detachedHead=false checkout -q -f FETCH_HEAD
fi
[ "$(git rev-parse HEAD)" = "$QMK_SHA" ] || { echo "ERROR: tree is not at $QMK_SHA" >&2; exit 1; }

echo "$SUBMODULES" | while read -r path sha; do
  [ -n "$path" ] || continue
  want="$(git ls-tree HEAD "$path" | awk '{print $3}')"
  [ "$want" = "$sha" ] || { echo "ERROR: gitlink $path is $want, expected $sha" >&2; exit 1; }
  if [ "$(git -C "$path" rev-parse -q --verify HEAD 2>/dev/null || true)" != "$sha" ]; then
    echo "fetching $path"
    # shallow where the server allows fetching by SHA, full otherwise
    git submodule update --init --depth 1 -- "$path" 2>/dev/null || git submodule update --init -- "$path"
  fi
  [ "$(git -C "$path" rev-parse HEAD)" = "$sha" ] || { echo "ERROR: $path is not at $sha" >&2; exit 1; }
done

# Copy (not symlink: works the same on Windows and Linux) the keyboard directory into the tree.
rm -rf "${QMK_DIR:?}/$KB_REL"
mkdir -p "$(dirname "$QMK_DIR/$KB_REL")"
cp -r "$KBD_SRC" "$QMK_DIR/$KB_REL"
find "$QMK_DIR/$KB_REL" -name '*.bak' -delete
echo "QMK tree ready: $QMK_DIR (keyboard copied to $KB_REL)"
