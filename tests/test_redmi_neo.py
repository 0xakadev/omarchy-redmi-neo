#!/usr/bin/python3
"""Unit and regression test suite for Omarchy REDMI Headphones Neo plugin (`0xakadev.redmi-neo`)."""

from __future__ import annotations

import ctypes
import importlib.machinery
import importlib.util
from pathlib import Path
import re
import sys
import unittest

sys.dont_write_bytecode = True

ROOT_DIR = Path(__file__).resolve().parent.parent
BIN_DIR = ROOT_DIR / "bin"
if str(BIN_DIR) not in sys.path:
    sys.path.insert(0, str(BIN_DIR))

import protocol  # noqa: E402


def _load_redmid():
    loader = importlib.machinery.SourceFileLoader("redmid", str(BIN_DIR / "redmid"))
    spec = importlib.util.spec_from_loader("redmid", loader)
    assert spec is not None
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


redmid = _load_redmid()


class TestSaferPlusAndAuth(unittest.TestCase):
    def test_safer_plus_encryption_deterministic(self) -> None:
        challenge = bytes(range(16))
        enc1 = protocol.safer_encrypt(challenge)
        enc2 = protocol.safer_encrypt(challenge)
        self.assertEqual(len(enc1), 16)
        self.assertEqual(enc1, enc2)
        self.assertNotEqual(enc1, challenge)

        resp = protocol.safer_challenge_response(challenge)
        self.assertEqual(len(resp), 16)
        self.assertNotEqual(resp, enc1)

        # Verify against real Redmi hardware capture vector
        hw_challenge = bytes.fromhex("9762e8a1836d32cc4b7b8605c553545f")
        hw_expected = bytes.fromhex("f7fe2d0211bab715efe4c33ac740574d")
        self.assertEqual(protocol.safer_challenge_response(hw_challenge), hw_expected)

    def test_mutual_auth_handshake_state_machine(self) -> None:
        seq_counter = 0

        def next_seq() -> int:
            nonlocal seq_counter
            seq_counter += 1
            return seq_counter

        challenge = bytes([0xAA] * 16)
        session = protocol.CommandAuthSession(next_seq, challenge_bytes=challenge)

        # Step 1: Host sends 0x50 challenge
        step1_raw = session.start()
        pkt1 = protocol.parse_frame(step1_raw)
        self.assertIsNotNone(pkt1)
        assert pkt1 is not None
        self.assertEqual(pkt1.flags, protocol.FLAG_REQUEST)
        self.assertEqual(pkt1.opcode, protocol.OP_AUTH_CHALLENGE)
        self.assertEqual(pkt1.payload, b"\x01" + challenge)
        self.assertEqual(session.stage, "wait_50_resp")

        # Step 2: Headset responds 0x04 0x50 -> Host sends 0xC4 0x51 (01 00)
        headset_50_resp = protocol.build_response(
            protocol.OP_AUTH_CHALLENGE,
            1,
            b"\x01" + protocol.safer_challenge_response(challenge),
        )
        pkt_resp = protocol.parse_frame(headset_50_resp)
        assert pkt_resp is not None
        replies = session.feed_packet(pkt_resp)
        self.assertEqual(len(replies), 1)
        pkt_51_req = protocol.parse_frame(replies[0])
        assert pkt_51_req is not None
        self.assertEqual(pkt_51_req.opcode, protocol.OP_AUTH_CONFIRM)
        self.assertEqual(pkt_51_req.payload, b"\x01\x00")
        self.assertEqual(session.stage, "wait_peer_50")

        # Step 3: Headset sends its own 0x50 challenge -> Host replies 0x04 0x50 with SAFER+ response
        peer_challenge = bytes([0x55] * 16)
        peer_50_req = protocol.build_request(protocol.OP_AUTH_CHALLENGE, 9, b"\x01" + peer_challenge, flags=0xC0)
        pkt_peer_50 = protocol.parse_frame(peer_50_req)
        assert pkt_peer_50 is not None
        replies = session.feed_packet(pkt_peer_50)
        self.assertEqual(len(replies), 1)
        pkt_host_50_resp = protocol.parse_frame(replies[0])
        assert pkt_host_50_resp is not None
        self.assertTrue(pkt_host_50_resp.is_response)
        self.assertEqual(pkt_host_50_resp.seq, 9)
        self.assertEqual(pkt_host_50_resp.payload, b"\x01" + protocol.safer_challenge_response(peer_challenge))

        # Step 4: Headset sends 0x51 pass (01 00) -> Host replies 0x04 0x51 (01) and marks authenticated
        peer_51_req = protocol.build_request(protocol.OP_AUTH_CONFIRM, 10, b"\x01\x00", flags=0xC0)
        pkt_peer_51 = protocol.parse_frame(peer_51_req)
        assert pkt_peer_51 is not None
        replies = session.feed_packet(pkt_peer_51)
        self.assertEqual(len(replies), 1)
        self.assertTrue(session.authenticated)
        self.assertEqual(session.stage, "done")


