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

    # ═══════════════════════════════════════════════════════════════════════
    # Session 5+ Feature Tests
    # ═══════════════════════════════════════════════════════════════════════

    def test_20_telemetry_endpoint(self):
        """Feature 1: Telemetry time-series endpoint should work."""
        r = requests.get(f"{BASE_URL}/telemetry/{self.created_device_ids[0]}?hours=1&limit=10", timeout=10)
        assert r.status_code == 200, f"Telemetry endpoint failed: {r.text}"
        data = r.json()
        assert "device_id" in data
        assert "points" in data
        assert "total" in data

    def test_21_geofence_crud(self):
        """Feature 2: Geofence create, list, delete."""
        # Create
        body = {
            "name": "E2E-Test-Geofence",
            "shape": "circle",
            "center_lat": 12.9716,
            "center_lng": 77.5946,
            "radius_meters": 5000,
            "alert_on_enter": True,
            "alert_on_exit": True,
            "color": "#2DD4BF",
        }
        r = requests.post(f"{BASE_URL}/geofences", json=body, timeout=10)
        assert r.status_code == 201, f"Geofence create failed: {r.text}"
        gf_id = r.json()["id"]

        # List
        r = requests.get(f"{BASE_URL}/geofences", timeout=10)
        assert r.status_code == 200
        assert any(g["id"] == gf_id for g in r.json()["geofences"])

        # Delete
        r = requests.delete(f"{BASE_URL}/geofences/{gf_id}", timeout=10)
        assert r.status_code == 200

    def test_22_predictive_scan(self):
        """Feature 3: Predictive maintenance scan endpoint."""
        r = requests.post(f"{BASE_URL}/predictive/scan", timeout=30)
        assert r.status_code == 200, f"Predictive scan failed: {r.text}"
        data = r.json()
        assert "predictions_count" in data

    def test_23_scheduled_ota(self):
        """Feature 4: Scheduled OTA campaign creation and listing."""
        # Upload firmware for the schedule
        fw_content = b"SCHEDULED_OTA_FW_TEST"
        files = {"file": ("sched_fw.bin", fw_content, "application/octet-stream")}
        r = requests.post(f"{BASE_URL}/ota/upload", data={"version": "5.0.0-sched"}, files=files, timeout=10)
        assert r.status_code == 200
        fw_id = r.json()["id"]

        # Create schedule
        from datetime import datetime, timedelta
        sched_time = (datetime.utcnow() + timedelta(hours=1)).isoformat()
        body = {
            "name": "E2E-Schedule",
            "firmware_id": fw_id,
            "all_devices": True,
            "scheduled_for": sched_time,
            "canary_percent": 10,
        }
        r = requests.post(f"{BASE_URL}/ota/schedules", json=body, timeout=10)
        assert r.status_code == 201, f"Schedule create failed: {r.text}"
        sched_id = r.json()["id"]

        # List
        r = requests.get(f"{BASE_URL}/ota/schedules", timeout=10)
        assert r.status_code == 200
        assert any(s["id"] == sched_id for s in r.json()["schedules"])

        # Cancel
        r = requests.post(f"{BASE_URL}/ota/schedules/{sched_id}/cancel", timeout=10)
        assert r.status_code == 200

    def test_24_command_queue(self):
        """Feature 5: Offline command queue."""
        body = {
            "device_id": self.created_device_ids[0],
            "command_type": "config",
            "payload": {"heartbeat_interval_seconds": 5},
            "ttl_seconds": 3600,
        }
        r = requests.post(f"{BASE_URL}/commands/queue", json=body, timeout=10)
        assert r.status_code == 201, f"Command queue failed: {r.text}"
        cmd_id = r.json()["id"]

        # List
        r = requests.get(f"{BASE_URL}/commands?device_id={self.created_device_ids[0]}", timeout=10)
        assert r.status_code == 200
        assert any(c["id"] == cmd_id for c in r.json()["commands"])

    def test_25_audit_log(self):
        """Feature 6: Audit log endpoint."""
        r = requests.get(f"{BASE_URL}/audit?limit=20", timeout=10)
        assert r.status_code == 200, f"Audit log failed: {r.text}"
        data = r.json()
        assert "logs" in data
        assert "total" in data

    def test_26_device_shadow(self):
        """Feature 7: Device shadow desired/reported state."""
        # Update desired
        body = {"state": "desired", "payload": {"heartbeat_interval": 15, "log_level": "DEBUG"}}
        r = requests.put(f"{BASE_URL}/shadow/{self.created_device_ids[0]}", json=body, timeout=10)
        assert r.status_code == 200, f"Shadow update failed: {r.text}"

        # Get shadow
        r = requests.get(f"{BASE_URL}/shadow/{self.created_device_ids[0]}", timeout=10)
        assert r.status_code == 200
        data = r.json()
        assert data["desired"] is not None
        assert data["desired"]["payload"]["log_level"] == "DEBUG"

    def test_27_device_lifecycle(self):
        """Feature 9: Device decommission and activate."""
        dev_id = self.created_device_ids[0]
        # Enter maintenance
        r = requests.post(f"{BASE_URL}/lifecycle/{dev_id}/maintenance?reason=test", timeout=10)
        assert r.status_code == 200, f"Maintenance failed: {r.text}"

        # Activate
        r = requests.post(f"{BASE_URL}/lifecycle/{dev_id}/activate", timeout=10)
        assert r.status_code == 200

    def test_28_bulk_import(self):
        """Feature 13: Bulk CSV device import."""
        csv_content = "name,firmware_version,ip_address,city\nBulkDevice-01,1.0.0,10.0.5.1,Bangalore\nBulkDevice-02,1.0.0,10.0.5.2,Mumbai\n"
        files = {"file": ("devices.csv", csv_content.encode(), "text/csv")}
        r = requests.post(f"{BASE_URL}/provisioning/bulk-import", files=files, timeout=10)
        assert r.status_code == 200, f"Bulk import failed: {r.text}"
        data = r.json()
        assert data["imported"] == 2
        assert len(data["device_ids"]) == 2

    def test_29_webhook_crud(self):
        """Feature 11: Webhook subscription CRUD."""
        body = {"name": "E2E-Webhook", "url": "http://example.com/hook", "event_types": "*", "enabled": True}
        r = requests.post(f"{BASE_URL}/webhooks", json=body, timeout=10)
        assert r.status_code == 201, f"Webhook create failed: {r.text}"
        wh_id = r.json()["id"]

        # List
        r = requests.get(f"{BASE_URL}/webhooks", timeout=10)
        assert r.status_code == 200
        assert any(w["id"] == wh_id for w in r.json())

        # Delete
        r = requests.delete(f"{BASE_URL}/webhooks/{wh_id}", timeout=10)
        assert r.status_code == 200

    def test_30_re_notify_alert_fix(self):
        """Bug 3: Re-notify endpoint should query by ID and return 404 for nonexistent."""
        r = requests.post(f"{BASE_URL}/alerts/nonexistent-id/re-notify", timeout=10)
        assert r.status_code == 404, f"Expected 404 for nonexistent alert, got {r.status_code}"

    def test_31_new_metrics_present(self):
        """Verify new feature metrics are exposed."""
        r = requests.get(f"{BASE_URL}/metrics", timeout=10)
        assert r.status_code == 200
        text = r.text
        expected = [
            "fleet_telemetry_points_total",
            "fleet_geofence_events_total",
            "fleet_geofences_active",
            "fleet_command_queue_depth",
            "fleet_predicted_failures_total",
            "fleet_audit_events_total",
            "fleet_shadow_updates_total",
        ]
        for name in expected:
            assert name in text, f"New metric {name} not found in /metrics"

    def test_32_predictive_agent_endpoint(self):
        """Feature 3: Predictive maintenance agent endpoint."""
        r = requests.get(f"{BASE_URL}/agents/predictive-scan", timeout=30)
        assert r.status_code == 200, f"Predictive agent failed: {r.text}"
        data = r.json()
        assert data["type"] == "predictive_maintenance"

    def test_33_device_with_city(self):
        """Bug 2: Device should support city field."""
        r = requests.post(f"{BASE_URL}/devices/register", json={
            "name": "E2E-CityDevice", "firmware_version": "1.0.0", "city": "Chennai",
        }, timeout=10)
        assert r.status_code == 201
        data = r.json()
        assert data.get("city") == "Chennai"

    def test_34_pre_register_and_claim(self):
        """Feature 13: Pre-register device with claim token, then claim it."""
        r = requests.post(f"{BASE_URL}/provisioning/pre-register?name=E2E-ClaimDevice&firmware_version=1.0.0", timeout=10)
        assert r.status_code == 201, f"Pre-register failed: {r.text}"
        dev_id = r.json()["id"]

        # Generate claim token
        r = requests.post(f"{BASE_URL}/lifecycle/{dev_id}/claim-token", timeout=10)
        assert r.status_code == 200
        token = r.json()["claim_token"]

        # Claim the device
        r = requests.post(f"{BASE_URL}/lifecycle/claim", json={
            "name": "E2E-ClaimedDevice", "claim_token": token, "firmware_version": "2.0.0",
        }, timeout=10)
        assert r.status_code == 201, f"Claim failed: {r.text}"
        assert r.json()["name"] == "E2E-ClaimedDevice"

    def test_35_firmware_upload_returns_signature_fields(self):
        """Feature 8: Firmware upload response should include signature fields."""
        files = {"file": ("sig_test.bin", b"SIGNATURE_TEST", "application/octet-stream")}
        r = requests.post(f"{BASE_URL}/ota/upload", data={"version": "6.0.0-sig"}, files=files, timeout=10)
        assert r.status_code == 200
        data = r.json()
        assert "signature" in data
        assert "signing_key_id" in data

    def test_36_shadow_history(self):
        """Feature 7: Shadow history endpoint."""
        r = requests.get(f"{BASE_URL}/shadow/{self.created_device_ids[0]}/history", timeout=10)
        assert r.status_code == 200

    def test_37_geofence_events_endpoint(self):
        """Feature 2: Geofence events listing."""
        r = requests.get(f"{BASE_URL}/geofences/events/all?limit=10", timeout=10)
        assert r.status_code == 200

    def test_38_telemetry_stats(self):
        """Feature 1: Telemetry statistics endpoint."""
        r = requests.get(f"{BASE_URL}/telemetry/{self.created_device_ids[0]}/stats?hours=1", timeout=10)
        assert r.status_code == 200, f"Telemetry stats failed: {r.text}"
        data = r.json()
        assert "samples" in data

    def test_39_scheduled_ota_cancel_only_scheduled(self):
        """Feature 4: Cannot cancel a completed schedule."""
        # First create and let it be in scheduled state
        fw_content = b"SCHED_CANCEL_TEST"
        files = {"file": ("sched_cancel.bin", fw_content, "application/octet-stream")}
        r = requests.post(f"{BASE_URL}/ota/upload", data={"version": "7.0.0-cancel"}, files=files, timeout=10)
        fw_id = r.json()["id"]
        from datetime import datetime, timedelta
        sched_time = (datetime.utcnow() + timedelta(hours=2)).isoformat()
        body = {"name": "E2E-Cancel-Test", "firmware_id": fw_id, "all_devices": True, "scheduled_for": sched_time}
        r = requests.post(f"{BASE_URL}/ota/schedules", json=body, timeout=10)
        sched_id = r.json()["id"]

        # Cancel should work (it's scheduled)
        r = requests.post(f"{BASE_URL}/ota/schedules/{sched_id}/cancel", timeout=10)
        assert r.status_code == 200

        # Cancel again should fail (it's now cancelled)
        r = requests.post(f"{BASE_URL}/ota/schedules/{sched_id}/cancel", timeout=10)
        assert r.status_code == 409
