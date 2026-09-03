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
from .commands import BRIGHTNESS_LEVEL_TO_RAW, Command, DisplayMode

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

    # -- traced operations (see commands.py; recovered from the Android
    #    app's native SDK, not yet exercised live from this project) ------
    def set_brightness(self, raw: int) -> bytes:
        """CONFIRMED -- see commands.COMMANDS[0x09]. `raw` is the literal
        device-side table index, NOT the OSD level -- e.g. raw=3 shows as
        OSD level 6. Use set_brightness_level() unless you specifically
        want to poke the raw index. raw=4 also reads as max brightness but
        leaves the glasses' own physical OSD unable to keep controlling
        brightness afterward -- a device-side issue, not a software one.
        Avoid it; use raw=8 (or set_brightness_level(7)) for max instead."""
        resp = self.send(Command.BRIGHTNESS, raw)
        if resp is None:
            raise TimeoutError("no response to BRIGHTNESS command")
        return resp

    def set_brightness_level(self, level: int) -> bytes:
        """CONFIRMED -- friendly wrapper around set_brightness() that
        translates an OSD brightness level (0=dimmest..7=brightest) through
        BRIGHTNESS_LEVEL_TO_RAW before sending, so the level you pass here
        matches what the glasses' own OSD shows."""
        if level not in BRIGHTNESS_LEVEL_TO_RAW:
            raise ValueError(f"brightness level must be 0-7, got {level!r}")
        return self.set_brightness(BRIGHTNESS_LEVEL_TO_RAW[level])

    def save_brightness(self) -> bytes:
        """TRACED -- see commands.COMMANDS[0x0D]. No value byte; persists
        whatever set_brightness() last set."""
        resp = self.send(Command.BRIGHTNESS_SAVE)
        if resp is None:
            raise TimeoutError("no response to BRIGHTNESS_SAVE command")
        return resp

    def set_refresh_rate(self, hz: int) -> bytes:
        """TRACED -- see commands.COMMANDS[0x20]/[0x21]. Unlike the other
        commands here, the rate isn't a value byte -- it's a different
        cmd_id per rate. Only 60 and 120 are known; anything else raises."""
        if hz == 60:
            cmd = Command.REFRESH_RATE_60
        elif hz == 120:
            cmd = Command.REFRESH_RATE_120
        else:
            raise ValueError(f"unknown refresh rate {hz!r} -- only 60 and 120 are traced")
        resp = self.send(cmd)
        if resp is None:
            raise TimeoutError("no response to REFRESH_RATE command")
        return resp

    def set_audio_tube_mode(self, on: bool) -> bytes:
        """TRACED -- see commands.COMMANDS[0x48]. Best guess: the OSD's
        'Sound Tube: Off/On' toggle."""
        resp = self.send(Command.AUDIO_TUBE_MODE, 1 if on else 0)
        if resp is None:
            raise TimeoutError("no response to AUDIO_TUBE_MODE command")
        return resp

    def set_audio_mode(self, mode: int) -> bytes:
        """TRACED -- see commands.COMMANDS[0x49]. Distinct from the
        AUDIO_TUBE_MODE (0x48) command below."""
        resp = self.send(Command.AUDIO_MODE, mode)
        if resp is None:
            raise TimeoutError("no response to AUDIO_MODE command")
        return resp

    def set_volume(self, level: int) -> bytes:
        """TRACED -- see commands.COMMANDS[0x50]."""
        resp = self.send(Command.VOLUME, level)
        if resp is None:
            raise TimeoutError("no response to VOLUME command")
        return resp

    def reboot_to_bootloader(self) -> bytes:
        """TRACED -- see commands.COMMANDS[0x66]. Software DFU entry --
        no button-hold needed. No value byte."""
        resp = self.send(Command.REBOOT_TO_BOOTLOADER)
        if resp is None:
            raise TimeoutError("no response to REBOOT_TO_BOOTLOADER command")
        return resp

    def switch_to_3d(self) -> bytes:
        """TRACED -- see commands.COMMANDS[0x06]. Explicit set to 3D mode,
        no value byte. Matches the physical brightness+volume button combo
        used to switch modes while the glasses are on."""
        resp = self.send(Command.SWITCH_TO_3D)
        if resp is None:
            raise TimeoutError("no response to SWITCH_TO_3D command")
        return resp

    def switch_to_2d(self) -> bytes:
        """TRACED -- see commands.COMMANDS[0x07]. Explicit set to 2D mode,
        no value byte."""
        resp = self.send(Command.SWITCH_TO_2D)
        if resp is None:
            raise TimeoutError("no response to SWITCH_TO_2D command")
        return resp

    def switch_side_by_side(self) -> bytes:
        """TRACED -- see commands.COMMANDS[0x30]. An actual toggle (flips
        current 2D/3D state) rather than an explicit set like switch_to_3d()/
        switch_to_2d() above, no value byte."""
        resp = self.send(Command.SWITCH_SIDE_BY_SIDE)
        if resp is None:
            raise TimeoutError("no response to SWITCH_SIDE_BY_SIDE command")
        return resp

    def set_picture_mode(self, mode: int, p1: int = 0, p2: int = 0) -> bytes:
        """CONFIRMED (mode=0x0F) -- see commands.COMMANDS[0x73]. The STM32
        handler for this command (0x08012278, ground-truth decompiled from
        the firmware image, not guessed) only does anything when `mode` is
        exactly 0x0C or 0x0F -- every other value, including the 0/1/2 this
        project originally guessed for Standard/Movie/Eye Comfort, silently
        no-ops with no response. mode=0x0F is confirmed live: it acks with
        what looks like a PANELCOLORPARAMS-style readback. mode=0x0C calls a
        no-arg internal function and has never acked in testing -- may not
        send a response by design (fire-and-forget), not necessarily broken.
        p1 must be < 0x65 (101) or the whole call silently no-ops -- looks
        like a 0-100 style parameter (contrast/hue/gain?), not yet mapped to
        a specific field."""
        pkt = bytearray(protocol.build_short(Command.PICTURE_MODE, mode))
        pkt[4] = 0x00
        pkt[5] = p1
        pkt[6] = p2
        resp = self.send_raw(bytes(pkt))
        if resp is None:
            raise TimeoutError("no response to PICTURE_MODE command")
        return resp

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
