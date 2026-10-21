"""Build-time shim: make time.strftime() return G80_BUILD_DATE for the QMK timestamp format.

Loaded automatically through PYTHONPATH by tools/build.sh. QMK's `generate-version-h` stamps
QMK_BUILDDATE with time.strftime('%Y-%m-%d-%H:%M:%S'); the VIA EEPROM magic derives from it, so
an unpinned date makes every build differ. Only that exact format string is intercepted.
"""
import os
import time

_date = os.environ.get("G80_BUILD_DATE")
if _date:
    _orig = time.strftime

    def _strftime(fmt, *args):
        if fmt == "%Y-%m-%d-%H:%M:%S":
            return _date
        return _orig(fmt, *args)

    time.strftime = _strftime
