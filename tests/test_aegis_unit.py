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
from app.aegis.engine import AegisEngine


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
        assert len(registry.rules) == 8
        names = [r.name for r in registry.rules]
        assert "r001_throttle_ota" in names
        assert "r002_mqtt_qos_downgrade" in names
        assert "r003_device_soft_restart" in names
        assert "r004_scale_heartbeat" in names
        assert "r005_rollback_ota_batch" in names
        assert "r006_human_escalation" in names
        assert "r007_migrate_device_pool" in names
        assert "r008_cleanup_firmware_artifacts" in names

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
        assert len(registry.rules) == 7


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

    def test_action_registry_has_r001_to_r008(self):
        assert "throttle_ota" in ACTION_REGISTRY
        assert "mqtt_qos_downgrade" in ACTION_REGISTRY
        assert "device_soft_restart" in ACTION_REGISTRY
        assert "scale_heartbeat" in ACTION_REGISTRY
        assert "rollback_ota_batch" in ACTION_REGISTRY
        assert "human_escalation" in ACTION_REGISTRY
        assert "migrate_device_pool" in ACTION_REGISTRY
        assert "cleanup_firmware_artifacts" in ACTION_REGISTRY


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


class TestRuleCooldown:
    def test_cooldown_enforced(self):
        registry = RuleRegistry()
        registry.add_rule(RemediationRule(
            name="cooldown_rule",
            condition=lambda s: True,
            action_name="throttle_ota",
            cooldown_seconds=3600,
            priority=1,
        ))
        signal = _make_signal()
        first = registry.get_matching_rule(signal)
        assert first is not None
        second = registry.get_matching_rule(_make_signal(metric="fleet_ota_in_progress", value=10.0))
        assert second is None, "Cooldown should prevent second match"

    def test_cooldown_expired_allows_match(self):
        registry = RuleRegistry()
        registry.add_rule(RemediationRule(
            name="fast_cooldown",
            condition=lambda s: True,
            action_name="throttle_ota",
            cooldown_seconds=0,
            priority=1,
        ))
        signal = _make_signal()
        first = registry.get_matching_rule(signal)
        assert first is not None
        second = registry.get_matching_rule(_make_signal(metric="fleet_ota_in_progress", value=10.0))
        assert second is not None, "Zero cooldown should allow immediate re-match"


class TestRuleEnableDisable:
    def test_enable_rule_returns_false_for_unknown(self):
        registry = RuleRegistry()
        assert registry.enable_rule("nonexistent", False) is False

    def test_enable_rule_toggles(self):
        registry = build_default_registry()
        assert registry.get_rule("r001_throttle_ota").enabled is True
        registry.enable_rule("r001_throttle_ota", False)
        assert registry.get_rule("r001_throttle_ota").enabled is False
        registry.enable_rule("r001_throttle_ota", True)
        assert registry.get_rule("r001_throttle_ota").enabled is True


class TestActionFailurePaths:
    @pytest.mark.asyncio
    async def test_device_soft_restart_mqtt_disconnected(self):
        action = DeviceSoftRestartAction()
        signal = _make_signal(metric="fleet_device_signal", value=-95.0,
                              severity="critical", device_ids=["dev-1", "dev-2"])
        context = {}
        with patch("app.aegis.actions.mqtt_client") as mock_mqtt:
            mock_mqtt.is_connected = False
            result = await action.execute(signal, context)
        assert result.success is False
        assert "MQTT publish failed" in (result.error_message or "")
        assert len(result.output_snapshot.get("devices_failed", [])) == 2


class TestEngineMetricsParsing:
    def test_classify_metrics_parses_fleet_gauges(self):
        from app.aegis.engine import AegisEngine
        engine = AegisEngine()
        text = "fleet_active_devices 1.0\nfleet_ota_in_progress 5.0\n"
        signals = engine._classify_metrics(text)
        assert len(signals) == 2

    def test_classify_metrics_ignores_non_fleet(self):
        engine = AegisEngine()
        text = "python_info 1.0\nfleet_active_devices 10.0\n"
        signals = engine._classify_metrics(text)
        assert len(signals) == 0

    def test_classify_metrics_ignores_comments(self):
        engine = AegisEngine()
        text = "# HELP fleet_active_devices Active devices\n# TYPE fleet_active_devices gauge\nfleet_active_devices 1.0\n"
        signals = engine._classify_metrics(text)
        assert len(signals) == 1
        assert signals[0].metric_name == "fleet_active_devices"

    def test_signal_history_uses_metric_name_key(self):
        engine = AegisEngine()
        text = "fleet_active_devices 1.0\nfleet_ota_in_progress 5.0\n"
        engine._classify_metrics(text)
        assert "fleet_active_devices" in engine._signal_history
        assert "fleet_ota_in_progress" in engine._signal_history
        assert engine._signal_history["fleet_active_devices"] == [1.0]


