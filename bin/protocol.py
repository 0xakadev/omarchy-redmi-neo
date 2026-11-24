#!/usr/bin/python3
"""Xiaomi / Redmi RCSP & MMA wire protocol for REDMI Headphones Neo (miwear.headphone.o70cg / M2546E1).

Implements:
- SAFER+ 128-bit 8-round mutual authentication (opcodes 0x50 / 0x51)
- RCSP frame encoding/decoding (FE DC BA <flags> <opcode> <len_be16> <body> EF)
- Device info & runtime status parsing (opcodes 0x02, 0x09, 0x0E)
- Active Noise Cancellation (ANC / Transparency / Off, 3 ANC strength levels, Adaptive ANC, Transparency modes)
- Equalizer presets (Balanced, Bass, Treble, Voice, Custom) + 10-band custom EQ curve
- Dual-device connection (multipoint), Low-latency Game Mode, Auto-answer calls
- MFB button gesture mapping (Single, Double, Triple, Long press) + ANC cycle mask
- Find My Headphones audio alert
- BlueZ ManufacturerData (0xFFFF & 0x1727) battery/PID advertisement parser
"""

from __future__ import annotations

from dataclasses import dataclass, field
import os
import struct
from typing import Any, Callable

# ── Device Catalog Constants (REDMI Headphones Neo / miwear.headphone.o70cg) ──

MODEL_ID = "miwear.headphone.o70cg"
SALE_MODEL = "M2546E1"
DEVICE_NAME_DEFAULT = "REDMI Headphones Neo"
VENDOR_ID = 0x2717
PRODUCT_ID = 0x50F0
PDID = 33104
HEADPHONE_TYPE = 2  # Over-ear single-battery headset

# Exposed Bluetooth Service UUIDs on REDMI Headphones Neo
UUID_MIUI_SPP = "0000fd2d-0000-1000-8000-00805f9b34fb"
UUID_FASTPAIR_STREAM = "df21fe2c-2515-4fdb-8886-f12c4d67927c"
UUID_FASTPAIR_FE2C = "0000fe2c-0000-1000-8000-00805f9b34fb"
UUID_SPP = "00001101-0000-1000-8000-00805f9b34fb"
UUID_XIAOMI_MMA = "0000af00-0000-1000-8000-00805f9b34fb"
UUID_VENDOR_SPEC = "9b26d8c0-a8ed-440b-95b0-c4714a518bcc"

SERVICE_UUID_PRIORITY = (
    UUID_MIUI_SPP,
    UUID_XIAOMI_MMA,
    UUID_VENDOR_SPEC,
    UUID_SPP,
)

# ── RCSP Framing Constants ──

FRAME_HEAD = b"\xFE\xDC\xBA"
FRAME_TAIL = 0xEF

FLAG_REQUEST = 0xC4
FLAG_RESPONSE = 0x04

# Opcodes
OP_GET_DEVICE_INFO = 0x02
OP_ANC = 0x08
OP_GET_RUN_INFO = 0x09
OP_REPORT_STATUS = 0x0E
OP_AUTH_CHALLENGE = 0x50
OP_AUTH_CONFIRM = 0x51
OP_SET_CONFIG = 0xF2
OP_GET_CONFIG = 0xF3
OP_NOTIFY_CONFIG = 0xF4

# Config IDs (0xF2 / 0xF3 / 0xF4)
CFG_GESTURES = 0x02
CFG_AUTO_ANSWER = 0x03
CFG_MULTIPOINT = 0x04
CFG_EQ_PRESET = 0x07
CFG_FIND_DEVICE = 0x09
CFG_ANC_CYCLE = 0x0A
CFG_EFFECT_STRENGTH = 0x0B
CFG_IN_EAR = 0x0C
CFG_TRANS_MODE = 0x23
CFG_ADAPTIVE_ANC = 0x25
CFG_SERIAL_NUMBER = 0x27
CFG_GAME_MODE = 0x2F
CFG_EQ_CURVE = 0x37

# Device Info Bitmask Indices (OP_GET_DEVICE_INFO 0x02: payload = 1 << bit in 4-byte BE)
DEV_INFO_BIT_NAME = 0
DEV_INFO_BIT_FIRMWARE = 1
DEV_INFO_BIT_SINGLE_BATTERY = 2
DEV_INFO_BIT_VID_PID = 3
DEV_INFO_BIT_ANC = 4
DEV_INFO_BIT_UBOOT = 6
DEV_INFO_BIT_BATTERY = 7

# ANC Modes (OP_ANC sub-op 0x04 / RUN_INFO TLV 0x09 / REPORT_STATUS TLV 0x04)
ANC_MODE_TO_BYTE = {
    "off": 0x00,
    "anc": 0x01,
    "transparency": 0x02,
}
BYTE_TO_ANC_MODE = {v: k for k, v in ANC_MODE_TO_BYTE.items()}

# ANC Strength Levels (CFG_EFFECT_STRENGTH 0x0B target=0x01, catalog func 1002 tws_gear=[1,0,2])
ANC_LEVEL_TO_BYTE = {
    "light": 0x01,
    "balanced": 0x00,
    "deep": 0x02,
    "adaptive": 0x03,
}
BYTE_TO_ANC_LEVEL = {v: k for k, v in ANC_LEVEL_TO_BYTE.items()}

