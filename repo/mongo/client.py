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


def get_client(uri: str):
    """The process-wide client for this URI, created on first use."""
    global _client, _uri
    with _lock:
        if _client is None or _uri != uri:
            from pymongo import MongoClient
            if _client is not None:
                _client.close()
            _client = MongoClient(
                uri,
                # Not the 30s default. A kiosk with a client standing at the
                # desk needs to be told quickly that the database is
                # unreachable, not to hang for half a minute first.
                serverSelectionTimeoutMS=5000,
                connectTimeoutMS=5000,
                retryWrites=True,
            )
            _uri = uri
        return _client


def reset():
    """Drop the cached client. For tests that switch URIs."""
    global _client, _uri
    with _lock:
        if _client is not None:
            _client.close()
        _client, _uri = None, None