class TestEngineFullCycle:
    @pytest.mark.asyncio
    async def test_run_cycle_creates_remediations(self):
        from app.database import Base
        from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
        from app.aegis.models import Remediation
        from sqlalchemy import select

        test_engine = create_async_engine("sqlite+aiosqlite://")
        test_session_factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
        async with test_engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        engine = AegisEngine()
        with patch.object(engine, '_scrape_metrics') as mock:
            mock.return_value = "fleet_active_devices 1.0\nfleet_ota_in_progress 5.0\n"
            async with test_session_factory() as db:
                await engine.run_cycle(db)

                result = await db.execute(select(Remediation))
                rows = result.scalars().all()
                assert len(rows) > 0
        await test_engine.dispose()

    @pytest.mark.asyncio
    async def test_run_cycle_no_metrics_no_remediation(self):
        from app.database import Base
        from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
        from app.aegis.models import Remediation
        from sqlalchemy import select

        test_engine = create_async_engine("sqlite+aiosqlite://")
        test_session_factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
        async with test_engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        engine = AegisEngine()
        with patch.object(engine, '_scrape_metrics') as mock:
            mock.return_value = ""
            async with test_session_factory() as db:
                await engine.run_cycle(db)

                result = await db.execute(select(Remediation))
                rows = result.scalars().all()
                assert len(rows) == 0
        await test_engine.dispose()

    @pytest.mark.asyncio
    async def test_process_ingest_creates_remediation(self):
        from app.database import Base
        from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
        from app.aegis.models import Remediation
        from app.aegis.schemas import IngestRequest
        from sqlalchemy import select

        test_engine = create_async_engine("sqlite+aiosqlite://")
        test_session_factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
        async with test_engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        engine = AegisEngine()
        req = IngestRequest(
            metric_name="fleet_ota_in_progress",
            value=10.0,
            threshold=3.0,
            severity="warning",
        )
        async with test_session_factory() as db:
            signal = await engine.process_ingest(db, req)
            assert signal is not None

            result = await db.execute(select(Remediation))
            rows = result.scalars().all()
            assert len(rows) > 0
        await test_engine.dispose()

    @pytest.mark.asyncio
    async def test_process_ingest_escalates_on_no_match(self):
        from app.database import Base
        from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
        from app.aegis.models import Remediation
        from app.aegis.schemas import IngestRequest
        from app.models import Alert
        from sqlalchemy import select

        test_engine = create_async_engine("sqlite+aiosqlite://")
        test_session_factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
        async with test_engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        engine = AegisEngine()
        req = IngestRequest(
            metric_name="fleet_unknown_metric",
            value=999.0,
            threshold=1.0,
            severity="critical",
        )
        async with test_session_factory() as db:
            signal = await engine.process_ingest(db, req)
            assert signal is not None

            result = await db.execute(select(Remediation))
            rows = result.scalars().all()
            assert len(rows) > 0
        await test_engine.dispose()


class TestRuleConfigModel:
    def test_rule_config_defaults(self):
        from app.aegis.models import RuleConfig
        config = RuleConfig(rule_name="test_rule")
        assert config.rule_name == "test_rule"
        assert config.enabled is True
        assert config.get_threshold_overrides() == {}

    def test_rule_config_threshold_overrides(self):
        from app.aegis.models import RuleConfig
        config = RuleConfig(rule_name="test_rule", threshold_overrides='{"cpu": 90}')
        assert config.get_threshold_overrides() == {"cpu": 90}

    def test_rule_config_bad_json_returns_empty(self):
        from app.aegis.models import RuleConfig
        config = RuleConfig(rule_name="test_rule", threshold_overrides="not-json")
        assert config.get_threshold_overrides() == {}


