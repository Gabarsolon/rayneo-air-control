# rayneo-control

Runtime USB-HID control for RayNeo Air 4 Pro AR glasses (codename `taurus4p0`),
built from reverse-engineering the STM32 firmware image and confirming
behavior against a real device.

```
pip install -e .
rayneo status
rayneo mode sdr        # sdr | ai-hdr | hdr10
rayneo brightness 128 --save   # traced, not confirmed -- see below
rayneo volume 50               # traced, not confirmed
rayneo reboot-dfu              # software DFU entry, traced, not confirmed
rayneo list             # every known command + how sure we are about it
rayneo raw --cmd 0x1A --val 0x00
rayneo scan             # probe the full command-id range live
```

There's also a Windows tray GUI (`rayneo_control/gui.py`) covering the
same ground as the CLI above — status, mode buttons, and, since it's
meant as a live probing tool rather than a polished consumer app, the
raw-command and scan panels too:

```
pip install -e .[gui]
rayneo-gui
```

It minimizes to the system tray (SDR/AI-HDR/HDR10 available directly
from the tray menu) if `pystray`/`Pillow` are installed, otherwise the
window just quits normally on close. Same boundary as the CLI: it only
calls `RayNeoDevice`, no separate protocol implementation, no DFU path.

**Don't want to install Python?** Grab `RayNeoControl.exe` from the
[latest release](https://github.com/Gabarsolon/rayneo-air-control/releases/latest)
— double-click it, no setup. It's a PyInstaller one-file build of the
exact same GUI above; two processes showing up for it in Task Manager
is normal PyInstaller onefile behavior (an outer bootloader launching
the real one), not two copies running.

To build it yourself instead of trusting a downloaded binary:

```
pip install -e .[gui] pyinstaller
pyinstaller --name RayNeoControl --onefile --windowed --clean run_gui.py
# -> dist/RayNeoControl.exe
```

## Why this exists

The glasses' Pixelworks PX8618 does SDR→HDR "inverse tone mapping" —
brightening/lifting an SDR signal to simulate HDR — whenever display
mode is AI-HDR or HDR10 and the incoming signal isn't actually HDR.
That's a shadow-lift by design, and on a black screen it shows up as
"black looks gray." An existing control tool for these glasses
(`rayneo_control.py`, from an earlier Antigravity/Gemini session) got
partway there but shipped a `--status` decoder that reads mostly
hardcoded constants as if they were live telemetry, and preset name
tables (`--gamma-index`, `--gamut-mode`) that don't correspond to
anything in the firmware. This project starts over with an explicit
confidence rating on every command, verified against the disassembly
and, where possible, a live device.

**Runtime control never writes firmware.** `rayneo_control` itself only
speaks the existing runtime HID channel — the same one the OEM app
uses. Separately, `tools/dfu_write.py` exists as an explicit, clearly
isolated research tool for probing the DFU write path directly against
a real device (see [Investigating the bootloader](#investigating-the-bootloader)
below) — it's opt-in, lives outside the CLI, and every write it has
been used for so far has been rejected by the hardware itself. See
[`docs/write-protection-findings.md`](docs/write-protection-findings.md)
for the full trace.

## Protocol

Device: VID `0x1BBB`, PID `0xAF50`. 65-byte HID reports (byte 0 is the
report-ID hidapi requires on Windows, ignored by the device).

```
[0]      report id (0x00)
[1]      magic: 0x66 short-form | 0x77 long-form | 0x88 raw passthrough
[2]      cmd id            (0x66 form)
[3]      value byte        (0x66 form)
[4..]    "extra" byte (always 0x56 in every confirmed send) + padding
```

Responses start with header bytes `99 C8 40 00` regardless of command.

This was recovered by disassembling the packet framer at firmware
address `0x08010C84` and the 165-entry command dispatch table at
`0x08012414` (~72 of the 165 slots point at a real handler; the rest
share one "unimplemented" stub at `0x080123A7`). Framing details live in
[`rayneo_control/protocol.py`](rayneo_control/protocol.py); per-command
notes live in [`rayneo_control/commands.py`](rayneo_control/commands.py).

### Confidence ratings

| Rating | Meaning |
|---|---|
| `confirmed` | Sent to a real device; response/behavior matched prediction |
| `traced` | Handler fully disassembled; not exercised live yet |
| `experimental` | Dispatch located (e.g. a vtable slot); callee not resolved |

| cmd | name | confidence | notes |
|---|---|---|---|
| `0x00` | STATUS | confirmed | see caveat below — most fields are fake |
| `0x09` | BRIGHTNESS | traced | see below — value scale not confirmed |
| `0x0D` | BRIGHTNESS_SAVE | traced | no value byte; persists last BRIGHTNESS |
| `0x0E` | PANEL_POWER_ON | traced | no value byte |
| `0x0F` | PANEL_POWER_OFF | traced | no value byte |
| `0x12` | PANEL_POWER_SWAP | traced | no value byte |
| `0x1A` | DISPLAY_MODE | confirmed | `0`=SDR, `1`=AI-HDR, `2`=HDR10; persisted to NVM |
| `0x29`/`0x54` | PANEL_HDR10_CHANGE | traced | `hid_call_panel_hdr10_change`, value semantics unresolved |
| `0x48` | AUDIO_TUBE_MODE | traced | audio routing, not display |
| `0x49` | AUDIO_MODE | traced | adjacent to 0x48, distinct handler |
| `0x50` | VOLUME | confirmed | val=0..12 (13 OSD levels, not a percentage) |
| `0x66` | REBOOT_TO_BOOTLOADER | traced | software DFU entry, no button-hold |
| `0x6D` | GAMMA_INDEX | experimental | indirects through `*(0x2005A090)+0x30`; returns -1 if null |
| `0x6E` | GAMUT_MODE | experimental | indirects through `*(0x2005A090)+0x34`; returns -1 if null |
| `0x73` | PICTURE_MODE | experimental | 3-byte payload; wire layout not confirmed |

Brightness, volume, audio mode, and software DFU entry were all
**unmapped from the STM32 image alone** — that stayed the most valuable
open question for a while. They were finally recovered from the
*other* side of the wire: disassembling the RayNeo Android app's
native SDK (`libFFalconXRServer.so`, class `ffalcon::XRService`) shows
every one of `PanelLunaSet`/`SetAudioVolume`/`SetAudioMode`/
`RebootAndBootloader`/etc. funneling through one shared
`Send(this, cmd_id, value, extra_ptr, extra_len)` call, with the
cmd_id literal each one passes matching straight into this same
0x00–0xA4 numbering space. None of the above have been exercised live
from this project yet — that's the natural next step with a real
device in hand, and each one should move from `traced`/`experimental`
to `confirmed` (or get corrected) once it has been.

### The STATUS command caveat

The prior tool's `--status` output (`brightness`, `volume`, `mode_raw`,
`wear_sensor`) is not real. Disassembling the status builder at
`0x08010EF0` shows:

- `resp[0x24..0x27]` is the **hardcoded literal** `0x0A01001A`
- `resp[0x34..0x37]` and `resp[0x38..0x3B]` are two **hardcoded float
  `6.0`** values, not volume/mode bytes
- `resp[0x3E]` is a **hardcoded `1`**, not a worn-sensor read
- current display mode is **not present** in the status response at all

`rayneo_control.protocol.parse_status()` only exposes the fields that
are actually live: uptime ticks, build-date string, and refresh rate
(`0x3C`/`0x5A`/`0x78` → 60/90/120 Hz).

## The gray-black diagnosis (context for why display mode matters)

Live NVAPI query on this setup showed Windows HDR was **off** the whole
time, wire signal RGB / VESA full-range SDR, glasses as sole attached
display at 1920x1080@120. Every "HDR" toggle in earlier testing was
therefore the glasses' own `px8618_sdr2hdr_hdr_set`/`_load` inverse
tone-mapper running on a plain SDR desktop — which lifts shadows by
construction. `rayneo mode sdr` takes that tone-mapper out of the loop.

Separately, the LT7911UXC bridge carries 7 EDIDs (swapped per display
mode) with real defects: `QS=0` (RGB-quantization-range-not-selectable)
plus a native CE VIC-16 timing and an HDMI 1.4 VSDB on several of them —
the classic "GPU treats me as a TV and defaults to limited range"
combination — and the HDR variants declare `MaxLuminance=1600 nits` /
`MaxFALL=800 nits` against a real panel closer to 600, so any real PQ
content that does arrive gets tone-mapped for a display much brighter
than what's in front of your eyes. None of that is fixed by this tool —
fixing it safely would mean a Windows-side EDID override, never a
firmware rewrite — but it's documented here since it's load-bearing
context for anyone continuing this investigation.

The raised black floor also traces to concrete static data: the RAM
LUT buffer at `0x200074EC` is populated by nothing more exotic than
the standard C-runtime `.data` startup copy in `Reset_Handler`
(`0x08015470`), sourced from flash at `0x0805F79C` — six `0x5B40`-byte
blocks (one per PX8618 mode) of structured, non-monotonic 32-bit
entries consistent with baked-in PQ/HDR10 tone-mapping coefficients,
not a simple gamma ramp. It's static per firmware build, which matches
the observation that the gray floor persisted across firmware
versions. Interpreting the entries further needs Pixelworks PX8618
vendor documentation, which isn't available.

## Continuing the reverse engineering

`rayneo scan` sends `val=0` to every command ID in `0x00..0xA4` and
groups responses so the "unimplemented stub" cluster (all identical) is
easy to tell apart from real handlers (their own distinct response).
Anything that stands out from the cluster and isn't in
`commands.COMMANDS` yet is worth a disassembly pass — PRs welcome.

The other productive direction, which is how brightness/volume/audio
mode/DFU-reboot got found at all: cross-reference the **Android app**
instead of the STM32 image. Decompiling the RayNeo Android app (jadx)
and reading its native SDK's exported JNI symbols (`FxrApi` in the
Java layer, `ffalcon::XRService` in `libFFalconXRServer.so`) turns up
named, human-readable methods for functionality the STM32 dispatch
table alone gives you only as an anonymous handler address. Every
`Panel*`/`SetAudio*`/`Reboot*` method there ends up calling the same
shared `Send(this, cmd_id, value, ...)` primitive, and the cmd_id
literal each one passes is directly usable here.

