"""P0 UC-25 — internal PKI unit tests (issue / fingerprint / revoke / CRL)."""

import asyncio

import pytest

from app.config import settings


@pytest.fixture()
def tmp_ca(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "internal_ca_dir", str(tmp_path / "ca"))
    return tmp_path / "ca"


class TestIssuance:
    def test_issue_returns_keypair_and_fingerprint(self, tmp_ca):
        from app.pki import issue_device_cert
        from cryptography import x509

        out = issue_device_cert("sim-001")
        assert len(out["fingerprint_sha256"]) == 64
        assert out["key_pem"].startswith("-----BEGIN PRIVATE KEY-----")

        cert = x509.load_pem_x509_certificate(out["cert_pem"].encode())
        cn = cert.subject.get_attributes_for_oid(x509.NameOID.COMMON_NAME)[0].value
        assert cn == "sim-001"

    def test_two_issues_have_distinct_serials(self, tmp_ca):
        from app.pki import issue_device_cert

        a = issue_device_cert("dev-a")
        b = issue_device_cert("dev-b")
        assert a["serial"] != b["serial"]
        assert a["fingerprint_sha256"] != b["fingerprint_sha256"]

    def test_ca_material_created_once(self, tmp_ca):
        from app.pki import issue_device_cert, ca_cert_path

        issue_device_cert("d1")
        first = open(ca_cert_path(), "rb").read()
        issue_device_cert("d2")
        second = open(ca_cert_path(), "rb").read()
        assert first == second  # same CA signs everything


class TestRevocation:
    def test_initial_crl_is_empty_but_loadable(self, tmp_ca):
        from app.pki import write_initial_crl, crl_path
        from cryptography import x509

        path = write_initial_crl()
        assert path == str(crl_path())
        crl = x509.load_pem_x509_crl(open(path, "rb").read())
        assert len(list(crl)) == 0

    def test_crl_contains_revoked_serial(self, tmp_ca):
        from app.pki import build_and_write_crl
        from cryptography import x509

        out = issue = None
        from app.pki import issue_device_cert
        issued = issue_device_cert("revoke-me")
        path = build_and_write_crl([issued["serial"]])

        crl = x509.load_pem_x509_crl(open(path, "rb").read())
        serials = {r.serial_number for r in crl}
        assert int(issued["serial"]) in serials

    def test_refresh_crl_from_db(self, tmp_ca, monkeypatch, tmp_path):
        """End-to-end: DB row marked revoked → CRL regenerated with its serial."""
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine, AsyncSession
        from sqlalchemy import select
        from app.database import Base
        import app.pki as pki
        from app.models import DeviceCertificate

        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/pki.db")
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        async def scenario():
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            issued = pki.issue_device_cert("db-dev")
            async with factory() as db:
                db.add(DeviceCertificate(
                    id="c1",
                    device_id="db-dev",
                    org_id="org-default",
                    fingerprint_sha256=issued["fingerprint_sha256"],
                    pem=issued["cert_pem"],
                    serial=issued["serial"],
                    status="active",
                ))
                await db.commit()

                # Simulate revocation via the API's code path.
                row = (await db.execute(
                    select(DeviceCertificate).where(
                        DeviceCertificate.fingerprint_sha256 == issued["fingerprint_sha256"])
                )).scalar_one()
                row.status = "revoked"
                await db.commit()

                await pki.refresh_crl_from_db(db)

            from cryptography import x509
            crl = x509.load_pem_x509_crl(open(pki.crl_path(), "rb").read())
            serials = {r.serial_number for r in crl}
            assert int(issued["serial"]) in serials

        asyncio.run(scenario())