# Transparency Sub-Modes (CFG_TRANS_MODE 0x23, catalog func 1005)
TRANS_LEVEL_TO_BYTE = {
    "regular": 0x00,
    "voice": 0x01,
    "ambient": 0x02,
}
BYTE_TO_TRANS_LEVEL = {v: k for k, v in TRANS_LEVEL_TO_BYTE.items()}

# Equalizer Presets (CFG_EQ_PRESET 0x07, catalog func 2016 effects=[21,5,6,1,10])
EQ_PRESET_TO_BYTE = {
    "balanced": 21,   # 0x15 Default Balanced on REDMI Headphones Neo
    "bass": 5,        # 0x05 Enhance Bass
    "treble": 6,      # 0x06 Enhance Treble
    "voice": 1,       # 0x01 Enhance Voice / Vocal
    "custom": 10,     # 0x0A Custom 10-band EQ
    "standard": 0,    # 0x00 Legacy Standard fallback
}
BYTE_TO_EQ_PRESET = {
    21: "balanced",
    0: "balanced",
    5: "bass",
    2: "bass",
    6: "treble",
    3: "treble",
    1: "voice",
    4: "voice",
    10: "custom",
    0xFF: "custom",
}

# 10-band Equalizer Frequencies (Hz) — hardware-verified on REDMI Headphones Neo (0x50F0)
EQ_FREQUENCIES = (62, 125, 250, 500, 1000, 2000, 4000, 8000, 12000, 16000)
EQ_GAIN_MIN = -6
EQ_GAIN_MAX = 6

# Gesture / MFB Button Mapping (CFG_GESTURES 0x02, REDMI Headphones Neo 5-byte format: 00 01 <slot> 00 <action>)
# Catalog func 4018 (single=0x01), 4012 (double=0x02), 4013 (triple=0x03), 4014 (long=0x04)
GESTURE_SLOT_TO_BYTE = {
    "single": 0x01,
    "double": 0x02,
    "triple": 0x03,
    "long": 0x04,
}
BYTE_TO_GESTURE_SLOT = {v: k for k, v in GESTURE_SLOT_TO_BYTE.items()}

GESTURE_ACTION_TO_BYTE = {
    "voice_assistant": 0x00,
    "play_pause": 0x01,
    "volume_up": 0x02,
    "volume_down": 0x03,
    "previous": 0x04,
    "next": 0x05,
    "game_mode": 0x0D,
    "anc_cycle": 0x0F,
    "none": 0x10,
}
BYTE_TO_GESTURE_ACTION = {
    0x00: "voice_assistant",
    0x01: "play_pause",
    0x02: "volume_up",
    0x03: "volume_down",
    0x04: "previous",
    0x05: "next",
    0x06: "anc_cycle",
    0x07: "game_mode",
    0x08: "none",
    0x0D: "game_mode",
    0x0F: "anc_cycle",
    0x10: "none",
}

# Long-Press ANC Cycle Mask (CFG_ANC_CYCLE 0x0A: bit0=ANC, bit1=Transparency, bit2=Off)
ANC_CYCLE_TO_BYTE = {
    "all": 0x07,        # ANC + Transparency + Off
    "anc_trans": 0x06,  # ANC + Transparency
    "anc_off": 0x03,    # ANC + Off
    "trans_off": 0x05,  # Transparency + Off
}
BYTE_TO_ANC_CYCLE = {
    0x07: "all",
    0x06: "anc_trans",
    0x03: "anc_off",
    0x05: "trans_off",
}


# ── SAFER+ 128-bit 8-Round Cipher (Hardware-validated Xiaomi/Redmi variant) ──

SAFER_BLOCK_SIZE = 16
SAFER_PATTERN = 0x9999
SAFER_SEQ = bytes([
    0x11, 0x22, 0x33, 0x33, 0x22, 0x11, 0x11, 0x22,
    0x33, 0x33, 0x22, 0x11, 0x11, 0x22, 0x33, 0x33,
])

SAFER_COEFFICIENTS = (
    (2, 1, 1, 1, 4, 2, 1, 1, 2, 2, 4, 2, 4, 4, 16, 8),
    (2, 1, 1, 1, 4, 2, 1, 1, 1, 1, 2, 1, 2, 2, 8, 4),
    (1, 1, 4, 2, 2, 2, 4, 2, 16, 8, 4, 4, 2, 1, 1, 1),
    (1, 1, 4, 2, 1, 1, 2, 1, 8, 4, 2, 2, 2, 1, 1, 1),
    (16, 8, 2, 2, 4, 2, 4, 4, 1, 1, 4, 2, 1, 1, 2, 1),
    (8, 4, 1, 1, 2, 1, 2, 2, 1, 1, 4, 2, 1, 1, 2, 1),
    (2, 2, 4, 2, 4, 4, 16, 8, 2, 1, 1, 1, 4, 2, 1, 1),
    (1, 1, 2, 1, 2, 2, 8, 4, 2, 1, 1, 1, 4, 2, 1, 1),
    (4, 2, 4, 4, 16, 8, 2, 2, 1, 1, 2, 1, 1, 1, 4, 2),
    (2, 1, 2, 2, 8, 4, 1, 1, 1, 1, 2, 1, 1, 1, 4, 2),
    (4, 4, 16, 8, 1, 1, 2, 1, 4, 2, 1, 1, 4, 2, 2, 2),
    (2, 2, 8, 4, 1, 1, 2, 1, 4, 2, 1, 1, 2, 1, 1, 1),
    (1, 1, 2, 1, 1, 1, 4, 2, 4, 4, 16, 8, 2, 2, 4, 2),
    (1, 1, 2, 1, 1, 1, 4, 2, 2, 2, 8, 4, 1, 1, 2, 1),
    (4, 2, 1, 1, 2, 1, 1, 1, 4, 2, 2, 2, 16, 8, 4, 4),
    (4, 2, 1, 1, 2, 1, 1, 1, 2, 1, 1, 1, 8, 4, 2, 2),
)

