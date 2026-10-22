#!/usr/bin/env python3
"""Fail if a Galaxy80 image does not fit its flash/RAM budget.

usage: check_size.py [ELF_OR_DIR ...]      (default: out/emu next to this repo)

For every *.elf found:
  - code (text) + initialised data must end below 0xF6C0 (the wear-leveling aux sectors start there, then the
    wear-leveling area at 0xF7C0);
  - the heap (__heap_end__ - __heap_base__, from nm) must be at least 512 bytes;
  - the matching .bin, when present, must be 65536 bytes with 0xAAAA5555 (little endian) at 0xFFFC.
Reads the ELF with arm-none-eabi-size / arm-none-eabi-nm (PATH, or ARM_NONE_EABI_PREFIX for a prefix).
"""
import os
import struct
import subprocess
import sys

FLASH_LIMIT = 0xF6C0
MIN_HEAP = 512
BOOT_FLAG_OFF = 0xFFFC
BOOT_FLAG = 0xAAAA5555
IMAGE_SIZE = 65536

PREFIX = os.environ.get("ARM_NONE_EABI_PREFIX", "arm-none-eabi-")


def run(tool, *args):
    return subprocess.run([PREFIX + tool, *args], check=True, capture_output=True, text=True).stdout


def check_elf(elf):
    """Return a list of (ok, message) for one ELF."""
    name = os.path.basename(elf)
    res = []
    row = run("size", elf).strip().splitlines()[-1].split()
    text, data = int(row[0]), int(row[1])
    flash = text + data
    res.append((flash < FLASH_LIMIT,
                "%s: text %d + data %d = %d B, limit %d, free %d" % (name, text, data, flash, FLASH_LIMIT, FLASH_LIMIT - flash)))
    syms = {}
    for line in run("nm", elf).splitlines():
        p = line.split()
        if len(p) == 3 and p[2] in ("__heap_base__", "__heap_end__"):
            syms[p[2]] = int(p[0], 16)
    if len(syms) == 2:
        heap = syms["__heap_end__"] - syms["__heap_base__"]
        res.append((heap >= MIN_HEAP, "%s: heap %d B (minimum %d)" % (name, heap, MIN_HEAP)))
    else:
        res.append((False, "%s: __heap_base__/__heap_end__ not found" % name))
    binf = os.path.splitext(elf)[0] + ".bin"
    if os.path.exists(binf):
        b = open(binf, "rb").read()
        flag = struct.unpack_from("<I", b, BOOT_FLAG_OFF)[0] if len(b) >= BOOT_FLAG_OFF + 4 else None
        ok = len(b) == IMAGE_SIZE and flag == BOOT_FLAG
        res.append((ok, "%s: image %d B, flag at 0x%X = %s" % (os.path.basename(binf), len(b), BOOT_FLAG_OFF,
                                                               "0x%08X" % flag if flag is not None else "n/a")))
    return res


def main(argv):
    targets = argv or [os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "out", "emu")]
    elfs = []
    for t in targets:
        if os.path.isdir(t):
            elfs += sorted(os.path.join(t, f) for f in os.listdir(t) if f.endswith(".elf"))
        else:
            elfs.append(t)
    if not elfs:
        print("check_size: no .elf files found", file=sys.stderr)
        return 2
    bad = 0
    for e in elfs:
        for ok, msg in check_elf(e):
            print(("OK   " if ok else "FAIL ") + msg)
            bad += not ok
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
