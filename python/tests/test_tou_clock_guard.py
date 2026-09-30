"""UTC-offset retention must not silently activate an hourly C1000 plan."""

import asyncio
import pytest

from solix_link.ap_service_config import APServiceConfig
from solix_link.mqtt_intercept import LocalMqttServer
from solix_link.protocol import Model
from solix_link.tou import TouPeriod


@pytest.mark.parametrize('plan', [
    (TouPeriod('peak', 0, 12), TouPeriod('off_peak', 12, 24)),
    (TouPeriod('peak', 0, 23),), (TouPeriod('peak', 1, 24),),
])
def test_utc_hourly_activation_is_rejected_before_network(tmp_path, plan):
    cfg = APServiceConfig('test', 'wlan_unused', 'phy9', 'AT', 'A1763SYNTHETIC001', 'a' * 40,
                          model=Model.C1000_GEN2)
    server = LocalMqttServer(cfg, tmp_path, allow_control=True)
    with pytest.raises(ValueError, match='timezone offset'):
        asyncio.run(server.set_tou_plan(plan, enabled=True))


@pytest.mark.parametrize('model,zone,enabled,plan', [
    (Model.C1000_GEN2, 'Etc/UTC', True, (TouPeriod('peak', 0, 24),)),
    (Model.C1000_GEN2, 'Etc/UTC', True, (TouPeriod('mid_peak', 0, 24),)),
    (Model.C1000_GEN2, 'Etc/UTC', True, (TouPeriod('off_peak', 0, 24),)),
    (Model.C1000_GEN2, 'Etc/UTC', False, (TouPeriod('peak', 0, 12),)),
    (Model.C1000_GEN2, 'Europe/Vienna', True, (TouPeriod('peak', 0, 12),)),
    (Model.C2000_GEN2, 'Etc/UTC', True, (TouPeriod('peak', 0, 12),)),
])
def test_clock_guard_does_not_block_all_day_storage_or_other_profiles(tmp_path, model, zone, enabled, plan):
    cfg = APServiceConfig('test', 'wlan_unused', 'phy9', 'AT', 'A1763SYNTHETIC001', 'a' * 40,
                          model=model, timezone_name=zone)
    server = LocalMqttServer(cfg, tmp_path, allow_control=True)
    with pytest.raises(ConnectionError, match='not connected'):
        asyncio.run(server.set_tou_plan(plan, enabled=enabled))