class TestRcspProtocolAndCommands(unittest.TestCase):
    def test_stream_parser_chunked_and_concatenated(self) -> None:
        f1 = protocol.build_set_anc_mode(1, "anc")
        f2 = protocol.build_set_eq_preset(2, "bass")
        stream = b"\x00\x99" + f1 + b"\xFF" + f2

        parser = protocol.RcspStreamParser()
        out = []
        for i in range(0, len(stream), 3):
            out.extend(parser.feed(stream[i : i + 3]))

        self.assertEqual(len(out), 2)
        self.assertEqual(out[0].opcode, protocol.OP_ANC)
        self.assertEqual(out[0].payload, b"\x02\x04\x01")
        self.assertEqual(out[1].opcode, protocol.OP_SET_CONFIG)
        self.assertEqual(out[1].payload, b"\x03\x00\x07\x05")

    def test_anc_and_config_roundtrips(self) -> None:
        # ANC strength level (Light = 1, Balanced = 0, Deep = 2)
        frame_deep = protocol.build_set_anc_level(5, "deep")
        pkt = protocol.parse_frame(frame_deep)
        assert pkt is not None
        parsed = protocol.parse_config_records(pkt.payload)
        self.assertEqual(parsed.get("anc_level"), "deep")

        # Transparency level (Enhance Voice = 1)
        frame_voice = protocol.build_set_trans_level(6, "voice")
        pkt_v = protocol.parse_frame(frame_voice)
        assert pkt_v is not None
        parsed_v = protocol.parse_config_records(pkt_v.payload)
        self.assertEqual(parsed_v.get("trans_level"), "voice")

        # Adaptive ANC (0x25)
        frame_adap = protocol.build_set_adaptive_anc(7, True)
        pkt_a = protocol.parse_frame(frame_adap)
        assert pkt_a is not None
        self.assertTrue(protocol.parse_config_records(pkt_a.payload).get("adaptive_anc"))

        # Multipoint (0x04) & Game Mode (0x2F)
        pkt_mp = protocol.parse_frame(protocol.build_set_multipoint(8, True))
        assert pkt_mp is not None
        self.assertTrue(protocol.parse_config_records(pkt_mp.payload).get("multipoint"))

        pkt_gm = protocol.parse_frame(protocol.build_set_game_mode(9, True))
        assert pkt_gm is not None
        self.assertTrue(protocol.parse_config_records(pkt_gm.payload).get("game_mode"))

        # 10-Band Custom EQ Curve (0x37) with negative & positive dB values
        bands = [-6, -3, -1, 0, 1, 2, 4, 6, -2, 3]
        pkt_eq = protocol.parse_frame(protocol.build_set_eq_curve(10, bands))
        assert pkt_eq is not None
        parsed_eq = protocol.parse_config_records(pkt_eq.payload)
        self.assertEqual(parsed_eq.get("eq_bands"), bands)

        # MFB Gesture (0x02) & ANC Cycle Mask (0x0A)
        pkt_gest = protocol.parse_frame(protocol.build_set_gesture(11, "double", "next"))
        assert pkt_gest is not None
        parsed_gest = protocol.parse_config_records(pkt_gest.payload)
        self.assertEqual(parsed_gest.get("gestures"), {"double": "next"})

        # Hardware 20-byte (4 x 5-byte) gesture response from REDMI Headphones Neo (0x50F0)
        hw_gest_payload = bytes.fromhex("160002000101000100010200050001030004000104000f")
        parsed_hw_gest = protocol.parse_config_records(hw_gest_payload)
        self.assertEqual(
            parsed_hw_gest.get("gestures"),
            {
                "single": "play_pause",
                "double": "next",
                "triple": "previous",
                "long": "anc_cycle",
            },
        )

        pkt_cycle = protocol.parse_frame(protocol.build_set_anc_cycle(12, "anc_trans"))
        assert pkt_cycle is not None
        parsed_cycle = protocol.parse_config_records(pkt_cycle.payload)
        self.assertEqual(parsed_cycle.get("anc_cycle"), "anc_trans")

    def test_device_info_and_report_status_parsing(self) -> None:
        # Device info: TLV 0x01 firmware (0x10, 0x24 -> 1.0.2.4), TLV 0x03 VID/PID (2717:50f0), TLV 0x07 battery (65% charging, 0x7f, 0x7f)
        tlvs = (
            b"\x03\x01\x10\x24"
            b"\x05\x03\x27\x17\x50\xF0"
            + bytes([0x04, 0x07, 65 | 0x80, 0x7F, 0x7F])
        )
        info = protocol.parse_device_info_tlvs(tlvs)
        self.assertEqual(info["firmware"], "1.0.2.4")
        self.assertEqual(info["vid"], "2717")
        self.assertEqual(info["pid"], "50f0")
        self.assertEqual(info["battery"], 65)
        self.assertTrue(info["charging"])

        # Report status (0x0E): TLV 0x00 battery (50%), TLV 0x04 ANC mode (0x01=ANC, 0x02=Deep)
        status_tlvs = bytes([0x04, 0x00, 50, 0x7F, 0x7F, 0x03, 0x04, 0x01, 0x02])
        status = protocol.parse_report_status_tlvs(status_tlvs)
        self.assertEqual(status["battery"], 50)
        self.assertFalse(status["charging"])
        self.assertEqual(status["anc"], "anc")
        self.assertEqual(status["anc_level"], "deep")

    def test_rcsp_zero_flag_response_and_bitmask_device_info(self) -> None:
        # Hardware responses with flags=0x00 must be recognized as responses (bit 7 == 0)
        raw_f3_resp = bytes.fromhex("fedcba00f30007000104000b0102ef")
        pkt = protocol.parse_frame(raw_f3_resp)
        self.assertIsNotNone(pkt)
        assert pkt is not None
        self.assertTrue(pkt.is_response)
        self.assertFalse(pkt.is_request_or_notify)
        self.assertEqual(pkt.status, 0x00)
        self.assertEqual(pkt.seq, 0x01)
        self.assertEqual(pkt.payload, bytes.fromhex("04000b0102"))
        cfg = protocol.parse_config_records(pkt.payload)
        self.assertEqual(cfg["anc"], "anc")
        self.assertEqual(cfg["anc_level"], "deep")
        self.assertNotIn("adaptive_anc", cfg)

        # Bitmask query for battery (bit 7 = 0x80) and ANC (bit 4 = 0x10)
        mask_batt = protocol.parse_frame(protocol.build_get_device_info_mask(3, protocol.DEV_INFO_BIT_BATTERY))
        assert mask_batt is not None
        self.assertEqual(mask_batt.payload, b"\x00\x00\x00\x80")

        # Hardware response for mask 4 (TLV 0x04 = constant ANC capability flag, NOT live mode)
        # and mask 7 (Battery 90% in slot 1)
        dev_anc = protocol.parse_device_info_tlvs(bytes.fromhex("020401"))
        self.assertTrue(dev_anc["anc_available"])
        self.assertNotIn("anc", dev_anc)
        dev_batt = protocol.parse_device_info_tlvs(bytes.fromhex("0407ff5aff"))
        self.assertEqual(dev_batt["battery"], 90)
        self.assertFalse(dev_batt["charging"])

    def test_hardware_eq_curve_and_transparency_mode_regressions(self) -> None:
        # Exact 0x37 custom EQ response captured from REDMI Headphones Neo (preset=0x0A, curve_idx=0x00, 2000Hz band 6)
        hw_custom_eq = bytes.fromhex(
            "270037010a060600000a003e01007d0200fa0301f40003e88107d0820fa0001f40012ee0023e8003"
        )
        parsed_custom = protocol.parse_config_records(hw_custom_eq)
        self.assertEqual(parsed_custom.get("eq_bands"), [1, 2, 3, 0, -1, -2, 0, 1, 2, 3])

        # Non-custom preset curve (e.g. Voice preset=0x01) must NOT overwrite custom eq_bands
        hw_voice_eq = bytes.fromhex(
            "2700370101060600000a003e06007d0600fa0601f40603e80607d0060fa0061f40062ee0063e8086"
        )
        parsed_voice = protocol.parse_config_records(hw_voice_eq)
        self.assertNotIn("eq_bands", parsed_voice)

        # CFG_EFFECT_STRENGTH (0x0B) in Transparency (02 00 or transient 02 02) must NOT clobber trans_level;
        # CFG_TRANS_MODE (0x23) is the authoritative Transparency sub-mode register.
        eff_trans_00 = protocol.parse_config_records(bytes.fromhex("04000b0200"))
        self.assertEqual(eff_trans_00, {"anc": "transparency"})
        eff_trans_02 = protocol.parse_config_records(bytes.fromhex("04000b0202"))
        self.assertEqual(eff_trans_02, {"anc": "transparency"})
        self.assertEqual(protocol.parse_config_records(bytes.fromhex("03002300")), {"trans_level": "regular"})
        self.assertEqual(protocol.parse_config_records(bytes.fromhex("03002301")), {"trans_level": "voice"})
        self.assertEqual(protocol.parse_config_records(bytes.fromhex("03002302")), {"trans_level": "ambient"})

    def test_stream_parsers_bound_memory_on_malformed_input(self) -> None:
        rcsp_parser = protocol.RcspStreamParser()
        # Feed 32 KiB of unframed header-like bytes without tail; internal buffer must stay bounded
        for _ in range(32):
            rcsp_parser.feed(protocol.FRAME_HEAD + b"\x04\xf3\x07\xd0" + (b"\xAA" * 1017))
        self.assertLessEqual(len(rcsp_parser._buf), protocol.RcspStreamParser.MAX_BUFFER_BYTES)

        fp_parser = protocol.FastPairStreamParser()
        for _ in range(32):
            fp_parser.feed(b"\x03\x03\x02\x00" + (b"\xBB" * 250))
        self.assertLessEqual(len(fp_parser._buf), protocol.FastPairStreamParser.MAX_BUFFER_BYTES)

    def test_live_redmi_neo_manufacturer_data_parsing(self) -> None:
        # Exact ManufacturerData captured from user's REDMI Headphones Neo (A0:90:B5:F8:E2:A7)
        mfg = {
            0x1727: bytes.fromhex("08030250f0b5f8e2a708"),
            0xFFFF: bytes.fromhex("16013a880e04327f7fd2e390a0b5a7e2f8d58c7ef46ad263e6c289"),
        }
        parsed = protocol.parse_manufacturer_data(mfg)
        self.assertEqual(parsed["vid"], "2717")
        self.assertEqual(parsed["pid"], "50f0")
        self.assertEqual(parsed["battery"], 50)
        self.assertFalse(parsed["charging"])
        self.assertTrue(parsed["battery_from_beacon"])

        # Exact Fast Pair Message Stream captured from user's REDMI Headphones Neo on RFCOMM Ch 5
        fp_stream = bytes.fromhex("030100034c298003020006639c00ddbd6f030300015a")
        fp_parsed = protocol.FastPairStreamParser().feed(fp_stream)
        self.assertEqual(fp_parsed["fastpair_model"], "4c2980")
        self.assertEqual(fp_parsed["ble_mac"], "63:9C:00:DD:BD:6F")
        self.assertEqual(fp_parsed["battery"], 90)
        self.assertFalse(fp_parsed["charging"])
        self.assertEqual(fp_parsed["battery_source"], "fastpair")


