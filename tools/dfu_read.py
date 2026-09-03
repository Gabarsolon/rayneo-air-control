#!/usr/bin/env python3
"""dfu_read.py -- READ-ONLY DfuSe flash dumper.

This talks to a device sitting in USB DFU mode and reads flash back with
DFU_UPLOAD. It does not implement DFU_DNLOAD-with-firmware-data at all --
the only DNLOAD requests it ever sends are the two mandatory DfuSe
*special commands* needed to set up a read (SET_ADDRESS_POINTER and, for
scan mode, GET_COMMAND), never a data payload. There is no code path in
this file that can write firmware. That's structural, not a flag.

Usage:
    python dfu_read.py --list                         # find DFU-class interfaces
    python dfu_read.py --vid 0x1bbb --pid 0xdf11 \
        --start 0x08000000 --length 0xC000 --out lowregion.bin
    python dfu_read.py --vid 0x0483 --pid 0xdf11 --scan-header

If --vid/--pid aren't known yet, run --list first with the device in DFU
mode; it walks every USB device looking for a DFU-class interface
(bInterfaceClass 0xFE, bInterfaceSubClass 0x01) regardless of VID/PID.
"""
from __future__ import annotations

import argparse
import glob
import os
import struct
import sys
import time

import usb.core
import usb.util

# -- libusb backend bootstrap (works from a bare `pip install pyusb libusb`,
#    no system-wide libusb install needed) --------------------------------
def _get_backend():
    import usb.backend.libusb1 as libusb1_backend
    try:
        import libusb
        base = os.path.dirname(libusb.__file__)
        candidates = glob.glob(os.path.join(base, "**", "libusb-1.0.dll"), recursive=True)
        # prefer the DLL matching this Python's actual architecture
        import platform
        arch = "x86_64" if (sys.maxsize > 2**32 and platform.machine().lower() in ("amd64", "x86_64")) else \
               "arm64" if "arm" in platform.machine().lower() else "x86"
        candidates.sort(key=lambda p: 0 if f"\\{arch}\\" in p or f"/{arch}/" in p else 1)
        if candidates:
            dll = candidates[0]
            be = libusb1_backend.get_backend(find_library=lambda x: dll)
            if be is not None:
                return be
    except ImportError:
        pass
    return libusb1_backend.get_backend()


BACKEND = _get_backend()

# -- USB DFU class constants (USB DFU 1.1) ---------------------------------
DFU_CLASS = 0xFE
DFU_SUBCLASS = 0x01

DFU_DETACH = 0
DFU_DNLOAD = 1
DFU_UPLOAD = 2
DFU_GETSTATUS = 3
DFU_CLRSTATUS = 4
DFU_GETSTATE = 5
DFU_ABORT = 6

REQTYPE_OUT = usb.util.build_request_type(
    usb.util.CTRL_OUT, usb.util.CTRL_TYPE_CLASS, usb.util.CTRL_RECIPIENT_INTERFACE
)
REQTYPE_IN = usb.util.build_request_type(
    usb.util.CTRL_IN, usb.util.CTRL_TYPE_CLASS, usb.util.CTRL_RECIPIENT_INTERFACE
)

DFU_STATE_NAMES = {
    0: "appIDLE", 1: "appDETACH", 2: "dfuIDLE", 3: "dfuDNLOAD-SYNC",
    4: "dfuDNBUSY", 5: "dfuDNLOAD-IDLE", 6: "dfuMANIFEST-SYNC",
    7: "dfuMANIFEST", 8: "dfuMANIFEST-WAIT-RESET", 9: "dfuUPLOAD-IDLE",
    10: "dfuERROR",
}


def find_dfu_interfaces():
    """Scan every USB device for a DFU-class interface. Returns a list of
    (device, cfg, intf) tuples. Pure enumeration, no I/O beyond descriptors."""
    found = []
    for dev in usb.core.find(find_all=True, backend=BACKEND):
        try:
            for cfg in dev:
                for intf in cfg:
                    if intf.bInterfaceClass == DFU_CLASS and intf.bInterfaceSubClass == DFU_SUBCLASS:
                        found.append((dev, cfg, intf))
        except (usb.core.USBError, NotImplementedError):
            continue
    return found


def list_dfu_devices():
    hits = find_dfu_interfaces()
    if not hits:
        print("No DFU-class interfaces found. Is the device plugged in and in DFU mode?")
        return
    for dev, cfg, intf in hits:
        try:
            manu = usb.util.get_string(dev, dev.iManufacturer) if dev.iManufacturer else "?"
            prod = usb.util.get_string(dev, dev.iProduct) if dev.iProduct else "?"
        except Exception:
            manu = prod = "<no string descriptor / not claimed>"
        print(f"VID=0x{dev.idVendor:04x} PID=0x{dev.idProduct:04x}  "
              f"intf={intf.bInterfaceNumber} alt={intf.bAlternateSetting}  "
              f"{manu} / {prod}")


