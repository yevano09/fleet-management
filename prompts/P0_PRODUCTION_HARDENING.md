# Agent prompt: P0 production hardening (UC-23 … UC-27)

Copy everything below the line into a new coding-agent chat (or open this file and say “execute this prompt”). Do not implement P1/P2. Do not refactor unrelated features. Verify with a real Docker production-profile deployment before declaring done.

---

## Mission

You are implementing **P0 ship-blockers** for **Fleet Commander** (`C:\code\IoT-Forge\fleet-management`). Today this is a strong demo: dashboard Google OAuth exists, MQTT and REST device APIs do not. A customer cannot safely put hardware on the internet.

Implement these five use cases **end to end**, then **prove** them on a running compose stack:

| ID | Name | Outcome |
|---|---|---|
| **UC-23** | API auth + RBAC | Unauthenticated mutating REST calls return 401. Roles are enforced. Audit `actor` is a real user id/email, not `"dashboard"`. |
| **UC-24** | MQTT TLS + identity + ACLs | Production broker rejects anonymous clients. A device can only pub/sub its own `iot/fleet/{id}/…` topics. Backend uses a privileged client cert. |
| **UC-25** | Certificate lifecycle | Issue / rotate / revoke device certs (internal CA). Revoked device cannot reconnect. JITP: first connection with a CA-signed cert provisions the device. |
| **UC-26** | Multi-tenancy | Org isolation. Org A cannot read or command Org B devices. Existing single-tenant demo still works via a seeded `default` org. |
| **UC-27** | Production datastore + HA | Production profile defaults to PostgreSQL (async). Backend is not a single unreplicated worker. Health checks do not depend on `/devices`. |

Read first (do not skim): `AGENTS.md`, `SECURITY.md` § MQTT + API auth, `app/auth.py`, `app/models.py`, `app/config.py`, `app/database.py`, `app/mqtt_client.py`, `app/main.py`, `app/routers/*.py`, `docker-compose.yml`, `docker/mosquitto/mosquitto.conf`, `simulator/simulator.py`, `tests/test_e2e.py`, `Dockerfile`, `.env.example`.

Ponytail: smallest working design. Reuse existing `UserRole`, JWT cookies, `log_action()`, MQTT topic layout. No new product features. No Alembic unless `create_all` is genuinely insufficient (prefer `create_all` + documented `docker compose down -v` for this slice).

---

## Ground truth (what is true today — do not re-discover incorrectly)

### Auth that exists
- Dashboard HTML is gated: `app/routers/dashboard.py` redirects to `/auth/login`.
- Google OAuth + admin password login live in `app/auth.py` / `app/routers/auth.py`.
- JWT in httponly cookies `fleet_session` / `fleet_admin_session`.
- `UserRole` enum already: `user`, `admin`, `operator`, `viewer`, `fleet_manager` (`app/models.py`).
- `UserSession.role` column exists. `DEFAULT_USER_ROLE` / `default_user_role` defaults to `viewer`.
- **REST routers are open.** `app/routers/devices.py`, `ota.py`, and every other API router use `Depends(get_db)` only. Anyone can `POST /devices/register`, `POST /ota/trigger`, upload firmware, decommission devices.

### MQTT that exists
- Topics (QoS 1): `iot/fleet/register`, `iot/fleet/{id}/heartbeat`, `iot/fleet/{id}/status/ota`, `iot/fleet/{id}/status/v2g`, `iot/fleet/{id}/command/{ota,config,v2g,restart,rollback,shadow,maintenance}`.
- `docker/mosquitto/mosquitto.conf`: port **1883**, `allow_anonymous true`. No TLS, no password file, no ACL.
- `MqttClient.connect()` optionally sets username/password; **never configures TLS**. Client id is hardcoded `fleet-commander-backend`.
- Simulator publishes with no credentials (`simulator/simulator.py`).

