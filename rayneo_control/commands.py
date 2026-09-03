"""Command-ID catalogue for the taurus4p0 HID control protocol.

Every entry below was placed by disassembling the STM32 firmware image
(taurus4p0_20260110.dfu) at the handler-table dispatch (165 function
pointers starting at firmware address 0x08012414) and reading each
handler's body. The ``confidence`` field is not decoration — it is the
difference between "traced and sent to a live device with a matching
ack" and "the vtable slot exists and isn't null." Treat CONFIRMED as
safe, EXPERIMENTAL as reversible-but-unverified, and don't trust
anything not listed here at all (about 90 of the 165 table slots point
at the same "unimplemented" stub at 0x080123A7).
"""

from enum import Enum, IntEnum


class Confidence(Enum):
    CONFIRMED = "confirmed"        # sent live, response/behavior matched prediction
    TRACED = "traced"              # handler fully disassembled, not exercised live
    EXPERIMENTAL = "experimental"  # vtable slot located, callee not resolved


class DisplayMode(IntEnum):
    """Values for Command.DISPLAY_MODE (0x1A).

    Confirmed live: 0x00 was sent and produced ack 99c8400016ce2f02e308000d05010100.
    1 and 2 are inferred from the prior session's usage and from the
    px8618_sdr2hdr_hdr_set/_load code path, not independently verified here.
    """
    SDR = 0x00          # plain SDR, Pixelworks tone-mapper OUT of the pipeline
    AI_HDR = 0x01        # px8618 SDR->HDR inverse tone mapping (shadow-lifting)
    HDR10 = 0x02          # panel expects real PQ/HDR10 signal


# cmd_id -> (name, confidence, notes)
#
# 0x09/0x0D/0x0E/0x0F/0x12/0x49/0x50/0x66/0x73 were recovered from the other
# side of the wire, not the STM32 image: disassembling the RayNeo Android
# app's native SDK (libFFalconXRServer.so, class ffalcon::XRService) shows
# every one of PanelLunaSet/PanelLunaSave/PanelPowerOn/Off/Swap/SetAudioMode/
# SetAudioVolume/RebootAndBootloader/PanelColorAdjust funneling through one
# shared Send(this, cmd_id, value, extra_ptr, extra_len) call -- these are
# the cmd_id literals each one passes. Same numbering space as the STM32
# dispatch table (0x00-0xA4), never exercised live from this project yet,
# hence TRACED/EXPERIMENTAL rather than CONFIRMED.
COMMANDS = {
    0x00: ("STATUS", Confidence.CONFIRMED,
           "Handler at 0x08010EF0. Only header/uptime/build-date/refresh-rate "
           "fields are real; see protocol.parse_status() for the honest field map."),
    0x09: ("BRIGHTNESS", Confidence.CONFIRMED,
           "ffalcon::XRService::PanelLunaSet. val is a scrambled index into an "
           "8-level table, not a smooth brightness byte -- see "
           "BRIGHTNESS_LEVEL_TO_RAW below for the full raw->OSD-level mapping, "
           "confirmed live against the glasses' own OSD readout. val=4 also "
           "reads as max brightness in the OSD but leaves the glasses' own "
           "physical OSD unable to keep controlling brightness afterward "
           "-- a device-side issue, not a software one. val=8 reaches "
           "the same visible max cleanly, so BRIGHTNESS_LEVEL_TO_RAW uses 8 for "
           "level 7 and val=4 should be avoided entirely, not just treated as "
           "an alternate route to max."),
    0x0D: ("BRIGHTNESS_SAVE", Confidence.TRACED,
           "ffalcon::XRService::PanelLunaSave. No value byte (val=0) -- persists "
           "whatever BRIGHTNESS was last set to."),
    0x0E: ("PANEL_POWER_ON", Confidence.TRACED, "ffalcon::XRService::PanelPowerOn. No value byte."),
    0x0F: ("PANEL_POWER_OFF", Confidence.TRACED, "ffalcon::XRService::PanelPowerOff. No value byte."),
    0x12: ("PANEL_POWER_SWAP", Confidence.TRACED, "ffalcon::XRService::PanelPowerSwap. No value byte."),
    0x20: ("REFRESH_RATE_60", Confidence.TRACED,
           "ffalcon::XRService::PanelFrameRateSet(60). Not a value byte -- the cmd_id "
           "itself changes per rate (0x20 vs 0x21 below), val=0 either way. Matches "
           "the glasses' own OSD: Refresh rate 60Hz/120Hz."),
    0x21: ("REFRESH_RATE_120", Confidence.TRACED,
           "ffalcon::XRService::PanelFrameRateSet(120). See 0x20 -- same call site, "
           "the other branch."),
    0x29: ("PANEL_HDR10_CHANGE_A", Confidence.TRACED,
           "Handler at 0x08011EEC (hid_call_panel_hdr10_change). Same handler as 0x54."),
    0x49: ("AUDIO_MODE", Confidence.TRACED,
           "ffalcon::XRService::SetAudioMode. Distinct from 0x48 AUDIO_TUBE_MODE below. "
           "Likely the OSD's 'Audio effect: Standard/Whisper/Surround' (3 values) --"
           "unconfirmed correspondence, not verified against the actual value bytes."),
    0x50: ("VOLUME", Confidence.CONFIRMED,
           "ffalcon::XRService::SetAudioVolume. Not a 0-100 percentage -- the "
           "glasses' OSD offers 13 discrete volume levels, val=0..12, confirmed "
           "live and working correctly across that range."),
    0x54: ("PANEL_HDR10_CHANGE_B", Confidence.TRACED,
           "Alias of 0x29 -- both dispatch to hid_call_panel_hdr10_change."),
    0x48: ("AUDIO_TUBE_MODE", Confidence.TRACED,
           "Handler at 0x08011BD8 (hid_call_audio_tube_mode). Audio routing, not display. "
           "Likely the OSD's 'Sound Tube: Off/On' -- unconfirmed correspondence."),
    0x66: ("REBOOT_TO_BOOTLOADER", Confidence.TRACED,
           "ffalcon::XRService::RebootAndBootloader. No value byte (val=0). Software "
           "DFU-entry -- the answer to 'is there a command instead of holding both "
           "brightness buttons': yes, this is it, per the Android app's own SDK."),
    0x6D: ("GAMMA_INDEX", Confidence.EXPERIMENTAL,
           "Handler at 0x080104EC indirects through *(0x2005A090)+0x30. Prior tool's "
           "0-5 'Cinema 2.4 / Dark Room / Bright Room' preset names are NOT in the "
           "firmware -- treat them as invented until the callee is resolved."),
    0x6E: ("GAMUT_MODE", Confidence.EXPERIMENTAL,
           "Handler at 0x08010504 indirects through *(0x2005A090)+0x34. Same caveat "
           "as GAMMA_INDEX -- prior tool's DCI-P3/Display-P3/BT.2020 names unverified."),
    0x73: ("PICTURE_MODE", Confidence.CONFIRMED,
           "ffalcon::XRService::PanelColorAdjust(mode, p1, p2) -- a 3-byte payload, "
           "not a single value byte. RESOLVED: the earlier 'not implemented' "
           "conclusion was wrong -- ground-truth Ghidra decompilation of the real "
           "STM32 handler (0x08012278) shows it only acts when mode is EXACTLY "
           "0x0C or 0x0F; every other value (including the 0/1/2 originally "
           "guessed for Standard/Movie/Eye Comfort) silently no-ops with no "
           "response, which is what every earlier live test was actually hitting. "
           "mode=0x0F is confirmed live: it acks with a PANELCOLORPARAMS-style "
           "readback. mode=0x0C calls a separate no-arg internal function and has "
           "never acked -- may be fire-and-forget by design, not necessarily "
           "broken. p1 must be < 0x65 (101) or the call silently no-ops -- looks "
           "like a 0-100 style parameter, not yet mapped to a specific field. "
           "The Standard/Movie/Eye-Comfort UI naming was never confirmed to "
           "correspond to 0x0C/0x0F at all -- that mapping is still open."),
    0x06: ("SWITCH_TO_3D", Confidence.TRACED,
           "ffalcon::XRService::SwitchTo3D. No value byte -- explicit set, not a "
           "toggle. Matches the physical brightness+volume button combo used to "
           "switch into 3D mode while the glasses are on."),
    0x07: ("SWITCH_TO_2D", Confidence.TRACED,
           "ffalcon::XRService::SwitchTo2D. No value byte -- explicit set, the "
           "other half of SWITCH_TO_3D above."),
    0x30: ("SWITCH_SIDE_BY_SIDE", Confidence.TRACED,
           "ffalcon::XRService::SwitchSideBySide. No value byte -- an actual "
           "toggle (flips current state) rather than an explicit set like 0x06/"
           "0x07 above. Closer to matching a single button-combo press if "
           "SWITCH_TO_3D/SWITCH_TO_2D turn out not to."),
}


