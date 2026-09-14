"""
The certificate bundle a packaged build talks to Atlas with.

This is the one failure that took the reception Mac down at startup, and it
cannot be caught by any test that needs a server: the binary never got far
enough to be refused a connection, it could not verify the certificate at
all. What is testable without a server is the thing that was actually
missing — that a CA file is chosen, that it exists, and that an explicit one
in the URI still wins.
"""

import os

import pytest

pytestmark = pytest.mark.sqlite_only   # about packaging, not about a backend

from repo.mongo.client import ca_file  # noqa: E402


def test_a_bundle_is_chosen_and_is_a_real_file():
    """
    pymongo depends on dnspython alone, not certifi, and a frozen build has no
    usable system store: PyInstaller carries its own Python and macOS keeps
    its roots in the Keychain, not at the OpenSSL paths ssl falls back to. So
    without this the Mac binary dies with

        [SSL: CERTIFICATE_VERIFY_FAILED] unable to get local issuer certificate
    """
    path = ca_file("mongodb+srv://u:p@cluster0.example.mongodb.net/")
    assert path, "no CA bundle chosen — the Atlas handshake has nothing to trust"
    assert os.path.exists(path), path


def test_the_bundle_actually_loads_roots():
    """A path that exists is not the same as a usable trust store."""
    import ssl

    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.load_verify_locations(cafile=ca_file("mongodb://h:27017/?tls=true"))
    assert ctx.cert_store_stats()["x509_ca"] > 100


@pytest.mark.parametrize("uri", [
    "mongodb://h:27017/?tls=true&tlsCAFile=/etc/own.pem",
    "mongodb://h:27017/?ssl_ca_certs=/etc/own.pem",
])
def test_a_uri_naming_its_own_bundle_wins(uri):
    """
    A keyword argument overrides a URI option in pymongo, so passing ours
    would silently discard the one someone deliberately configured.
    """
    assert ca_file(uri) is None


def test_certifi_is_declared_where_the_build_reads_it():
    """
    Two files decide what ships. requirements.txt is what CI and the build
    scripts install; academy.spec's hiddenimports is what makes PyInstaller's
    certifi hook run and collect cacert.pem into the bundle. Missing from
    either one and the binary is back to having no roots.
    """
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    assert "certifi" in open(os.path.join(root, "requirements.txt")).read()
    assert '"certifi"' in open(os.path.join(root, "academy.spec")).read()