### Data store that exists
- Default `DATABASE_URL=sqlite+aiosqlite:///./data/fleet.db`.
- Engine is **async**: `create_async_engine` in `app/database.py`.
- `.env.example` and compose production URL use `postgresql+psycopg2://` — **that will not work** with `create_async_engine`. You must use `postgresql+asyncpg://` and add `asyncpg` (keep or drop unused `psycopg2-binary`).
- Postgres service is behind compose **`profiles: [production]`**. Volume is declared as `pgdata:` but the service mounts `pgdata:` — **fix this name mismatch**.
- Schema is `Base.metadata.create_all` on startup. No migrations.
- Backend Dockerfile runs **one** uvicorn worker: `uvicorn app.main:app --host 0.0.0.0 --port 8000`.
- Backend healthcheck is `GET http://localhost:8000/devices` — **this will 401 after UC-23**. There is `/health/mqtt` but no general `/health`.

### Tests that will break
- `tests/test_e2e.py` `wait_for_backend()` and all calls hit `/devices` with no token.
- Docker `tests` service same.
- Unit tests that call routers directly must still run.

### Constraints from `AGENTS.md`
- Naive UTC datetimes (`utcnow()`), never timezone-aware.
- Dual agent tools (`agents/async_tools.py` vs `agents/tools.py`) — HTTP tool path must send auth headers once APIs require them.
- Do not weaken MQTT reconnect (`reconnect_delay_set(1, 60)`).
- Pin new Python deps in `requirements.txt`.

---

## Non-negotiable product rules

1. **Local demo still boots.** `docker compose --profile demo up --build` must work. Use an explicit mode flag, not “always TLS / always Postgres”.
2. **Production profile is secure by default.** `docker compose --profile production up --build` must not allow anonymous MQTT or open REST writes.
3. **Dev escape hatch is one env var**, not scattered `if settings.debug` checks:
   - `AUTH_MODE=open` — current behaviour (unauthenticated REST, anonymous MQTT). Default **only** when unset **and** `DATABASE_URL` is sqlite (local/dev).
   - `AUTH_MODE=strict` — required for `--profile production`. Fail startup if JWT secret is still `change-me-to-a-random-secret` or admin password is still the default.
4. **Do not auth-gate** (in either mode):
   - `GET /health` (add this; liveness: process up)
   - `GET /health/ready` (readiness: DB ping + MQTT connected)
   - `GET /health/mqtt` (keep)
   - `GET /metrics` and `GET /metrics/` (Prometheus scrape)
   - `GET /auth/*` login/callback pages
   - `GET /docs` / `GET /openapi.json` may stay open in `open` mode; in `strict` mode require admin **or** disable them via settings (`DOCS_ENABLED=false` in production compose).
5. **Firmware download** `GET /firmware/{filename}` is a C2 channel. In `strict` mode require a short-lived signed query token issued when the OTA command is published (HMAC with `JWT_SECRET_KEY`, embed `device_id` + `firmware` + expiry). Simulator and ESP path must use the tokenized URL. In `open` mode keep current behaviour.
6. Every mutating route’s `log_action(..., actor=...)` must use the authenticated principal (`email` or `admin:{username}`), never the string `"dashboard"`.

---

## Implementation order (do not reorder)

Schema first, then auth, then MQTT, then certs, then compose/HA. Each phase must leave tests runnable.

### Phase 0 — Plumbing (half day)

- Add `GET /health` → `{"status":"ok"}`.
- Add `GET /health/ready` → 200 only if DB `SELECT 1` succeeds and MQTT `_connected`; else 503.
- Point **all** Docker healthchecks (backend, tests wait loops) at `/health`, not `/devices`.
- Add settings:
  - `auth_mode: str = "open"`  # open | strict
  - `mqtt_tls_enabled: bool = False`
  - `mqtt_ca_cert`, `mqtt_client_cert`, `mqtt_client_key` paths
  - `mqtt_broker_port` already exists; production TLS port **8883**
  - `internal_ca_dir: str = "./certs"`
- `validate_settings()`: if `auth_mode==strict`, refuse default JWT secret and default admin password.

### Phase 1 — UC-26 Multi-tenancy (schema + query filter)

