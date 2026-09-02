"""Protocol framing tests. No hardware required -- these only check the
byte layout matches what was verified by disassembly / prior live sends."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rayneo_control import protocol  # noqa: E402


def test_build_short_layout():
    pkt = protocol.build_short(0x1A, 0x00)
    assert len(pkt) == 65
    assert pkt[0] == 0x00  # HID report id
    assert pkt[1] == 0x66  # magic
    assert pkt[2] == 0x1A  # cmd id
    assert pkt[3] == 0x00  # val
    assert pkt[4] == 0x56  # default extra
    assert all(b == 0 for b in pkt[5:])


def test_build_short_matches_known_good_sdr_send():
    # This exact packet produced ack 99c8400016ce2f02e308000d05010100 on a
    # real device (mode 0 = SDR).
    pkt = protocol.build_short(0x1A, 0x00, 0x56)
    assert pkt.hex() == "00" + "661a0056" + "00" * 60


def test_build_short_rejects_out_of_range():
    import pytest

    with pytest.raises(ValueError):
        protocol.build_short(0x100, 0)
    with pytest.raises(ValueError):
        protocol.build_short(0, 0x100)


def test_check_header_ok():
    resp = bytes.fromhex("99c8400016ce2f02e308000d05010100" + "00" * 47)
    protocol.check_header(resp)  # should not raise


def test_check_header_rejects_bad_header():
    import pytest

    with pytest.raises(protocol.MalformedResponse):
        protocol.check_header(bytes(64))


def test_parse_status_refresh_rate():
    b = bytearray(64)
    b[0:4] = protocol.RESPONSE_HEADER
    b[0x28] = 0x78  # 120 Hz
    st = protocol.parse_status(bytes(b))
    assert st.refresh_hz == 120


def test_build_raw_pads_and_truncates():
    pkt = protocol.build_raw(b"\x01\x02\x03")
    assert len(pkt) == 65
    assert pkt[1] == 0x88
    assert pkt[2:5] == b"\x01\x02\x03"

    long_payload = bytes(range(80))
    pkt2 = protocol.build_raw(long_payload)
    assert len(pkt2) == 65
