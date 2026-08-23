"""Fleet Commander — Internal PKI service (P0 UC-25).

Minimal internal CA using the already-pinned `cryptography` package:

  - CA keypair lives under `settings.internal_ca_dir` (ca.crt / ca.key),
    generated on first use if absent.
  - `issue_device_cert()` mints a short-lived leaf cert with CN = device_id.
    The private key is returned ONCE to the caller and never persisted here.
  - `revoke()` marks the row revoked and regenerates a PEM CRL at
    `<ca_dir>/ca.crl`, which mosquitto loads via `crlfile`. The broker picks
    up the refreshed CRL on restart (`scripts/reload-broker.sh`) — see
    SECURITY.md § Certificate lifecycle.
"""

from __future__ import annotations

import datetime
import hashlib
import logging
import os

from typing import Optional, Tuple

from app.config import settings, DEFAULT_ORG_ID
from app.utils import utcnow

logger = logging.getLogger(__name__)

try:
    from cryptography import x509
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ed25519
    from cryptography.x509.oid import NameOID
    _CRYPTO_AVAILABLE = True
except ImportError:  # pragma: no cover
    _CRYPTO_AVAILABLE = False
    logger.info("cryptography not installed — internal PKI disabled")


def crypto_available() -> bool:
    return _CRYPTO_AVAILABLE


def _ca_dir() -> str:
    d = settings.internal_ca_dir
    os.makedirs(d, exist_ok=True)
    return d


_CA_CERT_FILE = "ca.crt"
_CA_KEY_FILE = "ca.key"
_CRL_FILE = "ca.crl"


def ca_cert_path() -> str:
    return os.path.join(_ca_dir(), _CA_CERT_FILE)


def ca_key_path() -> str:
    return os.path.join(_ca_dir(), _CA_KEY_FILE)


def crl_path() -> str:
    return os.path.join(_ca_dir(), _CRL_FILE)


# ── CA material ───────────────────────────────────────────────────────────

def _load_or_create_ca() -> Tuple["x509.Certificate", "ed25519.Ed25519PrivateKey"]:
    cert_file, key_file = ca_cert_path(), ca_key_path()
    if os.path.exists(cert_file) and os.path.exists(key_file):
        with open(key_file, "rb") as f:
            key = serialization.load_pem_private_key(f.read(), password=None)
        with open(cert_file, "rb") as f:
            cert = x509.load_pem_x509_certificate(f.read())
        return cert, key

    key = ed25519.Ed25519PrivateKey.generate()
    name = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, u"Fleet Commander Internal CA"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, u"Fleet Commander"),
    ])
    now = utcnow().replace(tzinfo=datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=3650))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, None)
    )
    with open(key_file, "wb") as f:
        f.write(key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ))
    with open(cert_file, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))
    logger.info("Generated internal CA at %s", cert_file)
    return cert, key


def get_ca_cert_pem() -> str:
    cert, _ = _load_or_create_ca()
    return cert.public_bytes(serialization.Encoding.PEM).decode()


# ── Issuance ──────────────────────────────────────────────────────────────

def issue_device_cert(
    device_id: str,
    org_id: str = DEFAULT_ORG_ID,
    ttl_days: Optional[int] = None,
) -> dict:
    """Mint a leaf certificate with CN = device_id.

    Returns {cert_pem, key_pem, fingerprint_sha256, serial, expires_at}.
    key_pem is for one-time delivery to the operator/device — never stored.
    """
    if not _CRYPTO_AVAILABLE:
        raise RuntimeError("cryptography package required for device certificate issuance")

    ca_cert, ca_key = _load_or_create_ca()
    ttl_days = ttl_days or settings.device_cert_ttl_days

    key = ed25519.Ed25519PrivateKey.generate()
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, device_id)])
    now = utcnow().replace(tzinfo=datetime.timezone.utc)

    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(ca_cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=ttl_days))
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName(device_id)]),
            critical=False,
        )
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .sign(ca_key, None)
    )

    cert_pem = cert.public_bytes(serialization.Encoding.PEM).decode()
    key_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    fingerprint = hashlib.sha256(
        cert.public_bytes(serialization.Encoding.DER)
    ).hexdigest()

    return {
        "cert_pem": cert_pem,
        "key_pem": key_pem,
        "fingerprint_sha256": fingerprint,
        "serial": str(cert.serial_number),
        "expires_at": (now + datetime.timedelta(days=ttl_days)).replace(tzinfo=None),
    }


# ── Revocation / CRL ──────────────────────────────────────────────────────

def build_and_write_crl(revoked_serials: list[str]) -> str:
    """Regenerate <ca_dir>/ca.crl covering every revoked serial. Returns path."""
    if not _CRYPTO_AVAILABLE:
        raise RuntimeError("cryptography package required for CRL generation")

    ca_cert, ca_key = _load_or_create_ca()
    now = utcnow().replace(tzinfo=datetime.timezone.utc)

    builder = (
        x509.CertificateRevocationListBuilder()
        .issuer_name(ca_cert.subject)
        .last_update(now - datetime.timedelta(minutes=5))
        .next_update(now + datetime.timedelta(days=30))
    )
    for serial_str in revoked_serials:
        try:
            serial_int = int(serial_str)
        except (TypeError, ValueError):
            continue
        revoked = (
            x509.RevokedCertificateBuilder()
            .serial_number(serial_int)
            .revocation_date(now)
            .build()
        )
        # cryptography ≥49 renamed add_revoked → add_revoked_certificate.
        if hasattr(builder, "add_revoked_certificate"):
            builder = builder.add_revoked_certificate(revoked)
        else:
            builder = builder.add_revoked(revoked)

    crl = builder.sign(private_key=ca_key, algorithm=None)
    path = crl_path()
    with open(path, "wb") as f:
        f.write(crl.public_bytes(serialization.Encoding.PEM))
    logger.info("Wrote CRL with %d revoked serial(s) to %s", len(revoked_serials), path)
    return path


async def refresh_crl_from_db(db) -> str:
    """Rebuild the CRL from all revoked DeviceCertificate rows."""
    from sqlalchemy import select
    from app.models import DeviceCertificate

    result = await db.execute(
        select(DeviceCertificate.serial).where(DeviceCertificate.status == "revoked")
    )
    return build_and_write_crl([s for (s,) in result.all()])


def write_initial_crl() -> str:
    """Empty CRL so mosquitto's `crlfile` exists on first production boot."""
    return build_and_write_crl([])
