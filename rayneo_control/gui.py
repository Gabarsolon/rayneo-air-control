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
        self._build_mode_tab(nb)
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
    def _build_mode_tab(self, nb: ttk.Notebook) -> None:
        frame = ttk.Frame(nb)
        nb.add(frame, text="Display mode")
        ttk.Label(
            frame,
            text="Only SDR (mode 0) has been confirmed live -- AI-HDR and HDR10 "
                 "are inferred from the disassembly. Check the glasses after "
                 "switching.",
            wraplength=560, foreground="#a06000", justify="left",
        ).pack(pady=(12, 8), padx=8, anchor="w")
        btns = ttk.Frame(frame)
        btns.pack(pady=8)
        for name, mode in [("SDR", DisplayMode.SDR), ("AI-HDR", DisplayMode.AI_HDR), ("HDR10", DisplayMode.HDR10)]:
            ttk.Button(btns, text=name, command=lambda m=mode, n=name: self._set_mode(m, n)).pack(
                side="left", padx=8
            )

    def _set_mode(self, mode: DisplayMode, name: str) -> None:
        self._log(f"setting display mode -> {name}...")

        def work() -> bytes:
            with RayNeoDevice() as dev:
                return dev.set_display_mode(mode)

        def done(resp: bytes) -> None:
            self._append_log(f"mode -> {name} ack: {resp.hex()}")

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
