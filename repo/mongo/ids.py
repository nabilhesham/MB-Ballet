"""
Integer ids, from a counters collection.

Not a preference. `tokens.py` packs `client_id` as a **uint32** into every
card already in a client's hands; the client id *is* the printed member
number reception types in when the scanner is down; and it is in filenames on
disk. An ObjectId fits none of those.

`find_one_and_update` with `$inc` is atomic at the document level, needs no
transaction, and never reuses a number — which is what makes it agree with
SQLite's AUTOINCREMENT. **Both only agree because neither reuses**: drop
AUTOINCREMENT and SQLite starts handing out max(id)+1, so after a hard delete
the next client gets a recycled number and a printed card in a drawer belongs
to somebody else.
"""

COUNTERS = "_counters"


def next_id(database, coll: str, session=None, count: int = 1) -> int:
    """
    Reserve `count` ids and return the first.

    A block in one round trip, because against Atlas an id per insert doubles
    the cost of every write — and add_plan books twelve at a time, a repeated
    term far more, and seed.py thousands.
    """
    doc = database[COUNTERS].find_one_and_update(
        {"_id": coll},
        {"$inc": {"seq": count}},
        upsert=True,
        return_document=True,          # ReturnDocument.AFTER
        session=session,
    )
    return doc["seq"] - count + 1


def sync_counters(database, collections) -> None:
    """
    Set each counter to the highest id present.

    Run at init and after any import. Without it, a database populated by the
    migration script would hand out ids that already exist.
    """
    for coll in collections:
        top = database[coll].find_one(sort=[("_id", -1)], projection={"_id": 1})
        if top is None:
            continue
        database[COUNTERS].update_one(
            {"_id": coll},
            {"$max": {"seq": top["_id"]}},
            upsert=True,
        )
