"""Static checks on the built images: no literal pointing at the code-option / CS area
(0x1FFF2xxx), at SYS0_EXRSTCTRL (0x4006001C) or the CS / ISP registers used by the stock
updater; ROM addresses referenced; reset vector; flag; EEPROM area blank."""
import struct, hashlib, sys, os
d = sys.argv[1]
for k in sys.argv[2:]:
    sub, name = (k.split('/') + [''])[:2] if '/' in k else ('', k)
    p = os.path.join(d, sub, 'epomaker_galaxy80_%s.bin' % (name or sub))
    b = open(p, 'rb').read()
    w = [struct.unpack_from('<I', b, i)[0] for i in range(0, 0xFFFC, 4)]
    bad = [hex(x) for x in w if (x >> 12) == 0x1FFF2 or x in (0x4006001C, 0x40060024, 0x40042000 + 0x1000)]
    rom = sorted(set(hex(x) for x in w if (x >> 16) == 0x1FFF))
    end = max(i for i in range(0xFFFC) if b[i]) + 1
    print('%-14s sp=%#x reset=%#x end=%#x flag=%s eeprom_blank=%s bad_literals=%s rom_refs=%s sha256=%s' % (
        k, w[0], w[1], end, b[0xFFFC:].hex(), not any(b[0xF7C0:0xFFC0]), bad, rom, hashlib.sha256(b).hexdigest()[:16]))