class TestUiSafetyAndQmlConventions(unittest.TestCase):
    def test_every_qml_text_sets_plaintext_format(self) -> None:
        panel_qml = (ROOT_DIR / "Panel.qml").read_text(encoding="utf-8")
        # Find every `Text {` block and ensure `textFormat: Text.PlainText` appears right inside it
        matches = list(re.finditer(r"\bText\s*\{", panel_qml))
        self.assertGreater(len(matches), 5)
        for m in matches:
            snippet = panel_qml[m.start() : m.start() + 260]
            self.assertIn(
                "textFormat: Text.PlainText",
                snippet,
                f"Text block at offset {m.start()} is missing textFormat: Text.PlainText:\n{snippet}",
            )

    def test_no_unwrapped_animation_durations_in_qml(self) -> None:
        panel_qml = (ROOT_DIR / "Panel.qml").read_text(encoding="utf-8")
        for m in re.finditer(r"duration\s*:\s*([^\n;}]+)", panel_qml):
            expr = m.group(1).strip()
            self.assertTrue(
                expr.startswith("Style.duration("),
                f"Animation duration must use Style.duration(ms), found: {expr}",
            )

    def test_model_js_neutralize_markup_regression(self) -> None:
        model_js = (ROOT_DIR / "Model.js").read_text(encoding="utf-8")
        self.assertIn("function neutralizeMarkup", model_js)
        self.assertIn("\\uFF1C", model_js)
        self.assertIn("\\uFF1E", model_js)
        self.assertIn("\\uFF06", model_js)

        # Simulate the exact JS replacement in Python to verify hostile strings are neutralized
        def py_neutralize(val: str) -> str:
            val = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", val)
            return val.replace("&", "\uFF06").replace("<", "\uFF1C").replace(">", "\uFF1E")

        hostile = '<img src="https://evil.example/track.png"><a href="http://x">REDMI & Neo\x07</a>'
        safe = py_neutralize(hostile)
        self.assertNotIn("<", safe)
        self.assertNotIn(">", safe)
        self.assertNotIn("&", safe)
        self.assertNotIn("\x07", safe)