**Model**
- New table `organizations`: `id` (uuid str PK), `name` (unique), `slug` (unique), `created_at`.
- Seed org `{id: "org-default", name: "Default", slug: "default"}` on startup if missing.
- Add **nullable-then-backfilled** `org_id` (FK, index) to: `devices`, `user_sessions`, `firmware` (or keep firmware global to the org), `geofences`, `webhook_subscriptions`, `ota_schedules`, `alerts` if they exist as rows, and any other tenant-owned table you touch. Device-child tables (`telemetry`, `ota_deployments`, `device_shadows`, `command_queue`, …) inherit tenancy via `device_id` — do **not** duplicate `org_id` on every child unless a query needs it.
- `UserSession.org_id` default `org-default`. Admin users may later belong to an org too.
- `Device.org_id` default `org-default`.

**API**
- `POST /orgs` — admin only (after Phase 2). `{name}` → create org.
- `GET /orgs` — admin lists all; others see only their org.
- JWT payload **must include** `org_id` and `role` (update `create_user_jwt` / `create_admin_jwt`). Admin JWT: `org_id` may be `*` meaning all orgs (super-admin). Existing cookies become invalid — that is OK; bump nothing, just re-login.
- Helper `current_org_ids(user) -> list[str] | None` where `None` means all (super-admin).
- **Every list/get/update/delete query on tenant data must filter `Device.org_id.in_(orgs)`** (or equivalent). Missing filter is a P0 bug.
- Cross-tenant access → **404** (do not leak existence with 403).

**Bootstrap**
- Existing SQLite files: on startup, if `devices.org_id` is null, set `org-default`. Same for sessions.
- Simulator devices land in `org-default`.

### Phase 2 — UC-23 API auth + RBAC

**Dependencies**
- `app/auth.py`: add FastAPI dependencies (cookie **or** `Authorization: Bearer <jwt>`):
  - `require_user` — 401 if missing/invalid/revoked.
  - `require_role(*roles)` — 403 if role not in set. Treat `admin` as satisfying all role checks.
  - `require_admin` — keep/extend existing.

**Role matrix (enforce this, nothing looser)**

| Action | viewer | user | operator | fleet_manager | admin |
|---|---|---|---|---|---|
| GET lists, telemetry, shadows, audit (own org) | yes | yes | yes | yes | yes |
| Register / heartbeat / config via REST | | | yes | yes | yes |
| OTA upload / trigger / schedule | | | | yes | yes |
| Lifecycle decommission | | | | yes | yes |
| Provisioning bulk import | | | | yes | yes |
| Webhook CRUD | | | | yes | yes |
| Aegis ingest / rerun, rule changes | | | | | yes |
| Org CRUD, session revoke, user role change | | | | | yes |
| Agent GET endpoints that only read | yes | yes | yes | yes | yes |
| Agent endpoints that trigger OTA / V2G publish | | | | yes | yes |

Heartbeat **MQTT path** stays device-authenticated (Phase 3), not JWT.

**Apply `Depends` on every router that mutates or reads tenant data.** Do it once via a shared `deps.py`, not copy-paste 40 times.

**API tokens for automation (minimal)**
- Table `api_keys`: `id`, `org_id`, `name`, `prefix`, `key_hash` (SHA-256 of secret), `role`, `created_at`, `revoked`.
- `POST /admin/api-keys` admin-only; returns the secret **once**.
- Header `X-API-Key: fck_<secret>` accepted as an alternative to JWT. Hash-lookup, then synthesize the same principal dict (`email=f"apikey:{name}"`, `role`, `org_id`).
- E2E tests and `agents/tools.py` use an API key in `strict` mode so they do not need Google OAuth.

**Audit**
- Replace `"dashboard"` actors with `principal["email"]`.
- MQTT-originated events stay `actor="system"` or `actor=f"device:{id}"`.

**Docs / OpenAPI**
- In `strict` mode, hide `/docs` unless admin (or disable).