SAFER_EXP = tuple(0 if i == 128 else pow(45, i, 257) % 256 for i in range(256))


def _build_safer_log() -> tuple[int, ...]:
    log = [0] * 256
    log[0] = 128
    for i in range(1, 256):
        val = pow(45, i, 257)
        if val != 256:
            log[val] = i % 256
    return tuple(log)


SAFER_LOG = _build_safer_log()

SAFER_BIAS = tuple(
    tuple(pow(45, pow(45, 17 * (i + 2) + (j + 1), 257), 257) % 256 for j in range(SAFER_BLOCK_SIZE))
    for i in range(SAFER_BLOCK_SIZE)
)


def _safer_xor_bit(i: int) -> bool:
    return bool(SAFER_PATTERN & (1 << i))


def _rotl8_3(value: int) -> int:
    return ((value << 3) | (value >> 5)) & 0xFF


def _safer_combine(value: int, other: int, i: int) -> int:
    return (value ^ other) if _safer_xor_bit(i) else (value + other) & 0xFF


def safer_generate_subkeys(key: bytes = SAFER_SEQ) -> list[list[int]]:
    if len(key) != SAFER_BLOCK_SIZE:
        raise ValueError("SAFER+ key must be 16 bytes")
    first = list(key)
    first[15] ^= 6

    register = first + [0]
    for b in first:
        register[16] ^= b

    keys = [first]
    for key_index in range(1, 17):
        register = [_rotl8_3(b) for b in register]
        keys.append([
            (register[(key_index + i) % 17] + SAFER_BIAS[key_index - 1][i]) & 0xFF
            for i in range(SAFER_BLOCK_SIZE)
        ])
    return keys


def safer_encrypt(plaintext: bytes, key: bytes = SAFER_SEQ) -> bytes:
    """Encrypts a 16-byte plaintext block with a 16-byte key using 8-round SAFER+."""
    if len(plaintext) != SAFER_BLOCK_SIZE or len(key) != SAFER_BLOCK_SIZE:
        raise ValueError("SAFER+ requires 16-byte block and 16-byte key")
    keys = safer_generate_subkeys(key)
    block = list(plaintext)

    for round_index in range(8):
        if round_index == 2:
            block = [_safer_combine(block[i], plaintext[i], i) for i in range(SAFER_BLOCK_SIZE)]

        block = [_safer_combine(block[i], keys[2 * round_index][i], i) for i in range(SAFER_BLOCK_SIZE)]
        block = [SAFER_EXP[b] if _safer_xor_bit(i) else SAFER_LOG[b] for i, b in enumerate(block)]
        block = [
            (keys[2 * round_index + 1][i] + b) & 0xFF if _safer_xor_bit(i) else (keys[2 * round_index + 1][i] ^ b)
            for i, b in enumerate(block)
        ]
        block = [
            sum(SAFER_COEFFICIENTS[i][j] * block[j] for j in range(SAFER_BLOCK_SIZE)) & 0xFF
            for i in range(SAFER_BLOCK_SIZE)
        ]

    block = [
        (keys[16][i] ^ b) if _safer_xor_bit(i) else ((keys[16][i] + b) & 0xFF)
        for i, b in enumerate(block)
    ]
    return bytes(block)


def safer_challenge_response(challenge: bytes) -> bytes:
    """Computes the Xiaomi mutual auth challenge response: encrypt(SAFER_SEQ, key=challenge)."""
    return safer_encrypt(SAFER_SEQ, challenge)


# ── RCSP Frame Encoding / Decoding ──

@dataclass
class RcspPacket:
    flags: int
    opcode: int
    body: bytes

    @property
    def is_response(self) -> bool:
        return (self.flags & 0x80) == 0

    @property
    def is_request_or_notify(self) -> bool:
        return (self.flags & 0x80) != 0

    @property
    def expects_ack(self) -> bool:
        return self.is_request_or_notify and bool(self.flags & 0x40)

    @property
    def status(self) -> int | None:
        if self.is_response and len(self.body) >= 1:
            return self.body[0]
        return None

    @property
    def seq(self) -> int | None:
        if self.is_response and len(self.body) >= 2:
            return self.body[1]
        if self.is_request_or_notify and len(self.body) >= 1:
            return self.body[0]
        return None

    @property
    def payload(self) -> bytes:
        if self.is_response:
            return self.body[2:] if len(self.body) >= 2 else b""
        return self.body[1:] if len(self.body) >= 1 else b""


