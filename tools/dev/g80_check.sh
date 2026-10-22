#!/usr/bin/env bash
# Build the Galaxy80 images from a keyboard source dir and run the whole emulator suite in parallel.
#
#   tools/dev/g80_check.sh [-s SRC_KBD_DIR] [-n NAME] [-t] OUT_DIR
#     -s  keyboard source dir (default: keyboards/epomaker/galaxy80 of this repo)
#     -n  build slot (default: main = <qmk>/keyboards/epomaker/galaxy80). Any other slot builds a copy
#         in <qmk>/keyboards/g80w_SLOT/galaxy80 (the leaf dir must stay "galaxy80": QMK only compiles
#         <leaf>.c), so several agents can build at the same time; one lock per slot.
#     -t  build only, no tests
#     -T  tests only, on the images already in OUT_DIR/fw
# OUT_DIR gets: fw/ (bins, elfs, nm_*.txt named epomaker_galaxy80_<image>), build logs, test outputs,
# SUMMARY.txt (sizes, heap, PASS/FAIL per block, every FAIL line).
#
# Environment (all optional; tools/dev/local.env, git-ignored, is sourced first when present):
#   G80_QMK         QMK tree (default <repo>/third_party/qmk_firmware, see tools/fetch_qmk.sh)
#   G80_GCC_BIN     directory of arm-none-eabi-* (default: from PATH)
#   G80_MSYS_BASH   bash used to run `qmk compile` (default: bash from PATH; set it on Windows when the
#                   QMK toolchain lives in an MSYS2 environment)
#   G80_QMK_ENV     shell script sourced before `qmk compile` (toolchain / venv setup)
#   G80_JOBS        parallel test processes (default 6), G80_BUILD_JOBS make jobs (default 8)
#   G80_KBD, G80_OBJDUMP are exported for the tests from the build slot and the toolchain.
# Images: via, default, debug and stage1 = the "minimal" keymap (the emulator tests address the
# safety image by the name 'stage1').
set -u
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
SRC="$REPO/keyboards/epomaker/galaxy80"
NAME=main
TESTS=1
BUILD=1
FAILB=0
while getopts "s:n:tT" o; do
  case $o in s) SRC="$OPTARG" ;; n) NAME="$OPTARG" ;; t) TESTS=0 ;; T) BUILD=0 ;; *) exit 2 ;; esac
done
shift $((OPTIND - 1))
OUT="${1:?usage: g80_check.sh [-s src] [-n name] [-t] OUT_DIR}"
mkdir -p "$OUT/fw"
OUT="$(cd "$OUT" && pwd)"

[ -f "$REPO/tools/dev/local.env" ] && . "$REPO/tools/dev/local.env"
QMK="${G80_QMK:-$REPO/third_party/qmk_firmware}"
if [ "$NAME" = main ]; then KB=epomaker/galaxy80; else KB="g80w_$NAME/galaxy80"; fi
DST="$QMK/keyboards/$KB"
GCC="${G80_GCC_BIN:-}"; [ -n "$GCC" ] && GCC="${GCC%/}/"
MSYS="${G80_MSYS_BASH:-bash}"
LOCK="$QMK/.build/.g80_lock_$NAME"
mkdir -p "$QMK/.build"
if [ $BUILD = 1 ]; then
until mkdir "$LOCK" 2>/dev/null; do echo "waiting for build lock $LOCK"; sleep 5; done
echo "$$ $(date)" > "$LOCK/owner"
trap 'rm -rf "$LOCK"' EXIT
rm -rf "$DST"; mkdir -p "$(dirname "$DST")"
cp -r "$SRC" "$DST"
find "$DST" -name "*.bak" -delete

PFX="$(echo "$KB" | tr '/' '_')"
FAILB=0
# keymaps built; the image of the "minimal" keymap is stored as epomaker_galaxy80_stage1.* because the
# emulator suite (run_tests.py, test_features.py, test_via_eeprom.py) addresses that image as 'stage1'
for km in via default minimal debug; do
  EXTRA=""
  img="$km"; [ "$km" = minimal ] && img=stage1
  "$MSYS" -lc "${G80_QMK_ENV:+source '$G80_QMK_ENV' && }cd '$QMK' && qmk compile -j ${G80_BUILD_JOBS:-8} -kb $KB -km $km $EXTRA" > "$OUT/build_$km.log" 2>&1
  if [ ! -f "$QMK/.build/${PFX}_$km.bin" ] || grep -qiE "error|\[ERRORS\]" "$OUT/build_$km.log"; then
    echo "BUILD FAIL $km (see $OUT/build_$km.log)"; FAILB=1; continue
  fi
  cp "$QMK/.build/${PFX}_$km.bin" "$OUT/fw/epomaker_galaxy80_$img.bin"
  cp "$QMK/.build/${PFX}_$km.elf" "$OUT/fw/epomaker_galaxy80_$img.elf"
  [ -f "$QMK/.build/${PFX}_$km.map" ] && cp "$QMK/.build/${PFX}_$km.map" "$OUT/fw/epomaker_galaxy80_$img.map"
  "${GCC}arm-none-eabi-nm" -n "$OUT/fw/epomaker_galaxy80_$img.elf" > "$OUT/fw/nm_$img.txt"
  rm -f "$QMK/.build/${PFX}_$km".*