**Tests (unit)**
- New `tests/test_auth_rbac.py`:
  - no token → 401 on `POST /devices/register` and `POST /ota/trigger`.
  - viewer token → 403 on OTA trigger, 200 on `GET /devices`.
  - operator can register, cannot upload firmware.
  - fleet_manager can trigger OTA.
  - API key works.
- Keep `AUTH_MODE=open` as default for **existing** unit tests that talk to routers without tokens, **or** set `AUTH_MODE=open` in those test modules’ fixtures. Do not silently skip RBAC tests.

### Phase 3 — UC-24 MQTT TLS + ACLs

**Cert material (repo-local, gitignored except scripts)**
- Script `scripts/gen-mqtt-pki.sh` (bash; this project already expects WSL for compose):
  - Internal CA (`certs/ca.crt`, `certs/ca.key`).
  - Broker cert SAN: `DNS:mosquitto`, `DNS:localhost`.
  - Backend client cert CN=`fleet-backend`.
  - Simulator device certs CN=`{device_id}` for the N demo devices (or generate on the fly in simulator startup).
- Add `certs/` to `.gitignore` except `certs/README.md` explaining how to regenerate.
- `docker-compose` production mounts `./certs` into mosquitto, backend, simulator.

**Mosquitto production config** (`docker/mosquitto/mosquitto.ssl.conf` — **do not destroy** the anonymous `mosquitto.conf` used by demo):
```
listener 8883
cafile /mosquitto/certs/ca.crt
certfile /mosquitto/certs/server.crt
keyfile /mosquitto/certs/server.key
require_certificate true
use_identity_as_username true
allow_anonymous false
acl_file /mosquitto/config/acl
max_connections 5000
```
ACL pattern (Mosquitto `%u` = cert CN):
```
# Backend
user fleet-backend
topic readwrite iot/fleet/#

# Devices: CN must equal device_id
pattern read iot/fleet/%u/command/#
pattern write iot/fleet/%u/heartbeat
pattern write iot/fleet/%u/status/#
pattern write iot/fleet/register
```
Tighten register if you implement JITP in Phase 4 (device may publish register only once; optional).

**Backend client**
- When `mqtt_tls_enabled=true`, `MqttClient.connect()` must:
  - `tls_set(ca_certs=..., certfile=..., keyfile=..., tls_version=ssl.PROTOCOL_TLS_CLIENT)`
  - connect to port 8883
  - client_id / cert CN `fleet-backend`
- Username/password remain optional fallback for non-TLS staging; production compose uses certs only.

**Simulator**
- Env: `MQTT_TLS=1`, `MQTT_CA_CERT`, `MQTT_CLIENT_CERT`, `MQTT_CLIENT_KEY`, `MQTT_BROKER_PORT=8883`.
- Each simulated device uses **its own** cert (CN = that device’s id). If generating N devices, generate N certs in an entrypoint script. Do not share the backend cert with devices.

**Negative tests**
- Device cert for `dev-A` publishing to `iot/fleet/dev-B/heartbeat` is **rejected** by ACL (assert via mosquitto logs or a python paho client in tests).
- Anonymous connect to 8883 fails.

Keep demo profile on 1883 + `allow_anonymous true` so `docker compose --profile demo` still matches `DEMO.md`.

### Phase 4 — UC-25 Certificate lifecycle + JITP

**Model** `device_certificates`:
- `id`, `device_id` (nullable until claimed), `org_id`, `fingerprint_sha256` (unique), `pem` (public cert only — **never store device private keys after issue response**), `issued_at`, `expires_at`, `revoked_at`, `serial`, `status` (`issued|active|revoked|expired`).

