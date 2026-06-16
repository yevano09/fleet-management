"""
Unit tests for Aegis auto-remediation engine.
"""

from __future__ import annotations

import json
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from app.aegis.schemas import RemediationSignal
from app.aegis.rules import build_default_registry, RemediationRule, RuleRegistry
from app.aegis.actions import (
    ThrottleOtaAction, MqttQosDowngradeAction,
    DeviceSoftRestartAction, ScaleHeartbeatAction,
    ACTION_REGISTRY, RemediationResult,
)


def _make_signal(metric: str = "fleet_ota_in_progress", value: float = 5.0,
                 threshold: float = 3.0, severity: str = "warning",
                 device_ids: list[str] = None) -> RemediationSignal:
    return RemediationSignal(
        id="test-sig-1",
        metric_name=metric,
        value=value,
        threshold=threshold,
        severity=severity,
        timestamp=datetime.now(timezone.utc),
        device_ids=device_ids or [],
        window_seconds=60,
    )


class TestRuleRegistry:
    def test_build_default_registry(self):
        registry = build_default_registry()
        assert len(registry.rules) == 4
        names = [r.name for r in registry.rules]
        assert "r001_throttle_ota" in names
        assert "r002_mqtt_qos_downgrade" in names
        assert "r003_device_soft_restart" in names
        assert "r004_scale_heartbeat" in names

    def test_rules_sorted_by_priority(self):
        registry = build_default_registry()
        priorities = [r.priority for r in registry.rules]
        assert priorities == sorted(priorities)

    def test_matching_rule_ota(self):
        registry = build_default_registry()
        signal = _make_signal(metric="fleet_ota_in_progress", value=5.0, threshold=3.0)
        rule = registry.get_matching_rule(signal)
        assert rule is not None
        assert rule.name == "r001_throttle_ota"

    def test_matching_rule_scale_heartbeat(self):
        registry = build_default_registry()
        signal = _make_signal(metric="fleet_active_devices", value=1.0, threshold=2.0)
        rule = registry.get_matching_rule(signal)
        assert rule is not None
        assert rule.name == "r004_scale_heartbeat"

    def test_matching_rule_device_soft_restart(self):
        registry = build_default_registry()
        signal = _make_signal(metric="fleet_device_signal", value=-95.0,
                              threshold=-90, severity="critical")
        rule = registry.get_matching_rule(signal)
        assert rule is not None
        assert rule.name == "r003_device_soft_restart"

    def test_no_match_returns_none(self):
        registry = build_default_registry()
        signal = _make_signal(metric="fleet_some_other", value=1.0, threshold=5.0)
        rule = registry.get_matching_rule(signal)
        assert rule is None

    def test_disabled_rule_not_matched(self):
        registry = RuleRegistry()
        registry.add_rule(RemediationRule(
            name="test_disabled",
            condition=lambda s: True,
            action_name="throttle_ota",
            enabled=False,
            priority=1,
        ))
        signal = _make_signal()
        rule = registry.get_matching_rule(signal)
        assert rule is None

    def test_rule_priority_order(self):
        registry = RuleRegistry()
        results = []
        registry.add_rule(RemediationRule(
            name="high_priority", condition=lambda s: True,
            action_name="throttle_ota", priority=10,
        ))
        registry.add_rule(RemediationRule(
            name="low_priority", condition=lambda s: True,
            action_name="throttle_ota", priority=100,
        ))
        rule = registry.get_matching_rule(_make_signal())
        assert rule is not None
        assert rule.name == "high_priority"

    def test_get_rule_by_name(self):
        registry = build_default_registry()
        rule = registry.get_rule("r001_throttle_ota")
        assert rule is not None
        assert rule.name == "r001_throttle_ota"

    def test_remove_rule(self):
        registry = build_default_registry()
        registry.remove_rule("r001_throttle_ota")
        assert registry.get_rule("r001_throttle_ota") is None
        assert len(registry.rules) == 3