def build_frame(flags: int, opcode: int, body: bytes) -> bytes:
    header = FRAME_HEAD + struct.pack(">BBH", flags & 0xFF, opcode & 0xFF, len(body))
    return header + body + bytes([FRAME_TAIL])


def build_request(opcode: int, seq: int, payload: bytes = b"", flags: int = FLAG_REQUEST) -> bytes:
    body = bytes([seq & 0xFF]) + payload
    return build_frame(flags, opcode, body)


def build_response(opcode: int, seq: int, payload: bytes = b"", status: int = 0x00) -> bytes:
    body = bytes([status & 0xFF, seq & 0xFF]) + payload
    return build_frame(FLAG_RESPONSE, opcode, body)


def parse_frame(frame: bytes) -> RcspPacket | None:
    if len(frame) < 8:
        return None
    if frame[:3] != FRAME_HEAD or frame[-1] != FRAME_TAIL:
        return None
    flags, opcode, length = struct.unpack(">BBH", frame[3:7])
    if len(frame) != 7 + length + 1:
        return None
    return RcspPacket(flags=flags, opcode=opcode, body=frame[7 : 7 + length])


class RcspStreamParser:
    """Extracts complete RCSP packets from an arbitrary RFCOMM byte stream."""

    MAX_BUFFER_BYTES = 8192

    def __init__(self) -> None:
        self._buf = bytearray()

    def feed(self, data: bytes) -> list[RcspPacket]:
        self._buf.extend(data)
        if len(self._buf) > self.MAX_BUFFER_BYTES:
            del self._buf[: -2056]
        packets: list[RcspPacket] = []
        while True:
            idx = self._buf.find(FRAME_HEAD)
            if idx < 0:
                if len(self._buf) > 2:
                    del self._buf[:-2]
                break
            if idx > 0:
                del self._buf[:idx]
            if len(self._buf) < 8:
                break
            flags, opcode, length = struct.unpack(">BBH", self._buf[3:7])
            if length > 2048:
                del self._buf[:3]
                continue
            total_len = 7 + length + 1
            if len(self._buf) < total_len:
                break
            if self._buf[total_len - 1] != FRAME_TAIL:
                del self._buf[:3]
                continue
            body = bytes(self._buf[7 : 7 + length])
            del self._buf[:total_len]
            packets.append(RcspPacket(flags=flags, opcode=opcode, body=body))
        return packets


# ── SAFER+ Mutual Authentication Session ──

class CommandAuthSession:
    """Handles SAFER+ mutual authentication (0x50 / 0x51) over RCSP."""

    def __init__(
        self,
        next_seq: Callable[[], int],
        challenge_bytes: bytes | None = None,
    ) -> None:
        self._next_seq = next_seq
        self.challenge = challenge_bytes if challenge_bytes is not None else os.urandom(16)
        self.expected_response = safer_challenge_response(self.challenge)
        self.stage = "idle"
        self.authenticated = False
        self.failed_reason: str | None = None

    def start(self) -> bytes:
        self.stage = "wait_50_resp"
        self.authenticated = False
        self.failed_reason = None
        seq = self._next_seq()
        return build_request(OP_AUTH_CHALLENGE, seq, b"\x01" + self.challenge)

    def feed_packet(self, pkt: RcspPacket) -> list[bytes]:
        replies: list[bytes] = []

        if pkt.opcode == OP_AUTH_CHALLENGE and pkt.is_response:
            payload = pkt.payload
            if len(payload) >= 17 and payload[0] == 0x01:
                self.stage = "wait_peer_50"
                seq = self._next_seq()
                replies.append(build_request(OP_AUTH_CONFIRM, seq, b"\x01\x00"))
            else:
                self.failed_reason = f"Invalid 0x50 response payload: {payload.hex()}"
            return replies

        if pkt.opcode == OP_AUTH_CHALLENGE and pkt.is_request_or_notify:
            payload = pkt.payload
            seq = pkt.seq or 0
            if len(payload) >= 17 and payload[0] == 0x01:
                peer_challenge = payload[1:17]
                resp = safer_challenge_response(peer_challenge)
                self.stage = "wait_peer_51"
                replies.append(build_response(OP_AUTH_CHALLENGE, seq, b"\x01" + resp))
            else:
                self.failed_reason = f"Invalid peer 0x50 challenge: {payload.hex()}"
            return replies

        if pkt.opcode == OP_AUTH_CONFIRM and pkt.is_request_or_notify:
            payload = pkt.payload
            seq = pkt.seq or 0
            if len(payload) >= 2 and payload[0] == 0x01 and payload[1] == 0x00:
                replies.append(build_response(OP_AUTH_CONFIRM, seq, b"\x01"))
                self.stage = "done"
                self.authenticated = True
            else:
                self.failed_reason = f"Peer rejected auth in 0x51: {payload.hex()}"
            return replies

        return replies


# ── Command Builders ──

def build_get_device_info(seq: int, mask: int = 0xFFFFFFFF) -> bytes:
    return build_request(OP_GET_DEVICE_INFO, seq, struct.pack(">I", mask & 0xFFFFFFFF))


def build_get_device_info_mask(seq: int, bit: int) -> bytes:
    """Builds a single-attribute OP_GET_DEVICE_INFO (0x02) request using 1 << bit."""
    return build_get_device_info(seq, 1 << (bit & 31))