class Command(IntEnum):
    STATUS = 0x00
    SWITCH_TO_3D = 0x06
    SWITCH_TO_2D = 0x07
    BRIGHTNESS = 0x09
    BRIGHTNESS_SAVE = 0x0D
    PANEL_POWER_ON = 0x0E
    PANEL_POWER_OFF = 0x0F
    PANEL_POWER_SWAP = 0x12
    SWITCH_SIDE_BY_SIDE = 0x30
    REFRESH_RATE_60 = 0x20
    REFRESH_RATE_120 = 0x21
    DISPLAY_MODE = 0x1A
    PANEL_HDR10_CHANGE_A = 0x29
    AUDIO_MODE = 0x49
    VOLUME = 0x50
    PANEL_HDR10_CHANGE_B = 0x54
    AUDIO_TUBE_MODE = 0x48
    REBOOT_TO_BOOTLOADER = 0x66
    GAMMA_INDEX = 0x6D
    GAMUT_MODE = 0x6E
    PICTURE_MODE = 0x73


# BRIGHTNESS (0x09) val -- OSD brightness level, confirmed live against the
# glasses' own on-screen readout while sweeping val=0..8:
#   val:      0  1  2  3  4       5  6  7  8
#   OSD level:2  0  1  6  7(max)  3  4  5  7(max)
# Scrambled, not a simple offset or reversal -- this is a real lookup table
# on the device side, not a bug in this project's framing. val=4 also reads
# as max brightness but leaves the glasses' own physical OSD unable to keep
# controlling brightness afterward -- a device-side state issue, not a
# software one. val=8 reaches the same visible max cleanly, so this inverse
# mapping (OSD level -> val to send) deliberately uses 8 for level 7 and
# never 4.
BRIGHTNESS_LEVEL_TO_RAW = {
    0: 1,
    1: 2,
    2: 0,
    3: 5,
    4: 6,
    5: 7,
    6: 3,
    7: 8,  # not 4 -- see note above
}


# Highest handler-table index seen in the firmware (165 entries, 0x00..0xA4).
# Anything not in COMMANDS above is unmapped: either it's one of the ~90
# slots pointing at the shared "unimplemented" stub (0x080123A7), or it's a
# real handler nobody has traced yet. `rayneo scan` walks this range live.
MAX_COMMAND_ID = 0xA4