class _FakeCFunc:
    def __init__(self, fn):
        self._fn = fn
        self.argtypes = None
        self.restype = None

    def __call__(self, *args):
        return self._fn(*args)


class _FakeLibBluetooth:
    """Fake libbluetooth.so.3 harness tracking every SDP list, record, and session allocation."""

    def __init__(self, records_ports: list[int], search_err: int = 0, raise_on_port: bool = False):
        self._records_ports = records_ports
        self._search_err = search_err
        self._raise_on_port = raise_on_port
        self._keepalive: list[object] = []
        self._next_list_id = 1
        self._ptr_to_list_id: dict[int, int] = {}

        self.active_lists: set[int] = set()
        self.freed_lists: list[int] = []
        self.active_records: set[int] = set()
        self.freed_records: list[int] = []
        self.access_proto_queries: list[int] = []
        self.requested_attr_ranges: list[int] = []
        self.closed_sessions: list[int] = []

        self.sdp_connect = _FakeCFunc(self._sdp_connect)
        self.sdp_close = _FakeCFunc(self._sdp_close)
        self.sdp_uuid128_create = _FakeCFunc(self._sdp_uuid128_create)
        self.sdp_uuid16_create = _FakeCFunc(self._sdp_uuid16_create)
        self.sdp_list_append = _FakeCFunc(self._sdp_list_append)
        self.sdp_list_free = _FakeCFunc(self._sdp_list_free)
        self.sdp_service_search_attr_req = _FakeCFunc(self._sdp_service_search_attr_req)
        self.sdp_get_access_protos = _FakeCFunc(self._sdp_get_access_protos)
        self.sdp_get_proto_port = _FakeCFunc(self._sdp_get_proto_port)
        self.sdp_record_free = _FakeCFunc(self._sdp_record_free)

    def _alloc_chain(self, values: list[int]) -> ctypes.POINTER(redmid._sdp_list_t):
        head = ctypes.POINTER(redmid._sdp_list_t)()
        prev = None
        nodes = []
        for val in values:
            node = redmid._sdp_list_t()
            node.data = ctypes.c_void_p(val)
            node.next = ctypes.POINTER(redmid._sdp_list_t)()
            ptr = ctypes.pointer(node)
            if prev is None:
                head = ptr
            else:
                prev.contents.next = ptr
            prev = ptr
            nodes.append(node)
        self._keepalive.extend(nodes)
        addr = ctypes.cast(head, ctypes.c_void_p).value or 0
        lid = self._next_list_id
        self._next_list_id += 1
        self._ptr_to_list_id[addr] = lid
        self.active_lists.add(lid)
        return head

    def _sdp_connect(self, _src, _dst, _flags):
        return 0x5E5510

    def _sdp_close(self, session):
        self.closed_sessions.append(int(session))

    def _sdp_uuid128_create(self, _u, _raw):
        return None

    def _sdp_uuid16_create(self, _u, _val):
        return None

    def _sdp_list_append(self, existing, data_ptr):
        assert not existing
        raw_addr = ctypes.cast(data_ptr, ctypes.c_void_p).value or 0
        return self._alloc_chain([raw_addr])

    def _sdp_list_free(self, lst, _free_fn):
        if not lst:
            return
        addr = ctypes.cast(lst, ctypes.c_void_p).value or 0
        lid = self._ptr_to_list_id.get(addr)
        assert lid is not None, f"Unknown or unallocated sdp_list_t freed: {addr:#x}"
        assert lid in self.active_lists, f"Double free of sdp_list_t id={lid}"
        self.active_lists.remove(lid)
        self.freed_lists.append(lid)

    def _sdp_service_search_attr_req(self, _sess, _search, _reqtype, attrid_list, rsp_out):
        val_ptr = ctypes.cast(attrid_list.contents.data, ctypes.POINTER(ctypes.c_uint32))
        self.requested_attr_ranges.append(int(val_ptr.contents.value))
        rec_handles = []
        for idx, _port in enumerate(self._records_ports, start=1):
            handle = 0x1000 + idx
            self.active_records.add(handle)
            rec_handles.append(handle)
        if rec_handles:
            chain = self._alloc_chain(rec_handles)
            ctypes.memmove(ctypes.addressof(rsp_out._obj), ctypes.addressof(chain), ctypes.sizeof(chain))  # type: ignore[attr-defined]
        return self._search_err

    def _sdp_get_access_protos(self, rec_handle, proto_out):
        rec_id = int(rec_handle)
        self.access_proto_queries.append(rec_id)
        idx = rec_id - 0x1001
        port = self._records_ports[idx]
        # Allocate two inner protocol descriptor sub-lists (e.g., L2CAP + RFCOMM)
        inner_l2cap = self._alloc_chain([0x0100])
        inner_rfcomm = self._alloc_chain([0x0003, port])
        outer = self._alloc_chain(
            [
                ctypes.cast(inner_l2cap, ctypes.c_void_p).value or 0,
                ctypes.cast(inner_rfcomm, ctypes.c_void_p).value or 0,
            ]
        )
        self._port_by_list_addr = getattr(self, "_port_by_list_addr", {})
        self._port_by_list_addr[ctypes.cast(outer, ctypes.c_void_p).value or 0] = port
        ctypes.memmove(ctypes.addressof(proto_out._obj), ctypes.addressof(outer), ctypes.sizeof(outer))  # type: ignore[attr-defined]
        return 0

    def _sdp_get_proto_port(self, proto_list, proto_uuid):
        assert proto_uuid == redmid.RFCOMM_UUID
        if self._raise_on_port:
            raise RuntimeError("simulated ctypes failure")
        addr = ctypes.cast(proto_list, ctypes.c_void_p).value or 0
        return self._port_by_list_addr[addr]

    def _sdp_record_free(self, rec_handle):
        rec_id = int(rec_handle)
        assert rec_id in self.active_records, f"Double or invalid free of sdp_record_t {rec_id:#x}"
        self.active_records.remove(rec_id)
        self.freed_records.append(rec_id)


