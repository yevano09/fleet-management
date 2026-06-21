"""
Fleet Commander — Firmware Cryptographic Signing (Feature 8)

Ed25519 sign/verify for firmware binaries. The signing key is loaded from
config (PEM format). If no key is configured, signing is skipped and
verification accepts unsigned firmware (unless firmware_require_signature=True).
"""

from __future__ import annotations

import logging
from typing import Optional, Tuple

from app.config import settings

logger = logging.getLogger(__name__)

try:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
    from cryptography.hazmat.primitives.serialization import (
        load_pem_private_key, load_pem_public_key, Encoding, PrivateFormat, PublicFormat, NoEncryption,
    )
    _CRYPTO_AVAILABLE = True
except ImportError:
    _CRYPTO_AVAILABLE = False
    logger.info("cryptography package not installed — firmware signing disabled")


def _load_private_key() -> Optional[Ed25519PrivateKey]:
    if not _CRYPTO_AVAILABLE or not settings.firmware_signing_private_key:
        return None
    try:
        key = load_pem_private_key(
            settings.firmware_signing_private_key.encode(), password=None
        )
        if isinstance(key, Ed25519PrivateKey):
            return key
        logger.warning("Configured signing key is not Ed25519 — signing disabled")
        return None
    except Exception:
        logger.exception("Failed to load firmware signing private key")
        return None


def _load_public_key() -> Optional[Ed25519PublicKey]:
    if not _CRYPTO_AVAILABLE or not settings.firmware_signing_public_key:
        return None
    try:
        key = load_pem_public_key(settings.firmware_signing_public_key.encode())
        if isinstance(key, Ed25519PublicKey):
            return key
        return None
    except Exception:
        logger.exception("Failed to load firmware signing public key")
        return None


def sign_firmware(content: bytes) -> Tuple[Optional[str], Optional[str]]:
    """Sign firmware bytes with the configured Ed25519 private key.

    Returns (signature_hex, key_id) or (None, None) if signing unavailable.
    The key_id is a short hash of the public key for identification.
    """
    key = _load_private_key()
    if key is None:
        return None, None
    signature = key.sign(content)
    sig_hex = signature.hex()
    pub = key.public_key()
    pub_bytes = pub.public_bytes(Encoding.Raw, PublicFormat.Raw)
    key_id = pub_bytes[:8].hex()
    logger.info("Firmware signed with key_id=%s", key_id)
    return sig_hex, key_id


def verify_firmware(content: bytes, signature_hex: str, key_id: Optional[str] = None) -> bool:
    """Verify a firmware signature.

    Returns True if the signature is valid. If no public key is configured,
    returns True (verification skipped). If firmware_require_signature is True
    and no signature is provided, returns False.
    """
    if not signature_hex:
        if settings.firmware_require_signature:
            return False
        return True

    if not _CRYPTO_AVAILABLE:
        logger.warning("cryptography not installed — cannot verify signature")
        return not settings.firmware_require_signature

    pub = _load_public_key()
    if pub is None:
        logger.warning("No public key configured — signature verification skipped")
        return not settings.firmware_require_signature

    try:
        pub.verify(bytes.fromhex(signature_hex), content)
        return True
    except Exception:
        logger.warning("Firmware signature verification FAILED")
        return False


def generate_keypair() -> Tuple[str, str]:
    """Generate a new Ed25519 keypair and return (private_pem, public_pem)."""
    if not _CRYPTO_AVAILABLE:
        raise RuntimeError("cryptography package not installed")
    priv = Ed25519PrivateKey.generate()
    pub = priv.public_key()
    priv_pem = priv.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()).decode()
    pub_pem = pub.public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo).decode()
    return priv_pem, pub_pem