def build_get_run_info(seq: int) -> bytes:
    return build_request(OP_GET_RUN_INFO, seq, b"\xFF\xFF\xFF\xFF")


def build_set_anc_mode(seq: int, mode: str) -> bytes:
    if mode not in ANC_MODE_TO_BYTE:
        raise ValueError(f"Unknown ANC mode: {mode}")
    return build_request(OP_ANC, seq, bytes([0x02, 0x04, ANC_MODE_TO_BYTE[mode]]))


def build_set_anc_effect(seq: int, mode: str, sub_byte: int = 0x00) -> bytes:
    """Sets ANC mode + sub-mode via CFG_EFFECT_STRENGTH (0xF2 / 0x000B)."""
    if mode not in ANC_MODE_TO_BYTE:
        raise ValueError(f"Unknown ANC mode: {mode}")
    return build_set_config(seq, CFG_EFFECT_STRENGTH, bytes([ANC_MODE_TO_BYTE[mode], sub_byte & 0xFF]))


def build_set_ear_detection(seq: int, enabled: bool) -> bytes:
    # 0x00 = enabled, 0x01 = disabled
    val = 0x00 if enabled else 0x01
    return build_request(OP_ANC, seq, bytes([0x02, 0x06, val]))


def build_get_config(seq: int, config_id: int) -> bytes:
    return build_request(OP_GET_CONFIG, seq, bytes([0x00, config_id & 0xFF]))


def build_set_config_record(config_id: int, value: bytes) -> bytes:
    rec_len = 2 + len(value)
    return bytes([rec_len & 0xFF, 0x00, config_id & 0xFF]) + value


def build_set_config(seq: int, config_id: int, value: bytes) -> bytes:
    return build_request(OP_SET_CONFIG, seq, build_set_config_record(config_id, value))


def build_set_anc_level(seq: int, level: str) -> bytes:
    if level not in ANC_LEVEL_TO_BYTE:
        raise ValueError(f"Unknown ANC level: {level}")
    return build_set_config(seq, CFG_EFFECT_STRENGTH, bytes([0x01, ANC_LEVEL_TO_BYTE[level]]))


def build_set_trans_level(seq: int, level: str) -> bytes:
    if level not in TRANS_LEVEL_TO_BYTE:
        raise ValueError(f"Unknown transparency level: {level}")
    return build_set_config(seq, CFG_TRANS_MODE, bytes([TRANS_LEVEL_TO_BYTE[level]]))


def build_set_adaptive_anc(seq: int, enabled: bool) -> bytes:
    return build_set_config(seq, CFG_ADAPTIVE_ANC, bytes([0x01 if enabled else 0x00]))


def build_set_multipoint(seq: int, enabled: bool) -> bytes:
    return build_set_config(seq, CFG_MULTIPOINT, bytes([0x01 if enabled else 0x00]))


def build_set_auto_answer(seq: int, enabled: bool) -> bytes:
    return build_set_config(seq, CFG_AUTO_ANSWER, bytes([0x01 if enabled else 0x00]))


def build_set_game_mode(seq: int, enabled: bool) -> bytes:
    return build_set_config(seq, CFG_GAME_MODE, bytes([0x01 if enabled else 0x00]))


def build_set_eq_preset(seq: int, preset: str) -> bytes:
    if preset not in EQ_PRESET_TO_BYTE:
        raise ValueError(f"Unknown EQ preset: {preset}")
    return build_set_config(seq, CFG_EQ_PRESET, bytes([EQ_PRESET_TO_BYTE[preset]]))


def encode_eq_gain_byte(gain_db: int) -> int:
    gain = max(EQ_GAIN_MIN, min(EQ_GAIN_MAX, int(round(gain_db))))
    return gain if gain >= 0 else (128 - gain)


def decode_eq_gain_byte(raw: int) -> int:
    raw &= 0xFF
    if raw >= 128:
        val = -(raw - 128)
    elif raw > 64:
        val = raw - 256
    else:
        val = raw
    return max(EQ_GAIN_MIN, min(EQ_GAIN_MAX, val))


def build_set_eq_curve(seq: int, bands: list[int], preview: bool = False) -> bytes:
    if len(bands) != 10:
        raise ValueError("Custom EQ curve requires exactly 10 bands")
    # Hardware-verified on REDMI Headphones Neo (0x50F0): 05 01 01 <count=10> (<freq_be16><gain>)*10
    # writes all 10 custom EQ bands directly to the headset DSP & NVRAM.
    payload = bytearray(b"\x05\x01\x01")
    payload.append(len(bands))
    for freq, gain in zip(EQ_FREQUENCIES, bands):
        payload.extend(int(freq).to_bytes(2, "big"))
        payload.append(encode_eq_gain_byte(gain))
    return build_set_config(seq, CFG_EQ_CURVE, bytes(payload))


def build_eq_reset(seq: int) -> bytes:
    return build_set_config(seq, CFG_EQ_CURVE, bytes.fromhex("04 01 01 01"))


def build_set_find_device(seq: int, ringing: bool, target: int = 0x03) -> bytes:
    # action: 0x01 = start, 0x00 = stop; target: 0x03 = both/headphones, 0x01 = left, 0x02 = right
    return build_set_config(
        seq,
        CFG_FIND_DEVICE,
        bytes([0x01 if ringing else 0x00, target & 0xFF]),
    )


