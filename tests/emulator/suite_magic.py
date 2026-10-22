# Copyright 2026 gitgiovik
# SPDX-License-Identifier: GPL-2.0-or-later
"""QMK magic keycodes and EE_CLR assigned from VIA (g80_fn.c g80_magic_key / g80_frst_start).
usage: python suite_magic.py <fw_dir>      (VIA image, simulated module)

QMK's process_magic() reads keymap_config from EEPROM and writes it back inside the key event (no write
gate, no critical-battery inhibit); QK_CLEAR_EEPROM invalidates the EEPROM at once. The keyboard handles
them itself:
T-MAGIC-1  CL_TOGG and GE_SWAP set through VIA (0x05) on F13 / PrtSc: RAM keymap_config changes on the press,
           0 flash ops and no eeconfig_read/update_keymap during the key event and the next 0.9 s, then one
           deferred save sequence (>= 1 s after the last change, G80_SAVE_DELAY_MS, one eeconfig_update_keymap), EEPROM == RAM
T-MAGIC-2  AG_TOGG twice (Alt/GUI swapped and back, both bits follow the left one as in QMK), EH_LEFT (split
           only): no RAM change left, the toggled-back value is not written (eeprom_update compares)
T-MAGIC-3  critical battery (5B 22): CL_TOGG changes RAM, 0 flash ops while critical, the save is pending; EE_CLR
           is refused (red flashes, no reset, no write); after 5B 23 one save, EEPROM == RAM
T-PDF-1    PDF(1) on F12 (layer 0) and PDF(0) on F12 of layer 1: the default layer changes on the release, 0 flash ops
           and no eeconfig_update_default_layer during the key event and the next 0.85 s, then one deferred save;
           the EEPROM default layer == RAM; PDF(0) from the new default layer goes back the same way
T-OS-1     OS_TOGG (QK_ONE_SHOT_TOGGLE) on F11: keymap_config.oneshot_enable flips on the press, 0 flash ops and no
           eeconfig_update_keymap during the key event and the next 0.85 s, then one deferred save; EEPROM bit == RAM
T-OS-2     critical battery (5B 22): OS_ON on F10 sets the bit, 0 flash ops while critical, the save is pending; after
           5B 23 one save, EEPROM bit set
T-OS-3     OS_ON again (already on): no RAM change, nothing pending, 0 flash ops; OS_OFF on F8 clears the bit, one
           deferred save, EEPROM bit clear
T-PDF-2    PDF(3) on F9 (VIA image: 3 dynamic layers): ignored, the default layer stays, nothing pending, 0 flash ops
T-MAGIC-4  EE_CLR with a normal battery: the factory reset (white flashes, then reset), no flash op inside the
           key event (the EEPROM is invalidated by the reset path >= 1 s later), afterwards the defaults
"""
import struct
from bat_common import *

F12, F13, PSCR, SCRL, PAUS = (0, 12), (0, 13), (0, 14), (0, 15), (0, 16)
F8, F9, F10, F11 = (0, 8), (0, 9), (0, 10), (0, 11)
PDF0, PDF1, PDF3 = 0x52E0, 0x52E1, 0x52E3
OS_ON, OS_OFF, OS_TOGG = 0x7C5A, 0x7C5B, 0x7C5C
KM_ONESHOT = 0x400
CL_TOGG, GE_SWAP, AG_TOGG, EH_LEFT, EE_CLR = 0x7002, 0x700C, 0x7016, 0x701E, 0x7C03
KM_CTRL_CAPS, KM_LALT, KM_RALT, KM_GRAVE = 0x01, 0x04, 0x08, 0x20


def u8(m, sym, n=1):
    return bytes(m.uc.mem_read(m.sym[sym], n))


def km(m):
    return struct.unpack('<H', u8(m, 'keymap_config', 2))[0]


