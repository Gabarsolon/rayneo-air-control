"""Wire-level framing for the taurus4p0 USB-HID control protocol.

Confirmed by disassembling the packet framer at firmware address
0x08010C84. Byte 0 of every outbound report is the HID report-ID (0x00,
required by hidapi on Windows even though the device doesn't use
numbered reports); byte 1 is a magic that selects how the firmware
interprets the rest of the 64-byte payload:

    0x66  short form: cmd @ frame[1], arg @ frame[2] (-> internal msg[8]),
          rest of frame (frame[3:]) copied to internal msg[9:].
          This is what every confirmed command in commands.py uses.
    0x77  long form: cmd @ frame[1], arg @ frame[5], payload @ frame[9:].
          Seen in the dispatch table but no confirmed use captured yet.
    0x88  raw 64-byte passthrough -- traced as far as "the framer stops
          interpreting and hands the whole frame to a subsystem", not
          further than that. Candidate path for hid_call_px8618_write_read_reg
          (string at 0x0803A99C) but unconfirmed. Treat as experimental.

Response frames start with header bytes 99 C8 40 00 (0x0040C899 as a
little-endian u32) -- this is the one structural fact true of every
response regardless of command.
"""

from __future__ import annotations

from dataclasses import dataclass

REPORT_LEN = 65          # byte 0 = report ID, bytes 1..64 = payload
FRAME_LEN = 64
RESPONSE_HEADER = bytes.fromhex("99c84000")

MAGIC_SHORT = 0x66
MAGIC_LONG = 0x77
MAGIC_RAW = 0x88

DEFAULT_EXTRA = 0x56      # first payload byte in every confirmed short-form send;
                          # meaning not resolved, but every working command uses it.


def build_short(cmd_id: int, val: int = 0x00, extra: int = DEFAULT_EXTRA) -> bytes:
    """Build a 65-byte HID report using the confirmed 0x66 short form."""
    if not 0 <= cmd_id <= 0xFF:
        raise ValueError(f"cmd_id out of range: {cmd_id!r}")
    if not 0 <= val <= 0xFF:
        raise ValueError(f"val out of range: {val!r}")
    if not 0 <= extra <= 0xFF:
        raise ValueError(f"extra out of range: {extra!r}")
    pkt = bytearray(REPORT_LEN)
    pkt[0] = 0x00
    pkt[1] = MAGIC_SHORT
    pkt[2] = cmd_id
    pkt[3] = val
    pkt[4] = extra
    return bytes(pkt)


def build_raw(payload: bytes) -> bytes:
    """Build a 65-byte HID report using the 0x88 raw-passthrough magic.

    `payload` is copied starting at frame[1] (i.e. right after the magic
    byte the caller must NOT include -- it's added here); truncated/padded
    to FRAME_LEN - 1 bytes. EXPERIMENTAL: the framer takes this path but
    what consumes it downstream hasn't been resolved. Use --experimental.
    """
    pkt = bytearray(REPORT_LEN)
    pkt[0] = 0x00
    pkt[1] = MAGIC_RAW
    body = payload[: FRAME_LEN - 1]
    pkt[2 : 2 + len(body)] = body
    return bytes(pkt)


class MalformedResponse(RuntimeError):
    pass


def check_header(resp: bytes) -> None:
    if resp is None or len(resp) < 4 or bytes(resp[0:4]) != RESPONSE_HEADER:
        got = bytes(resp[0:4]).hex() if resp else "<no response>"
        raise MalformedResponse(
            f"expected response header {RESPONSE_HEADER.hex()}, got {got}"
        )


@dataclass
class Status:
    """Honest field map of the cmd 0x00 status response.

    Field boundaries come from disassembling the status builder at
    0x08010EF0 (resp[N] == sp[0x10+N] in that function's own frame).
    Several fields the prior control tool reported (brightness, volume,
    mode_raw, wear_sensor) are NOT real telemetry -- they're hardcoded
    constants or bytes of an unrelated hardcoded float, confirmed by
    reading the literal pool. They are intentionally not exposed here.
    """
    raw: bytes
    uptime_ticks: int          # resp[0x04:0x08], real device uptime counter
    build_date: str            # resp[0x18:0x22], ASCII build date string
    refresh_hz: int            # resp[0x28], decoded 0x3C/0x5A/0x78 -> 60/90/120

    @property
    def unknown_bytes(self) -> bytes:
        """Everything else in the payload, for anyone continuing the RE."""
        return self.raw


_REFRESH_MAP = {0x3C: 60, 0x5A: 90, 0x78: 120}


def parse_status(resp: bytes) -> Status:
    check_header(resp)
    b = bytes(resp)
    uptime = int.from_bytes(b[0x04:0x08], "little")
    try:
        build_date = b[0x18:0x22].split(b"\x00", 1)[0].decode("ascii", "replace")
    except Exception:
        build_date = ""
    refresh_raw = b[0x28] if len(b) > 0x28 else 0
    refresh_hz = _REFRESH_MAP.get(refresh_raw, 0)
    return Status(raw=b, uptime_ticks=uptime, build_date=build_date, refresh_hz=refresh_hz)
