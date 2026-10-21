#!/usr/bin/env python3
"""Galaxy80 QMK debug console reader (READ-ONLY).

Opens the QMK console HID interface (usage page 0xFF31, usage 0x74) of the Galaxy80
QMK firmware (VID:PID FEED:0880), reads 32-byte input reports, reassembles text lines,
prefixes each with a host timestamp and writes it to stdout and to a log file.

It never writes to the device (no write(), no send_feature_report(), no output reports).
If the keyboard is unplugged / rebooted it waits and reopens it automatically, so a
whole series of reboots can be captured in one log.

Requires the hidapi Python binding:   pip install hidapi      (module name "hid")
(the ctypes "hid" package from PyPI, which needs hidapi.dll, is also supported).

Usage:
    python g80_console.py LOGFILE [--vid 0xFEED] [--pid 0x0880]
Stop with Ctrl+C.
"""
import argparse
import datetime
import sys
import time

try:
    import hid
except ImportError:
    sys.exit("missing module 'hid': install it with  pip install hidapi")

CONSOLE_USAGE_PAGE = 0xFF31
CONSOLE_USAGE = 0x74
REPORT_SIZE = 32


def find_console(vid, pid):
    for d in hid.enumerate(vid, pid):
        if d.get("usage_page") == CONSOLE_USAGE_PAGE and d.get("usage") == CONSOLE_USAGE:
            return d
    return None


class Reader:
    """Read-only wrapper over the two common 'hid' bindings."""

    def __init__(self, path):
        if hasattr(hid, "device"):  # cython-hidapi (pip install hidapi)
            self.dev = hid.device()
            self.dev.open_path(path)
            self.dev.set_nonblocking(False)
            self._read = lambda: self.dev.read(64, 200)
        else:  # pyhidapi (pip install hid)
            self.dev = hid.Device(path=path)
            self._read = lambda: self.dev.read(64, 200)

    def read(self):
        return bytes(self._read() or b"")

    def close(self):
        try:
            self.dev.close()
        except Exception:
            pass


def stamp():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("logfile")
    ap.add_argument("--vid", type=lambda s: int(s, 0), default=0xFEED)
    ap.add_argument("--pid", type=lambda s: int(s, 0), default=0x0880)
    a = ap.parse_args()

    with open(a.logfile, "a", encoding="utf-8", buffering=1) as log:

        def emit(text):
            line = "%s\t%s" % (stamp(), text)
            print(line, flush=True)
            log.write(line + "\n")

        emit("# g80_console start vid=%04X pid=%04X" % (a.vid, a.pid))
        waiting_msg = False
        try:
            while True:
                info = find_console(a.vid, a.pid)
                if info is None:
                    if not waiting_msg:
                        emit("# waiting for console interface (FF31/0074)...")
                        waiting_msg = True
                    time.sleep(0.3)
                    continue
                waiting_msg = False
                try:
                    rd = Reader(info["path"])
                except Exception as e:
                    emit("# open failed: %r" % (e,))
                    time.sleep(0.5)
                    continue
                emit("# connected: %s" % (info["path"].decode(errors="replace") if isinstance(info["path"], bytes) else info["path"]))
                buf = b""
                try:
                    while True:
                        data = rd.read()
                        if not data:
                            continue
                        # QMK console: text, zero-padded to 32 bytes (no report id on this interface)
                        chunk = data[:REPORT_SIZE].split(b"\x00", 1)[0]
                        buf += chunk
                        while b"\n" in buf:
                            ln, buf = buf.split(b"\n", 1)
                            emit(ln.rstrip(b"\r").decode("ascii", errors="replace"))
                        if len(buf) > 4096:  # runaway line without newline
                            emit(buf.decode("ascii", errors="replace"))
                            buf = b""
                except Exception as e:  # read error = unplug/reboot (OSError or hid.HIDException)
                    if buf:
                        emit(buf.decode("ascii", errors="replace"))
                    emit("# disconnected: %r" % (e,))
                finally:
                    rd.close()
                time.sleep(0.3)
        except KeyboardInterrupt:
            emit("# g80_console stop")


if __name__ == "__main__":
    main()
