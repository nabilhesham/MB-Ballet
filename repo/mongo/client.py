"""
The MongoClient, and the transaction boundary.

**One client per process, not one per request.** A MongoClient *is* the
connection pool — it carries its own topology monitoring and its own TLS
sessions — so constructing one per request against Atlas means a handshake
and a round of server discovery before every query. That is the usual way a
Mongo application ends up dramatically slower than the database it is
talking to.
"""

import threading

_lock = threading.Lock()
_client = None
_uri = None


def ca_file(uri: str):
    """
    The CA bundle to verify Atlas's certificate against, or None.

    **A packaged build has no usable system CA store**, and this is what that
    looks like on the reception Mac:

        [SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed:
        unable to get local issuer certificate (_ssl.c:1006)
        ERROR: Application startup failed. Exiting.

    PyInstaller bundles its own Python, and macOS keeps its root certificates
    in the Keychain rather than at the OpenSSL paths `ssl` falls back to --
    which is also why a normal macOS Python install ships an "Install
    Certificates.command". There is no such step for a binary someone
    double-clicks, so the build that works on the machine it was made on
    fails on every other Mac, at startup, with an error naming nothing the
    receptionist can act on.

    certifi ships Mozilla's root store as an ordinary file and PyInstaller's
    hook bundles it, so it is the one CA path that is the same on every
    machine. pymongo does not depend on it (only dnspython), so it is
    declared in requirements.txt and in academy.spec's hiddenimports.

    Skipped when the URI already names a bundle, since a keyword argument
    would override the explicit choice someone made there.
    """
    if "tlsCAFile" in uri or "ssl_ca_certs" in uri:
        return None
    try:
        import certifi
    except ImportError:
        # Better to try the system store than to refuse to start: on Linux
        # and Windows it usually works, and this is only the fallback.
        return None
    return certifi.where()


def get_client(uri: str):
    """The process-wide client for this URI, created on first use."""
    global _client, _uri
    with _lock:
        if _client is None or _uri != uri:
            from pymongo import MongoClient
            if _client is not None:
                _client.close()
            options = {
                # Not the 30s default. A kiosk with a client standing at the
                # desk needs to be told quickly that the database is
                # unreachable, not to hang for half a minute first.
                "serverSelectionTimeoutMS": 5000,
                "connectTimeoutMS": 5000,
                "retryWrites": True,
            }
            ca = ca_file(uri)
            if ca:
                options["tlsCAFile"] = ca
            _client = MongoClient(uri, **options)
            _uri = uri
        return _client


def reset():
    """Drop the cached client. For tests that switch URIs."""
    global _client, _uri
    with _lock:
        if _client is not None:
            _client.close()
        _client, _uri = None, None