class TestSdpRecordCleanup(unittest.TestCase):
    def test_frees_all_trailing_sdp_records_after_first_usable_channel(self) -> None:
        # First record already has RFCOMM channel 6; peer also returns 3 trailing records
        fake_bt = _FakeLibBluetooth(records_ports=[6, 9, 12, 0])
        ch = redmid.sdp_find_rfcomm_channel("A0:90:B5:F8:E2:A7", protocol.UUID_MIUI_SPP, libbt=fake_bt)
        self.assertEqual(ch, 6)
        # Every single returned record (0x1001..0x1004) MUST be freed
        self.assertEqual(fake_bt.freed_records, [0x1001, 0x1002, 0x1003, 0x1004])
        self.assertEqual(len(fake_bt.active_records), 0)
        # Trailing records should not even have access_protos allocated once channel is found
        self.assertEqual(fake_bt.access_proto_queries, [0x1001])
        # Every sdp_list_t (search_list, attrid_list, seq_list, outer + 2 inner proto_lists) MUST be freed
        self.assertEqual(len(fake_bt.active_lists), 0)
        self.assertEqual(fake_bt.closed_sessions, [0x5E5510])
        # Only attribute 0x0004 (ProtocolDescriptorList) is requested
        self.assertEqual(fake_bt.requested_attr_ranges, [0x00040004])

    def test_frees_all_sdp_records_on_exception_or_search_error(self) -> None:
        fake_exc = _FakeLibBluetooth(records_ports=[6, 8, 10], raise_on_port=True)
        ch = redmid.sdp_find_rfcomm_channel("A0:90:B5:F8:E2:A7", protocol.UUID_MIUI_SPP, libbt=fake_exc)
        self.assertIsNone(ch)
        self.assertEqual(fake_exc.freed_records, [0x1001, 0x1002, 0x1003])
        self.assertEqual(len(fake_exc.active_records), 0)
        self.assertEqual(len(fake_exc.active_lists), 0)
        self.assertEqual(fake_exc.closed_sessions, [0x5E5510])

        fake_err = _FakeLibBluetooth(records_ports=[6, 8], search_err=-1)
        ch_err = redmid.sdp_find_rfcomm_channel("A0:90:B5:F8:E2:A7", protocol.UUID_MIUI_SPP, libbt=fake_err)
        self.assertIsNone(ch_err)
        self.assertEqual(fake_err.freed_records, [0x1001, 0x1002])
        self.assertEqual(len(fake_err.active_records), 0)
        self.assertEqual(len(fake_err.active_lists), 0)
        self.assertEqual(fake_err.closed_sessions, [0x5E5510])


