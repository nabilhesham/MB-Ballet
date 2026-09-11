"""
The MongoDB implementation. Built in a later phase.

It is a separate module imported only on demand so that a SQLite-only
install — which is what the reception laptop runs — never needs pymongo
present at all.
"""


class MongoRepo:
    def __init__(self, *args, **kwargs):
        raise NotImplementedError(
            "the MongoDB backend is not built yet; set MB_DB_BACKEND=sqlite")
