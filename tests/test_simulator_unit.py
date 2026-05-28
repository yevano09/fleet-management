"""
Unit tests for the Fleet Commander device simulator.

Tests the fix for Bug 1: simulator must use deployment_id from the
MQTT payload instead of generating a random one.
"""

import json
import uuid
from unittest.mock import MagicMock, patch
from simulator.simulator import SimulatedDevice


class TestSimulatorDeploymentId:
    def setup_method(self):
        self.device = SimulatedDevice(
            device_id=str(uuid.uuid4()),
            name="Test-Device-001",
            is_ev=False,
        )
        # Mock the MQTT client
        self.device._client = MagicMock()
        self.device._client.publish.return_value.rc = 0

    def test_uses_deployment_id_from_payload(self):
        """Bug 1: Must use the deployment_id sent by the backend."""
        expected_deployment_id = str(uuid.uuid4())
        payload = {
            "firmware_url": "http://localhost:8000/firmware/test.bin",
            "sha256_hash": "abc123",
            "deployment_id": expected_deployment_id,
        }

        # Run the handler synchronously via the event loop
        import asyncio
        loop = asyncio.new_event_loop()
        self.device._loop = loop
        try:
            loop.run_until_complete(self.device._handle_ota_command(payload))
        finally:
            loop.close()

        # Verify all OTA status publish calls used the expected deployment_id
        for call_args in self.device._client.publish.call_args_list:
            topic, payload_json = call_args[0]
            published = json.loads(payload_json)
            assert published["deployment_id"] == expected_deployment_id, (
                f"Expected deployment_id {expected_deployment_id}, "
                f"got {published['deployment_id']}"
            )

    def test_falls_back_to_uuid_when_no_deployment_id(self):
        """When payload has no deployment_id, a UUID should be generated."""
        payload = {
            "firmware_url": "http://localhost:8000/firmware/test.bin",
            "sha256_hash": "abc123",
        }

        import asyncio
        loop = asyncio.new_event_loop()
        self.device._loop = loop
        try:
            loop.run_until_complete(self.device._handle_ota_command(payload))
        finally:
            loop.close()

        # Verify all publish calls have some deployment_id (not None/empty)
        for call_args in self.device._client.publish.call_args_list:
            topic, payload_json = call_args[0]
            published = json.loads(payload_json)
            assert "deployment_id" in published
            assert published["deployment_id"] is not None
            assert len(published["deployment_id"]) > 0
