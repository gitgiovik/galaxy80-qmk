#!/usr/bin/env bash
# Build the Galaxy80 firmware images from the pinned QMK tree.
#
#   tools/build.sh [KEYMAP ...]      keymaps: via default minimal debug   (default: all four)
#
# Output (G80_OUT, default <repo>/out):
#   galaxy80_<keymap>.bin   firmware image
#   SHA256SUMS              hashes of the .bin files
#   BUILDINFO.txt           pinned commits, toolchain, build date
#   emu/                    epomaker_galaxy80_<k>.{bin,elf,map}, nm_<k>.txt for the emulator suite
#                           (the minimal image is stored as stage1, the name the tests use)
#
# Environment overrides:
#   G80_QMK_DIR      QMK tree (default <repo>/third_party/qmk_firmware; created by tools/fetch_qmk.sh)
#   G80_OUT          output directory (default <repo>/out)
#   G80_BUILD_DATE   build timestamp YYYY-MM-DD-HH:MM:SS baked into the image (see below)
#   G80_JOBS         make parallelism (default 4)
#   G80_ENV_SCRIPT   optional shell script sourced first (toolchain PATH setup)
#
# Reproducible builds: QMK derives the VIA EEPROM magic from the build date (QMK_BUILDDATE), so
# the date is pinned. Default: the commit date (UTC) of HEAD of this repository, or a fixed
# constant outside a git checkout. G80_BUILD_DATE overrides both.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
QMK_DIR="${G80_QMK_DIR:-$REPO/third_party/qmk_firmware}"
OUT="${G80_OUT:-$REPO/out}"
JOBS="${G80_JOBS:-4}"
# shellcheck disable=SC1090
[ -n "${G80_ENV_SCRIPT:-}" ] && . "$G80_ENV_SCRIPT"

command -v arm-none-eabi-gcc >/dev/null || { echo "arm-none-eabi-gcc not in PATH" >&2; exit 1; }
command -v qmk >/dev/null || { echo "qmk CLI not in PATH (pip install qmk)" >&2; exit 1; }

# --- pinned build date -------------------------------------------------------------------------
if [ -z "${G80_BUILD_DATE:-}" ]; then
  G80_BUILD_DATE="$(TZ=UTC git -C "$REPO" log -1 --format=%cd --date=format-local:%Y-%m-%d-%H:%M:%S 2>/dev/null || true)"
  G80_BUILD_DATE="${G80_BUILD_DATE:-2026-01-01-00:00:00}"
fi
case "$G80_BUILD_DATE" in
  [0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]-[0-9][0-9]:[0-9][0-9]:[0-9][0-9]) ;;
  *) echo "bad G80_BUILD_DATE '$G80_BUILD_DATE' (want YYYY-MM-DD-HH:MM:SS)" >&2; exit 1 ;;
esac
export G80_BUILD_DATE
# `qmk generate-version-h` stamps the current time with time.strftime(); a sitecustomize shim
# returns the pinned date instead, without touching the QMK tree.
export PYTHONPATH="$REPO/tools/reproducible${PYTHONPATH:+:$PYTHONPATH}"

KEYMAPS=("$@")
[ ${#KEYMAPS[@]} -gt 0 ] || KEYMAPS=(via default minimal debug)

export G80_QMK_DIR="$QMK_DIR"
"$REPO/tools/fetch_qmk.sh"   # makes sure the tree exists and refreshes the keyboard copy
export QMK_HOME="$QMK_DIR"
cd "$QMK_DIR"
mkdir -p "$OUT/emu"
KB=epomaker/galaxy80
PFX=epomaker_galaxy80

for km in "${KEYMAPS[@]}"; do
  # the emulator suite addresses the minimal image as 'stage1' (its internal name)
  img="$km"; [ "$km" = minimal ] && img=stage1
  echo "== building $km (build date $G80_BUILD_DATE)"
  rm -f ".build/${PFX}_${km}".*
  qmk compile -j "$JOBS" -kb "$KB" -km "$km" > "$OUT/build_$km.log" 2>&1 \
    || { tail -30 "$OUT/build_$km.log"; echo "BUILD FAIL $km" >&2; exit 1; }
  [ -f ".build/${PFX}_${km}.bin" ] || { tail -30 "$OUT/build_$km.log"; echo "BUILD FAIL $km (no bin)" >&2; exit 1; }
  cp ".build/${PFX}_${km}.bin" "$OUT/galaxy80_$km.bin"
  cp ".build/${PFX}_${km}.bin" "$OUT/emu/${PFX}_$img.bin"
  cp ".build/${PFX}_${km}.elf" "$OUT/emu/${PFX}_$img.elf"
  [ -f ".build/${PFX}_${km}.map" ] && cp ".build/${PFX}_${km}.map" "$OUT/emu/${PFX}_$img.map"
  arm-none-eabi-nm -n "$OUT/emu/${PFX}_$img.elf" > "$OUT/emu/nm_$img.txt"
done

( cd "$OUT" && sha256sum galaxy80_*.bin > SHA256SUMS )
{
  echo "repo_commit: $(git -C "$REPO" rev-parse HEAD 2>/dev/null || echo unknown)"
  echo "qmk_firmware: $(git -C "$QMK_DIR" rev-parse HEAD)"
  for sm in lib/chibios lib/chibios-contrib lib/lufa lib/printf lib/vusb; do
    echo "submodule $sm: $(git -C "$QMK_DIR/$sm" rev-parse HEAD)"
  done
  echo "toolchain: $(arm-none-eabi-gcc --version | head -1)"
  echo "build_date: $G80_BUILD_DATE"
} > "$OUT/BUILDINFO.txt"
echo "== done: $OUT"
cat "$OUT/SHA256SUMS"
