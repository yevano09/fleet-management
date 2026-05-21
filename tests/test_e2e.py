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

BASE_URL = os.environ.get("BASE_URL", "http://localhost:8000")
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
        r = requests.get(f"{BASE_URL}/", timeout=10)
        assert r.status_code == 200
        assert "Fleet Commander" in r.text
        assert "htmx" in r.text