class TestActions:
    @pytest.mark.asyncio
    async def test_throttle_ota_execute(self):
        action = ThrottleOtaAction()
        signal = _make_signal(metric="fleet_ota_in_progress", value=5.0)
        context = {}
        result = await action.execute(signal, context)
        assert result.success is True
        assert result.output_snapshot["action"] == "throttle_ota"
        assert result.output_snapshot["throttled"] is True
        assert context.get("ota_throttled") is True

    @pytest.mark.asyncio
    async def test_throttle_ota_rollback(self):
        action = ThrottleOtaAction()
        signal = _make_signal()
        context = {"ota_throttled": True}
        ok = await action.rollback(signal, context)
        assert ok is True
        assert context.get("ota_throttled") is False

    @pytest.mark.asyncio
    async def test_mqtt_qos_downgrade_execute(self):
        action = MqttQosDowngradeAction()
        signal = _make_signal(metric="fleet_mqtt_messages_published_total", value=100.0)
        context = {}
        result = await action.execute(signal, context)
        assert result.success is True
        assert "downgraded_topics" in result.output_snapshot
        assert len(result.output_snapshot["downgraded_topics"]) > 0

    @pytest.mark.asyncio
    async def test_mqtt_qos_downgrade_rollback(self):
        action = MqttQosDowngradeAction()
        action._downgraded_topics = ["test/topic"]
        ok = await action.rollback(_make_signal(), {})
        assert ok is True
        assert len(action._downgraded_topics) == 0

    @pytest.mark.asyncio
    async def test_device_soft_restart_execute(self):
        action = DeviceSoftRestartAction()
        signal = _make_signal(metric="fleet_device_signal", value=-95.0,
                              severity="critical", device_ids=["dev-1", "dev-2"])
        context = {}
        with patch("app.aegis.actions.mqtt_client") as mock_mqtt:
            mock_mqtt.is_connected = True
            mock_mqtt.client.publish.return_value.rc = 0
            result = await action.execute(signal, context)
        assert result.success is True

    @pytest.mark.asyncio
    async def test_device_soft_restart_rollback(self):
        action = DeviceSoftRestartAction()
        with patch("app.aegis.actions.mqtt_client") as mock_mqtt:
            mock_mqtt.is_connected = True
            ok = await action.rollback(_make_signal(), {})
        assert ok is True

    @pytest.mark.asyncio
    async def test_scale_heartbeat_execute(self):
        action = ScaleHeartbeatAction()
        signal = _make_signal(metric="fleet_active_devices", value=1.0,
                              threshold=2.0, device_ids=["dev-1"])
        context = {}
        with patch("app.aegis.actions.mqtt_client") as mock_mqtt:
            mock_mqtt.is_connected = True
            mock_mqtt.client.publish.return_value.rc = 0
            result = await action.execute(signal, context)
        assert result.success is True
        assert result.output_snapshot["action"] == "scale_heartbeat"
        assert result.output_snapshot["new_interval_seconds"] == 5

    @pytest.mark.asyncio
    async def test_scale_heartbeat_rollback(self):
        action = ScaleHeartbeatAction()
        action._original_intervals = {"dev-1": 10}
        with patch("app.aegis.actions.mqtt_client") as mock_mqtt:
            mock_mqtt.is_connected = True
            ok = await action.rollback(_make_signal(), {})
        assert ok is True
        assert len(action._original_intervals) == 0

    @pytest.mark.asyncio
    async def test_execute_with_retry_success(self):
        action = ThrottleOtaAction()
        signal = _make_signal()
        result = await action.execute_with_retry(signal, {})
        assert result.success is True

    @pytest.mark.asyncio
    async def test_execute_with_retry_exhaustion(self):
        class FailingAction(ThrottleOtaAction):
            name = "failing_action"
            max_retries = 1

            async def execute(self, signal, context):
                raise RuntimeError("always fails")

        action = FailingAction()
        signal = _make_signal()
        result = await action.execute_with_retry(signal, {})
        assert result.success is False
        assert "Exhausted" in (result.error_message or "")

    def test_action_registry_has_r001_to_r004(self):
        assert "throttle_ota" in ACTION_REGISTRY
        assert "mqtt_qos_downgrade" in ACTION_REGISTRY
        assert "device_soft_restart" in ACTION_REGISTRY
        assert "scale_heartbeat" in ACTION_REGISTRY


class TestSignals:
    def test_signal_creation(self):
        signal = _make_signal()
        assert signal.id == "test-sig-1"
        assert signal.metric_name == "fleet_ota_in_progress"
        assert signal.value == 5.0
        assert signal.severity == "warning"

    def test_signal_json_serializable(self):
        signal = _make_signal()
        d = signal.model_dump()
        assert d["metric_name"] == "fleet_ota_in_progress"
        assert d["value"] == 5.0
        json_str = signal.model_dump_json()
        parsed = json.loads(json_str)
        assert parsed["id"] == "test-sig-1"
