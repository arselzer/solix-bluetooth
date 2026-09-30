"""Schedule framing, guarded controls and power-flow confirmation failures."""

import asyncio
import base64
import json
from types import SimpleNamespace
import time

import pytest

from solix_link import APServiceConfig, LocalMqttServer, NativeMqttCommands, PowerFlowTimeout, TouPeriod, power_flow
from solix_link.protocol import decode_telemetry, parse_packet, parse_tlvs, tlv
from solix_link.tou import periods_from_d9, validate_periods


def fields(request):
    payload = json.loads(json.loads(request.payload)["payload"])
    frame = parse_packet(base64.b64decode(payload["data"]))
    return frame.command.hex(), parse_tlvs(frame.payload)


def test_corrected_schedule_has_no_duplicate_count_or_output_fields():
    commands = NativeMqttCommands("SYNTHETIC", "a" * 40)
    command, tags = fields(commands.tou_plan((TouPeriod("peak", 0, 24),), enabled=True))
    assert command == "0090" and set(tags) == {0xA1, 0xA2, 0xA3, 0xA4, 0xA6, 0xA7, 0xFD}
    assert tags[0xA1] == b"\x22" and tags[0xA2] == b"\x01\x01"
    assert tags[0xA6] == b"\x01\x01" and tags[0xA7] == b"\x04\x01\x00\x18"
    _, clear = fields(commands.tou_plan(()))
    assert clear[0xA2] == b"\x01\x00" and clear[0xA6] == b"\x01\x00" and clear[0xA7] == b"\x04\x00"
    _, reserve = fields(commands.backup_reserve(85))
    assert set(reserve) == {0xA1, 0xA5, 0xFD} and reserve[0xA5] == b"\x01\x55"


@pytest.mark.parametrize("args", [("unknown", 0, 24), ("peak", True, 24), ("peak", 23, 2),
                                  ("peak", -1, 24), ("peak", 0, 25), ("peak", 1, 1)])
def test_reject_invalid_or_overnight_period(args):
    with pytest.raises(ValueError):
        TouPeriod(*args)


def test_reject_overlapping_and_excess_slots_and_roundtrip_overnight_split():
    with pytest.raises(ValueError, match="overlap"):
        validate_periods([TouPeriod("peak", 0, 12), TouPeriod("off_peak", 11, 24)])
    with pytest.raises(ValueError, match="six"):
        validate_periods([TouPeriod("peak", i, i + 1) for i in range(7)])
    plan = (TouPeriod("peak", 0, 2), TouPeriod("off_peak", 2, 23), TouPeriod("peak", 23, 24))
    d9 = bytes([4, 1, 1, 85, 90, 1, 3]) + b"".join(p.to_bytes() for p in plan) + bytes(19)
    assert periods_from_d9(d9) == plan
    with pytest.raises(ValueError, match="Incomplete"):
        periods_from_d9(d9[:-1])
    with pytest.raises(ValueError, match="nonempty"):
        NativeMqttCommands("SYNTHETIC", "a" * 40).tou_plan((), enabled=True)


@pytest.mark.parametrize("percentage", [True, 0, 6, 101, 85.0])
def test_reserve_validation_precedes_io(percentage):
    with pytest.raises(ValueError):
        NativeMqttCommands("SYNTHETIC", "a" * 40).backup_reserve(percentage)


def test_flow_requires_actual_power_and_battery_state():
    m = {"ac_output_enabled": 1, "ac_input_connected": 1, "usage_mode": "standard",
         "battery_status": "discharging", "ac_input_power_w": 0, "ac_output_power_w": 900}
    assert power_flow(m) == "battery"  # Standard is not proof of grid return.
    assert power_flow({**m, "battery_status": "idle"}) == "transitioning"
    assert power_flow({**m, "battery_status": "idle", "ac_input_power_w": 900}) == "grid"
    assert power_flow({**m, "ac_input_power_w": 0, "ac_output_power_w": 0}) == "unknown"


