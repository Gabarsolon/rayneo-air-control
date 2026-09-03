"""Desktop GUI control panel for the taurus4p0 RayNeo glasses.

Calls straight through to RayNeoDevice -- the exact same runtime HID
transport the CLI uses. There is no separate protocol implementation
here and no DFU/write path, matching the same boundary documented in
device.py and the README.

    pip install -e .[gui]
    rayneo-gui

The tray icon is optional -- if pystray/Pillow aren't installed the
window still works, it just quits on close instead of minimizing to
tray.
"""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from tkinter import scrolledtext, ttk
from typing import Callable, Optional

from . import protocol
from .commands import COMMANDS, Confidence, DisplayMode, MAX_COMMAND_ID
from .device import RayNeoDevice

try:
    import pystray
    from PIL import Image, ImageDraw

    _HAVE_TRAY = True
except ImportError:
    _HAVE_TRAY = False

APP_TITLE = "RayNeo Control"


def _make_icon_image() -> "Image.Image":
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((4, 4, 60, 60), fill="#2b6cb0")
    d.text((24, 18), "R", fill="white")
    return img


class RayNeoGUI:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title(APP_TITLE)
        self.root.geometry("640x680")
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        # every cross-thread update (tray callbacks, worker-thread results)
        # goes through this queue and is only ever applied from _poll_queue,
        # which runs on the Tk main loop -- tkinter widgets aren't safe to
        # touch from any other thread.
        self._work_q: "queue.Queue[Callable[[], None]]" = queue.Queue()
        self._tray: Optional["pystray.Icon"] = None
        self._debounce_ids: dict[str, str] = {}

        self._build_ui()
        if _HAVE_TRAY:
            self._start_tray()
        else:
            self._log("(pystray/Pillow not installed -- no tray icon; closing the window quits)")
        self.root.after(50, self._poll_queue)
        self._refresh_status()

    # -- cross-thread plumbing ------------------------------------------
    def _run_async(
        self,
        fn: Callable[[], object],
        on_done: Optional[Callable[[object], None]] = None,
    ) -> None:
        def worker() -> None:
            try:
                result = fn()
            except Exception as e:  # noqa: BLE001 -- surfaced to the log, not swallowed
                # Python unbinds `e` at the end of this except block, so the
                # message must be captured as a plain string now, not closed
                # over -- otherwise the queued lambda hits a NameError later.
                msg = f"error: {e}"
                self._work_q.put(lambda msg=msg: self._append_log(msg))
                return
            if on_done:
                self._work_q.put(lambda: on_done(result))

        threading.Thread(target=worker, daemon=True).start()

    def _debounce(self, key: str, delay_ms: int, fn: Callable[[], None]) -> None:
        """Collapse rapid-fire calls (e.g. dragging a slider) under `key`
        into one call `delay_ms` after the last one. Keeps live-updating
        controls from flooding the device with a send per pixel of drag."""
        pending = self._debounce_ids.pop(key, None)
        if pending is not None:
            self.root.after_cancel(pending)
        self._debounce_ids[key] = self.root.after(delay_ms, fn)

    def _poll_queue(self) -> None:
        try:
            while True:
                self._work_q.get_nowait()()
        except queue.Empty:
            pass
        self.root.after(50, self._poll_queue)

    def _log(self, msg: str) -> None:
        """Safe to call from any thread."""
        self._work_q.put(lambda: self._append_log(msg))

    def _append_log(self, msg: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", msg + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    # -- UI construction ---------------------------------------------------
    def _build_ui(self) -> None:
        nb = ttk.Notebook(self.root)
        nb.pack(fill="both", expand=True, padx=8, pady=8)

        self._build_status_tab(nb)
        self._build_traced_tab(nb)
        self._build_experimental_tab(nb)
        self._build_raw_tab(nb)
        self._build_scan_tab(nb)

        self.log = scrolledtext.ScrolledText(self.root, height=10, state="disabled")
        self.log.pack(fill="x", padx=8, pady=(0, 8))

    # -- Status tab ----------------------------------------------------
    def _build_status_tab(self, nb: ttk.Notebook) -> None:
        frame = ttk.Frame(nb)
        nb.add(frame, text="Status")

        self.status_vars = {
            "build_date": tk.StringVar(value="—"),
            "uptime": tk.StringVar(value="—"),
            "refresh": tk.StringVar(value="—"),
        }
        rows = [("Build date", "build_date"), ("Uptime ticks", "uptime"), ("Refresh rate", "refresh")]
        for i, (label, key) in enumerate(rows):
            ttk.Label(frame, text=label + ":").grid(row=i, column=0, sticky="w", padx=8, pady=4)
            ttk.Label(frame, textvariable=self.status_vars[key]).grid(row=i, column=1, sticky="w", padx=8, pady=4)

        ttk.Label(
            frame,
            text="Only build date / uptime / refresh rate are real -- brightness, "
                 "volume, mode and wear-sensor in the raw STATUS response are "
                 "hardcoded constants in this firmware build, not live telemetry.",
            wraplength=560, justify="left", foreground="#666",
        ).grid(row=len(rows), column=0, columnspan=2, sticky="w", padx=8, pady=(8, 4))

        ttk.Button(frame, text="Refresh status", command=self._refresh_status).grid(
            row=len(rows) + 1, column=0, columnspan=2, pady=12
        )

    def _refresh_status(self) -> None:
        self._log("querying status...")

        def work() -> protocol.Status:
            with RayNeoDevice() as dev:
                return dev.get_status()

        def done(st: protocol.Status) -> None:
            self.status_vars["build_date"].set(repr(st.build_date))
            self.status_vars["uptime"].set(str(st.uptime_ticks))
            self.status_vars["refresh"].set(f"{st.refresh_hz or 'unknown'} Hz")
            self._append_log("status ok")

        self._run_async(work, done)

    # -- Mode tab --------------------------------------------------------
    def _set_mode(self, mode: DisplayMode, name: str) -> None:
        self._log(f"setting display mode -> {name}...")

        def work() -> bytes:
            with RayNeoDevice() as dev:
                return dev.set_display_mode(mode)

        def done(resp: bytes) -> None:
            self._append_log(f"mode -> {name} ack: {resp.hex()}")

        self._run_async(work, done)

    # -- Traced tab (brightness/volume/audio/DFU-reboot) -----------------
    def _build_traced_tab(self, nb: ttk.Notebook) -> None:
        frame = ttk.Frame(nb)
        nb.add(frame, text="Display && Audio")
        ttk.Label(
            frame,
            text="TRACED, not CONFIRMED: recovered from the RayNeo Android app's "
                 "native SDK (ffalcon::XRService), not exercised live from this "
                 "project before. Watch/listen to the glasses after sending. Labels "
                 "in [brackets] are this project's best guess at the matching item "
                 "in the glasses' own OSD menu, not a confirmed correspondence.",
            wraplength=560, foreground="#a06000", justify="left",
        ).pack(padx=8, pady=(12, 8), anchor="w")

        # -- Dynamic quality [OSD: SDR/AI-HDR/HDR10] -- the one CONFIRMED
        # control on this tab (everything else here is TRACED/EXPERIMENTAL).
        row = ttk.Frame(frame)
        row.pack(fill="x", padx=8, pady=6)
        ttk.Label(row, text="Dynamic quality (0x1A)\n[SDR/AI-HDR/HDR10]", width=20, justify="left").pack(side="left")
        for name, mode in [("SDR", DisplayMode.SDR), ("AI-HDR", DisplayMode.AI_HDR), ("HDR10", DisplayMode.HDR10)]:
            ttk.Button(row, text=name, command=lambda m=mode, n=name: self._set_mode(m, n)).pack(
                side="left", padx=4
            )

        # -- Brightness (slider, applies live/debounced -- no Set button).
        # BRIGHTNESS_SAVE (0x0D) writes to flash, so that stays a separate,
        # deliberate button rather than firing on every drag tick. --------
        row = ttk.Frame(frame)
        row.pack(fill="x", padx=8, pady=6)
        ttk.Label(row, text="Brightness (0x09)", width=20).pack(side="left")
        self.brightness_var = tk.IntVar(value=128)
        self.brightness_label = ttk.Label(row, text="128", width=4)
        scale = ttk.Scale(
            row, from_=0, to=255, orient="horizontal", variable=self.brightness_var,
            command=self._on_brightness_change,
        )
        scale.pack(side="left", fill="x", expand=True, padx=4)
        self.brightness_label.pack(side="left", padx=(0, 4))
        ttk.Button(row, text="Save (0x0D)", command=self._save_brightness).pack(side="left", padx=4)

        # -- Volume (slider, applies live/debounced) ------------------------
        row = ttk.Frame(frame)
        row.pack(fill="x", padx=8, pady=6)
        ttk.Label(row, text="Volume (0x50)", width=20).pack(side="left")
        self.volume_var = tk.IntVar(value=50)
        self.volume_label = ttk.Label(row, text="50", width=4)
        scale = ttk.Scale(
            row, from_=0, to=100, orient="horizontal", variable=self.volume_var,
            command=self._on_volume_change,
        )
        scale.pack(side="left", fill="x", expand=True, padx=4)
        self.volume_label.pack(side="left", padx=(0, 4))

        # -- Refresh rate (two cmd_ids, not a value byte; applies on click) --
        row = ttk.Frame(frame)
        row.pack(fill="x", padx=8, pady=6)
        ttk.Label(row, text="Refresh rate (0x20/0x21)", width=20).pack(side="left")
        self.refresh_rate_var = tk.IntVar(value=60)
        for hz in (60, 120):
            ttk.Radiobutton(
                row, text=f"{hz}Hz", value=hz, variable=self.refresh_rate_var, command=self._set_refresh_rate,
            ).pack(side="left", padx=4)

        # -- Audio effect [OSD: Standard/Whisper/Surround] -- applies on select
        row = ttk.Frame(frame)
        row.pack(fill="x", padx=8, pady=6)
        ttk.Label(row, text="Audio effect (0x49)\n[Standard/Whisper/Surround]", width=20, justify="left").pack(
            side="left"
        )
        self.audio_mode_var = tk.StringVar(value="Standard")
        audio_combo = ttk.Combobox(
            row, textvariable=self.audio_mode_var, state="readonly", width=12,
            values=["Standard", "Whisper", "Surround"],
        )
        audio_combo.pack(side="left", padx=4)
        audio_combo.bind("<<ComboboxSelected>>", lambda e: self._set_audio_mode())

        # -- Sound Tube [OSD: Off/On] -- applies on toggle --------------------
        row = ttk.Frame(frame)
        row.pack(fill="x", padx=8, pady=6)
        ttk.Label(row, text="Sound Tube (0x48)\n[Off/On]", width=20, justify="left").pack(side="left")
        self.audio_tube_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(row, text="on", variable=self.audio_tube_var, command=self._set_audio_tube).pack(
            side="left", padx=4
        )

        # -- Picture mode + Color enhancement [OSD, both via 0x73] -- applies
        # on either changing ---------------------------------------------
        row = ttk.Frame(frame)
        row.pack(fill="x", padx=8, pady=6)
        ttk.Label(row, text="Picture mode (0x73)\n[Standard/Movie/Eye Comfort]", width=20, justify="left").pack(
            side="left"
        )
        self.picture_mode_name_var = tk.StringVar(value="Standard")
        picture_combo = ttk.Combobox(
            row, textvariable=self.picture_mode_name_var, state="readonly", width=12,
            values=["Standard", "Movie", "Eye Comfort"],
        )
        picture_combo.pack(side="left", padx=4)
        picture_combo.bind("<<ComboboxSelected>>", lambda e: self._set_picture_mode_friendly())
        self.color_enhance_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            row, text="color enhancement", variable=self.color_enhance_var,
            command=self._set_picture_mode_friendly,
        ).pack(side="left", padx=8)

        ttk.Separator(frame, orient="horizontal").pack(fill="x", padx=8, pady=10)
        ttk.Label(
            frame,
            text="Reboot to DFU (0x66): software bootloader entry, no button-hold "
                 "needed. The glasses will drop off this HID interface immediately.",
            wraplength=560, foreground="#b00000", justify="left",
        ).pack(padx=8, pady=(0, 6), anchor="w")
        ttk.Button(frame, text="Reboot to DFU", command=self._reboot_dfu).pack(padx=8, pady=(0, 8), anchor="w")

    def _on_brightness_change(self, value: str) -> None:
        level = int(float(value))
        self.brightness_label.configure(text=str(level))
        # debounced: only the last value in a burst of drag events actually
        # gets sent, ~120ms after dragging stops -- not one send per pixel.
        self._debounce("brightness", 120, lambda: self._send_brightness(level))

    def _send_brightness(self, level: int) -> None:
        def work() -> bytes:
            with RayNeoDevice() as dev:
                return dev.set_brightness(level)

        def done(resp: bytes) -> None:
            self._append_log(f"brightness -> {level} ack: {resp.hex()}")

        self._run_async(work, done)

    def _save_brightness(self) -> None:
        self._log("saving brightness (0x0D)...")

        def work() -> bytes:
            with RayNeoDevice() as dev:
                return dev.save_brightness()

        def done(resp: bytes) -> None:
            self._append_log(f"brightness saved ack: {resp.hex()}")

        self._run_async(work, done)

    def _on_volume_change(self, value: str) -> None:
        level = int(float(value))
        self.volume_label.configure(text=str(level))
        self._debounce("volume", 120, lambda: self._send_volume(level))

    def _send_volume(self, level: int) -> None:
        def work() -> bytes:
            with RayNeoDevice() as dev:
                return dev.set_volume(level)

        def done(resp: bytes) -> None:
            self._append_log(f"volume -> {level} ack: {resp.hex()}")

        self._run_async(work, done)

    def _set_refresh_rate(self) -> None:
        hz = self.refresh_rate_var.get()
        self._log(f"setting refresh rate -> {hz}Hz...")

        def work() -> bytes:
            with RayNeoDevice() as dev:
                return dev.set_refresh_rate(hz)

        def done(resp: bytes) -> None:
            self._append_log(f"refresh rate -> {hz}Hz ack: {resp.hex()}")

        self._run_async(work, done)

    _AUDIO_MODE_VALUES = {"Standard": 0, "Whisper": 1, "Surround": 2}

    def _set_audio_mode(self) -> None:
        name = self.audio_mode_var.get()
        mode = self._AUDIO_MODE_VALUES[name]
        self._log(f"setting audio effect -> {name} ({mode})...")

        def work() -> bytes:
            with RayNeoDevice() as dev:
                return dev.set_audio_mode(mode)

        def done(resp: bytes) -> None:
            self._append_log(f"audio effect -> {name} ack: {resp.hex()}")

        self._run_async(work, done)

    def _set_audio_tube(self) -> None:
        on = self.audio_tube_var.get()
        self._log(f"setting sound tube -> {'on' if on else 'off'}...")

        def work() -> bytes:
            with RayNeoDevice() as dev:
                return dev.set_audio_tube_mode(on)

        def done(resp: bytes) -> None:
            self._append_log(f"sound tube -> {'on' if on else 'off'} ack: {resp.hex()}")

        self._run_async(work, done)

    _PICTURE_MODE_VALUES = {"Standard": 0, "Movie": 1, "Eye Comfort": 2}

    def _set_picture_mode_friendly(self) -> None:
        name = self.picture_mode_name_var.get()
        mode = self._PICTURE_MODE_VALUES[name]
        enhance = 1 if self.color_enhance_var.get() else 0
        self._log(f"setting picture mode -> {name} ({mode}), color enhancement={enhance}...")

        def work() -> bytes:
            with RayNeoDevice() as dev:
                return dev.set_picture_mode(mode, enhance, 0)

        def done(resp: bytes) -> None:
            self._append_log(f"picture mode -> {name}, enhancement={enhance} ack: {resp.hex()}")

        self._run_async(work, done)

    def _reboot_dfu(self) -> None:
        self._log("sending REBOOT_TO_BOOTLOADER...")

        def work() -> bytes:
            with RayNeoDevice() as dev:
                return dev.reboot_to_bootloader()

        def done(resp: bytes) -> None:
            self._append_log(f"reboot-to-dfu ack: {resp.hex()} (glasses should now be in DFU mode)")

        self._run_async(work, done)

    # -- Experimental tab ------------------------------------------------
    def _build_experimental_tab(self, nb: ttk.Notebook) -> None:
        frame = ttk.Frame(nb)
        nb.add(frame, text="Experimental")
        ttk.Label(
            frame,
            text="EXPERIMENTAL: dispatch table slot located, callee not fully "
                 "resolved. The handler returns -1 (0xFFFFFFFF) when the "
                 "underlying driver object is null -- that means \"did "
                 "nothing\", not \"success\". Valid index ranges are unknown.",
            wraplength=560, foreground="#b00000", justify="left",
        ).pack(padx=8, pady=(12, 8), anchor="w")

        for label, method in [("Gamma index (0x6D)", "set_gamma_index"), ("Gamut mode (0x6E)", "set_gamut_mode")]:
            row = ttk.Frame(frame)
            row.pack(fill="x", padx=8, pady=6)
            ttk.Label(row, text=label, width=20).pack(side="left")
            var = tk.StringVar(value="0")
            ttk.Entry(row, textvariable=var, width=6).pack(side="left", padx=4)
            ttk.Button(row, text="Send", command=lambda m=method, v=var: self._send_experimental(m, v)).pack(
                side="left", padx=4
            )

        ttk.Separator(frame, orient="horizontal").pack(fill="x", padx=8, pady=10)
        row = ttk.Frame(frame)
        row.pack(fill="x", padx=8, pady=6)
        ttk.Label(row, text="Picture mode (0x73)", width=20).pack(side="left")
        self.picture_mode_var = tk.StringVar(value="0")
        self.picture_p1_var = tk.StringVar(value="0")
        self.picture_p2_var = tk.StringVar(value="0")
        for label, var in [("mode", self.picture_mode_var), ("p1", self.picture_p1_var), ("p2", self.picture_p2_var)]:
            ttk.Label(row, text=label).pack(side="left", padx=(6, 2))
            ttk.Entry(row, textvariable=var, width=5).pack(side="left")
        ttk.Button(row, text="Send", command=self._send_picture_mode).pack(side="left", padx=6)
        ttk.Label(
            frame,
            text="3-byte payload -- the wire layout (which of mode/p1/p2 lands "
                 "where in the frame) is a best guess, not confirmed.",
            wraplength=560, foreground="#666", justify="left",
        ).pack(padx=8, anchor="w")

    def _send_picture_mode(self) -> None:
        try:
            mode = int(self.picture_mode_var.get(), 0)
            p1 = int(self.picture_p1_var.get(), 0)
            p2 = int(self.picture_p2_var.get(), 0)
        except ValueError:
            self._append_log("invalid mode/p1/p2 -- use integers")
            return
        self._log(f"set_picture_mode({mode}, {p1}, {p2})...")

        def work() -> bytes:
            with RayNeoDevice() as dev:
                return dev.set_picture_mode(mode, p1, p2)

        def done(resp: bytes) -> None:
            self._append_log(f"picture_mode({mode},{p1},{p2}) -> {resp.hex()}")

        self._run_async(work, done)

    def _send_experimental(self, method_name: str, var: tk.StringVar) -> None:
        try:
            idx = int(var.get(), 0)
        except ValueError:
            self._append_log(f"invalid index: {var.get()!r}")
            return
        self._log(f"{method_name}({idx})...")

        def work() -> bytes:
            with RayNeoDevice() as dev:
                return getattr(dev, method_name)(idx)

        def done(resp: bytes) -> None:
            self._append_log(f"{method_name}({idx}) -> {resp.hex()}")

        self._run_async(work, done)

    # -- Raw tab -----------------------------------------------------------
    def _build_raw_tab(self, nb: ttk.Notebook) -> None:
        frame = ttk.Frame(nb)
        nb.add(frame, text="Raw")
        ttk.Label(
            frame,
            text="Sends an arbitrary short-form (0x66) command. Still only the "
                 "runtime HID channel -- there's no DFU/write path behind this, "
                 "same boundary as the CLI's `rayneo raw`.",
            wraplength=560, justify="left",
        ).pack(padx=8, pady=(12, 8), anchor="w")

        row = ttk.Frame(frame)
        row.pack(padx=8, pady=6)
        self.raw_cmd = tk.StringVar(value="0x1A")
        self.raw_val = tk.StringVar(value="0x00")
        self.raw_extra = tk.StringVar(value=hex(protocol.DEFAULT_EXTRA))
        for label, var in [("cmd", self.raw_cmd), ("val", self.raw_val), ("extra", self.raw_extra)]:
            ttk.Label(row, text=label).pack(side="left", padx=(8, 2))
            ttk.Entry(row, textvariable=var, width=8).pack(side="left")
        ttk.Button(frame, text="Send raw command", command=self._send_raw).pack(pady=10)

    def _send_raw(self) -> None:
        try:
            cmd_id = int(self.raw_cmd.get(), 0)
            val = int(self.raw_val.get(), 0)
            extra = int(self.raw_extra.get(), 0)
        except ValueError:
            self._append_log("invalid cmd/val/extra -- use hex like 0x1A")
            return
        info = COMMANDS.get(cmd_id)
        if info:
            name, confidence, notes = info
            self._append_log(f"# {name} ({confidence.value}): {notes}")
        else:
            self._append_log(f"# 0x{cmd_id:02x} not in known table -- sending blind")

        def work() -> Optional[bytes]:
            with RayNeoDevice() as dev:
                return dev.send(cmd_id, val, extra)

        def done(resp: Optional[bytes]) -> None:
            self._append_log(f"-> {resp.hex() if resp else '<no response>'}")

        self._run_async(work, done)

    # -- Scan tab ------------------------------------------------------------
    def _build_scan_tab(self, nb: ttk.Notebook) -> None:
        frame = ttk.Frame(nb)
        nb.add(frame, text="Scan")
        ttk.Label(
            frame,
            text="Walks a command-id range live with val=0 and groups by "
                 "response, so real handlers stand out from the shared "
                 "'unimplemented' stub cluster. Can take a little while.",
            wraplength=560, justify="left",
        ).pack(padx=8, pady=(12, 8), anchor="w")

        row = ttk.Frame(frame)
        row.pack(pady=6)
        self.scan_start = tk.StringVar(value="0x00")
        self.scan_end = tk.StringVar(value=hex(MAX_COMMAND_ID))
        ttk.Label(row, text="start").pack(side="left", padx=(8, 2))
        ttk.Entry(row, textvariable=self.scan_start, width=8).pack(side="left")
        ttk.Label(row, text="end").pack(side="left", padx=(8, 2))
        ttk.Entry(row, textvariable=self.scan_end, width=8).pack(side="left")
        self.scan_btn = ttk.Button(frame, text="Scan", command=self._start_scan)
        self.scan_btn.pack(pady=8)

    def _start_scan(self) -> None:
        try:
            start = int(self.scan_start.get(), 0)
            end = int(self.scan_end.get(), 0)
        except ValueError:
            self._append_log("invalid start/end -- use hex like 0x00")
            return
        self.scan_btn.configure(state="disabled")
        self._log(f"scanning 0x{start:02x}..0x{end:02x}...")

        def work() -> dict:
            seen: dict[str, list[int]] = {}
            with RayNeoDevice() as dev:
                for cmd_id in range(start, end + 1):
                    try:
                        resp = dev.send(cmd_id, 0x00, protocol.DEFAULT_EXTRA)
                    except Exception as e:  # noqa: BLE001
                        self._log(f"0x{cmd_id:02x}: error {e}")
                        continue
                    key = resp.hex() if resp else "<no response>"
                    seen.setdefault(key, []).append(cmd_id)
                    known = COMMANDS.get(cmd_id)
                    tag = f" [{known[0]}]" if known else ""
                    self._log(f"0x{cmd_id:02x}{tag}: {key}")
            return seen

        def done(seen: dict) -> None:
            self._append_log("\n-- grouped by response --")
            for resp_hex, ids in sorted(seen.items(), key=lambda kv: -len(kv[1])):
                id_list = ", ".join(f"0x{i:02x}" for i in ids)
                self._append_log(f"{resp_hex}  <- {len(ids)} ids: {id_list}")
            self.scan_btn.configure(state="normal")

        self._run_async(work, done)

    # -- tray ------------------------------------------------------------
    def _start_tray(self) -> None:
        image = _make_icon_image()
        menu = pystray.Menu(
            pystray.MenuItem("Show", self._tray_show, default=True),
            pystray.MenuItem("SDR", lambda: self._set_mode(DisplayMode.SDR, "SDR")),
            pystray.MenuItem("AI-HDR", lambda: self._set_mode(DisplayMode.AI_HDR, "AI-HDR")),
            pystray.MenuItem("HDR10", lambda: self._set_mode(DisplayMode.HDR10, "HDR10")),
            pystray.MenuItem("Quit", self._tray_quit),
        )
        self._tray = pystray.Icon(APP_TITLE, image, APP_TITLE, menu)
        threading.Thread(target=self._tray.run, daemon=True).start()

    def _tray_show(self, *_: object) -> None:
        # runs on the tray's own thread -- marshal into the Tk main loop
        self._work_q.put(self.root.deiconify)

    def _tray_quit(self, *_: object) -> None:
        if self._tray is not None:
            self._tray.stop()
        self._work_q.put(self.root.destroy)

    def _on_close(self) -> None:
        if _HAVE_TRAY and self._tray is not None:
            self.root.withdraw()
        else:
            self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


def main() -> None:
    RayNeoGUI().run()


if __name__ == "__main__":
    main()