def build_set_gesture(seq: int, slot: str, action: str, right_action: str | None = None) -> bytes:
    if slot not in GESTURE_SLOT_TO_BYTE:
        raise ValueError(f"Unknown gesture slot: {slot}")
    if action not in GESTURE_ACTION_TO_BYTE:
        raise ValueError(f"Unknown gesture action: {action}")
    # REDMI Headphones Neo (miwear.headphone.o70cg / 0x50F0) 5-byte MFB gesture record: 00 01 <slot> 00 <action>
    return build_set_config(
        seq,
        CFG_GESTURES,
        bytes([
            0x00,
            0x01,
            GESTURE_SLOT_TO_BYTE[slot],
            0x00,
            GESTURE_ACTION_TO_BYTE[action],
        ]),
    )


def build_set_anc_cycle(seq: int, cycle_mode: str) -> bytes:
    if cycle_mode not in ANC_CYCLE_TO_BYTE:
        raise ValueError(f"Unknown ANC cycle mode: {cycle_mode}")
    mask = ANC_CYCLE_TO_BYTE[cycle_mode]
    return build_set_config(seq, CFG_ANC_CYCLE, bytes([mask, mask]))


# ── Response & Notification Parsers ──

def parse_battery_byte(raw: int) -> tuple[int | None, bool]:
    raw &= 0xFF
    if raw == 0x7F or raw == 0xFF:
        return None, False
    level = raw & 0x7F
    charging = bool(raw & 0x80)
    if level == 0 or level > 100:
        return None, False
    return level, charging


def parse_battery_bytes(data: bytes) -> dict[str, Any]:
    """Parses 1..3 battery bytes into unified headphone / left / right / case state."""
    out: dict[str, Any] = {}
    if not data:
        return out
    b0_lvl, b0_chg = parse_battery_byte(data[0])
    b1_lvl, b1_chg = parse_battery_byte(data[1]) if len(data) >= 2 else (None, False)
    b2_lvl, b2_chg = parse_battery_byte(data[2]) if len(data) >= 3 else (None, False)

    # On single-battery over-ear headphones (REDMI Headphones Neo, headphone_type=2),
    # slot 0 holds the main headset battery and slots 1..2 are 0x7F.
    if b0_lvl is not None and b1_lvl is None:
        out["battery"] = b0_lvl
        out["charging"] = b0_chg
    elif b0_lvl is not None and b1_lvl is not None:
        out["battery_left"] = b0_lvl
        out["charging_left"] = b0_chg
        out["battery_right"] = b1_lvl
        out["charging_right"] = b1_chg
        out["battery"] = min(b0_lvl, b1_lvl)
        out["charging"] = b0_chg or b1_chg
    elif b1_lvl is not None:
        out["battery"] = b1_lvl
        out["charging"] = b1_chg

    if b2_lvl is not None:
        out["battery_case"] = b2_lvl
        out["charging_case"] = b2_chg

    return out


def parse_tlvs(payload: bytes) -> list[tuple[int, bytes]]:
    """Parses RCSP TLV records: [len][type][value...(len-1 bytes)]."""
    records: list[tuple[int, bytes]] = []
    idx = 0
    n = len(payload)
    while idx + 2 <= n:
        rec_len = payload[idx]
        if rec_len < 1 or idx + 1 + rec_len > n:
            break
        rec_type = payload[idx + 1]
        rec_val = payload[idx + 2 : idx + 1 + rec_len]
        records.append((rec_type, rec_val))
        idx += 1 + rec_len
    return records


def parse_device_info_tlvs(payload: bytes) -> dict[str, Any]:
    """Parses OP_GET_DEVICE_INFO (0x02) response TLVs."""
    info: dict[str, Any] = {}
    for tlv_type, val in parse_tlvs(payload):
        if tlv_type == 0x00 and val:
            try:
                name = val.decode("utf-8", errors="ignore").strip("\x00").strip()
                if name:
                    info["device_reported_name"] = name
            except Exception:
                pass
        elif tlv_type == 0x01 and len(val) >= 2:
            b0, b1 = val[0], val[1]
            info["firmware"] = f"{(b0 >> 4) & 0xF}.{b0 & 0xF}.{(b1 >> 4) & 0xF}.{b1 & 0xF}"
        elif tlv_type == 0x02 and val:
            batt = parse_battery_bytes(val[:1])
            for k, v in batt.items():
                info.setdefault(k, v)
        elif tlv_type == 0x03 and len(val) >= 4:
            vid, pid = struct.unpack(">HH", val[:4])
            info["vid"] = f"{vid:04x}"
            info["pid"] = f"{pid:04x}"
        elif tlv_type == 0x04 and len(val) >= 1:
            # Hardware-verified on REDMI Headphones Neo (fw 3.0.2.9): this TLV is a constant
            # ANC *capability* flag (always 0x01 in Off, ANC and Transparency). It is NOT the
            # live mode; the live mode comes from CFG_EFFECT_STRENGTH (0xF3/0xF4 0x0B) and
            # OP_REPORT_STATUS (0x0E). Treating it as the mode reverted the UI to "ANC".
            info["anc_available"] = val[0] != 0x00
        elif tlv_type == 0x07 and val:
            info.update(parse_battery_bytes(val))
    return info