class TestNewActionsR005ToR008:
    def test_action_registry_has_r005_to_r008(self):
        assert "rollback_ota_batch" in ACTION_REGISTRY
        assert "human_escalation" in ACTION_REGISTRY
        assert "migrate_device_pool" in ACTION_REGISTRY
        assert "cleanup_firmware_artifacts" in ACTION_REGISTRY

    @pytest.mark.asyncio
    async def test_rollback_ota_batch_execute_no_devices(self):
        from app.aegis.actions import RollbackOtaBatchAction
        action = RollbackOtaBatchAction()
        signal = _make_signal(metric="fleet_ota_in_progress", value=5.0, device_ids=[])
        result = await action.execute(signal, {})
        assert result.success is True
        assert result.output_snapshot["devices_rolled_back"] == []

    @pytest.mark.asyncio
    async def test_human_escalation_execute(self):
        from app.database import Base
        from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
        test_engine = create_async_engine("sqlite+aiosqlite://")
        test_session_factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
        async with test_engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with test_session_factory() as db:
            from app.aegis.actions import HumanEscalationAction
            action = HumanEscalationAction()
            signal = _make_signal(metric="fleet_ota_in_progress", value=10.0, threshold=3.0,
                                  severity="critical")
            result = await action.execute(signal, {"db": db})
            assert result.success is True
            assert result.output_snapshot["action"] == "human_escalation"
        await test_engine.dispose()

    @pytest.mark.asyncio
    async def test_migrate_device_pool_execute(self):
        from app.aegis.actions import MigrateDevicePoolAction
        action = MigrateDevicePoolAction()
        signal = _make_signal(metric="fleet_device_cpu", value=95.0, threshold=90.0,
                              device_ids=["dev-1", "dev-2"])
        with patch("app.aegis.actions.mqtt_client") as mock_mqtt:
            mock_mqtt.is_connected = True
            mock_mqtt.client.publish.return_value.rc = 0
            result = await action.execute(signal, {})
        assert result.success is True
        assert "devices_migrated" in result.output_snapshot

    @pytest.mark.asyncio
    async def test_migrate_device_pool_mqtt_disconnected(self):
        from app.aegis.actions import MigrateDevicePoolAction
        action = MigrateDevicePoolAction()
        signal = _make_signal(metric="fleet_device_cpu", value=95.0, threshold=90.0,
                              device_ids=["dev-1"])
        with patch("app.aegis.actions.mqtt_client") as mock_mqtt:
            mock_mqtt.is_connected = False
            result = await action.execute(signal, {})
        assert result.success is False
        assert "Failed to migrate" in (result.error_message or "")

    @pytest.mark.asyncio
    async def test_migrate_device_pool_rollback(self):
        from app.aegis.actions import MigrateDevicePoolAction
        action = MigrateDevicePoolAction()
        with patch("app.aegis.actions.mqtt_client") as mock_mqtt:
            mock_mqtt.is_connected = True
            ok = await action.rollback(_make_signal(device_ids=["dev-1"]), {})
        assert ok is True

    @pytest.mark.asyncio
    async def test_cleanup_firmware_artifacts_execute(self):
        from app.database import Base
        from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
        test_engine = create_async_engine("sqlite+aiosqlite://")
        test_session_factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
        async with test_engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with test_session_factory() as db:
            from app.aegis.actions import CleanupFirmwareArtifactsAction
            action = CleanupFirmwareArtifactsAction()
            signal = _make_signal(metric="fleet_disk_usage", value=85.0, threshold=80.0)
            result = await action.execute(signal, {"db": db})
            assert result.success is True
            assert "artifacts_deleted" in result.output_snapshot
            assert "total_freed_mb" in result.output_snapshot
        await test_engine.dispose()

    @pytest.mark.asyncio
    async def test_new_action_status_methods(self):
        from app.aegis.actions import RollbackOtaBatchAction, HumanEscalationAction, MigrateDevicePoolAction, CleanupFirmwareArtifactsAction
        for action_cls in [RollbackOtaBatchAction, HumanEscalationAction, MigrateDevicePoolAction, CleanupFirmwareArtifactsAction]:
            action = action_cls()
            s = await action.status()
            assert s["action"] == action.name
            assert s["ready"] is True

    @pytest.mark.asyncio
    async def test_rollback_ota_batch_rollback_returns_true(self):
        from app.aegis.actions import RollbackOtaBatchAction
        action = RollbackOtaBatchAction()
        ok = await action.rollback(_make_signal(), {})
        assert ok is True

    @pytest.mark.asyncio
    async def test_human_escalation_rollback_returns_true(self):
        from app.aegis.actions import HumanEscalationAction
        action = HumanEscalationAction()
        ok = await action.rollback(_make_signal(), {})
        assert ok is True

    @pytest.mark.asyncio
    async def test_cleanup_firmware_artifacts_rollback_returns_true(self):
        from app.aegis.actions import CleanupFirmwareArtifactsAction
        action = CleanupFirmwareArtifactsAction()
        ok = await action.rollback(_make_signal(), {})
        assert ok is True