class DfuDevice:
    def __init__(self, vid: int, pid: int, intf_num: int | None = None):
        dev = usb.core.find(idVendor=vid, idProduct=pid, backend=BACKEND)
        if dev is None:
            raise SystemExit(f"no device {vid:04x}:{pid:04x} found")
        self.dev = dev
        # find the DFU interface + its functional descriptor's wTransferSize
        self.intf_num = intf_num
        self.xfer_size = 2048  # sane default, overwritten below if we find the real one
        for cfg in dev:
            for intf in cfg:
                if intf.bInterfaceClass == DFU_CLASS and intf.bInterfaceSubClass == DFU_SUBCLASS:
                    if self.intf_num is None:
                        self.intf_num = intf.bInterfaceNumber
                    if intf.bInterfaceNumber == self.intf_num:
                        self._parse_functional_descriptor(intf)
        if self.intf_num is None:
            raise SystemExit("no DFU interface found on that VID:PID")
        try:
            if dev.is_kernel_driver_active(self.intf_num):
                dev.detach_kernel_driver(self.intf_num)
        except (NotImplementedError, usb.core.USBError):
            pass
        dev.set_configuration()
        usb.util.claim_interface(dev, self.intf_num)

    def _parse_functional_descriptor(self, intf):
        extra = bytes(intf.extra_descriptors) if intf.extra_descriptors else b""
        i = 0
        while i + 1 < len(extra):
            blen, btype = extra[i], extra[i + 1]
            if blen == 0:
                break
            if btype == 0x21 and blen >= 9:  # DFU functional descriptor
                # layout: bLength,bDescriptorType,bmAttributes,wDetachTimeout(2),wTransferSize(2),bcdDFUVersion(2)
                self.xfer_size = struct.unpack_from("<H", extra, i + 5)[0]
            i += blen

    # -- low-level DFU control requests -----------------------------------
    def _dnload_command(self, data: bytes):
        """Send a DfuSe *special command* via DNLOAD block 0. `data` is the
        command block (e.g. b'\\x21' + addr for Set Address Pointer). This
        is NOT firmware data -- it is the DfuSe control-command mechanism."""
        self.dev.ctrl_transfer(REQTYPE_OUT, DFU_DNLOAD, 0, self.intf_num, data)
        self._wait_idle()

    def _getstatus(self):
        resp = self.dev.ctrl_transfer(REQTYPE_IN, DFU_GETSTATUS, 0, self.intf_num, 6)
        status = resp[0]
        poll_ms = resp[1] | (resp[2] << 8) | (resp[3] << 16)
        state = resp[4]
        return status, poll_ms, state

    def _wait_idle(self):
        for _ in range(50):
            status, poll_ms, state = self._getstatus()
            if state in (2, 5, 9):  # dfuIDLE / dfuDNLOAD-IDLE / dfuUPLOAD-IDLE
                return state
            if state == 10:  # dfuERROR
                raise RuntimeError(f"device in dfuERROR (status=0x{status:02x}); clearing")
            time.sleep(max(poll_ms, 1) / 1000.0)
        raise TimeoutError("device never returned to an idle DFU state")

    def clear_status(self):
        self.dev.ctrl_transfer(REQTYPE_OUT, DFU_CLRSTATUS, 0, self.intf_num, None)

    def set_address_pointer(self, addr: int):
        cmd = bytes([0x21]) + struct.pack("<I", addr)
        self._dnload_command(cmd)

    def abort(self):
        self.dev.ctrl_transfer(REQTYPE_OUT, DFU_ABORT, 0, self.intf_num, None)
        self._wait_idle()

    # -- the only data-moving operation: reading ----------------------------
    def upload_range(self, addr: int, length: int) -> bytes:
        """Read `length` bytes starting at `addr` via DFU_UPLOAD. Read-only."""
        self.set_address_pointer(addr)
        self.abort()  # DfuSe quirk: abort after set-address re-arms upload at that address
        out = bytearray()
        block = 2  # DfuSe upload block numbering starts at 2 for the address just set
        remaining = length
        while remaining > 0:
            chunk_len = min(self.xfer_size, remaining)
            data = self.dev.ctrl_transfer(REQTYPE_IN, DFU_UPLOAD, block, self.intf_num, chunk_len)
            data = bytes(data)
            if not data:
                break
            out.extend(data)
            remaining -= len(data)
            block += 1
            if len(data) < chunk_len:
                break  # short read == end of readable region
        return bytes(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--list", action="store_true", help="list DFU-class interfaces on any VID/PID and exit")
    ap.add_argument("--vid", type=lambda s: int(s, 0))
    ap.add_argument("--pid", type=lambda s: int(s, 0))
    ap.add_argument("--start", type=lambda s: int(s, 0), default=0x08000000)
    ap.add_argument("--length", type=lambda s: int(s, 0), default=0xC000,
                     help="default 0xC000 = the 48KB bootloader gap below the app image")
    ap.add_argument("--out", default="dump.bin")
    args = ap.parse_args(argv)

    if args.list or not (args.vid and args.pid):
        list_dfu_devices()
        if not (args.vid and args.pid):
            return 0

    d = DfuDevice(args.vid, args.pid)
    print(f"transfer size (from functional descriptor): {d.xfer_size} bytes")
    print(f"reading 0x{args.start:08X} .. 0x{args.start+args.length:08X} ({args.length} bytes)")
    data = d.upload_range(args.start, args.length)
    with open(args.out, "wb") as f:
        f.write(data)
    print(f"got {len(data)} bytes -> {args.out}")
    if len(data) < args.length:
        print("note: short read -- region may be read-protected past this point, or shorter than requested")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