class Station:
    """Simulate ACKed-but-ignored plans and latched discharge after Standard."""

    def __init__(self, server):
        self.server = server
        self.writer = SimpleNamespace(is_closing=lambda: False)
        self.mode, self.reserve, self.fast, self.timer = 0, 10, 0, 0
        self.plan = ()
        self.battery = False
        self.ignore_plans = self.grid_stuck = self.truncated = False
        self.ready = b"\x34"
        self.requests = []

    async def request(self, request, timeout=12):
        command, tags = fields(request)
        self.requests.append((command, tags))
        if command == "0089":
            return b"\x00" + tlv(0xA1, self.ready)
        if command == "0090":
            if 0xA5 in tags:
                self.reserve = tags[0xA5][1]
            if 0xA2 in tags and not self.ignore_plans:
                self.mode = tags[0xA2][1]
                count = tags[0xA6][1]
                data = tags[0xA7][1:1 + 3 * count]
                self.plan = tuple(data[i:i + 3] for i in range(0, len(data), 3))
                if self.mode and self.plan:
                    if self.plan[0][0] == 1:
                        self.battery = True
                    elif self.plan[0][0] == 3 and not self.grid_stuck:
                        self.battery = False
            return b"\x00"
        assert command == "0100"
        d9 = bytes([4, self.plan[0][0] if self.mode and self.plan else 0,
                    self.mode, self.reserve, 90, 1, len(self.plan)]) + b"".join(self.plan) + bytes(19)
        if self.truncated:
            d9 = d9[:-1]
        a4 = bytearray(34)
        a4[0] = 4
        a4[1:5] = self.timer.to_bytes(4, "little")
        a4[5:7] = (1800).to_bytes(2, "little")
        a4[21] = self.fast
        watts = (900).to_bytes(2, "little")
        data = (tlv(0xA3, bytes([1, int(self.battery)]))
                + tlv(0xA4, a4) + tlv(0xA5, bytes([4, 25, 0, 90, 100]))
                + tlv(0xA6, b"\x04" + watts + (bytes(2) if self.battery else watts) + bytes(4))
                + tlv(0xA7, b"\x04\x01" + watts + b"\x01\x00\x00") + tlv(0xD9, d9))
        self.server.metrics, _ = decode_telemetry(data, self.server.config.model)
        self.server.last_seen = time.time()
        return b"\x00" + data


def server(tmp_path, *, allow_control=True):
    config = APServiceConfig("ups", "wlan_ap", "phy9", "AT", "A1783SYNTHETIC001", "a" * 40)
    result = LocalMqttServer(config, tmp_path, allow_control=allow_control)
    station = Station(result)
    result.connection = station
    return result, station


def test_controls_disabled_and_missing_baseline_send_no_write(tmp_path):
    async def run():
        service, station = server(tmp_path, allow_control=False)
        with pytest.raises(PermissionError):
            await service.set_tou_plan((TouPeriod("peak", 0, 24),), enabled=True)
        assert not station.requests
        service.allow_control = True
        station.truncated = True
        with pytest.raises(ValueError, match="Incomplete"):
            await service.set_backup_reserve(85)
        assert not any(command == "0090" for command, _ in station.requests)
    asyncio.run(run())


@pytest.mark.parametrize("guard", ["ready", "fast", "timer"])
def test_activation_guards_prevent_any_write(tmp_path, guard):
    async def run():
        service, station = server(tmp_path)
        setattr(station, guard, b"\x31" if guard == "ready" else 1)
        with pytest.raises((ValueError, RuntimeError)):
            await service.set_tou_plan((TouPeriod("peak", 0, 24),), enabled=True)
        assert not any(command == "0090" for command, _ in station.requests)
    asyncio.run(run())


def test_reserve_bounds_and_ignored_plan_are_not_success(tmp_path):
    async def run():
        service, station = server(tmp_path)
        for value in (5, 95):
            with pytest.raises(ValueError, match="Reserve must"):
                await service.set_backup_reserve(value)
        assert not any(command == "0090" for command, _ in station.requests)
        result = await service.set_backup_reserve(85)
        assert result["metrics"]["backup_reserve_percentage"] == 85
        station.ignore_plans = True
        with pytest.raises(RuntimeError, match="not confirmed"):
            await service.set_tou_plan((TouPeriod("peak", 0, 24),), enabled=True)
        assert station.mode == 0 and station.reserve == 85
    asyncio.run(run())


