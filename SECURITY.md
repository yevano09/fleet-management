# Fleet Commander — Security Policy

## Supported Versions

| Version | Supported |
|---------|-----------|
| 1.x     | ✅ Active development |
| < 1.0   | ❌ Pre-release |

---

## Reporting a Vulnerability

We take security seriously. If you discover a vulnerability in Fleet Commander, please follow the coordinated disclosure process below.

**Do not open public GitHub issues for security vulnerabilities.**

### Contact

- **Email:** security@fleet-commander.io
- **PGP Key:** Available at https://fleet-commander.io/pgp-key.asc
- **Response SLA:** 48 hours for initial acknowledgment

### Disclosure Process

1. **Report** — Send details to security@fleet-commander.io. Include:
   - Component and version affected
   - Type of vulnerability (XSS, RCE, auth bypass, etc.)
   - Steps to reproduce (PoC preferred)
   - Impact assessment

2. **Acknowledgment** — We confirm receipt within 48 hours and assign a tracking ID.

3. **Assessment** — Our security team triages the report within 5 business days:
   - Critical / High: Immediate fix, patch release within 48 hours
   - Medium: Fix in next sprint cycle (≤ 14 days)
   - Low: Fix in upcoming release (≤ 30 days)

4. **Fix & Release** — A patched version is published. The vulnerability is disclosed publicly after the fix ships.

5. **Credit** — Reporters are credited in release notes (unless anonymity is requested).

---

## Threat Model

### Assets Protected

| Asset | Sensitivity | Impact if Compromised |
|-------|-------------|-----------------------|
| Device credentials & identity | High | Device impersonation, fleet takeover |
| Firmware binaries | Critical | Malware injection into IoT fleet |
| OTA deployment tokens | High | Unauthorized firmware pushes |
| Device telemetry (heartbeats) | Low | Fleet topology disclosure |
| Database (device records) | High | PII exposure, configuration leak |
| MQTT broker access | Critical | Full command & control compromise |

### Threat Actors

| Actor | Capability | Motivation |
|-------|-----------|------------|
| Malicious IoT device | Low — constrained HW | Join fleet, extract firmware |
| Network attacker | Medium — MITM | Intercept OTA commands |
| External hacker | High — zero-days | Ransomware, botnet recruitment |
| Insider | Medium — credentialed | Sabotage, data exfiltration |

### Attack Surface

| Surface | Risk | Mitigation |
|---------|------|------------|
| REST API endpoints | High | Rate limiting, API keys in production |
| MQTT unauthenticated topics | High | TLS + username/password or client certs in production |
| Firmware upload | Medium | SHA256 verification, file type validation |
| Dashboard (browser) | Low | Content-Security-Policy headers |
| Prometheus /metrics | Low | Network restriction to monitoring VLAN |

---

## P0 Production Hardening — Shipped (UC-23 … UC-27)

Implemented end-to-end and proven by `scripts/verify-p0.sh` (exit 0):

| UC | Outcome |
|----|---------|
| UC-23 | REST auth + RBAC matrix + API keys + real audit actors + HMAC-gated firmware downloads |
| UC-24 | mTLS broker on 8883, identity = cert CN, per-device topic ACLs, anonymous/refused paths tested |
| UC-25 | Internal CA: issue / rotate / revoke device certs, CRL reload, JITP auto-provisioning |
| UC-26 | Organizations with query-level isolation; seeded `org-default` keeps the demo intact |
| UC-27 | `postgresql+asyncpg` default in production; leader (MQTT+schedulers) vs stateless `backend-api` replicas; `/health` + `/health/ready` split |

Run it yourself:
```bash
wsl -d Ubuntu-24.04 -- bash -c \
  'cd /mnt/c/code/IoT-Forge/fleet-management && bash scripts/verify-p0.sh'
```

---
---

## Security Measures by Component

### 1. FastAPI Backend

- **Input Validation** — All endpoints use Pydantic schemas (`schemas.py`) for request validation. Malformed payloads are rejected with 422 before reaching business logic.
- **SQL Injection** — SQLAlchemy ORM with parameterized queries. No raw SQL anywhere.
- **File Upload Safety** — Firmware uploads (`/ota/upload`) validate SHA256 hash server-side. `python-multipart` is used securely (no temporary file races).
- **Dependency Confusion** — All dependencies pinned to exact versions in `requirements.txt` with hashes verified during Docker build.
- **CORS** — In production, restrict origins to known dashboard domains. Not currently set (allows all in dev).

**Production recommendation:**
```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://dashboard.fleet-commander.io"],
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization"],
)
```

### 2. MQTT (Mosquitto Broker)

#### Development (Docker Compose)

- Anonymous access allowed for local dev only.
- Listener on 1883 (no TLS). **Never expose to the internet.**

#### Production (`--profile production`) — **shipped**

Broker config lives in `docker/mosquitto/mosquitto.ssl.conf`; the anonymous
demo config is untouched in `mosquitto.conf`.