class TestDaemonStateSyncAndPipewireCodec(unittest.TestCase):
    def test_pipewire_a2dp_sink_aac_codec_parsing(self) -> None:
        from unittest import mock

        sample_pactl = (
            "Card #64\n"
            "\tName: bluez_card.A0_90_B5_F8_E2_A7\n"
            "\tProperties:\n"
            '\t\tapi.bluez5.address = "A0:90:B5:F8_E2:A7"\n'
            "\tProfiles:\n"
            "\t\toff: Off (sinks: 0, sources: 0, priority: 0, available: yes)\n"
            "\t\ta2dp-sink-sbc: High Fidelity Playback (A2DP Sink, codec SBC) (sinks: 1, sources: 0, priority: 131, available: yes)\n"
            "\t\ta2dp-sink: High Fidelity Playback (A2DP Sink, codec AAC) (sinks: 1, sources: 0, priority: 132, available: yes)\n"
            "\tActive Profile: a2dp-sink\n"
        )
        fake_proc = mock.Mock(returncode=0, stdout=sample_pactl)
        with mock.patch.object(redmid.subprocess, "run", return_value=fake_proc):
            info = redmid.query_audio_codec("A0:90:B5:F8:E2:A7")
        self.assertEqual(info, {"codec": "AAC", "profile": "A2DP"})

    def test_stale_read_rejection_and_dsp_settle_guard(self) -> None:
        from unittest import mock

        with mock.patch.object(redmid, "load_config", return_value={}), mock.patch.object(
            redmid, "save_config", return_value=None
        ):
            daemon = redmid.RedmiNeoDaemon()
            auth = protocol.CommandAuthSession(daemon._next_seq)
            daemon._rfcomm_authenticated = True

            # Simulate a background read sent BEFORE a user write
            daemon._pending_reads[7] = 100.0
            daemon._last_write_ts = 100.5
            daemon.state["anc"] = "off"

            stale_pkt = protocol.parse_frame(
                protocol.build_response(protocol.OP_GET_CONFIG, 7, bytes.fromhex("04000b0100"))
            )
            assert stale_pkt is not None
            daemon._handle_rcsp_packet(stale_pkt, auth)
            # Stale pre-write read must NOT revert "off" back to "anc"
            self.assertEqual(daemon.state["anc"], "off")

            # Simulate a read sent AFTER the user write across 256-seq wraparound
            now = redmid.time.monotonic()
            daemon._last_write_ts = now - 3.0
            daemon._pending_reads[7] = now - 0.1
            fresh_pkt = protocol.parse_frame(
                protocol.build_response(protocol.OP_GET_CONFIG, 7, bytes.fromhex("04000b0200"))
            )
            assert fresh_pkt is not None
            daemon._handle_rcsp_packet(fresh_pkt, auth)
            self.assertEqual(daemon.state["anc"], "transparency")

            # During DSP_SETTLE_SEC right after a write, transitional ANC push notifications are ignored
            # while battery notifications still update in real time
            daemon._begin_write()
            daemon.state["anc"] = "off"
            ntf_pkt = protocol.parse_frame(
                protocol.build_request(
                    protocol.OP_REPORT_STATUS,
                    12,
                    bytes([0x04, 0x00, 77, 0x7F, 0x7F, 0x02, 0x04, 0x01]),
                    flags=0x80,
                )
            )
            assert ntf_pkt is not None
            daemon._handle_rcsp_packet(ntf_pkt, auth)
            self.assertEqual(daemon.state["anc"], "off")
            self.assertEqual(daemon.state["battery"], 77)

    def test_adaptive_anc_persists_across_live_strength_updates(self) -> None:
        from unittest import mock

        with mock.patch.object(redmid, "load_config", return_value={}), mock.patch.object(
            redmid, "save_config", return_value=None
        ):
            daemon = redmid.RedmiNeoDaemon()
            auth = protocol.CommandAuthSession(daemon._next_seq)
            daemon._rfcomm_authenticated = True
            sent_frames: list[bytes] = []
            daemon._send_raw = lambda data: sent_frames.append(data) or True  # type: ignore[method-assign]

            # 1. Headset reports CFG_ADAPTIVE_ANC (0x25) = 0x01
            adap_on_pkt = protocol.parse_frame(
                protocol.build_response(protocol.OP_GET_CONFIG, daemon._next_seq(), bytes.fromhex("03002501"))
            )
            assert adap_on_pkt is not None
            daemon._handle_rcsp_packet(adap_on_pkt, auth)
            self.assertTrue(daemon.state["adaptive_anc"])

            # 2. Headset adapts in real time and reports CFG_EFFECT_STRENGTH (0x0B) = 0x01 (light)
            #    via poll response (0xF3) AND push notify (0xF4); adaptive_anc must stay True while anc_level updates
            daemon._last_write_ts = redmid.time.monotonic() - 3.0
            eff_light_pkt = protocol.parse_frame(
                protocol.build_response(protocol.OP_GET_CONFIG, daemon._next_seq(), bytes.fromhex("04000b0101"))
            )
            assert eff_light_pkt is not None
            daemon._handle_rcsp_packet(eff_light_pkt, auth)
            self.assertTrue(daemon.state["adaptive_anc"])
            self.assertEqual(daemon.state["anc"], "anc")
            self.assertEqual(daemon.state["anc_level"], "light")

            # Unsolicited 0xF4 notify changing adapted level to deep also triggers a 0x25 verification read
            ntf_deep = protocol.parse_frame(
                protocol.build_request(protocol.OP_NOTIFY_CONFIG, 42, bytes.fromhex("04000b0102"), flags=0x80)
            )
            assert ntf_deep is not None
            daemon._handle_rcsp_packet(ntf_deep, auth)
            self.assertTrue(daemon.state["adaptive_anc"])
            self.assertEqual(daemon.state["anc_level"], "deep")
            self.assertTrue(
                any(
                    (p := protocol.parse_frame(f)) is not None
                    and p.opcode == protocol.OP_GET_CONFIG
                    and p.payload == b"\x00\x25"
                    for f in sent_frames
                )
            )

            # 3. OP_REPORT_STATUS (0x0E) with TLV 0x04 = 0x01 0x00 (balanced) does not clear adaptive_anc
            ntf_status = protocol.parse_frame(
                protocol.build_request(protocol.OP_REPORT_STATUS, 43, bytes.fromhex("03040100"), flags=0x80)
            )
            assert ntf_status is not None
            daemon._handle_rcsp_packet(ntf_status, auth)
            self.assertTrue(daemon.state["adaptive_anc"])
            self.assertEqual(daemon.state["anc_level"], "balanced")

            # 4. Headset reports CFG_ADAPTIVE_ANC (0x25) = 0x00 -> adaptive_anc turns False in real time
            adap_off_pkt = protocol.parse_frame(
                protocol.build_response(protocol.OP_GET_CONFIG, daemon._next_seq(), bytes.fromhex("03002500"))
            )
            assert adap_off_pkt is not None
            daemon._handle_rcsp_packet(adap_off_pkt, auth)
            self.assertFalse(daemon.state["adaptive_anc"])

            # 5. _query_live_status includes CFG_ADAPTIVE_ANC (0x25)
            sent_frames.clear()
            daemon._query_live_status(full=False)
            op_cfg_ids = [
                p.payload
                for f in sent_frames
                if (p := protocol.parse_frame(f)) is not None and p.opcode == protocol.OP_GET_CONFIG
            ]
            self.assertIn(b"\x00\x25", op_cfg_ids)
            self.assertIn(b"\x00\x0b", op_cfg_ids)

            # 6. Manual anc-level write always sends CFG_ADAPTIVE_ANC=0 even if cached adaptive_anc was False
            sent_frames.clear()
            daemon._rfcomm_sock = mock.Mock()
            daemon.state["connected"] = True
            daemon.state["adaptive_anc"] = False
            daemon.handle_command({"cmd": "anc-level", "level": "deep"})
            set_payloads = [
                p.payload
                for f in sent_frames
                if (p := protocol.parse_frame(f)) is not None and p.opcode == protocol.OP_SET_CONFIG
            ]
            self.assertIn(b"\x03\x00\x25\x00", set_payloads)
            self.assertIn(b"\x04\x00\x0b\x01\x02", set_payloads)