def test_grid_return_uses_tariff3_then_checks_after_standard(tmp_path, monkeypatch):
    async def run():
        service, station = server(tmp_path)
        result = await service.set_tou_plan((TouPeriod("peak", 0, 24),), enabled=True)
        assert result["power_flow"] == "battery"
        async def yield_only(_seconds):
            pass
        monkeypatch.setattr("solix_link.mqtt_intercept.asyncio.sleep", yield_only)
        result = await service.return_to_grid(timeout=5)
        assert result["grid_power_confirmed"] and result["power_flow"] == "grid"
        assert result["metrics"]["usage_mode"] == "standard" and result["tou_plan"] == []
        writes = [tags for command, tags in station.requests if command == "0090"]
        assert any(tags.get(0xA7) == b"\x04\x03\x00\x18" for tags in writes)
        assert all(set(tags) <= {0xA1, 0xA2, 0xA3, 0xA4, 0xA6, 0xA7, 0xFD} for tags in writes)
    asyncio.run(run())


def test_grid_timeout_clears_plan_without_claiming_recovery(tmp_path):
    async def run():
        service, station = server(tmp_path)
        station.battery = station.grid_stuck = True
        with pytest.raises(PowerFlowTimeout) as error:
            await service.return_to_grid(timeout=5)
        assert station.mode == 0 and not station.plan and station.battery
        assert error.value.snapshot["power_flow"] == "battery"
        assert "grid_power_confirmed" not in error.value.snapshot
    asyncio.run(run())


@pytest.mark.parametrize("argv,command,expected", [
    (["ap-service-set-reserve", "--reserve", "85"], "set-backup-reserve", {"reserve": 85}),
    (["ap-service-set-tou", "--mode", "time_of_use", "--period", "peak:0:24"], "set-tou-plan",
     {"periods": [{"tariff": "peak", "start_hour": 0, "end_hour": 24}], "enabled": True}),
    (["ap-service-grid", "--timeout", "20"], "return-grid", {"timeout": 20}),
])
def test_cli_dispatch_uses_private_control_socket(tmp_path, monkeypatch, argv, command, expected):
    from solix_link import cli, ap_service_cli
    requests = []
    async def request(directory, action, **fields):
        requests.append((directory, action, fields))
        return {"metrics": {}}
    monkeypatch.setattr(ap_service_cli, "ap_service_request", request)
    assert cli.main(argv + ["--directory", str(tmp_path)]) == 0
    assert requests == [(tmp_path, command, expected)]


def test_socket_rejects_bad_schedule_before_io_and_confirms_reserve(tmp_path):
    from solix_link.ap_service import APService, ap_service_request
    async def run():
        mqtt, station = server(tmp_path)
        (tmp_path / "mqtt-response.json").write_text("{}")
        service = APService(mqtt.config, tmp_path, allow_control=True)
        service.mqtt = mqtt
        service.stations[mqtt.config.name] = mqtt
        listener = await asyncio.start_unix_server(service._control, path=service.socket_path)
        service._servers.append(listener)
        try:
            with pytest.raises(ValueError):
                await ap_service_request(tmp_path, "set-tou-plan", periods=[{"tariff": "peak", "start_hour": True, "end_hour": 24}], enabled=True)
            assert not station.requests
            result = await ap_service_request(tmp_path, "set-backup-reserve", reserve=85)
            assert result["metrics"]["backup_reserve_percentage"] == 85
            assert mqtt.config.device_serial not in json.dumps(result)
        finally:
            await service.stop()
    asyncio.run(run())


@pytest.mark.parametrize("timer_key", ["ac_output_timer_remaining_seconds", "ac_output_timeout_seconds"])
def test_gen2_tou_guard_accepts_both_countdowns_and_rejects_active_timer(tmp_path, timer_key):
    before = {"ac_output_enabled": 1, "ac_input_connected": 1, "max_charge_percentage": 100,
              "min_charge_percentage": 1, "ac_charging_power_limit_w": 1200,
              "ac_fast_charge_enabled": 0, "backup_reserve_percentage": 10, timer_key: 0}
    config = APServiceConfig("ups", "wlan_ap", "phy9", "AT", "A1763SYNTHETIC001", "a" * 40,
                             model="c1000_gen2")
    server = LocalMqttServer(config, tmp_path)
    server._tou_protected(before)
    with pytest.raises(RuntimeError, match="Protected setting"):
        server._tou_protected(before, {**before, timer_key: 1})
    requests = []
    async def ready(request):
        requests.append(request)
        return b"\x00" + tlv(0xA1, b"\x34")
    connection = SimpleNamespace(request=ready)
    asyncio.run(server._tou_ready(connection, before))
    assert len(requests) == 1
    with pytest.raises(ValueError, match="Active AC-output timer"):
        asyncio.run(server._tou_ready(connection, {**before, timer_key: 1}))
    assert len(requests) == 1  # Timer refusal precedes I/O.