def parse_run_info_tlvs(payload: bytes) -> dict[str, Any]:
    """Parses OP_GET_RUN_INFO (0x09) response TLVs."""
    info: dict[str, Any] = {}
    for tlv_type, val in parse_tlvs(payload):
        if tlv_type == 0x09 and len(val) >= 1:
            mode = BYTE_TO_ANC_MODE.get(val[0])
            if mode:
                info["anc"] = mode
        elif tlv_type == 0x0A and len(val) >= 1:
            info["ear_detection"] = val[0] == 0x00
    return info


def parse_report_status_tlvs(payload: bytes) -> dict[str, Any]:
    """Parses OP_REPORT_STATUS (0x0E) push notification TLVs."""
    info: dict[str, Any] = {}
    for tlv_type, val in parse_tlvs(payload):
        if tlv_type == 0x00 and val:
            info.update(parse_battery_bytes(val))
        elif tlv_type == 0x04 and len(val) >= 1:
            mode = BYTE_TO_ANC_MODE.get(val[0])
            if mode:
                info["anc"] = mode
            if len(val) >= 2 and info.get("anc") == "anc":
                if val[1] == 0x03:
                    info["adaptive_anc"] = True
                elif val[1] in BYTE_TO_ANC_LEVEL:
                    # Do not clear adaptive_anc here: while CFG_ADAPTIVE_ANC (0x25) is enabled,
                    # the headset reports the currently adapted ANC strength (0x00/0x01/0x02).
                    info["anc_level"] = BYTE_TO_ANC_LEVEL[val[1]]
    return info


def parse_config_records(payload: bytes) -> dict[str, Any]:
    """Parses OP_GET_CONFIG (0xF3) response or OP_NOTIFY_CONFIG (0xF4) records."""
    updates: dict[str, Any] = {}
    idx = 0
    n = len(payload)
    while idx + 3 <= n:
        rec_len = payload[idx]
        if rec_len < 2 or idx + 1 + rec_len > n:
            break
        prefix = payload[idx + 1]
        cfg_id = payload[idx + 2]
        val = payload[idx + 3 : idx + 1 + rec_len]
        idx += 1 + rec_len
        if prefix != 0x00:
            continue

        if cfg_id == CFG_GESTURES and len(val) >= 3:
            gestures = dict(updates.get("gestures") or {})
            if len(val) >= 5 and len(val) % 5 == 0 and val[0:2] == b"\x00\x01":
                for offset in range(0, len(val) - 4, 5):
                    slot = BYTE_TO_GESTURE_SLOT.get(val[offset + 2])
                    act_byte = val[offset + 4] if val[offset + 4] != 0xFF else val[offset + 3]
                    action = BYTE_TO_GESTURE_ACTION.get(act_byte)
                    if slot and action:
                        gestures[slot] = action
            else:
                for offset in range(0, len(val) - 2, 3):
                    slot = BYTE_TO_GESTURE_SLOT.get(val[offset])
                    act_byte = val[offset + 1] if val[offset + 1] != 0xFF else val[offset + 2]
                    action = BYTE_TO_GESTURE_ACTION.get(act_byte)
                    if slot and action:
                        gestures[slot] = action
            if gestures:
                updates["gestures"] = gestures

        elif cfg_id == CFG_AUTO_ANSWER and len(val) >= 1:
            updates["auto_answer"] = val[0] == 0x01

        elif cfg_id == CFG_MULTIPOINT and len(val) >= 1:
            updates["multipoint"] = val[0] == 0x01

        elif cfg_id == CFG_EQ_PRESET and len(val) >= 1:
            preset = BYTE_TO_EQ_PRESET.get(val[0])
            if preset:
                updates["eq"] = preset

        elif cfg_id == CFG_FIND_DEVICE and len(val) >= 1:
            updates["finding"] = val[0] == 0x01

        elif cfg_id == CFG_ANC_CYCLE and len(val) >= 1:
            cycle = BYTE_TO_ANC_CYCLE.get(val[0] & 0x07)
            if cycle:
                updates["anc_cycle"] = cycle

        elif cfg_id == CFG_EFFECT_STRENGTH and len(val) >= 2:
            target, strength = val[0], val[1]
            if target in BYTE_TO_ANC_MODE:
                updates["anc"] = BYTE_TO_ANC_MODE[target]
            if target == 0x01:
                if strength == 0x03:
                    updates["adaptive_anc"] = True
                elif strength in BYTE_TO_ANC_LEVEL:
                    # Hardware-verified on REDMI Headphones Neo (fw 3.0.2.9): while Adaptive ANC
                    # (CFG_ADAPTIVE_ANC 0x25 == 0x01) is active, 0x0B reports the live adapted
                    # strength (0x00/0x01/0x02). Only CFG_ADAPTIVE_ANC (0x25) authoritatively turns
                    # adaptive_anc off.
                    updates["anc_level"] = BYTE_TO_ANC_LEVEL[strength]

        elif cfg_id == CFG_TRANS_MODE and len(val) >= 1:
            if val[0] in BYTE_TO_TRANS_LEVEL:
                updates["trans_level"] = BYTE_TO_TRANS_LEVEL[val[0]]

        elif cfg_id == CFG_ADAPTIVE_ANC and len(val) >= 1:
            updates["adaptive_anc"] = val[0] == 0x01

        elif cfg_id == CFG_SERIAL_NUMBER and val:
            try:
                sn = val.decode("utf-8", errors="ignore").strip("\x00").strip()
                if sn:
                    updates["serial_number"] = sn
            except Exception:
                pass

        elif cfg_id == CFG_GAME_MODE and len(val) >= 1:
            updates["game_mode"] = val[0] == 0x01

        elif cfg_id == CFG_EQ_CURVE and len(val) >= 4:
            # Handle EQ reset record (04 01 01 01)
            if val == b"\x04\x01\x01\x01":
                updates["eq_bands"] = [0] * 10
            # Handle hardware 3-byte-per-band response:
            # 01 <preset_id> <max_gain> <min_gain> <curve_idx> <name_len> <name...> <count=10> (<freq_be16><gain>)*10
            elif len(val) >= 7 and val[0] == 0x01:
                preset_byte = val[1]
                offset = 6 + val[5]
                if offset < len(val):
                    count = val[offset]
                    if count == 10 and len(val) >= offset + 1 + 3 * count and preset_byte == 0x0A:
                        gains: list[int] = []
                        for pos in range(offset + 1, offset + 1 + 3 * count, 3):
                            gains.append(decode_eq_gain_byte(val[pos + 2]))
                        updates["eq_bands"] = gains
            elif len(val) >= 4 and val[:3] == b"\x05\x01\x01":
                count = val[3]
                if count == 10 and len(val) >= 4 + 3 * count:
                    updates["eq_bands"] = [
                        decode_eq_gain_byte(val[pos + 2])
                        for pos in range(4, 4 + 3 * count, 3)
                    ]
            elif len(val) in (10, 11):
                band_bytes = val[1:11] if len(val) == 11 else val[:10]
                updates["eq_bands"] = [decode_eq_gain_byte(b) for b in band_bytes]

    return updates