## Investigating the bootloader

The DFU package for this device only ever covers the application region
(`0x0800C000` onward) — the low 48KB (`0x08000000`–`0x0800C000`) isn't in
the update file at all, which is a strong sign it's a separate,
first-stage bootloader region. What that bootloader actually validates
before jumping to the app (a CRC? a signature? nothing?) determines the
real risk of ever flashing a patched image, and it can't be answered from
the application dump alone — that code simply isn't in the file.

`tools/dfu_read.py` reads it back directly, live, over the standard USB
DFU protocol (`DFU_UPLOAD`), with the device in DFU mode:

```
pip install -r tools/requirements-dfu.txt
python tools/dfu_read.py --list                     # find the DFU interface's VID:PID
python tools/dfu_read.py --vid 0x.... --pid 0x.... \
    --start 0x08000000 --length 0xC000 --out bootloader.bin
```

This tool has no code path that can send firmware data to the device —
the only `DFU_DNLOAD` requests it issues are the two mandatory DfuSe
control commands needed to set up a read (`Set Address Pointer`,
`Abort`), never a data payload. It's read-only by construction, not by
promise.

### DFU write-path research

`tools/dfu_write.py` adds the erase/program primitives on top of
`DfuDevice` — an explicit, separate opt-in for probing the write path
against a real device. Every write attempt made with it so far has
been cleanly rejected by the device (`errWRITE`, DFU status `0x03`),
while erase and read both succeed. Disassembling the bootloader traced
this to a real hardware condition, not a bug in this tool or a
software policy check we're missing a handshake for:

