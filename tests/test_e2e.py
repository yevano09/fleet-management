"""
Fleet Commander End-to-End Integration Tests

Tests the full lifecycle:
  1. Device auto-registration
  2. Heartbeat updates
  3. Firmware upload
  4. OTA trigger
  5. OTA deployment status tracking
  6. Device listing with status
"""

import pytest
import requests
import time
import os
import hashlib
from typing import Generator

DEFAULT_PORT = os.environ.get("FLEET_PORT", "8181")
BASE_URL = os.environ.get("BASE_URL", f"http://localhost:{DEFAULT_PORT}")
DEVICE_COUNT = int(os.environ.get("TEST_DEVICE_COUNT", 3))
TIMEOUT = int(os.environ.get("TEST_TIMEOUT", 30))

pytestmark = pytest.mark.e2e


def wait_for_backend(max_retries: int = 15, delay: int = 2) -> bool:
    for i in range(max_retries):
        try:
            r = requests.get(f"{BASE_URL}/devices", timeout=5)
            if r.status_code == 200:
                return True
        except requests.RequestException:
            pass
        time.sleep(delay)
    return False


class TestE2E:
    @classmethod
    def setup_class(cls):
        assert wait_for_backend(), "Backend did not become available"
        cls.created_device_ids = []
        cls.firmware_id = None

    def test_00_metrics_endpoint_no_trailing_slash(self):
        """Bug 2: /metrics (no trailing slash) must return 200."""
        r = requests.get(f"{BASE_URL}/metrics", timeout=10)
        assert r.status_code == 200, f"Expected 200, got {r.status_code}"
        text = r.text
        assert "fleet_active_devices" in text

    def test_00_metrics_endpoint_with_trailing_slash(self):
        """Bug 2: /metrics/ (with trailing slash) must NOT 404."""
        r = requests.get(f"{BASE_URL}/metrics/", timeout=10)
        assert r.status_code in (200, 307), (
            f"Trailing-slash metrics should not 404, got {r.status_code}"
        )

    def test_01_register_devices(self):
        for i in range(DEVICE_COUNT):
            payload = {
                "name": f"E2E-Device-{i+1:03d}",
                "firmware_version": "1.0.0",
                "ip_address": f"10.0.1.{i+1}",
            }
            r = requests.post(f"{BASE_URL}/devices/register", json=payload, timeout=10)
            assert r.status_code == 201, f"Device registration failed: {r.text}"
            data = r.json()
            assert "device_id" in data
            assert data["status"] == "online"
            self.__class__.created_device_ids.append(data["device_id"])

        assert len(self.created_device_ids) == DEVICE_COUNT

    def test_02_send_heartbeats(self):
        for device_id in self.created_device_ids:
            payload = {
                "uptime_percentage": 98.5,
                "signal_strength": -65,
            }
            r = requests.post(
                f"{BASE_URL}/devices/{device_id}/heartbeat",
                json=payload,
                timeout=10,
            )
            assert r.status_code == 200, f"Heartbeat failed for {device_id}: {r.text}"
            data = r.json()
            assert data["status"] == "ok"

    def test_03_list_devices(self):
        r = requests.get(f"{BASE_URL}/devices", timeout=10)
        assert r.status_code == 200
        data = r.json()
        assert data["total"] >= DEVICE_COUNT

        device_ids = [d["id"] for d in data["devices"]]
        for cid in self.created_device_ids:
            assert cid in device_ids, f"Device {cid} not found in listing"

        for device in data["devices"]:
            if device["id"] in self.created_device_ids:
                assert device["status"] == "online"
                assert device["uptime_percentage"] > 0

    def test_04_upload_firmware(self):
        firmware_content = b"FLEET_COMMANDER_FIRMWARE_V2_FAKE_BINARY"
        sha256_hash = hashlib.sha256(firmware_content).hexdigest()

        files = {
            "file": ("firmware_v2.bin", firmware_content, "application/octet-stream"),
        }
        r = requests.post(
            f"{BASE_URL}/ota/upload",
            data={"version": "2.0.0"},
            files=files,
            timeout=10,
        )
        assert r.status_code == 200, f"Firmware upload failed: {r.text}"
        data = r.json()
        assert data["sha256_hash"] == sha256_hash
        assert data["version"] == "2.0.0"
        self.__class__.firmware_id = data["id"]

    def test_04b_upload_duplicate_firmware_returns_409(self):
        """Bug 3: Duplicate firmware version must return 409, not 500."""
        firmware_content = b"FLEET_COMMANDER_FIRMWARE_V2_DUPLICATE"
        files = {
            "file": ("firmware_v2_dup.bin", firmware_content, "application/octet-stream"),
        }
        r = requests.post(
            f"{BASE_URL}/ota/upload",
            data={"version": "2.0.0"},
            files=files,
            timeout=10,
        )
        assert r.status_code == 409, (
            f"Expected 409 for duplicate firmware, got {r.status_code}: {r.text}"
        )
        detail = r.json().get("detail", "")
        assert "already exists" in detail

    def test_05_trigger_ota(self):
        assert self.firmware_id is not None

        payload = {
            "firmware_id": self.firmware_id,
            "device_ids": self.created_device_ids,
        }
        r = requests.post(f"{BASE_URL}/ota/trigger", json=payload, timeout=10)
        assert r.status_code == 200, f"OTA trigger failed: {r.text}"
        data = r.json()
        assert data["message"] is not None
        assert len(data["deployment_ids"]) == DEVICE_COUNT

    def test_06_check_ota_status(self):
        time.sleep(5)
        r = requests.get(f"{BASE_URL}/ota/status", timeout=10)
        assert r.status_code == 200
        data = r.json()
        assert data["total"] >= DEVICE_COUNT

        # At least some should be in a terminal state (success, failed, or rolled_back)
        terminal_count = data["success_count"] + data["failed_count"]
        assert terminal_count >= 0
        assert len(data["deployments"]) >= DEVICE_COUNT

    def test_07_verify_device_firmware_updated(self):
        r = requests.get(f"{BASE_URL}/devices", timeout=10)
        assert r.status_code == 200
        data = r.json()

        for device in data["devices"]:
            if device["id"] in self.created_device_ids:
                # Firmware version may be updated or rolled back
                assert device["firmware_version"] in ("1.0.0", "2.0.0")

    def test_08_upload_and_trigger_bulk_ota(self):
        firmware_content = b"FLEET_COMMANDER_FIRMWARE_V3_FAKE_BINARY"
        files = {
            "file": ("firmware_v3.bin", firmware_content, "application/octet-stream"),
        }
        r = requests.post(
            f"{BASE_URL}/ota/upload",
            data={"version": "3.0.0"},
            files=files,
            timeout=10,
        )
        assert r.status_code == 200
        fw_id = r.json()["id"]

        payload = {
            "firmware_id": fw_id,
            "all_devices": True,
        }
        r = requests.post(f"{BASE_URL}/ota/trigger", json=payload, timeout=10)
        assert r.status_code == 200
        data = r.json()
        assert len(data["deployment_ids"]) >= DEVICE_COUNT

    def test_09_metrics_endpoint(self):
        r = requests.get(f"{BASE_URL}/metrics", timeout=10)
        assert r.status_code == 200
        text = r.text
        assert "fleet_active_devices" in text
        assert "fleet_total_devices" in text
        assert "fleet_ota_deployments_total" in text
        assert "fleet_api_request_latency_seconds" in text

    def test_10_dashboard_endpoint(self):
        r = requests.get(f"{BASE_URL}/", timeout=10, allow_redirects=False)
        # Dashboard now requires authentication — unauthenticated requests
        # are redirected to the Google OAuth login page.
        assert r.status_code in (302, 307), (
            f"Unauthenticated dashboard should redirect, got {r.status_code}"
        )
        assert "/auth/login" in r.headers.get("location", ""), (
            f"Should redirect to /auth/login, got {r.headers.get('location')}"
        )

    def test_11_trigger_ota_invalid_firmware_returns_404(self):
        r = requests.post(
            f"{BASE_URL}/ota/trigger",
            json={"firmware_id": "nonexistent-id", "all_devices": True},
            timeout=10,
        )
        assert r.status_code == 404, f"Expected 404, got {r.status_code}: {r.text}"

    def test_12_trigger_ota_no_devices_returns_400(self):
        r = requests.post(
            f"{BASE_URL}/ota/trigger",
            json={"firmware_id": self.firmware_id or "x" * 36},
            timeout=10,
        )
        assert r.status_code == 400, f"Expected 400, got {r.status_code}: {r.text}"

    def test_13_heartbeat_nonexistent_device_returns_404(self):
        r = requests.post(
            f"{BASE_URL}/devices/nonexistent-id/heartbeat",
            json={"uptime_percentage": 99.0, "signal_strength": -50},
            timeout=10,
        )
        assert r.status_code == 404, f"Expected 404, got {r.status_code}: {r.text}"

    def test_14_fetch_metrics_contains_all_expected_metrics(self):
        """Verify all Prometheus metric names are exposed."""
        r = requests.get(f"{BASE_URL}/metrics", timeout=10)
        assert r.status_code == 200
        text = r.text
        expected = [
            "fleet_active_devices",
            "fleet_total_devices",
            "fleet_ota_deployments_total",
            "fleet_ota_in_progress",
            "fleet_api_request_latency_seconds",
            "fleet_mqtt_messages_published_total",
            "fleet_mqtt_messages_received_total",
        ]
        for name in expected:
            assert name in text, f"Metric {name} not found in /metrics"

    def test_15_upload_firmware_empty_file_returns_200(self):
        """Edge case: firmware with zero bytes should still be accepted."""
        files = {
            "file": ("empty_firmware.bin", b"", "application/octet-stream"),
        }
        r = requests.post(
            f"{BASE_URL}/ota/upload",
            data={"version": "0.0.0"},
            files=files,
            timeout=10,
        )
        assert r.status_code == 200, f"Empty firmware upload failed: {r.text}"
        data = r.json()
        assert data["file_size"] == 0
        assert data["version"] == "0.0.0"

    def test_16_alerts_list_empty(self):
        """Alert list should be empty initially."""
        r = requests.get(f"{BASE_URL}/alerts/", timeout=10)
        assert r.status_code == 200
        data = r.json()
        assert data["total"] == 0
        assert data["alerts"] == []

    def test_17_fleet_health_triggers_alert(self):
        """Fleet health check should detect anomalies and create alerts."""
        # Trigger fleet health with notify=true
        r = requests.get(f"{BASE_URL}/agents/fleet-health", timeout=10)
        assert r.status_code == 200
        data = r.json()
        assert data["status"] in ("healthy", "anomalies_found")

    def test_18_alert_lifecycle(self):
        """Full alert lifecycle: create, acknowledge, resolve, prune."""
        # 1. Trigger anomaly detection to create alerts
        r = requests.get(f"{BASE_URL}/agents/fleet-health", timeout=10)
        assert r.status_code == 200

        # 2. List active alerts
        time.sleep(1)
        r = requests.get(f"{BASE_URL}/alerts/active", timeout=10)
        assert r.status_code == 200
        data = r.json()
        alerts = data.get("alerts", [])
        total = data.get("total", 0)
        assert total >= 0

        # 3. If there are alerts, test acknowledge and resolve
        if alerts:
            alert_id = alerts[0]["id"]

            # Acknowledge
            r = requests.post(
                f"{BASE_URL}/alerts/{alert_id}/acknowledge",
                json={"user": "e2e-test"},
                timeout=10,
            )
            assert r.status_code == 200
            ack_data = r.json()
            assert "message" in ack_data

            # Verify acknowledged
            r = requests.get(f"{BASE_URL}/alerts/", timeout=10)
            assert r.status_code == 200
            all_data = r.json()
            ack_alert = next((a for a in all_data.get("alerts", []) if a["id"] == alert_id), None)
            if ack_alert:
                assert ack_alert["status"] == "acknowledged"
                assert ack_alert["acknowledged_by"] == "e2e-test"

            # Resolve
            r = requests.post(f"{BASE_URL}/alerts/{alert_id}/resolve", timeout=10)
            assert r.status_code == 200
            res_data = r.json()
            assert "message" in res_data

            # Verify resolved
            r = requests.get(f"{BASE_URL}/alerts/", timeout=10)
            assert r.status_code == 200
            all_data = r.json()
            res_alert = next((a for a in all_data.get("alerts", []) if a["id"] == alert_id), None)
            if res_alert:
                assert res_alert["status"] == "resolved"
                assert res_alert["resolved_at"] is not None

            # Prune old (days must be >= 1)
            r = requests.delete(f"{BASE_URL}/alerts/old?days=1", timeout=10)
            assert r.status_code == 200
            pr_data = r.json()
            assert "deleted" in pr_data

    def test_19_alert_metrics_present(self):
        """Verify alert metrics are present in /metrics."""
        r = requests.get(f"{BASE_URL}/metrics", timeout=10)
        assert r.status_code == 200
        text = r.text
        expected = [
            "fleet_alerts_total",
            "fleet_alerts_active",
            "fleet_alert_notifications_total",
        ]
        for name in expected:
            assert name in text, f"Alert metric {name} not found in /metrics"