**Internal CA service** `app/pki.py` (use already-pinned `cryptography`):
- `issue_device_cert(device_id, org_id, ttl_days=365) -> {cert_pem, key_pem, fingerprint}` — key_pem returned **once** to the caller, not persisted.
- `revoke(fingerprint)` — append serial to Mosquitto CRL **or** (ponytail acceptable) maintain an in-process + file CRL that mosquitto is configured to use (`crlfile`). If CRL reload is painful, ponytail: disconnect path = backend tracks revocation and **refuses to honor** messages from revoked fingerprints; plus delete/regenerate mosquitto `acl` is not enough. Prefer a real CRL file `certs/ca.crl` regenerated on revoke and send Mosquitto `SIGHUP`. Document the SIGHUP in compose (sidecar or backend calls `docker kill -s HUP fleet-mosquitto` is **not** OK from inside the backend). Simplest robust approach: `use_identity_as_username true` + backend JITP allow-list: unknown/revoked CN’s register/heartbeat are ignored and a metric incremented. Broker still requires a CA-signed cert (stolen revoked cert can connect to MQTT until CRL exists). **Implement CRL.** Reload: mosquitto 2.x reads CRL on new connections if `require_certificate true` and `crlfile` is set; restart mosquitto in the production compose `certs-reload` helper script.

**API** (admin / fleet_manager)
- `POST /devices/{id}/certs` — issue; returns cert+key once; audit log.
- `POST /devices/{id}/certs/rotate` — revoke old, issue new.
- `POST /certs/{fingerprint}/revoke` — revoke.
- `GET /devices/{id}/certs` — list status, no private keys.

**JITP**
- Device connects with CA-signed cert, CN = intended device id (or serial).
- On `iot/fleet/register`, backend:
  1. Reads TLS identity — **paho does not give you the cert on an incoming broker message.** Identity must be the MQTT username (`use_identity_as_username true` → username = CN).
  2. If CN has a non-revoked issued cert for that org and no device row: create device in the cert’s `org_id`, attach cert, emit `device.registered`.
  3. If CN does not match payload `device_id`/`name` identity: **reject** (log + metric, do not register).
- Claim-token path (UC-12) remains for QR bring-up: claiming issues a cert and returns PEM to the operator (not to a random MQTT client).

**ESP32 / DEMO**
- Add a short subsection to `ESP32.md` or `DEMO.md`: production devices need the CA + device cert flashed; demo profile unchanged.

### Phase 5 — UC-27 Postgres default + HA

**Database**
- Production compose:
  - Remove `profiles: [production]` **only from postgres if you instead introduce a dedicated compose file**; otherwise keep the profile but make `backend` in that profile **require** postgres (`depends_on: postgres: condition: service_healthy`) and set  
    `DATABASE_URL=postgresql+asyncpg://fleet:fleetpass@postgres:5432/fleet`
  - Fix volume name (`pgdata` vs `pgdata`) — one name, used by both the service and `volumes:`.
  - Add `asyncpg` to `requirements.txt` (pin). SQLAlchemy async URL **must** be `postgresql+asyncpg://`.
  - `app/database.py`: SQLite connect args stay; for Postgres set `pool_size=5`, `max_overflow=10`, `pool_pre_ping=True`. Do not use `NullPool` in production.
- `create_all` on empty Postgres must succeed for all current tables **plus** new P0 tables.

**HA (keep it small)**
- Backend command becomes gunicorn or uvicorn with workers, **but** this app has a process-local MQTT thread, OTA watchers, Aegis scheduler, command-queue flusher. Multiple workers = duplicate MQTT subscribers and duplicate schedulers.
- **Ponytail HA that is actually correct:**
  1. Split is out of scope.
  2. Run **one** “worker” replica that owns MQTT + schedulers (`ROLE=leader`).
  3. Optionally run **N** `ROLE=api` replicas that serve HTTP only (no MQTT loop, no Aegis, no OTA watcher). Compose production: `backend` (leader) + `backend-api` (replicas: 2) sharing the same image, firmware volume, and Postgres.
  4. Document that MQTT/schedulers are singleton. This is honest HA for the API, not pretend multi-worker uvicorn.
- Shared `firmware_data` volume already exists — mount it on all API replicas.
- Prometheus scrape both backends **or** only leader; don’t double-count custom gauges if both scrape (ponytail: scrape leader only).

**Health**
- Leader `/health/ready` includes MQTT.
- API replica `/health/ready` includes DB only.

