"""P0 strict-mode E2E (UC-23 + UC-26): register → heartbeat → OTA with API key,
plus cross-tenant isolation. Skipped unless AUTH_MODE=strict and a key is set.

Run via:  docker compose --profile testing run --rm tests   (with env wired)
or locally:
  AUTH_MODE=strict FLEET_API_KEY=fck_... pytest tests/test_e2e_strict.py
"""

import hashlib
import os

import pytest
import requests

from tests.test_e2e import wait_for_backend, BASE_URL, FLEET_API_KEY, AUTH_MODE

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        not (AUTH_MODE == "strict" and FLEET_API_KEY),
        reason="requires AUTH_MODE=strict and FLEET_API_KEY",
    ),
]

H = {"X-API-Key": FLEET_API_KEY}


def setup_module(module):
    assert wait_for_backend(), "Backend /health never returned 200"


class TestStrictAuthWalls:
    def test_00_unauthenticated_write_is_401(self):
        for method, url, kwargs in [
            ("POST", f"{BASE_URL}/devices/register",
             {"json": {"name": "nope"}}),
            ("POST", f"{BASE_URL}/ota/trigger", {"json": {"firmware_id": "x"}}),
            ("POST", f"{BASE_URL}/provisioning/pre-register",
             {"params": {"name": "nope2"}}),
        ]:
            r = requests.request(method, url, timeout=10, **kwargs)
            assert r.status_code == 401, f"{method} {url} → {r.status_code}"

    def test_01_health_and_metrics_stay_open(self):
        assert requests.get(f"{BASE_URL}/health", timeout=5).status_code == 200
        assert requests.get(f"{BASE_URL}/metrics", timeout=5).status_code == 200


class TestStrictCoreFlowWithKey:
    """The original happy path must still work when authed properly."""

    device_id = None

    def test_02_register_with_key(self):
        r = requests.post(
            f"{BASE_URL}/devices/register",
            headers=H,
            json={"name": "Strict-E2E-001", "firmware_version": "1.0.0"},
            timeout=10,
        )
        assert r.status_code == 201, r.text
        type(self).device_id = r.json()["device_id"]

    def test_03_heartbeat_with_key(self):
        r = requests.post(
            f"{BASE_URL}/devices/{type(self).device_id}/heartbeat",
            headers=H,
            json={"uptime_percentage": 99.0, "signal_strength": -60},
            timeout=10,
        )
        assert r.status_code == 200

    def test_04_upload_and_trigger_ota_with_key(self):
        files = {"file": ("fw.bin", b"STRICT_FW_BYTES" * 4, "application/octet-stream")}
        up = requests.post(f"{BASE_URL}/ota/upload", headers=H,
                           data={"version": "9.9.9-strict-e2e"}, files=files, timeout=15)
        if up.status_code == 409:  # re-run of the suite
            listing = requests.get(f"{BASE_URL}/ota/firmware", headers=H, timeout=10).json()
            fw = next(f for f in listing if f["version"] == "9.9.9-strict-e2e")
        else:
            assert up.status_code == 200, up.text
            fw = up.json()

        trig = requests.post(
            f"{BASE_URL}/ota/trigger",
            headers=H,
            json={"firmware_id": fw["id"], "device_ids": [type(self).device_id]},
            timeout=15,
        )
        # MQTT may be unavailable in an HTTP-only test run; accept either a
        # clean trigger or an explicit broker-down signal — never a 401/403.
        assert trig.status_code in (200, 503), trig.text
        if trig.status_code == 200:
            body = trig.json()
            assert type(self).device_id in body["deployment_ids"]

    def test_05_firmware_download_requires_token(self):
        listing = requests.get(f"{BASE_URL}/ota/firmware", headers=H, timeout=10)
        assert listing.status_code == 200
        for fw in listing.json():
            if not fw.get("filename"):
                continue
            r = requests.get(f"{BASE_URL}/firmware/{fw['filename']}", timeout=10)
            assert r.status_code == 401, (
                f"un-tokenized firmware download must fail in strict mode "
                f"(got {r.status_code} for {fw['filename']})"
            )


class TestTenancyIsolationE2E:
    """UC-26 across two orgs using admin-minted keys.

    Requires the runner to pre-provision (via admin session or seeded keys):
      ALPHA_KEY / BETA_KEY env vars scoped to org-alpha / org-beta.
    Skipped when they are absent so the standard strict suite still runs.
    """

    ALPHA_KEY = os.environ.get("ALPHA_KEY", "")
    BETA_KEY = os.environ.get("BETA_KEY", "")

    @pytest.mark.skipif(not os.environ.get("ALPHA_KEY"), reason="ALPHA_KEY not provided")
    def test_org_isolation(self):
        ha = {"X-API-Key": self.ALPHA_KEY}
        hb = {"X-API-Key": self.BETA_KEY}

        ra = requests.post(f"{BASE_URL}/devices/register", headers=ha,
                           json={"name": "device-alpha"}, timeout=10)
        rb = requests.post(f"{BASE_URL}/devices/register", headers=hb,
                           json={"name": "device-beta"}, timeout=10)
        assert ra.status_code == 201 and rb.status_code == 201
        alpha_id = ra.json()["device_id"]
        beta_id = rb.json()["device_id"]
        assert alpha_id != beta_id

        # Alpha cannot see beta's device…
        names = [d["name"] for d in
                 requests.get(f"{BASE_URL}/devices", headers=ha, timeout=10).json()["devices"]]
        assert "device-beta" not in names

        # …and gets a 404 (not 403) poking it directly.
        assert requests.get(f"{BASE_URL}/devices/{beta_id}", headers=ha,
                            timeout=10).status_code == 404

        # Cross-tenant OTA trigger is refused as not-found.
        fw_list = requests.get(f"{BASE_URL}/ota/firmware", headers=ha, timeout=10).json()
        if fw_list:
            r = requests.post(f"{BASE_URL}/ota/trigger", headers=ha,
                              json={"firmware_id": fw_list[0]["id"],
                                    "device_ids": [beta_id]}, timeout=15)
            assert r.status_code == 404