class TestUsbCableMode(unittest.TestCase):
    def test_sysfs_usb_detection_and_alsa_stream0_parsing(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # Interface dir (with ':') must be ignored even if it had vendor/product files
            iface_dir = root / "1-5:1.0"
            iface_dir.mkdir()
            (iface_dir / "idVendor").write_text("2717\n", encoding="utf-8")
            (iface_dir / "idProduct").write_text("50f0\n", encoding="utf-8")

            # Other unrelated USB device
            other = root / "1-1"
            other.mkdir()
            (other / "idVendor").write_text("062a\n", encoding="utf-8")
            (other / "idProduct").write_text("5918\n", encoding="utf-8")

            # Actual REDMI Headphones Neo USB device
            dev = root / "1-5"
            dev.mkdir()
            (dev / "idVendor").write_text("2717\n", encoding="utf-8")
            (dev / "idProduct").write_text("50f0\n", encoding="utf-8")
            (dev / "authorized").write_text("1\n", encoding="utf-8")
            (dev / "product").write_text("REDMI Headphones Neo\n", encoding="utf-8")
            (dev / "manufacturer").write_text("Generic\n", encoding="utf-8")
            (dev / "serial").write_text("73670/CEAGDF6QT0\n", encoding="utf-8")
            (dev / "bcdDevice").write_text("2681\n", encoding="utf-8")
            (dev / "bMaxPower").write_text("300mA\n", encoding="utf-8")

            found = redmid.query_usb_redmi_neo(sysfs_usb_root=root)
            self.assertIsNotNone(found)
            assert found is not None
            self.assertEqual(found["vid"], "2717")
            self.assertEqual(found["pid"], "50f0")
            self.assertEqual(found["product"], "REDMI Headphones Neo")
            self.assertEqual(found["serial"], "73670/CEAGDF6QT0")
            self.assertEqual(found["max_power"], "300mA")

            # Unauthorized USB device must be ignored
            (dev / "authorized").write_text("0\n", encoding="utf-8")
            self.assertIsNone(redmid.query_usb_redmi_neo(sysfs_usb_root=root))

        stream0 = (
            "Generic REDMI Headphones Neo at usb-0000:00:14.0-5, full speed : USB Audio\n\n"
            "Playback:\n"
            "  Status: Running\n"
            "    Interface = 1\n"
            "    Altset = 1\n"
            "    Packet Size = 432\n"
            "    Momentary freq = 48000 Hz (0x30.0000)\n"
            "  Interface 1\n"
            "    Altset 1\n"
            "    Format: S24_3LE\n"
            "    Channels: 2\n"
            "    Rates: 44100, 48000, 96000\n"
            "    Bits: 24\n"
            "  Interface 1\n"
            "    Altset 2\n"
            "    Format: S16_LE\n"
            "    Channels: 2\n"
            "    Rates: 44100, 48000, 96000\n"
            "    Bits: 16\n"
        )
        parsed = redmid._parse_alsa_stream0(stream0)
        self.assertEqual(parsed["status"], "Running")
        self.assertEqual(parsed["codec"], "PCM 24-bit/48kHz")

        pactl_sinks = (
            "Sink #56\n"
            "\tName: alsa_output.usb-Generic_REDMI_Headphones_Neo_73670_CEAGDF6QT0-00.analog-stereo\n"
            "\tSample Specification: s24le 2ch 48000Hz\n"
            "\tMute: no\n"
            "\tVolume: front-left: 29488 /  45% / -20.81 dB,   front-right: 29488 /  45% / -20.81 dB\n"
            "\tProperties:\n"
            '\t\tdevice.vendor.id = "0x2717"\n'
            '\t\tdevice.product.id = "0x50f0"\n'
        )
        pactl_cards = (
            "Card #57\n"
            "\tName: alsa_card.usb-Generic_REDMI_Headphones_Neo_73670_CEAGDF6QT0-00\n"
            "\tProperties:\n"
            '\t\tdevice.vendor.id = "0x2717"\n'
            '\t\tdevice.product.id = "0x50f0"\n'
            "\tActive Profile: output:analog-stereo+input:analog-stereo\n"
        )
        pw = redmid._parse_pactl_usb_audio(
            pactl_sinks,
            pactl_cards,
            "alsa_output.usb-Generic_REDMI_Headphones_Neo_73670_CEAGDF6QT0-00.analog-stereo",
        )
        self.assertEqual(
            pw,
            {
                "codec": "PCM 24-bit/48kHz",
                "profile": "USB-C Duplex",
                "sink_name": "alsa_output.usb-Generic_REDMI_Headphones_Neo_73670_CEAGDF6QT0-00.analog-stereo",
                "volume": 45,
                "muted": False,
                "is_default": True,
            },
        )

    def test_daemon_usb_cable_state_charging_and_unplug(self) -> None:
        from unittest import mock

        usb_info = {
            "vid": "2717",
            "pid": "50f0",
            "product": "REDMI Headphones Neo",
            "manufacturer": "Generic",
            "serial": "73670/CEAGDF6QT0",
            "bcd_device": "2681",
            "max_power": "300mA",
            "sysfs_path": "/sys/bus/usb/devices/1-5",
        }
        bt_dev = {
            "path": "/org/bluez/hci0/dev_A0_90_B5_F8_E2_A7",
            "mac": "A0:90:B5:F8:E2:A7",
            "name": "REDMI Headphones Neo",
            "alias": "REDMI Headphones Neo",
            "connected": False,
            "paired": True,
            "uuids": [],
            "bluez_battery": None,
            "beacon_battery": None,
            "beacon_charging": False,
            "vid": None,
            "pid": None,
            "is_redmi_neo": True,
        }

        saved_cfg: dict[str, object] = {}
        with mock.patch.object(
            redmid,
            "load_config",
            return_value={
                "last_battery": 10,
                "anc": "anc",
                "anc_level": "deep",
                "adaptive_anc": True,
                "eq": "voice",
            },
        ), mock.patch.object(redmid, "save_config", side_effect=lambda d: saved_cfg.update(d)):
            daemon = redmid.RedmiNeoDaemon()

            with mock.patch.object(redmid, "query_usb_redmi_neo", return_value=usb_info), mock.patch.object(
                redmid, "query_bluez_devices", return_value=[bt_dev]
            ), mock.patch.object(
                redmid,
                "query_usb_audio_info",
                return_value={
                    "codec": "PCM 24-bit/48kHz",
                    "profile": "USB-C Duplex",
                    "sink_name": "alsa_output.usb-Generic_REDMI_Headphones_Neo_73670_CEAGDF6QT0-00.analog-stereo",
                    "volume": 45,
                    "muted": False,
                    "is_default": True,
                },
            ):
                daemon.refresh_bluez(force_codec=True)

            snap = daemon._snapshot_state()
            self.assertTrue(snap["connected"])
            self.assertTrue(snap["usb_connected"])
            self.assertFalse(snap["bluetooth_connected"])
            self.assertEqual(snap["connection_type"], "usb")
            self.assertTrue(snap["charging"])
            self.assertTrue(snap["usb_charging"])
            # Stale pre-cable battery must not be displayed as live over USB-C
            self.assertIsNone(snap["battery"])
            self.assertEqual(snap["battery_source"], "usb")
            self.assertEqual(snap["codec"], "PCM 24-bit/48kHz")
            self.assertEqual(snap["profile"], "USB-C Duplex")
            self.assertEqual(snap["usb_volume"], 45)
            self.assertTrue(snap["usb_default_audio"])
            # Default mode on cable must be Off (ANC) and Balanced (EQ), and neither can be used
            self.assertEqual(snap["anc"], "off")
            self.assertFalse(snap["anc_available"])
            self.assertFalse(snap["adaptive_anc"])
            self.assertEqual(snap["eq"], "balanced")
            self.assertFalse(snap["eq_available"])
            # Persisted BT ANC / EQ / battery preferences must not be clobbered by USB-only mode
            self.assertEqual(saved_cfg.get("anc"), "anc")
            self.assertEqual(saved_cfg.get("anc_level"), "deep")
            self.assertEqual(saved_cfg.get("eq"), "voice")
            self.assertEqual(saved_cfg.get("last_battery"), 10)

            # Hardware write without Bluetooth RCSP must keep ANC off and report a clear error
            res = daemon.handle_command({"cmd": "anc", "mode": "anc"})
            self.assertEqual(res["anc"], "off")
            self.assertIn("USB-C cable", res["error"])

            # Disconnect over USB-only must not invoke bluez_connect_device
            with mock.patch.object(redmid, "query_usb_redmi_neo", return_value=usb_info), mock.patch.object(
                redmid, "query_bluez_devices", return_value=[bt_dev]
            ), mock.patch.object(redmid, "bluez_connect_device") as mock_bt_conn:
                res_disc = daemon.handle_command({"cmd": "disconnect"})
                mock_bt_conn.assert_not_called()
                self.assertIn("USB-C", res_disc["error"])

            # Unplugging cable clears connected, charging, and codec, and restores BT ANC/EQ/battery cache
            with mock.patch.object(redmid, "query_usb_redmi_neo", return_value=None), mock.patch.object(
                redmid, "query_bluez_devices", return_value=[bt_dev]
            ):
                daemon.refresh_bluez(force_codec=False)

            snap_after = daemon._snapshot_state()
            self.assertFalse(snap_after["connected"])
            self.assertFalse(snap_after["usb_connected"])
            self.assertEqual(snap_after["connection_type"], "none")
            self.assertFalse(snap_after["charging"])
            self.assertFalse(snap_after["usb_charging"])
            self.assertIsNone(snap_after["codec"])
            self.assertEqual(snap_after["battery"], 10)
            self.assertEqual(snap_after["battery_source"], "cached")
            self.assertTrue(snap_after["anc_available"])
            self.assertEqual(snap_after["anc"], "anc")
            self.assertTrue(snap_after["eq_available"])
            self.assertEqual(snap_after["eq"], "voice")


if __name__ == "__main__":
    unittest.main()