**Compose production env (strict)**
```
AUTH_MODE=strict
MQTT_TLS_ENABLED=true
MQTT_BROKER_PORT=8883
DOCS_ENABLED=false
JWT_SECRET_KEY=<non-default, from .env>
ADMIN_PASSWORD=<non-default, from .env>
```

`.env.example` updated with all new vars and comments. Never commit real secrets.

---

## Files you are expected to touch (keep the diff tight)

- `app/auth.py`, new `app/deps.py`, `app/pki.py`
- `app/models.py`, `app/database.py`, `app/config.py`, `app/mqtt_client.py`, `app/main.py`
- `app/routers/*.py` (Depends + org filter + actor)
- `app/audit.py` only if signature needs a principal helper
- `agents/tools.py` (pass API key)
- `simulator/simulator.py`, `Dockerfile.simulator` (need `cryptography`/`paho` TLS — paho already there)
- `docker-compose.yml`, `docker/mosquitto/mosquitto.conf` (leave), new `mosquitto.ssl.conf` + `acl`
- `scripts/gen-mqtt-pki.sh`, `scripts/verify-p0.sh` (see verification)
- `Dockerfile` (maybe gunicorn; only if you add it — pin in requirements)
- `requirements.txt`, `.env.example`, `.gitignore`
- `tests/test_auth_rbac.py`, `tests/test_tenancy.py`, `tests/test_pki.py`, update `tests/test_e2e.py`
- `SECURITY.md` (replace “add this in production” sample with what you actually shipped)
- `AGENTS.md` Current State / Next Steps — one short paragraph that P0 is implemented and how to run production profile
- `DEMO.md` — note demo profile remains open; production profile is strict
- `ESP32.md` — cert flashing note

Do **not** rewrite `USE_CASES.md`. Do **not** implement canary auto-promote, OCPP, LwM2M, delta OTA, remote shell.

---

## Verification (mandatory — you are not done until this passes)

Write and run `scripts/verify-p0.sh`. It must be runnable from WSL as documented in `AGENTS.md`:

```bash
wsl -d Ubuntu-24.04 -- bash -c 'cd /mnt/c/code/IoT-Forge/fleet-management && bash scripts/verify-p0.sh'
```

The script (and you) must perform **all** of the following. Fail the script on first error (`set -euo pipefail`).

### A. Unit / RBAC / tenancy / PKI
```bash
python -m pytest tests/test_aegis_unit.py tests/test_v2g.py tests/test_simulator_unit.py tests/test_config_unit.py tests/test_session5_unit.py tests/test_auth_rbac.py tests/test_tenancy.py tests/test_pki.py -q
```
All must pass.

### B. Demo profile still works (regression)
```bash
docker compose down --volumes --remove-orphans
docker compose --profile demo up --build -d
# wait for /health
curl -sf http://localhost:8000/health
curl -sf http://localhost:8000/devices   # AUTH_MODE=open → 200
docker compose --profile demo down --volumes --remove-orphans
```

### C. Production profile — bring-up
```bash
# generate PKI
bash scripts/gen-mqtt-pki.sh
# .env with non-default JWT_SECRET_KEY and ADMIN_PASSWORD
docker compose --profile production up --build -d
curl -sf http://localhost:8000/health
curl -sf http://localhost:8000/health/ready
```
Postgres is healthy. Backend leader is healthy. Mosquitto **8883** is listening. **1883 may be unpublished** in production.

### D. UC-23 — REST auth
```bash
# no token
code=$(curl -s -o /dev/null -w '%{http_code}' -X POST http://localhost:8000/devices/register -H 'Content-Type: application/json' -d '{"name":"x"}')
test "$code" = "401"

# admin login → cookie or JWT
# POST /auth/admin/login with production admin creds
# GET /devices with cookie → 200
# viewer API key GET /devices → 200
# viewer API key POST /ota/trigger → 403
```
Record actual commands in the script (admin login is a form POST; parse cookie).

