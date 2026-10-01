"""Native AC Smart guards use fresh full reports, independent of cached state."""
import asyncio
import base64
import json

import pytest

from solix_link.ap_service_config import APServiceConfig
from solix_link.mqtt_intercept import LocalMqttServer
from solix_link.protocol import Model, parse_packet, parse_tlvs, tlv
from test_original_prime_controls import BASELINE, FLAGS, payload_for

UNCHANGED = object()

def server_with_connection(tmp_path, *, enabled=False, timer=b"\x03\0\0\0\0", state=0,
                           post_timer=UNCHANGED, post_flags=False):
    config = APServiceConfig("original", "wlan_ap", "phy9", "AT", "A1761TEST0000001", "a" * 40,
                             model=Model.C1000)
    server = LocalMqttServer(config, tmp_path, allow_control=True)
    live = BASELINE | {"ac_output_enabled": state, "ac_power_saving_mode_enabled": int(not enabled)}
    # A cached zero and AC-off report cannot substitute for the fresh response.
    server.metrics = BASELINE | {"ac_output_enabled": 0, "ac_output_timer_remaining_seconds": 0}

    class Connection:
        writes = 0
        reads = 0
        finished = False

        async def request(self, request):
            assert request.response_command == "0840"
            self.reads += 1
            flags = bytearray(FLAGS)
            flags[2] = live["ac_power_saving_mode_enabled"] + 1
            if self.writes and post_flags:
                flags[-1] ^= 1
            fields = parse_tlvs(payload_for(live, bytes(flags)))
            actual_timer = post_timer if self.writes and post_timer is not UNCHANGED else timer
            fields.pop(0xA2, None)
            if actual_timer is not None:
                fields[0xA2] = actual_timer
            return b"\x00" + b"".join(tlv(tag, value) for tag, value in fields.items())

        async def send_original_setting(self, request):
            assert request.response_command == "0877" and not request.response_aliases
            packet = parse_packet(base64.b64decode(json.loads(json.loads(request.payload)["payload"])["data"]))
            assert packet.command.hex() == "0077"
            fields = parse_tlvs(packet.payload)
            assert set(fields) == {0xA1, 0xA2, 0xFE}
            assert fields[0xA1] == b"\x22" and fields[0xA2] == bytes((1, int(enabled)))
            self.writes += 1
            live["ac_power_saving_mode_enabled"] = int(enabled)

        def check_original_setting_response(self):
            pass

        def finish_original_setting(self):
            self.finished = True

    connection = Connection()
    server.connection = connection
    return server, connection


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("timer", [None, b"", b"\x01\0", b"\x02\0\0", b"\x03\0\0\0",
                                    b"\x03\x01\0\0\0", b"\x03\0\0\0\0\0"])
def test_missing_malformed_or_active_fresh_timer_blocks_before_write(tmp_path, enabled, timer):
    server, connection = server_with_connection(tmp_path, enabled=enabled, timer=timer)
    with pytest.raises(RuntimeError, match="fresh inactive AC timer"):
        asyncio.run(server.set_ac_power_saving_enabled(enabled))
    assert connection.reads == 1 and connection.writes == 0 and not connection.finished


@pytest.mark.parametrize("enabled", [False, True])
def test_fresh_ac_on_rejects_cached_off_in_both_directions(tmp_path, enabled):
    server, connection = server_with_connection(tmp_path, enabled=enabled, state=1)
    with pytest.raises(RuntimeError, match="AC output to be off; no write sent"):
        asyncio.run(server.set_ac_power_saving_enabled(enabled))
    assert connection.reads == 1 and connection.writes == 0


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("post_timer", [None, b"\x02\0\0", b"\x03\x01\0\0\0"])
def test_new_or_invalid_post_write_timer_fails_without_retry(tmp_path, enabled, post_timer):
    server, connection = server_with_connection(tmp_path, enabled=enabled, post_timer=post_timer)
    with pytest.raises(RuntimeError, match="setting may have changed"):
        asyncio.run(server.set_ac_power_saving_enabled(enabled))
    assert connection.writes == 1 and connection.reads == 2 and connection.finished


@pytest.mark.parametrize("enabled", [False, True])
def test_complete_f8_tail_remains_protected(tmp_path, enabled):
    server, connection = server_with_connection(tmp_path, enabled=enabled, post_flags=True)
    with pytest.raises(RuntimeError, match="setting may have changed"):
        asyncio.run(server.set_ac_power_saving_enabled(enabled))
    assert connection.writes == 1 and connection.finished
