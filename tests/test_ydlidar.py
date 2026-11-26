"""Protocol tests. MANUAL_PACKET is the worked example printed in the T-mini Plus manual, section 3.1.6
(fig. 8): its stated checksum is 0xE85A. These are test fixtures, never shown in the app."""
import struct

import numpy as np

from backend.ydlidar import PacketParser, ScanAssembler, checksum, decode_packet

MANUAL_PACKET = bytes.fromhex(
    "AA 55 20 13 95 A9 63 00 5A E8 01 08 B9 01 AC B8 01 F8 B6 01 88 B5 01 58 AE 01 C0 AB 01 04"
    " AA 01 78 A9 01 34 A9 01 C4 AD 01 34 AF 01 78 AE 01 2C AD 01 02 00 00 62 AD 01 60 AD 01"
    " F0 AB 01 88 AD 01 FE AD")


def test_manual_checksum():
    assert len(MANUAL_PACKET) == 10 + 19 * 3
    assert checksum(MANUAL_PACKET) == 0xE85A == struct.unpack_from("<H", MANUAL_PACKET, 8)[0]


def test_manual_distance_example():
    # Manual 3.1.4: sample 64 E5 6F -> intensity 100, distance 7161 mm
    pkt = bytearray(MANUAL_PACKET[:10]) + bytes.fromhex("64 E5 6F")
    pkt[3] = 1
    p = decode_packet(bytes(pkt))
    assert p.intensity[0] == 100 and p.dist_mm[0] == 7161 and p.flag[0] == 1


def test_manual_angles_wrap_clockwise():
    p = decode_packet(MANUAL_PACKET)
    assert np.isclose(p.angles[0], (0xA995 >> 1) / 64)       # ~338.6 deg
    assert np.isclose(p.angles[-1], (0x0063 >> 1) / 64)      # ~0.8 deg, after wrapping through 0
    assert np.all(p.angles < 360) and not p.zero


def test_parser_resyncs_and_rejects_corruption():
    bad = bytearray(MANUAL_PACKET)
    bad[20] ^= 0xFF
    pr = PacketParser()
    out = pr.feed(b"\x00\x13\xaa" + bytes(bad) + b"\x99" + MANUAL_PACKET[:30])
    out += pr.feed(MANUAL_PACKET[30:])
    assert len(out) == 1 and pr.ok == 1 and pr.bad >= 1


def test_assembler_splits_on_zero_packet():
    zero = bytearray(MANUAL_PACKET[:10]) + bytes.fromhex("00 00 00")
    zero[2], zero[3] = (60 << 1) | 1, 1        # 6.0 Hz reported
    zp = decode_packet(bytes(zero))
    asm = ScanAssembler()
    assert asm.push(zp) is None
    assert asm.push(decode_packet(MANUAL_PACKET)) is None
    scan = asm.push(zp)
    assert scan is not None and len(scan.angles) == 20 and scan.freq_hz == 6.0