def raw(m, mod, pkt):
    """one VIA packet into raw_hid_receive, called from a safe point of the main loop (inject)"""
    buf = m.sym['__heap_base__']

    def f(mm):
        mm.uc.mem_write(buf, (bytes(pkt) + b'\x00' * 32)[:32])
        mm.call('raw_hid_receive', buf, 32, max_insn=60000000)
    m.inject.append((m.t_ms() + 0.5, f))
    run(m, mod, m.t_ms() + 2)


def set_kc(m, mod, row, col, kc, layer=0):
    """VIA dynamic_keymap_set_keycode; waits until the deferred VIA write is in flash"""
    raw(m, mod, [0x05, layer, row, col, kc >> 8, kc & 0xFF])
    t_end = m.t_ms() + 1500
    run(m, mod, m.t_ms() + 20)
    while u8(m, 'g80_vq_busy')[0] and m.t_ms() < t_end:
        run(m, mod, m.t_ms() + 2)
    run(m, mod, m.t_ms() + 300)


def ee_keymap(m, mod):
    out = {}
    buf = m.sym['__heap_base__']

    def f(mm):
        mm.call('eeconfig_read_keymap', buf)
        out['v'] = struct.unpack('<H', mm.ram_read(buf, 2))[0]
    m.inject.append((m.t_ms() + 0.5, f))
    run(m, mod, m.t_ms() + 2)
    return out.get('v')


def tap(m, t, key, dur=60):
    hold(m, t, t + dur, key)


def seqs(ops):
    """flash ops grouped into sequences (gaps > 200 ms): [(t_first, t_last, n)]"""
    g = []
    for op in ops:
        if not g or op[-1] - g[-1][1] > 200:
            g.append([op[-1], op[-1], 0])
        g[-1][1] = op[-1]
        g[-1][2] += 1
    return [tuple(x) for x in g]


def setup():
    m, mod = mk(True, stress=True)  # stress: flash writes reach unicorn between chunks (g80emu code_write)
    solid(m, 250, 170)
    run(m, mod, 1500)
    mod.frame(0x5B, 0x32)                   # link connected
    run(m, mod, 1800)
    for key, kc in ((F13, CL_TOGG), (PSCR, GE_SWAP), (SCRL, AG_TOGG), (PAUS, EE_CLR), (F11, OS_TOGG), (F10, OS_ON),
                    (F8, OS_OFF), (F9, PDF3)):
        set_kc(m, mod, key[0], key[1], kc)
    set_kc(m, mod, 0, 13, EH_LEFT, layer=1)  # Fn+F13 = EH_LEFT
    set_kc(m, mod, F12[0], F12[1], PDF1)
    set_kc(m, mod, F12[0], F12[1], PDF0, layer=1)
    run(m, mod, m.t_ms() + 1000)
    return m, mod


