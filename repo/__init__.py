"""
Which implementation the app talks to.

The one public entry point. `config.backend()` decides, lazily — never at
import time, since .env is read after the first import of anything (see
config.py).

    repo = data.connect()
    try:
        ...
    finally:
        repo.close()

The Mongo implementation is imported only when it is asked for. A SQLite-only
install has no pymongo, and the reception laptop must not fail to start
because a wheel did not download.
"""

import config

from .base import Repo
from .errors import DuplicateKey, RepoError, Unavailable
from .filters import FilterError

__all__ = ["connect", "Repo", "RepoError", "DuplicateKey", "Unavailable",
           "FilterError"]


def connect(path: str = None) -> Repo:
    backend = config.backend()
    if backend == config.SQLITE:
        import db
        from .sqlite import SqliteRepo
        return SqliteRepo(db.connect(path))

    from .mongo import MongoRepo          # noqa: F401  -- built in phase 4
    return MongoRepo(config.mongo_uri(), config.mongo_db())