| Measure | Implementation (as shipped) |
|---------|------------------------------|
| TLS encryption | `listener 8883` with internal-CA `cafile/certfile/keyfile` |
| Identity | `require_certificate true` + `use_identity_as_username true` → MQTT username == client-cert CN |
| Anonymous access | `allow_anonymous false`; port 1883 is NOT published in production |
| ACL | `docker/mosquitto/acl`: `fleet-backend` gets `readwrite iot/fleet/#`; every device CN may only WRITE its own `register/heartbeat/status/#` and READ its own `command/#` |
| Revocation | PEM CRL at `certs/ca.crl`, regenerated by `POST /certs/{fp}/revoke`, loaded on broker restart via `scripts/reload-broker.sh` |
| Defense-in-depth | Backend drops MQTT messages from identities with no active certificate; counter `fleet_device_cert_rejected_total{reason}` |
| JITP | First verified publish to `iot/fleet/{cn}/register` provisions the device into the org recorded on its certificate |

**ACL as shipped (`docker/mosquitto/acl`):**
```
user fleet-backend
topic readwrite iot/fleet/#

pattern write iot/fleet/%u/register
pattern write iot/fleet/%u/heartbeat
pattern write iot/fleet/%u/status/#
pattern read  iot/fleet/%u/command/#
```

PKI bootstrap: `bash scripts/gen-mqtt-pki.sh <device_count>` (see
`certs/README.md`). Broker cert SAN covers `mosquitto`, `mosquitto-tls`,
`localhost`.

### 3. OTA Pipeline Security

| Stage | Security Control |
|-------|-----------------|
| Firmware upload | SHA256 computed server-side, stored in DB. Re-computed on download. |
| OTA trigger | Device selection validated against authorized device list. |
| Command publish | Payload includes `{firmware_url, sha256_hash, timestamp}` — device verifies hash |
| Download delivery | `/firmware/{filename}` requires a short-lived HMAC-SHA256 query token (issued per-device when the OTA command is published) whenever `AUTH_MODE=strict`; tokens embed device_id + firmware hash + expiry |
| Device verification | Device compares received SHA256 against expected. `hash_mismatch` triggers automatic rollback. |
| Rollback | Device firmware_version reverts to `previous_firmware_version`. Deployment marked `rolled_back`. |

### 4. Database

| Concern | Control |
|---------|---------|
| SQLite dev | File permissions: `chmod 600 fleet.db`. Not exposed to network. |
| PostgreSQL prod | Connection over TLS. Credentials in `.env` (not committed). |
| Secrets | Production URL uses the async driver `postgresql+asyncpg://…` (pooled, `pool_pre_ping=True`). Credentials live only in `.env`. |
| Least privilege | DB user has only `INSERT/SELECT/UPDATE/DELETE` on fleet tables. No `DROP` or schema changes. |

### 5. Observability Stack

| Component | Security |
|-----------|----------|
| Prometheus | Bind to `127.0.0.1:9090` or monitoring VLAN only. No auth — use reverse proxy with basic auth. |
| Grafana | Default `admin/admin` — **must change immediately in production**. Enable OAuth or LDAP. |
| Metrics | `/metrics` exposes request latencies, OTA counts — no PII or secrets. Still, restrict to monitoring network. |

### 6. API Authentication & RBAC — **shipped**

Auth behaviour is controlled by a single switch:

| `AUTH_MODE` | Behaviour |
|---|---|
| `open` | Legacy/demo mode: unauthenticated REST, anonymous MQTT. Default ONLY when unset AND `DATABASE_URL` is sqlite. |
| `strict` | Required for `--profile production`. Startup REFUSES default `JWT_SECRET_KEY` / admin credentials. Docs disabled via `DOCS_ENABLED=false`. Firmware downloads need HMAC tokens. |

Never auth-gated in either mode: `/health`, `/health/ready`, `/health/mqtt`,
`GET /metrics(/)`, `/auth/*`. In strict mode `POST /lifecycle/claim` stays
token-authenticated by design — the one-time claim token IS the credential.

**Principals:** Google-OAuth JWT cookie · admin JWT cookie · `Authorization: Bearer <jwt>` · `X-API-Key: fck_…`
API keys are minted via `POST /admin/api-keys` (admin-only); the secret is shown once, only its SHA-256 is stored, and keys can never hold super-admin scope.

**Role matrix (enforced by `app/deps.py`, rank hierarchy viewer<user<operator<fleet_manager<admin):**

| Action | Minimum role |
|---|---|
| GET lists, telemetry, shadows, audit, read-only agents | any authenticated |
| Register / heartbeat / remote-config (REST), alerts ack, shadow update, command queue, geofence CRUD, predictive scan | operator |
| OTA upload / trigger / schedules, lifecycle decommission, provisioning, webhook CRUD, certificates issue/rotate/revoke, V2G dispatch | fleet_manager |
| Aegis scan/ingest/rerun/prune, org CRUD, api-key management, audit prune | admin |

Every mutating route writes `log_action(...)` with the real principal email
(`apikey:<name>`, `admin:<user>`, or OAuth email) — never the old literal
`"dashboard"`. MQTT-originated events use `system` / device identity.