### E. UC-26 — tenancy
- Create org `alpha` and org `beta` as admin.
- Issue an API key in each org (operator or fleet_manager).
- Register `device-alpha` with alpha key, `device-beta` with beta key.
- Alpha key `GET /devices` must not include `device-beta`.
- Alpha key `GET /devices/{beta_id}` → 404.
- Alpha key must not successfully `POST /ota/trigger` for beta’s id.

### F. UC-24 — MQTT TLS / ACL
- `mosquitto_pub -p 1883` against production broker **fails** (connection refused or not exported).
- `mosquitto_pub -p 8883` with **no cert** fails.
- Publish heartbeat for device A using **device A cert** to `iot/fleet/{A}/heartbeat` succeeds (backend updates `last_seen`).
- Publish heartbeat for device B using **device A cert** to `iot/fleet/{B}/heartbeat` **fails** (ACL). Confirm B’s `last_seen` unchanged.

If `mosquitto_pub` is not on the host, run it via `docker compose exec mosquitto` or a one-shot eclipse-mosquitto container mounting `./certs`.

### G. UC-25 — cert lifecycle
- `POST /devices/{A}/certs/rotate` as fleet_manager/admin.
- Old cert can no longer complete TLS (CRL) **or** backend ignores it and `/health` metrics/`device_cert_rejected_total` increments. Prefer CRL: reconnect with old cert fails.
- JITP: a brand-new cert issued for a never-seen CN, device publishes `iot/fleet/register` with matching name → device row appears in the cert’s org.

### H. UC-27 — Postgres + API replica
- `docker compose --profile production exec postgres pg_isready`
- `docker compose --profile production exec backend python -c "from app.config import settings; assert settings.database_url.startswith('postgresql+asyncpg')"`
- Insert a device via API; `docker compose exec postgres psql -U fleet -d fleet -c 'select count(*) from devices;'` ≥ 1
- API replica container is up (`backend-api` or whatever you named it). `GET /health/ready` on that replica is 200. Killing the replica does not stop MQTT heartbeats from updating the leader.

### I. Existing E2E profile
Update `tests/test_e2e.py` and compose `tests` service:
- Wait on `/health` not `/devices`.
- In production/testing-strict, send `X-API-Key`.
- Either:
  - `docker compose --profile testing run --build --rm tests` against **open** demo (auth optional), **and**
  - a new `tests/test_e2e_strict.py` (or env `AUTH_MODE=strict` + key) covering register → heartbeat → OTA trigger still works with a key.

Do not leave the original 40 e2e tests permanently red.

### J. Cleanup
```bash
docker compose --profile production down --volumes --remove-orphans
docker compose --profile demo down --volumes --remove-orphans
```

---

## Definition of done

- [ ] All five UCs implemented as specified, not stubbed.
- [ ] `scripts/verify-p0.sh` exits 0 on this machine (WSL path above).
- [ ] Demo profile still registers simulator devices on 1883 with no certs.
- [ ] Production profile refuses anonymous MQTT and unauthenticated REST writes.
- [ ] `SECURITY.md` describes the **shipped** design (TLS 8883, RBAC matrix, API keys, CRL), not a future wishlist.
- [ ] No secrets committed. `certs/*.key` gitignored.
- [ ] Default JWT secret cannot start `AUTH_MODE=strict`.
- [ ] You pasted the last 50 lines of `verify-p0.sh` output in the PR/chat summary, plus `docker compose --profile production ps`.

## Out of scope (reject if requested mid-flight)

P1 groups/search, canary auto-promote, delta/A-B OTA, remote shell, OCPP, OpenADR, LwM2M, SSO/SAML, billing, Timescale hypertables (Postgres relational is enough for P0).

## If something blocks you

- Corporate proxy: `source ./behindproxy.sh` before image builds (`AGENTS.md`).
- WSL: `wsl -d Ubuntu-24.04`. Sudo password is in `AGENTS.md` (use only for docker if required).
- Do not flip `AUTH_MODE` default to `strict` for sqlite unit tests — you will spend the session repairing fixtures instead of shipping PKI.
