import json
import logging
from datetime import datetime
from typing import Optional

from sqlalchemy import select

from app.utils import utcnow
from app.aegis.schemas import RemediationSignal
from app.aegis.actions import ACTION_REGISTRY, RemediationAction
from app.aegis.metrics import aegis_decisions_total

logger = logging.getLogger(__name__)


class RemediationRule:
    def __init__(self, name: str, condition, action_name: str,
                 cooldown_seconds: int = 300, max_retries: int = 3,
                 priority: int = 100, enabled: bool = True):
        self.name = name
        self.condition = condition
        self.action_name = action_name
        self.cooldown_seconds = cooldown_seconds
        self.max_retries = max_retries
        self.priority = priority
        self.enabled = enabled

    def matches(self, signal: RemediationSignal) -> bool:
        if not self.enabled:
            return False
        return self.condition(signal)

    def get_action(self) -> Optional[RemediationAction]:
        return ACTION_REGISTRY.get(self.action_name)


class RuleRegistry:
    def __init__(self):
        self._rules: list[RemediationRule] = []
        self._last_fired: dict[str, datetime] = {}

    def add_rule(self, rule: RemediationRule):
        self._rules.append(rule)
        self._rules.sort(key=lambda r: r.priority, reverse=False)

    def remove_rule(self, name: str):
        self._rules = [r for r in self._rules if r.name != name]

    def get_rule(self, name: str) -> Optional[RemediationRule]:
        for r in self._rules:
            if r.name == name:
                return r
        return None

    def enable_rule(self, name: str, enabled: bool) -> bool:
        rule = self.get_rule(name)
        if rule:
            rule.enabled = enabled
            return True
        return False

    def update_rule_from_config(self, config: 'RuleConfig'):
        rule = self.get_rule(config.rule_name)
        if not rule:
            return
        if config.enabled is not None:
            rule.enabled = config.enabled
        if config.cooldown_seconds is not None:
            rule.cooldown_seconds = config.cooldown_seconds
        if config.max_retries is not None:
            rule.max_retries = config.max_retries
        if config.priority is not None:
            rule.priority = config.priority
            self._rules.sort(key=lambda r: r.priority, reverse=False)

    def get_matching_rule(self, signal: RemediationSignal) -> Optional[RemediationRule]:
        now = utcnow()
        for rule in self._rules:
            if not rule.matches(signal):
                continue
            last = self._last_fired.get(rule.name)
            if last is not None:
                elapsed = (now - last).total_seconds()
                if elapsed < rule.cooldown_seconds:
                    aegis_decisions_total.labels(rule=rule.name, decision="cooldown").inc()
                    logger.info("Rule '%s' in cooldown (%0.1fs/%ds)",
                                rule.name, elapsed, rule.cooldown_seconds)
                    continue
            self._last_fired[rule.name] = now
            aegis_decisions_total.labels(rule=rule.name, decision="match").inc()
            logger.info("Rule '%s' matched signal %s (metric=%s, value=%s)",
                        rule.name, signal.id, signal.metric_name, signal.value)
            return rule
        aegis_decisions_total.labels(rule="no_match", decision="no_match").inc()
        logger.info("No rule matched signal %s (metric=%s)", signal.id, signal.metric_name)
        return None

    @property
    def rules(self) -> list[RemediationRule]:
        return list(self._rules)


async def load_rule_configs(db) -> dict[str, 'RuleConfig']:
    from app.aegis.models import RuleConfig
    result = await db.execute(select(RuleConfig))
    return {row.rule_name: row for row in result.scalars().all()}


async def merge_configs(registry: RuleRegistry, configs: dict[str, 'RuleConfig']):
    for rule_name, config in configs.items():
        registry.update_rule_from_config(config)


def build_default_registry() -> RuleRegistry:
    registry = RuleRegistry()

    registry.add_rule(RemediationRule(
        name="r001_throttle_ota",
        condition=lambda s: s.metric_name == "fleet_ota_in_progress" and s.value > s.threshold,
        action_name="throttle_ota",
        cooldown_seconds=300,
        max_retries=2,
        priority=10,
    ))

    registry.add_rule(RemediationRule(
        name="r002_mqtt_qos_downgrade",
        condition=lambda s: "mqtt" in s.metric_name and s.value > s.threshold,
        action_name="mqtt_qos_downgrade",
        cooldown_seconds=600,
        max_retries=1,
        priority=20,
    ))

    registry.add_rule(RemediationRule(
        name="r003_device_soft_restart",
        condition=lambda s: s.severity == "critical" and "signal" in s.metric_name.lower(),
        action_name="device_soft_restart",
        cooldown_seconds=900,
        max_retries=2,
        priority=30,
    ))

    registry.add_rule(RemediationRule(
        name="r004_scale_heartbeat",
        condition=lambda s: s.metric_name == "fleet_active_devices" and s.value <= s.threshold,
        action_name="scale_heartbeat",
        cooldown_seconds=600,
        max_retries=2,
        priority=40,
    ))

    return registry
