"""P0 UC-23 — API auth + RBAC unit tests (DB-free).

Covers:
  - open-mode synthetic principal
  - strict-mode 401 (missing creds) / 403 (rank below minimum)
  - role hierarchy (operator < fleet_manager < admin)
  - JWT cookie + Bearer resolution incl. org_id claim
  - X-API-Key resolution (monkeypatched hash lookup)
  - allowed_orgs scoping ('*' super-admin vs concrete org)
  - firmware download HMAC token roundtrip + expiry
  - AUTH_MODE=strict refuses default secrets at startup
"""

import os
import time
import uuid
import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.config import settings, DEFAULT_ORG_ID, SUPER_ORG, DEFAULT_JWT_SECRET
import app.deps as deps
from app.deps import (
    require_user,
    require_role,
    require_admin,
    resolve_principal,
    allowed_orgs,
    OPEN_PRINCIPAL,
)


# ── helpers ──────────────────────────────────────────────────────────────────

def make_probe_app(dep):
    app = FastAPI()

    @app.get("/probe")
    async def probe(principal: dict = Depends(dep)):
        return {"role": principal["role"], "org_id": principal["org_id"],
                "email": principal["email"]}

    return app


@pytest.fixture()
def strict_mode(monkeypatch):
    monkeypatch.setattr(settings, "auth_mode", "strict")
    yield
    # monkeypatch restores automatically


def user_token(session_id="", role="user", org_id=None):
    from app.auth import create_user_jwt_token

    return create_user_jwt_token(
        email="u@example.com", name="U", picture="", session_id=session_id,
        role=role, org_id=org_id or DEFAULT_ORG_ID,
    )


def admin_token():
    from app.auth import create_admin_jwt_token

    return create_admin_jwt_token("root")


# ── open mode ────────────────────────────────────────────────────────────────

class TestOpenMode:
    def test_open_mode_yields_admin_principal(self):
        assert settings.auth_mode == "open"  # default unless env overrides
        app = make_probe_app(require_user())
        client = TestClient(make_probe_client := app)
        r = client.get("/probe")
        assert r.status_code == 200
        assert r.json()["role"] == "admin"
        assert r.json()["org_id"] == SUPER_ORG


# ── strict mode: authentication ──────────────────────────────────────────────

class TestStrictAuthn:
    def test_no_credentials_401(self, strict_mode):
        client = TestClient(make_probe_app(require_user()))
        r = client.get("/probe")
        assert r.status_code == 401

    def test_user_bearer_token_ok(self, strict_mode):
        app = make_probe_app(require_user())
        client = TestClient(app)
        r = client.get("/probe", headers={"Authorization": f"Bearer {user_token()}"})
        assert r.status_code == 200
        body = r.json()
        assert body["email"] == "u@example.com"
        assert body["org_id"] == DEFAULT_ORG_ID

    def test_user_cookie_ok(self, strict_mode):
        from app.auth import COOKIE_NAME

        client = TestClient(make_probe_app(require_user()))
        r = client.get("/probe", cookies={COOKIE_NAME: user_token()})
        assert r.status_code == 200

    def test_admin_bearer_is_super(self, strict_mode):
        app = make_probe_app(require_role("fleet_manager"))
        client = TestClient(app)
        r = client.get("/probe", headers={"Authorization": f"Bearer {admin_token()}"})
        assert r.status_code == 200
        assert r.json()["org_id"] == SUPER_ORG

    def test_garbage_token_401(self, strict_mode):
        client = TestClient(make_probe_app(require_user()))
        r = client.get("/probe", headers={"Authorization": "Bearer not-a-jwt"})
        assert r.status_code == 401


# ── strict mode: authorization matrix ────────────────────────────────────────

class TestStrictRbac:
    def test_viewer_cannot_pass_operator_gate(self, strict_mode):
        client = TestClient(make_probe_app(require_role("operator")))
        r = client.get("/probe", headers={
            "Authorization": f"Bearer {user_token(role='viewer')}"})
        assert r.status_code == 403

    def test_operator_passes_operator_gate(self, strict_mode):
        client = TestClient(make_probe_app(require_role("operator")))
        r = client.get("/probe", headers={
            "Authorization": f"Bearer {user_token(role='operator')}"})
        assert r.status_code == 200

    def test_fleet_manager_passes_operator_gate(self, strict_mode):
        """Rank hierarchy: fleet_manager ≥ operator."""
        client = TestClient(make_probe_app(require_role("operator")))
        r = client.get("/probe", headers={
            "Authorization": f"Bearer {user_token(role='fleet_manager')}"})
        assert r.status_code == 200

    def test_fleet_manager_cannot_pass_admin_gate(self, strict_mode):
        client = TestClient(make_probe_app(require_admin()))
        r = client.get("/probe", headers={
            "Authorization": f"Bearer {user_token(role='fleet_manager')}"})
        assert r.status_code == 403

    def test_admin_passes_everything(self, strict_mode):
        client = TestClient(make_probe_app(require_role("fleet_manager")))
        r = client.get("/probe", headers={
            "Authorization": f"Bearer {admin_token()}"})
        assert r.status_code == 200