# ── Google Fast Pair Message Stream (RFCOMM Channel 5 / df21fe2c) ──

def build_fastpair_ring(ringing: bool, timeout_sec: int = 30) -> bytes:
    """Builds a Google Fast Pair Message Stream Device Action -> Ring (Group 0x04, Code 0x01)."""
    action_byte = 0x03 if ringing else 0x00
    t_sec = max(0, min(255, int(timeout_sec))) if ringing else 0x00
    return struct.pack(">BBHBB", 0x04, 0x01, 2, action_byte, t_sec)


class FastPairStreamParser:
    """Parses Google Fast Pair Message Stream packets ([group:1][code:1][len:be16][data:len])."""

    MAX_BUFFER_BYTES = 2048

    def __init__(self) -> None:
        self._buf = bytearray()

    def feed(self, data: bytes) -> dict[str, Any]:
        self._buf.extend(data)
        if len(self._buf) > self.MAX_BUFFER_BYTES:
            del self._buf[:-516]
        updates: dict[str, Any] = {}
        while len(self._buf) >= 4:
            group, code, length = struct.unpack(">BBH", self._buf[:4])
            if length > 512:
                del self._buf[0]
                continue
            if len(self._buf) < 4 + length:
                break
            payload = bytes(self._buf[4 : 4 + length])
            del self._buf[: 4 + length]

            # Group 0x03: Device Information Event
            if group == 0x03:
                if code == 0x01 and len(payload) == 3:
                    updates["fastpair_model"] = payload.hex()
                elif code == 0x02 and len(payload) == 6:
                    updates["ble_mac"] = ":".join(f"{b:02X}" for b in payload)
                elif code in (0x03, 0x04) and payload:
                    batt = parse_battery_bytes(payload)
                    if batt:
                        updates.update(batt)
                        updates["battery_source"] = "fastpair"
            # Group 0x04: Device Action Event (Ring Device)
            elif group == 0x04 and code == 0x01 and payload:
                updates["finding"] = payload[0] != 0x00
        return updates


# ── BlueZ Advertisement / ManufacturerData Parser ──

def parse_manufacturer_data(mfg_data: dict[int, bytes]) -> dict[str, Any]:
    """Extracts PID and advertised battery from BlueZ ManufacturerData (0x1727 & 0xFFFF)."""
    result: dict[str, Any] = {}

    # 0x1727 (byte-swapped 0x2717 Xiaomi Inc.): e.g. 08 03 02 50 f0 b5 f8 e2 a7 08
    raw_xiaomi = mfg_data.get(0x1727) or mfg_data.get(0x2717)
    if raw_xiaomi and len(raw_xiaomi) >= 5:
        pid = (raw_xiaomi[3] << 8) | raw_xiaomi[4]
        result["vid"] = "2717"
        result["pid"] = f"{pid:04x}"

    # 0xFFFF Xiaomi Fast Pair / BLE status beacon:
    # e.g. 16 01 3a 88 0e 04 32 7f 7f d2 e3 90 a0 b5 a7 e2 f8 d5 ...
    # Index 6..8 hold [main/left battery, right battery, case battery]
    raw_ffff = mfg_data.get(0xFFFF)
    if raw_ffff and len(raw_ffff) >= 9 and raw_ffff[0] == 0x16:
        batt = parse_battery_bytes(raw_ffff[6:9])
        if batt:
            result.update(batt)
            result["battery_from_beacon"] = True

    return result