- The unlock/lock sequence (`0x0800339C`/`0x080033B8`) is completely
  standard STM32 FPEC code — KEY1 (`0x45670123`) written directly,
  KEY2 (`0xCDEF89AB`) computed as `KEY1 + (-0x77777778)` rather than
  stored as a literal.
- A live read of the FLASH peripheral registers (`0x40022000`+, the
  same range the bootloader's own code dereferences, so safe to read)
  shows the option register's low byte as `0x01` — since only `0xAA`
  (disabled) and `0xCC` (level 2) are the documented "safe" values,
  any other byte means **Read-Out Protection Level 1 is active**.
- The programming routine (`0x08003338`, called via `0x08003430`)
  checks `FLASH_SR` after every write and returns failure whenever the
  `0xFE0000` error-flag bits are set — the signature of **write
  protection (WRP)** rejecting the operation at the silicon level.
- Neither of those is bypassable through USB DFU: the device exposes
  only one DFU alt-setting (`@Internal Flash`), no `@Option Bytes`
  interface, so there's no USB-reachable way to reconfigure either
  protection.

Full trace, including how RayNeo's own official web updater
(`ota.rayneo.com`) *does* get writes through — a full mass-erase
(`erase(0x41414141, ...)`, a sentinel for "erase everything" rather
than a real address) as the documented, standard way STM32 chips clear
both RDP and WRP as a side effect, before rewriting the whole image —
is in [`docs/write-protection-findings.md`](docs/write-protection-findings.md).

## Safety

- The main CLI (`rayneo_control`) has no DFU / firmware-write code
  path, intentionally — it only ever speaks the runtime HID channel.
- `tools/dfu_write.py` is the one deliberate exception: an isolated,
  clearly-labeled research tool, not wired into the CLI. Every round
  trip it's been used for stages on real, previously-blank flash
  (never real firmware content), verifies erase before writing, and
  restores blank state after. Every actual write attempt has been
  rejected by hardware protection before touching real data — see
  above.
- `rayneo raw` and `rayneo scan` only use the confirmed 0x66 short form.
- The 0x88 raw-passthrough magic is documented in `protocol.py` but
  deliberately has no CLI command wired to it yet — it's the most
  likely path to direct PX8618 register access
  (`hid_call_px8618_write_read_reg`) and the least understood, so it
  isn't something to fire blindly at a $500 pair of glasses.

## License

MIT