# ------------------------------------------------------------------ T-MAGIC-1 / 2
m, mod = setup()
upd = recorder(m, 'eeconfig_update_keymap')
rdk = recorder(m, 'eeconfig_read_keymap')
T = (int(m.t_ms()) // 100 + 5) * 100
ops0 = len(m.flash_ops)
k0 = km(m)
tap(m, T, F13)
tap(m, T + 200, PSCR)
run(m, mod, T + 100)
k1 = km(m)
run(m, mod, T + 200 + 900)
early = m.flash_ops[ops0:]
pend = (u8(m, 'g80_save_dirty')[0], u8(m, 'g80_save_keymap')[0])
early_calls = (len(upd), len(rdk))
run(m, mod, T + 7000)
grp = seqs(m.flash_ops[ops0:])
k2 = km(m)
n_rd = len(rdk)
ee = ee_keymap(m, mod)                       # (calls eeconfig_read_keymap itself: counted before)
check('T-MAGIC-1', k1 == k0 ^ KM_CTRL_CAPS and k2 == k0 ^ KM_CTRL_CAPS ^ KM_GRAVE and not early and pend == (1, 1)
      and early_calls == (0, 0) and len(grp) == 1 and grp[0][0] >= T + 200 + 1000 and len(upd) == 1 and n_rd == 0
      and ee == k2 and ok_sys(m),
      'keymap_config %04x -> CL_TOGG %04x -> GE_SWAP %04x; flash ops in the key events + 0.9 s: %d, pending (dirty, keymap) %s, '
      'eeconfig update/read_keymap calls then %s; afterwards %d save sequence(s) %s (keys at %d / %d), update_keymap calls %d, '
      'read_keymap calls %d, EEPROM %s == RAM' % (
          k0, k1, k2, len(early), pend, early_calls, len(grp), [(round(a), round(b), n) for a, b, n in grp], T, T + 200,
          len(upd), n_rd, ee if ee is None else '%04x' % ee))

T2 = (int(m.t_ms()) // 100 + 5) * 100
ops1 = len(m.flash_ops)
tap(m, T2, SCRL)
run(m, mod, T2 + 100)
k3 = km(m)
tap(m, T2 + 300, SCRL)
hold(m, T2 + 600, T2 + 760, FN)
tap(m, T2 + 620, F13)                       # Fn+F13 = EH_LEFT
run(m, mod, T2 + 6500)
k4 = km(m)
ops_t2 = m.flash_ops[ops1:]
swap = KM_LALT | KM_RALT
k3_exp = (k2 & ~swap) if k2 & KM_LALT else (k2 | swap)   # QMK: both = !swap_lalt_lgui
check('T-MAGIC-2', k3 == k3_exp and k4 == k2 and len(upd) == 2 and not ops_t2 and ok_sys(m),
      'AG_TOGG: %04x -> %04x (L+R Alt/GUI both follow !LAlt), again -> %04x; EH_LEFT: nothing; flash ops %d (value back to the stored one), '
      'update_keymap calls %d' % (k2, k3, k4, len(ops_t2), len(upd)))

# ------------------------------------------------------------------ T-MAGIC-3: critical battery
T3 = (int(m.t_ms()) // 100 + 5) * 100
run(m, mod, T3)
mod.frame(0x5B, 0x22)
run(m, mod, T3 + 100)
ops2 = len(m.flash_ops)
tap(m, T3 + 200, F13)
tap(m, T3 + 600, PAUS)                      # EE_CLR: refused
run(m, mod, T3 + 700)
fl = u8(m, 'g80_fl', 5)
frst = u8(m, 'g80_frst_running')[0]
run(m, mod, T3 + 6500)
k5 = km(m)
crit_ops = m.flash_ops[ops2:]
pend = (u8(m, 'g80_save_dirty')[0], u8(m, 'g80_save_keymap')[0])
mod.frame(0x5B, 0x23)
t23 = m.t_ms()
run(m, mod, T3 + 11000)
grp = seqs(m.flash_ops[ops2:])
ee = ee_keymap(m, mod)
check('T-MAGIC-3', k5 == k4 ^ KM_CTRL_CAPS and not crit_ops and pend == (1, 1) and fl[0] != 0 and fl[2:] == bytes([255, 0, 0])
      and frst == 0 and len(grp) == 1 and grp[0][0] >= t23 and ee == k5 and ok_sys(m),
      'while critical: CL_TOGG %04x -> %04x, EE_CLR: flash %s, factory reset running %d, %d flash ops in 6 s, pending %s; '
      'after 5B 23: %d sequence(s) %s, EEPROM %s == RAM, resets %s' % (
          k4, k5, fl.hex(), frst, len(crit_ops), pend, len(grp), [(round(a), round(b), n) for a, b, n in grp],
          ee if ee is None else '%04x' % ee, m.resets))

# ------------------------------------------------------------------ T-PDF-1: persistent default layer
def ee_deflayer(m, mod):
    out = {}

    def f(mm):
        out['v'] = mm.call('eeconfig_read_default_layer') & 0xFF
    m.inject.append((m.t_ms() + 0.5, f))
    run(m, mod, m.t_ms() + 2)
    return out.get('v')


udl = recorder(m, 'eeconfig_update_default_layer')
pdf = []
for kc_exp in (2, 1):                        # PDF(1) from layer 0, then PDF(0) from the new default layer 1
    T5 = (int(m.t_ms()) // 100 + 5) * 100
    ops5 = len(m.flash_ops)
    n_udl = len(udl)
    hold(m, T5, T5 + 100, F12)
    run(m, mod, T5 + 50)
    dl_p = u8(m, 'default_layer_state')[0]
    run(m, mod, T5 + 150)
    dl_r = u8(m, 'default_layer_state')[0]
    run(m, mod, T5 + 950)
    early = (len(m.flash_ops) - ops5, len(udl) - n_udl, u8(m, 'g80_save_deflayer')[0])
    run(m, mod, T5 + 7000)
    grp = seqs(m.flash_ops[ops5:])
    pdf.append((dl_p, dl_r, early, len(grp), grp[0][0] - T5 if grp else None, len(udl) - n_udl, ee_deflayer(m, mod), kc_exp))
check('T-PDF-1', all(dl_r == e and dl_p != e and ea == (0, 0, 1) and ng == 1 and t0 >= 1000 and nu == 1 and ee == e
                     for dl_p, dl_r, ea, ng, t0, nu, ee, e in pdf) and ok_sys(m),
      'per PDF key (default layer on press, on release; key event + 0.85 s: flash ops, update_default_layer calls, pending; '
      'save sequences, first at ms after the press, update calls, EEPROM default layer, expected): %s' % (pdf,))

# ------------------------------------------------------------------ T-OS-1..3: one-shot keys
def os_case(key, crit=False):
    """tap key (critical battery around it if crit): (bit before, bit after the press, flash ops / update_keymap
    calls / pending (dirty, keymap) in the key event + 2.5 s (crit: + 6 s), save sequences, first sequence ms after
    the press (crit: after 5B 23), EEPROM bit)"""
    T = (int(m.t_ms()) // 100 + 5) * 100
    run(m, mod, T - 200)
    if crit:
        mod.frame(0x5B, 0x22)
    ops = len(m.flash_ops)
    n_upd = len(upd)
    b0 = km(m) & KM_ONESHOT
    tap(m, T, key)
    run(m, mod, T + 100)
    b1 = km(m) & KM_ONESHOT
    run(m, mod, T + (6500 if crit else 950))
    early = (len(m.flash_ops) - ops, len(upd) - n_upd, (u8(m, 'g80_save_dirty')[0], u8(m, 'g80_save_keymap')[0]))
    t_ref = T
    if crit:
        mod.frame(0x5B, 0x23)
        t_ref = m.t_ms()
    run(m, mod, t_ref + 5000)
    grp = seqs(m.flash_ops[ops:])
    ee = ee_keymap(m, mod)
    return (b0, b1, early, len(grp), round(grp[0][0] - t_ref) if grp else None, len(upd) - n_upd,
            None if ee is None else ee & KM_ONESHOT, km(m) & KM_ONESHOT)


o1 = os_case(F11)
check('T-OS-1', o1[1] == o1[0] ^ KM_ONESHOT and o1[2] == (0, 0, (1, 1)) and o1[3] == 1 and o1[4] >= 1000 and o1[5] == 1
      and o1[6] == o1[7] == o1[1] and ok_sys(m),
      'OS_TOGG (bit before, after the press, key event + 0.85 s: flash ops / update_keymap calls / pending, save sequences, '
      'first at ms, update calls, EEPROM bit, RAM bit): %s' % (o1,))
if o1[1]:                                    # make sure OS_ON has something to do
    o1b = os_case(F11)
o2 = os_case(F10, crit=True)
check('T-OS-2', o2[0] == 0 and o2[1] == KM_ONESHOT and o2[2] == (0, 0, (1, 1)) and o2[3] == 1 and o2[4] >= 0 and o2[5] == 1
      and o2[6] == o2[7] == KM_ONESHOT and ok_sys(m),
      'OS_ON under critical battery (bit before, after, while critical 6.5 s: flash ops / update calls / pending, '
      'sequences after 5B 23, first at ms after 5B 23, update calls, EEPROM bit, RAM bit): %s' % (o2,))
o3a = os_case(F10)
o3b = os_case(F8)
check('T-OS-3', o3a[0] == o3a[1] == KM_ONESHOT and o3a[2] == (0, 0, (0, 0)) and o3a[3] == 0 and o3a[5] == 0
      and o3b[0] == KM_ONESHOT and o3b[1] == 0 and o3b[2] == (0, 0, (1, 1)) and o3b[3] == 1 and o3b[4] >= 1000
      and o3b[5] == 1 and o3b[6] == o3b[7] == 0 and ok_sys(m),
      'OS_ON when on: %s; OS_OFF: %s' % (o3a, o3b))

# ------------------------------------------------------------------ T-PDF-2: PDF(n) past the keymap layers
T6 = (int(m.t_ms()) // 100 + 5) * 100
ops6 = len(m.flash_ops)
n_udl = len(udl)
dl0 = u8(m, 'default_layer_state')[0]
tap(m, T6, F9, dur=100)
run(m, mod, T6 + 300)
dl1 = u8(m, 'default_layer_state')[0]
pend = (u8(m, 'g80_save_dirty')[0], u8(m, 'g80_save_deflayer')[0])
run(m, mod, T6 + 6000)
check('T-PDF-2', dl0 == dl1 == 1 and pend == (0, 0) and len(m.flash_ops) == ops6 and len(udl) == n_udl
      and ee_deflayer(m, mod) == 1 and ok_sys(m),
      'PDF(3): default layer %d -> %d, pending (dirty, deflayer) %s, flash ops %d, update_default_layer calls %d' % (
          dl0, dl1, pend, len(m.flash_ops) - ops6, len(udl) - n_udl))

# ------------------------------------------------------------------ T-MAGIC-4: EE_CLR, normal battery
T4 = (int(m.t_ms()) // 100 + 5) * 100
ops3 = len(m.flash_ops)
tap(m, T4, PAUS)
run(m, mod, T4 + 200)
fl = u8(m, 'g80_fl', 5)
in_event = [op for op in m.flash_ops[ops3:] if op[-1] < T4 + 1000]
run(m, mod, T4 + 6000)
ops_r = m.flash_ops[ops3:]
rs = [r for r in m.resets if r[0] >= T4]
kc_after = None
if rs:
    buf = m.sym['__heap_base__']
    out = {}

    def f(mm):
        out['v'] = mm.call('dynamic_keymap_get_keycode', 0, PAUS[0], PAUS[1]) & 0xFFFF
    m.inject.append((m.t_ms() + 0.5, f))
    run(m, mod, m.t_ms() + 2)
    kc_after = out.get('v')
check('T-MAGIC-4', fl[0] != 0 and fl[2:] == bytes([255, 255, 255]) and not in_event and ops_r and len(rs) == 1
      and rs[0][0] - T4 >= 1000 and min(op[-1] for op in ops_r) >= T4 + 1000 and kc_after == 0x48 and not m.faults and m.isp is None,
      'EE_CLR: flash %s, flash ops in the key event + 1 s: %d, reset %s ms after the press (%d flash ops from %s ms), '
      'after the reset the Pause key is %s (default 0x0048), faults %s' % (
          fl.hex(), len(in_event), [round(r[0] - T4) for r in rs], len(ops_r),
          round(min(op[-1] for op in ops_r) - T4) if ops_r else None, None if kc_after is None else '%04x' % kc_after, m.faults[:1]))
m = None
done()
