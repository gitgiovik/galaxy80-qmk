# Copyright 2026 gitgiovik
# SPDX-License-Identifier: GPL-2.0-or-later
"""Host-side models on top of the (frozen) g80emu.G80 for the VIA suites (suite_via_stress.py,
suite_paint.py, suite_sec.py). Nothing here changes the emulated MCU; only the emulated USB host and
the USB OUT endpoint are refined:

  - VIA app lock-step: the VIA app sends one command and waits for its answer (IN report) before the
    next one. g80emu's hid_send_lockstep sends the next packet 1 ms after raw_hid_receive RETURNS;
    the firmware now answers EEPROM writes from housekeeping once they are in flash, so here the next
    packet leaves 1 ms after the ANSWER (send_raw_hid). hid_idle() also waits for every answer.
  - USB OUT endpoint capacity (ep_model=True): the ChibiOS input queue of the raw HID endpoint holds
    RAW_OUT_CAPACITY (2, config.h) packets; while both are full the endpoint NAKs and the host retries
    every 1 ms (bInterval 1). So one raw_hid_task call can take at most 2 queued packets plus one
    per millisecond it runs. g80emu by default hands every queued packet to the same call at once.
"""
import os, sys, gc
from collections import deque
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from g80emu import G80

RAW_OUT_CAPACITY = 2


class G80Via(G80):
    def __init__(self, *a, ep_model=False, **kw):
        self.ep_model = ep_model
        self.ep_last_take = -1e9
        super().__init__(*a, **kw)

    # --- lock-step on the answer, not on the return of raw_hid_receive
    def on_rawhid_ret(self, uc, address, size, user):
        scr, self.hid_script = self.hid_script, deque()
        try:
            super().on_rawhid_ret(uc, address, size, user)
        finally:
            self.hid_script = scr

    def on_out_report(self, uc, address, size, user):
        super().on_out_report(uc, address, size, user)
        if user[0] == 'hid' and self.hid_script and not self.in_call:
            self.hid_send(self.hid_script.popleft(), self.t_ms() + self.hid_latency_ms)

    def answers_pending(self):
        return len(self.hid_rx) - len(self.hid_tx)

    def hid_idle(self):
        return super().hid_idle() and self.answers_pending() <= 0

    # --- endpoint capacity
    def on_hid_rx(self, uc, address, size, user):
        if self.ep_model and self.hid_cur_task is not None and self.hid_queue and self.hid_queue[0][0] <= self.t_ms():
            if self.hid_cur_task[1] >= RAW_OUT_CAPACITY and self.t_ms() - self.ep_last_take < 1.0:
                q, self.hid_queue = self.hid_queue, deque()     # endpoint empty for this read: NAKed
                try:
                    return super().on_hid_rx(uc, address, size, user)
                finally:
                    self.hid_queue = q
            n0 = len(self.hid_rx)
            super().on_hid_rx(uc, address, size, user)
            if len(self.hid_rx) > n0:
                self.ep_last_take = self.t_ms()
            return
        return super().on_hid_rx(uc, address, size, user)


def mk_via(path, nm, **kw):
    gc.collect()
    m = G80Via(path, nm, **kw)
    m.keep_reg_writes = m.keep_col_reads = m.keep_ct1_events = False
    return m


def run_until(m, t_ms, cond=None, step=2.0):
    """run to t_ms (or until cond(m)); returns the run() result of the last step"""
    r = 'TIME'
    while m.t_ms() < t_ms:
        r = m.run(min(t_ms, m.t_ms() + step))
        if r != 'TIME':
            return r
        if cond and cond(m):
            return 'COND'
    return r


def via_sync(m, pkts, timeout_ms=5000):
    """lock-step VIA exchange through the main loop; returns (answers, run result)"""
    n0 = len(m.hid_tx)
    m.hid_send_lockstep(pkts, m.t_ms())
    r = run_until(m, m.t_ms() + timeout_ms, lambda mm: mm.hid_idle() and len(mm.hid_tx) - n0 >= len(pkts), step=1.0)
    return [b for _, b in m.hid_tx[n0:]], r


def boot_to_loop(m, extra_ms=300, limit_ms=8000):
    return run_until(m, limit_ms, lambda mm: mm.first_main_loop_t is not None and mm.first_main_loop_boot == mm.boot_index
                     and mm.t_ms() > mm.first_main_loop_t + extra_ms, step=20.0)


def parse_args(argv, name):
    """<fw_dir> [out_dir] [--opt[=v]...]; without out_dir a temporary directory is used (the suite
    runner calls the suites with the fw dir only and the cwd inside the repo)"""
    import tempfile
    args = [a for a in argv[1:] if not a.startswith('--')]
    opt = {a[2:].split('=')[0]: (a.split('=', 1)[1] if '=' in a else True) for a in argv[1:] if a.startswith('--')}
    fw = args[0]
    tmp = len(args) < 2
    out = tempfile.mkdtemp(prefix='g80_%s_' % name) if tmp else args[1]
    os.makedirs(out, exist_ok=True)
    return fw, out, opt, tmp
