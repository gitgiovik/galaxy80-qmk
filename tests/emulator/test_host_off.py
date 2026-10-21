"""Stage 7c: PC switched off with the USB 5 V still on -> LEDs off (galaxy80.c g80_host_task).
# LED samples are instantaneous (PWM phases + effect): a steadily lit keyboard reads ~50 %, so "lit" = > 30 %, dark = 0.
usage: python test_host_off.py <fw_dir>   (epomaker_galaxy80_<k>.bin + nm_<k>.txt, k = via, default)
H1  host alive 6 s: LEDs lit the whole time, no reset
H2  PC off at 1 s (no SOF, state unchanged): dark within ~2.1 s, stays dark; PC back at 6 s -> lit again;
    keys still scanned while dark; watchdog never fires
H3  keyboard powered with the PC already off (never enumerated, no SOF): dark after ~2 s
H4  real USB suspend (PC sleep) still dark through the suspend loop, wakes lit (no regression)
"""
import gc
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from g80emu import *

fw = sys.argv[1]
results = []


def check(tid, name, ok, detail):
    results.append(ok)
    print('%s  %-4s %s :: %s' % ('PASS' if ok else 'FAIL', tid, name, detail), flush=True)


def mkq(kind, **kw):
    gc.collect()  # unicorn segfaults in mem_map with many live engines
    m = G80(fw + '/epomaker_galaxy80_%s.bin' % kind, fw + '/nm_%s.txt' % kind, **kw)
    m.keep_reg_writes = m.keep_col_reads = m.keep_ct1_events = False
    m.schedule = []
    return m


def lit_window(m, t0, t1, step=3):
    """run t0..t1 and count samples with at least one LED lit"""
    n = lit = 0
    t = t0
    while t < t1:
        m.run(t)
        n += 1
        lit += bool(m.lit_leds())
        t += step
    return lit, n


def first_dark(m, t0, t1, step=5, hold=30):
    """first time from which `hold` consecutive samples are dark"""
    t, run_start, dark_n = t0, None, 0
    while t < t1:
        m.run(t)
        if m.lit_leds():
            run_start, dark_n = None, 0
        else:
            if run_start is None:
                run_start = t
            dark_n += 1
            if dark_n >= hold:
                return run_start
        t += step
    return None


def ok_sys(m):
    return not m.faults and not m.resets and m.isp is None


for kind in ('via', 'default'):
    # H1
    m = None  # free the previous unicorn engine first
    m = mkq(kind)
    lit, n = lit_window(m, 300, 6000, 7)
    check('H1-' + kind, 'PC on: LEDs lit for 6 s', lit > 0.3 * n and ok_sys(m), 'lit %d/%d samples, faults=%s resets=%s' % (lit, n, m.faults[:1], m.resets))

    # H2
    m = None  # free the previous unicorn engine first
    m = mkq(kind)
    m.run(1000)
    m.usb_host = False
    lit_before, n0 = lit_window(m, 1000, 2900, 7)      # still lit before the 2 s grace
    td = first_dark(m, 2900, 3600)
    lit_dark, n1 = lit_window(m, 3600, 5900, 7)
    m.schedule = [(4000, 4060, (3, 1))]                  # key A while dark: scan still runs
    m.usb_host = True
    lit_back, n2 = lit_window(m, 6000, 7000, 7)
    lit_after, n3 = lit_window(m, 7000, 9500, 7)
    check('H2-' + kind, 'PC off at 1 s -> dark at ~3 s, dark until the PC comes back at 6 s, then lit again; no reset',
          lit_before > 0.3 * n0 and td is not None and 2950 <= td <= 3150 and lit_dark == 0 and lit_after > 0.3 * n3 and ok_sys(m),
          'lit before=%d/%d, dark from %s ms, lit while off=%d/%d, back on %d/%d then %d/%d, faults=%s resets=%s' % (
              lit_before, n0, td, lit_dark, n1, lit_back, n2, lit_after, n3, m.faults[:1], m.resets))

    # H3
    m = None  # free the previous unicorn engine first
    m = mkq(kind)
    m.usb_host = False
    td = first_dark(m, 300, 4000)
    lit_dark, n1 = lit_window(m, 4000, 12000, 11)
    check('H3-' + kind, 'powered with the PC already off: dark after ~2 s and stays dark (12 s, past the watchdog period)',
          td is not None and td <= 2600 and lit_dark == 0 and ok_sys(m),
          'dark from %s ms, lit after=%d/%d, faults=%s resets=%s' % (td, lit_dark, n1, m.faults[:1], m.resets))

    # H4: USB suspend (no SOF while state 5), resume (state back to 2, SOFs again)
    m = None  # free the previous unicorn engine first
    m = mkq(kind)
    usbd = m.sym['USBD1']
    m.run(1000)
    m.inject = [(1000, lambda mm: mm.uc.mem_write(usbd, b'\x05')), (5000, lambda mm: (mm.uc.mem_write(usbd, b'\x02'), mm.call('suspend_wakeup_init')))]
    lit_s, n1 = lit_window(m, 1100, 4900, 7)
    lit_w, n2 = lit_window(m, 5200, 8000, 7)
    check('H4-' + kind, 'PC sleep (USB suspend 4 s) dark, wake-up lit again and stays lit',
          lit_s == 0 and lit_w > 0.3 * n2 and ok_sys(m),
          'lit while suspended=%d/%d, lit after wake=%d/%d, faults=%s resets=%s' % (lit_s, n1, lit_w, n2, m.faults[:1], m.resets))

print('%d/%d PASS' % (sum(results), len(results)))
