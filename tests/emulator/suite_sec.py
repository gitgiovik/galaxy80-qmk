# Copyright 2026 gitgiovik
# SPDX-License-Identifier: GPL-2.0-or-later
"""Galaxy80 security tests T-SEC-1..3 (design 16, sec. 9 SEC-1).

    python -u suite_sec.py <fw_dir> [out_dir]

The packets go the real way (USB OUT endpoint -> raw_hid_task -> raw_hid_receive -> via_command_kb,
G80 stress=True). Starts from an EEPROM already formatted by a first boot (no flash stall model).
  T-SEC-1   stock-updater magic AA 42 89 5A FF 71 62 CC without Fn: no reset, no ROM ISP.
            (Images before SEC-1 fail it: the magic always jumped.)
  T-SEC-1b  the same magic with other keys held (not Fn), and the magic at offset 1: no ISP.
  T-SEC-2   the magic with Fn physically held: ROM ISP reached (through the RAM magic + reset).
  T-SEC-3   VIA id_bootloader_jump (0x0B), with and without Fn: no ISP (the pinned QMK does not
            compile the command: answered 0xFF, no jump).
"""
import os, sys, json, gc
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from g80emu import *
from via_host import parse_args
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

MAGIC = [0xAA, 0x42, 0x89, 0x5A, 0xFF, 0x71, 0x62, 0xCC]
FN = (5, 11)
FW, OUT, _opt, OUT_TMP = parse_args(sys.argv, 'sec')
NM = os.path.join(FW, 'nm_via.txt')
results = []


def check(tid, name, ok, detail):
    results.append(dict(id=tid, name=name, result='PASS' if ok else 'FAIL', detail=detail))
    print('%s  %-9s %s :: %s' % ('PASS' if ok else 'FAIL', tid, name, detail), flush=True)


def mkm(path):
    gc.collect()
    m = G80(path, NM, stress=True)
    m.keep_reg_writes = m.keep_col_reads = m.keep_ct1_events = False
    return m


def run_until(m, t_ms, step=2.0):
    r = 'TIME'
    while m.t_ms() < t_ms:
        r = m.run(min(t_ms, m.t_ms() + step))
        if r != 'TIME':
            break
    return r


base = os.path.join(OUT, 'base_formatted_via.bin')
m0 = mkm(os.path.join(FW, 'epomaker_galaxy80_via.bin'))
run_until(m0, 1500, 50.0)
open(base, 'wb').write(bytes(m0.flash))
m0 = None


def scenario(pkt, held=(), t_hold=300.0, t_pkt=500.0, t_end=1500.0):
    m = mkm(base)
    run_until(m, t_hold, 20.0)
    m.pressed = set(held)
    run_until(m, t_pkt)
    m.hid_send(pkt, t_pkt)
    r = run_until(m, t_end)
    ans = m.hid_tx[-1][1][:8].hex() if m.hid_tx else None
    out = dict(ret=r, isp=m.isp is not None, isp_boot=m.isp['boot'] if m.isp else None, resets=m.resets,
               rx=len(m.hid_rx), answer=ans, faults=m.faults[:2], wdt_en_at_isp=m.isp['wdt_en'] if m.isp else None,
               usb_touched_before_isp=m.isp['usb_touched'] if m.isp else None)
    m = None
    return out


s1 = scenario(MAGIC)
check('T-SEC-1', 'raw HID ISP magic without Fn held: ignored (no reset, no ROM ISP)', not s1['isp'] and not s1['resets'],
      'ISP=%s (boot %s), resets=%s, packets taken=%d, answer=%s' % (s1['isp'], s1['isp_boot'], s1['resets'], s1['rx'], s1['answer']))

s1b = scenario(MAGIC, held=[(3, 4), (4, 13)])          # F + RShift held, Fn not held
s1c = scenario([0x00] + MAGIC)                          # magic not at offset 0
check('T-SEC-1b', 'magic with other keys held (F + RShift, not Fn): no ISP; magic at offset 1: no ISP',
      not s1b['isp'] and not s1b['resets'] and not s1c['isp'] and not s1c['resets'],
      'F+RShift: ISP=%s resets=%s | offset 1: ISP=%s resets=%s answer=%s' % (s1b['isp'], s1b['resets'], s1c['isp'], s1c['resets'], s1c['answer']))

s2 = scenario(MAGIC, held=[FN])
check('T-SEC-2', 'raw HID ISP magic with Fn physically held: ROM ISP reached (RAM magic + reset, WDT stopped)',
      s2['isp'] and not s2['faults'],
      'ISP=%s (boot %s), resets=%s, WDT running at ISP entry=%s, USB touched before ISP (in the ISP boot)=%s, faults=%s' % (
          s2['isp'], s2['isp_boot'], s2['resets'], s2['wdt_en_at_isp'], s2['usb_touched_before_isp'], s2['faults']))

s3a = scenario([0x0B])
s3b = scenario([0x0B], held=[FN])
check('T-SEC-3', 'VIA id_bootloader_jump (0x0B): no ISP without Fn (and today none with Fn either: not handled by via.c)',
      not s3a['isp'] and not s3a['resets'],
      'no Fn: ISP=%s resets=%s answer=%s | Fn held: ISP=%s resets=%s answer=%s' % (
          s3a['isp'], s3a['resets'], s3a['answer'], s3b['isp'], s3b['resets'], s3b['answer']))

json.dump(results, open(os.path.join(OUT, 'suite_sec_results.json'), 'w'), indent=1, default=str)
print('\nSEC SUMMARY: %d PASS, %d FAIL' % (sum(r['result'] == 'PASS' for r in results), sum(r['result'] == 'FAIL' for r in results)))
if OUT_TMP:
    import shutil
    shutil.rmtree(OUT, ignore_errors=True)
