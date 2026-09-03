"""rayneo — command-line control for RayNeo Air 4 Pro (taurus4p0) glasses.

    rayneo status
    rayneo mode sdr | ai-hdr | hdr10
    rayneo brightness <level> [--save]
    rayneo volume <level>
    rayneo reboot-dfu
    rayneo raw --cmd 0x1A --val 0x00 [--extra 0x56]
    rayneo scan [--start 0x00] [--end 0xA4]

This never touches DFU / firmware. It only speaks the runtime HID
control protocol the OEM app uses. See README.md for the protocol
writeup and per-command confidence ratings.
"""

from __future__ import annotations

import argparse
import sys

from . import protocol
from .commands import COMMANDS, Command, Confidence, DisplayMode, MAX_COMMAND_ID
from .device import RayNeoDevice, RayNeoNotFound

_MODE_NAMES = {
    "sdr": DisplayMode.SDR,
    "ai-hdr": DisplayMode.AI_HDR,
    "hdr10": DisplayMode.HDR10,
}


def _fmt_hex(b: bytes | None) -> str:
    return b.hex() if b else "<no response>"


def cmd_status(args: argparse.Namespace) -> int:
    with RayNeoDevice() as dev:
        st = dev.get_status()
    print(f"build date   : {st.build_date!r}")
    print(f"uptime ticks : {st.uptime_ticks}")
    print(f"refresh rate : {st.refresh_hz or 'unknown'} Hz")
    if args.verbose:
        print(f"raw          : {st.raw.hex()}")
    return 0


def cmd_mode(args: argparse.Namespace) -> int:
    mode = _MODE_NAMES[args.mode]
    with RayNeoDevice() as dev:
        resp = dev.set_display_mode(mode)
    print(f"set display mode -> {args.mode} (0x{int(mode):02x})")
    print(f"ack: {resp.hex()}")
    if mode != DisplayMode.SDR:
        print(
            "note: only SDR (mode 0) has been confirmed live in this project so far -- "
            "check the display and run `rayneo status` / look at the glasses to confirm this did what you expect."
        )
    return 0


def cmd_brightness(args: argparse.Namespace) -> int:
    with RayNeoDevice() as dev:
        resp = dev.set_brightness(args.level)
        if args.save:
            dev.save_brightness()
    print(f"set brightness -> {args.level} (0x{args.level:02x}){'  (saved)' if args.save else ''}")
    print(f"ack: {resp.hex()}")
    print("note: TRACED, not CONFIRMED -- the OEM app maps a UI index through a "
          "lookup table before sending this value; watch the glasses to see what it actually did.")
    return 0


def cmd_volume(args: argparse.Namespace) -> int:
    with RayNeoDevice() as dev:
        resp = dev.set_volume(args.level)
    print(f"set volume -> {args.level} (0x{args.level:02x})")
    print(f"ack: {resp.hex()}")
    print("note: TRACED, not CONFIRMED -- verify by ear.")
    return 0


def cmd_reboot_dfu(args: argparse.Namespace) -> int:
    with RayNeoDevice() as dev:
        resp = dev.reboot_to_bootloader()
    print("sent REBOOT_TO_BOOTLOADER -- glasses should drop into DFU mode without a button hold")
    print(f"ack: {resp.hex()}")
    return 0


def cmd_raw(args: argparse.Namespace) -> int:
    cmd_id = int(args.cmd, 0)
    val = int(args.val, 0)
    extra = int(args.extra, 0)
    info = COMMANDS.get(cmd_id)
    if info:
        name, confidence, notes = info
        print(f"# {name} ({confidence.value}): {notes}")
    else:
        print(f"# 0x{cmd_id:02x} is not in the known command table -- sending blind.")
    with RayNeoDevice() as dev:
        resp = dev.send(cmd_id, val, extra)
    print(f"-> {_fmt_hex(resp)}")
    return 0


def cmd_scan(args: argparse.Namespace) -> int:
    """Walk a command-ID range live, val=0, and log every distinct response.
    Useful for telling real handlers apart from the shared 'unimplemented'
    stub -- unimplemented slots tend to produce one identical short response
    across many IDs; a real handler usually stands out."""
    start, end = int(args.start, 0), int(args.end, 0)
    seen = {}
    with RayNeoDevice() as dev:
        for cmd_id in range(start, end + 1):
            try:
                resp = dev.send(cmd_id, 0x00, protocol.DEFAULT_EXTRA)
            except Exception as e:
                print(f"0x{cmd_id:02x}: error {e}")
                continue
            key = _fmt_hex(resp)
            seen.setdefault(key, []).append(cmd_id)
            known = COMMANDS.get(cmd_id)
            tag = f" [{known[0]}]" if known else ""
            print(f"0x{cmd_id:02x}{tag}: {key}")
    # group by response so the "unimplemented stub" cluster is obvious
    print("\n-- grouped by response --")
    for resp_hex, ids in sorted(seen.items(), key=lambda kv: -len(kv[1])):
        id_list = ", ".join(f"0x{i:02x}" for i in ids)
        print(f"{resp_hex}  <- {len(ids)} ids: {id_list}")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    for cmd_id, (name, confidence, notes) in sorted(COMMANDS.items()):
        print(f"0x{cmd_id:02x}  {name:24s} [{confidence.value:12s}]  {notes}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="rayneo", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("status", help="read confirmed status fields")
    sp.add_argument("-v", "--verbose", action="store_true", help="also print raw response hex")
    sp.set_defaults(func=cmd_status)

    sp = sub.add_parser("mode", help="set display mode")
    sp.add_argument("mode", choices=sorted(_MODE_NAMES))
    sp.set_defaults(func=cmd_mode)

    sp = sub.add_parser("brightness", help="set brightness (TRACED, not CONFIRMED -- see README)")
    sp.add_argument("level", type=lambda x: int(x, 0), help="raw device value, e.g. 0-255")
    sp.add_argument("--save", action="store_true", help="also persist it (cmd 0x0D)")
    sp.set_defaults(func=cmd_brightness)

    sp = sub.add_parser("volume", help="set volume (TRACED, not CONFIRMED -- see README)")
    sp.add_argument("level", type=lambda x: int(x, 0), help="raw device value")
    sp.set_defaults(func=cmd_volume)

    sp = sub.add_parser("reboot-dfu", help="software DFU entry (TRACED, not CONFIRMED -- see README)")
    sp.set_defaults(func=cmd_reboot_dfu)

    sp = sub.add_parser("raw", help="send a raw short-form (0x66) command")
    sp.add_argument("--cmd", required=True, help="command id, e.g. 0x1A")
    sp.add_argument("--val", default="0x00", help="value byte, e.g. 0x02")
    sp.add_argument("--extra", default=hex(protocol.DEFAULT_EXTRA), help="extra byte (default 0x56)")
    sp.set_defaults(func=cmd_raw)

    sp = sub.add_parser("scan", help="probe a command-id range live and log responses")
    sp.add_argument("--start", default="0x00")
    sp.add_argument("--end", default=hex(MAX_COMMAND_ID))
    sp.set_defaults(func=cmd_scan)

    sp = sub.add_parser("list", help="list known commands and their confidence rating")
    sp.set_defaults(func=cmd_list)

    return p


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except RayNeoNotFound as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except (TimeoutError, protocol.MalformedResponse) as e:
        print(f"error: {e}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