Cross-tenant access returns **404** (no existence leak). See `tests/test_auth_rbac.py`,
`tests/test_tenancy.py`, and the E-section of `scripts/verify-p0.sh`.

## Environment & Secrets Management

### .env File Rules

```bash
# NEVER commit .env to git (.env is in .gitignore)
# Use .env.example as a template with placeholder values

# Production secrets must use a secrets manager (HashiCorp Vault, AWS Secrets Manager, etc.)
# Example: injecting via Docker secrets:
echo "postgresql://fleet:${POSTGRES_PASSWORD}@postgres:5432/fleet" | docker secret create db_url -
```

### Docker Secrets

For Docker Swarm/Compose production:
```yaml
secrets:
  db_url:
    external: true
  mqtt_password:
    external: true

services:
  backend:
    secrets:
      - db_url
      - mqtt_password
```

---

## Network Security

### Port Exposure

| Service | Port | Dev | Prod |
|---------|------|-----|------|
| Backend (HTTP) | 8000 | ✅ localhost | 🔒 Internal LB only |
| Mosquitto (MQTT, demo) | 1883 | ✅ localhost | ❌ not published |
| Mosquitto (MQTT TLS, prod) | 8883 | n/a | 🔒 mTLS + CRL + ACL |
| Postgres | 5432 | ❌ Not exposed | 🔒 Private subnet |
| Prometheus | 9090 | ✅ localhost | 🔒 Monitoring subnet |
| Grafana | 3000 | ✅ localhost | 🔒 Reverse proxy + auth |

### Docker Network

```yaml
# All services communicate over a dedicated bridge network
networks:
  fleet-net:
    driver: bridge
    internal: false  # Set to true for prod if no external access needed

# Backend and DB should be on an internal network
services:
  backend:
    networks:
      - fleet-net
  postgres:
    networks:
      - fleet-net
    # No ports exposed to host
```

---

## Secure Development Lifecycle

### Pre-Commit

- [ ] No secrets committed (`git secrets` or `talisman` pre-commit hook)
- [ ] SQLAlchemy ORM used (no raw SQL)
- [ ] Input validation via Pydantic schemas
- [ ] All dependencies pinned and reviewed
- [ ] Docker images use slim base with `apt-get upgrade -y` at build time

### CI/CD Pipeline Checks

```yaml
# CI should run:
- safety check -r requirements.txt     # Known vulnerabilities
- bandit -r app/                        # Static security analysis
- pytest tests/                         # E2E tests pass
- docker scan fleet-management-backend  # Container image scan
```

**Dockerfile best practice:** Always include `apt-get update && apt-get upgrade -y` before `apt-get install` and clean up with `apt-get autoremove -y && rm -rf /var/lib/apt/lists/*` to apply base image security patches at build time.

### Dependency Auditing

```bash
# Check for known vulnerabilities in Python dependencies
pip install safety
safety check -r requirements.txt

# Check Docker base images for CVEs
docker scout quick fleet-management-backend
```

**Latest audit (May 2026):** 11 packages upgraded across 16 requirements to patch 10+ CVEs. All 3 Dockerfiles hardened with `apt-get upgrade -y` at build time. See [`AGENTS.md`](AGENTS.md) for the full change log.

### Update Cadence

| Dependency | Review Cadence |
|-----------|---------------|
| Python packages (requirements.txt) | Monthly |
| Docker base images | Monthly |
| Docker Compose images (mosquitto, postgres, prometheus, grafana) | Quarterly |
| TLS certificates | Before expiry + weekly monitoring |

---

## Compliance Considerations

For deployments requiring compliance (SOC 2, ISO 27001, etc.):

| Requirement | How Fleet Commander Addresses It |
|-------------|----------------------------------|
| Access control | Built-in RBAC + API keys + JWT (UC-23), per-device mTLS ACLs (UC-24) |
| Audit logging | All OTA state transitions logged; Prometheus metrics provide change history |
| Secure update mechanism | SHA256 verification + automatic rollback on mismatch |
| Least privilege | Per-device MQTT ACLs (production config); separate DB users |
| Data integrity | Firmware hash stored and verified at every stage |
| Incident response | OTA failure → automatic rollback; /metrics alerts via Alertmanager |

---

## Security Checklist for Production Deployment

- [ ] MQTT TLS enabled with client certificates
- [ ] MQTT `allow_anonymous false` with per-device ACLs
- [ ] PostgreSQL with TLS and strong password
- [ ] API authentication (JWT or API keys)
- [ ] CORS restricted to dashboard domain
- [ ] Grafana admin password changed from default
- [ ] Prometheus not exposed to public network
- [ ] Grafana behind reverse proxy with auth
- [ ] All `.env` secrets managed via secrets vault
- [ ] Docker images scanned for CVEs
- [ ] HTTPS enabled for backend (reverse proxy with cert)
- [ ] Firmware served over HTTPS (CDN with signed URLs)
- [ ] Rate limiting on `/devices/register` and `/ota/trigger`
- [ ] Logging and alerting configured for auth failures
- [ ] Regular dependency audit scheduled