done
rm -rf "$LOCK"; trap - EXIT
fi

{
  echo "== sizes (code must end below 0xF6C0 = 63168 B: wear-leveling aux sectors 0xF6C0, EEPROM 0xF7C0)"
  for km in via default stage1 debug; do
    e="$OUT/fw/epomaker_galaxy80_$km.elf"; [ -f "$e" ] || continue
    "${GCC}arm-none-eabi-size" "$e" | tail -1 | awk -v k=$km '{printf "%-8s text %6d data %5d bss %5d  flash %6d  free %6d\n", k, $1, $2, $3, $1+$2, 63168-$1-$2}'
    h=$(grep -E " __heap_base__| __heap_end__" "$OUT/fw/nm_$km.txt" | awk '{print $1}' | tr '\n' ' ')
    echo "         heap base/end: $h"
  done
  sha256sum "$OUT"/fw/*.bin | sed "s|$OUT/fw/||"
} > "$OUT/SUMMARY.txt"
[ $FAILB = 1 ] && { echo "BUILD FAILED" >> "$OUT/SUMMARY.txt"; cat "$OUT/SUMMARY.txt"; exit 1; }
rm -rf "$OUT/t"
[ $TESTS = 0 ] && { cat "$OUT/SUMMARY.txt"; exit 0; }

export G80_KBD="$DST" G80_OBJDUMP="${GCC}arm-none-eabi-objdump"
T="$REPO/tests/emulator"
BLOCKS="A B C D E F E-L1 E-L9 E-L10 E-L12 E-L13 E-L14 E-L15 E-L16 S3 S4"  # S3 = test_features.py, S4 = test_via_eeprom.py
cd "$T"
# each unicorn engine commits ~1 GiB (its translation cache; ctl_set_tcg_buffer_size crashes unicorn 2.1.4),
# so too many test processes at once exhaust the Windows commit limit and segfault: run G80_JOBS at a time.
JOBS=${G80_JOBS:-6}
jobs_list() {
  for b in $BLOCKS; do echo "run_tests.py --block=$b t/$b/console.txt"; done
  echo "test_wireless.py - t/wireless.txt"
  echo "test_host_off.py - t/host_off.txt"
  # extra suites added by work packages: tests/emulator/suite_*.py <fw_dir> (PASS/FAIL lines)
  for x in suite_*.py; do [ -f "$x" ] && echo "$x - t/${x%.py}.txt"; done
}
run_one() {
  local script=$1 opt=$2 out="$OUT/$3"
  mkdir -p "$(dirname "$out")"
  local try rc
  # exit 139 = unicorn segfault under memory pressure (harness, not firmware): rerun, at most twice, and
  # say so in the summary. A test FAIL is never rerun.
  for try in 1 2 3; do
    if [ "$opt" = - ]; then python -u "$script" "$OUT/fw" > "$out" 2>&1
    else python -u "$script" "$OUT/fw" "$(dirname "$out")" "$opt" > "$out" 2>&1; fi
    rc=$?
    [ $rc = 139 ] || break
  done
  [ $try -gt 1 ] && echo "RETRIED $((try - 1))x after unicorn segfault" >> "$out"
  echo "EXIT $rc" >> "$out"
}
export -f run_one; export OUT
jobs_list > "$OUT/jobs.txt"
xargs -P "$JOBS" -L 1 bash -c 'run_one "$0" "$1" "$2"' < "$OUT/jobs.txt"
# A block that still segfaulted after its parallel retries (unicorn crashes more often with several
# engines alive at once) is run again on its own, one block at a time.
while read -r script opt out; do
  if grep -q "^EXIT 139$" "$OUT/$out"; then
    run_one "$script" "$opt" "$out"
    echo "RETRIED serially after parallel segfaults" >> "$OUT/$out"
  fi
done < "$OUT/jobs.txt"

{
  echo "== tests"
  for f in $(find "$OUT/t" -name "*.txt" ! -name "trace.txt" | sort); do
    n=${f#$OUT/t/}
    p=$(grep -c "^PASS" "$f"); q=$(grep -c "^FAIL" "$f")
    crash=""; { grep -qE "Traceback|Segmentation|fatal exception" "$f" || ! grep -q "^EXIT 0$" "$f"; } && crash="  CRASH (see $n)"
    grep -q "^RETRIED" "$f" && crash="$crash  ($(grep "^RETRIED" "$f"))"
    printf "%-28s PASS %3d  FAIL %3d%s\n" "$n" "$p" "$q" "$crash"
  done
  echo "TOTAL PASS $(cat $(find "$OUT/t" -name "*.txt" ! -name "trace.txt") | grep -c '^PASS')  FAIL $(cat $(find "$OUT/t" -name "*.txt" ! -name "trace.txt") | grep -c '^FAIL')"
  echo "== FAIL lines"
  cat $(find "$OUT/t" -name "*.txt" ! -name "trace.txt") | grep "^FAIL" | cut -c1-300
} >> "$OUT/SUMMARY.txt"
cat "$OUT/SUMMARY.txt"