# ── API keys ────────────────────────────────────────────────────────────────

class _FakeKey:
    def __init__(self, name="ci", role="viewer", org_id=DEFAULT_ORG_ID):
        class R:
            def __init__(self, v):
                self.value = v

        self.name = name
        self.role = R(role)
        self.org_id = org_id
        self.revoked = 0


class TestApiKeys:
    def test_api_key_header_resolves_principal(self, strict_mode, monkeypatch):
        async def fake_lookup(db, raw_key):
            return _FakeKey(name="ci", role="viewer", org_id=DEFAULT_ORG_ID) \
                if raw_key.startswith("fck_") else None

        monkeypatch.setattr(deps, "_api_key_lookup", fake_lookup)

        client = TestClient(make_probe_app(require_user()))
        r = client.get("/probe", headers={"X-API-Key": "fck_abc123"})
        assert r.status_code == 200
        body = r.json()
        assert body["email"] == "apikey:ci"
        assert body["role"] == "viewer"

    def test_bad_api_key_401(self, strict_mode, monkeypatch):
        async def fake_lookup(db, raw_key):
            return None

        monkeypatch.setattr(deps, "_api_key_lookup", fake_lookup)
        client = TestClient(make_probe_app(require_user()))
        r = client.get("/probe", headers={"X-API-Key": "fck_wrong"})
        assert r.status_code == 401

    def test_viewer_key_blocked_at_fleet_manager_gate(self, strict_mode, monkeypatch):
        async def fake_lookup(db, raw_key):
            return _FakeKey(role="viewer")

        monkeypatch.setattr(deps, "_api_key_lookup", fake_lookup)
        client = TestClient(make_probe_app(require_role("fleet_manager")))
        r = client.get("/probe", headers={"X-API-Key": "fck_v"})
        assert r.status_code == 403


# ── tenancy scoping helper ───────────────────────────────────────────────────

class TestAllowedOrgs:
    def test_super_admin_unrestricted(self):
        p = {"role": "admin", "org_id": SUPER_ORG}
        assert allowed_orgs(p) is None

    def test_org_scoped_admin_sees_only_own(self):
        p = {"role": "admin", "org_id": "org-x"}
        assert allowed_orgs(p) == ["org-x"]

    def test_non_admin_cannot_hold_star(self):
        p = {"role": "user", "org_id": SUPER_ORG}
        assert allowed_orgs(p) == [DEFAULT_ORG_ID]

    def test_missing_org_defaults(self):
        p = {"role": "viewer"}
        assert allowed_orgs(p) == [DEFAULT_ORG_ID]


# ── firmware download tokens (UC-23 rule 5) ─────────────────────────────────

class TestFirmwareTokens:
    def test_roundtrip(self):
        from app.main import issue_firmware_download_token, verify_firmware_download_token

        tok, exp = issue_firmware_download_token("dev-1", "ab" * 32)
        assert verify_firmware_download_token("dev-1", "ab" * 32, exp, tok)

    def test_wrong_device_rejected(self):
        from app.main import issue_firmware_download_token, verify_firmware_download_token

        tok, exp = issue_firmware_download_token("dev-1", "ab" * 32)
        assert not verify_firmware_download_token("dev-2", "ab" * 32, exp, tok)

    def test_expired_rejected(self):
        from app.main import verify_firmware_download_token

        old_exp = int(time.time()) - 10
        sig = __import__("app.main", fromlist=["_firmware_token_sig"])._firmware_token_sig(
            "d", "h", old_exp)
        assert not verify_firmware_download_token("d", "h", old_exp, sig)


# ── startup guardrails ───────────────────────────────────────────────────────

class TestStrictStartupGuardrails:
    def test_default_secret_refused_in_strict(self, monkeypatch):
        from app.config import validate_settings, DEFAULT_ADMIN_PASSWORD

        monkeypatch.setattr(settings, "auth_mode", "strict")
        monkeypatch.setattr(settings, "jwt_secret_key", DEFAULT_JWT_SECRET)
        with pytest.raises(RuntimeError, match="JWT_SECRET_KEY"):
            validate_settings()

    def test_default_admin_password_refused_in_strict(self, monkeypatch):
        from app.config import validate_settings, DEFAULT_ADMIN_PASSWORD

        monkeypatch.setattr(settings, "auth_mode", "strict")
        monkeypatch.setattr(settings, "jwt_secret_key", "x" * 32)
        monkeypatch.setattr(settings, "admin_password", DEFAULT_ADMIN_PASSWORD)
        with pytest.raises(RuntimeError, match="admin credentials"):
            validate_settings()

    def test_open_mode_allows_defaults(self, monkeypatch):
        from app.config import validate_settings

        monkeypatch.setattr(settings, "auth_mode", "open")
        validate_settings()  # must not raise
