"""RayNeoDevice — thin, honest wrapper over the HID control endpoint.

Deliberately does not implement DFU / firmware writing. This library
only ever talks to the device's existing runtime HID interface -- the
same channel the OEM app uses to change display mode, read status,
etc. That's a hard boundary, not an oversight: don't add write paths
into the DFU/bootloader interface here.
"""

from __future__ import annotations

import time
from typing import Optional

import hid

from . import protocol
from .commands import Command, DisplayMode

VID = 0x1BBB
PID = 0xAF50


class RayNeoNotFound(RuntimeError):
    pass


class RayNeoDevice:
    def __init__(self, vid: int = VID, pid: int = PID, timeout_ms: int = 300):
        self.vid = vid
        self.pid = pid
        self.timeout_ms = timeout_ms
        self._dev: Optional[hid.device] = None

    # -- lifecycle ---------------------------------------------------
    def open(self) -> "RayNeoDevice":
        d = hid.device()
        try:
            d.open(self.vid, self.pid)
        except OSError as e:
            raise RayNeoNotFound(
                f"no HID device {self.vid:04x}:{self.pid:04x} found -- "
                f"are the glasses plugged in over USB-C?"
            ) from e
        self._dev = d
        return self

    def close(self) -> None:
        if self._dev is not None:
            self._dev.close()
            self._dev = None

    def __enter__(self) -> "RayNeoDevice":
        return self.open()

    def __exit__(self, *exc) -> None:
        self.close()

    def _require_open(self) -> hid.device:
        if self._dev is None:
            raise RuntimeError("device not open -- use RayNeoDevice() as a context manager or call .open()")
        return self._dev

    # -- transport -----------------------------------------------------
    def send_raw(self, packet: bytes, read: bool = True) -> Optional[bytes]:
        """Send a pre-built 65-byte report, return the 64-byte response (or None)."""
        dev = self._require_open()
        if len(packet) != protocol.REPORT_LEN:
            raise ValueError(f"packet must be {protocol.REPORT_LEN} bytes, got {len(packet)}")
        dev.write(packet)
        time.sleep(0.04)
        if not read:
            return None
        resp = dev.read(protocol.FRAME_LEN, timeout_ms=self.timeout_ms)
        return bytes(resp) if resp else None

    def send(self, cmd_id: int, val: int = 0x00, extra: int = protocol.DEFAULT_EXTRA) -> Optional[bytes]:
        """Send a confirmed-form (0x66) command and return the raw response bytes."""
        return self.send_raw(protocol.build_short(cmd_id, val, extra))

    # -- confirmed operations -------------------------------------------
    def get_status(self) -> protocol.Status:
        resp = self.send(Command.STATUS)
        if resp is None:
            raise TimeoutError("no response to STATUS command")
        return protocol.parse_status(resp)

    def set_display_mode(self, mode: DisplayMode) -> bytes:
        """Set SDR / AI-HDR / HDR10. Confirmed live for mode=SDR (ack
        99c8400016ce2f02e308000d05010100); other values inferred but not
        independently exercised by this codebase yet -- verify visually
        after setting."""
        resp = self.send(Command.DISPLAY_MODE, int(mode))
        if resp is None:
            raise TimeoutError("no response to DISPLAY_MODE command")
        protocol.check_header(resp)
        return resp

    # -- experimental operations -----------------------------------------
    def set_gamma_index(self, index: int) -> bytes:
        """EXPERIMENTAL -- see commands.COMMANDS[0x6D]. Valid range unknown;
        the handler returns 0xFFFFFFFF (-1) if the underlying driver object
        or vtable slot is null, so a -1-ish response means "did nothing",
        not "success"."""
        resp = self.send(Command.GAMMA_INDEX, index)
        if resp is None:
            raise TimeoutError("no response to GAMMA_INDEX command")
        return resp

    def set_gamut_mode(self, index: int) -> bytes:
        """EXPERIMENTAL -- see commands.COMMANDS[0x6E]. Same caveats as
        set_gamma_index."""
        resp = self.send(Command.GAMUT_MODE, index)
        if resp is None:
            raise TimeoutError("no response to GAMUT_MODE command")
        return resp
