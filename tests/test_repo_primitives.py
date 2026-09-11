"""
The twelve primitives and the filter dialect.

This is the contract both backends have to satisfy identically, so it is
pinned harder than the code that uses it. Everything here should pass
unchanged when the parametrisation grows a "mongo" entry — if a test needs a
backend-specific tweak, the abstraction has leaked.
"""

import pytest

import db
import repo as data
from repo.filters import FilterError


@pytest.fixture
def r(conn):
    """A repository over the same throwaway database the other tests use."""
    return data.connect()


@pytest.fixture
def people(r):
    """Six clients with a spread of values, including nulls."""
    ids = {}
    for name, phone, age, school in [
            ("Ana", "0100", 12.5, "Manor"),
            ("Bea", "0101", 9.0, "Manor"),
            ("Cat", "0102", 4.8, None),
            ("Dot", None, None, "Riverside"),
            ("Eve", "0104", 15.0, "Riverside"),
            ("Fay", "0105", 7.0, None)]:
        ids[name] = r.insert("clients", {
            "name_en": name, "phone": phone, "age": age, "school": school,
            "created_at": db.now(), "active": 1})
    return ids


# ---------------------------------------------------------------- reads

def test_get_returns_a_plain_dict(r, people):
    row = r.get("clients", people["Ana"])
    assert isinstance(row, dict), "never a sqlite3.Row or a BSON document"
    assert row["name_en"] == "Ana"
    assert row["id"] == people["Ana"]


def test_get_returns_none_for_a_missing_id(r):
    assert r.get("clients", 9999) is None


def test_find_with_no_filter_returns_everything(r, people):
    assert len(r.find("clients")) == 6


def test_equality_is_the_bare_form(r, people):
    rows = r.find("clients", {"school": "Manor"})
    assert {x["name_en"] for x in rows} == {"Ana", "Bea"}


def test_none_means_is_null_not_equals_null(r, people):
    """`= NULL` is never true in SQL, and matching missing-vs-null is the
    single likeliest place the two backends drift."""
    rows = r.find("clients", {"school": None})
    assert {x["name_en"] for x in rows} == {"Cat", "Fay"}


def test_ne_none_means_is_not_null(r, people):
    rows = r.find("clients", {"school": {"ne": None}})
    assert {x["name_en"] for x in rows} == {"Ana", "Bea", "Dot", "Eve"}


def test_comparison_operators(r, people):
    assert {x["name_en"] for x in r.find("clients", {"age": {"gte": 12.5}})} \
        == {"Ana", "Eve"}
    assert {x["name_en"] for x in r.find("clients", {"age": {"lt": 9.0}})} \
        == {"Cat", "Fay"}


def test_two_operators_on_one_field_are_anded(r, people):
    rows = r.find("clients", {"age": {"gte": 7.0, "lt": 12.5}})
    assert {x["name_en"] for x in rows} == {"Bea", "Fay"}


def test_several_fields_are_anded(r, people):
    rows = r.find("clients", {"school": "Riverside", "age": {"gt": 14}})
    assert [x["name_en"] for x in rows] == ["Eve"]


def test_in_and_not_in(r, people):
    got = r.find("clients", {"id": {"in": [people["Ana"], people["Cat"]]}})
    assert {x["name_en"] for x in got} == {"Ana", "Cat"}
    rest = r.find("clients", {"id": {"nin": [people["Ana"], people["Cat"]]}})
    assert len(rest) == 4


def test_an_empty_in_matches_nothing_and_an_empty_nin_matches_everything(r, people):
    """
    `IN ()` is a syntax error and `NOT IN (NULL)` is NULL rather than true,
    so both are answered by the compiler instead of being emitted.
    """
    assert r.find("clients", {"id": {"in": []}}) == []
    assert len(r.find("clients", {"id": {"nin": []}})) == 6


def test_like(r, people):
    assert [x["name_en"] for x in r.find("clients", {"name_en": {"like": "%a%"}})] \
        == ["Ana", "Bea", "Cat", "Fay"]


def test_or_at_the_top_level(r, people):
    rows = r.find("clients", {"$or": [{"name_en": "Ana"}, {"age": {"gt": 14}}]})
    assert {x["name_en"] for x in rows} == {"Ana", "Eve"}


def test_sort_ascending_and_descending(r, people):
    asc = [x["name_en"] for x in r.find("clients", sort=[("name_en", 1)])]
    assert asc == ["Ana", "Bea", "Cat", "Dot", "Eve", "Fay"]
    desc = [x["name_en"] for x in r.find("clients", sort=[("name_en", -1)])]
    assert desc == list(reversed(asc))


def test_a_sort_always_ends_with_the_primary_key(r):
    """
    Ties would otherwise come back in rowid order on SQLite and natural
    order on Mongo — two backends legitimately disagreeing, which would make
    the parity tests compare lists that were never promised to match.
    """
    for name in ("Same", "Same", "Same"):
        r.insert("clients", {"name_en": name, "created_at": db.now(), "active": 1})
    ids = [x["id"] for x in r.find("clients", sort=[("name_en", 1)])]
    assert ids == sorted(ids)


def test_limit(r, people):
    assert len(r.find("clients", sort=[("name_en", 1)], limit=2)) == 2


def test_fields_projects(r, people):
    row = r.find("clients", {"name_en": "Ana"}, fields=["id", "name_en"])[0]
    assert set(row) == {"id", "name_en"}


def test_find_one_returns_the_first_by_sort(r, people):
    assert r.find_one("clients", {}, sort=[("age", -1)])["name_en"] == "Eve"


def test_find_one_returns_none_when_nothing_matches(r):
    assert r.find_one("clients", {"name_en": "Nobody"}) is None


def test_count_and_exists(r, people):
    assert r.count("clients") == 6
    assert r.count("clients", {"school": "Manor"}) == 2
    assert r.exists("clients", {"name_en": "Ana"}) is True
    assert r.exists("clients", {"name_en": "Nobody"}) is False


def test_distinct(r, people):
    assert sorted(x for x in r.distinct("clients", "school") if x) \
        == ["Manor", "Riverside"]


# ---------------------------------------------------------------- writes

def test_insert_returns_the_new_id(r):
    new = r.insert("clients", {"name_en": "New", "created_at": db.now(), "active": 1})
    assert isinstance(new, int)
    assert r.get("clients", new)["name_en"] == "New"


def test_insert_many_returns_ids_in_order(r):
    ids = r.insert_many("clients", [
        {"name_en": n, "created_at": db.now(), "active": 1}
        for n in ("One", "Two", "Three")])
    assert len(ids) == 3
    assert [r.get("clients", i)["name_en"] for i in ids] == ["One", "Two", "Three"]


def test_ids_are_never_reused_after_a_delete(r):
    """
    AUTOINCREMENT on SQLite and a counters collection on Mongo agree only
    because neither reuses. A recycled client id means a printed card in a
    drawer belongs to a different person.
    """
    first = r.insert("clients", {"name_en": "Gone", "created_at": db.now(), "active": 1})
    r.delete("clients", first)
    second = r.insert("clients", {"name_en": "Next", "created_at": db.now(), "active": 1})
    assert second > first


def test_update_sets_fields_and_reports_whether_it_existed(r, people):
    assert r.update("clients", people["Ana"], {"school": "Moved"}) is True
    assert r.get("clients", people["Ana"])["school"] == "Moved"
    assert r.update("clients", 9999, {"school": "x"}) is False


def test_update_where_returns_how_many_changed(r, people):
    assert r.update_where("clients", {"school": "Manor"}, {"active": 0}) == 2
    assert r.count("clients", {"active": 0}) == 2


def test_delete_and_delete_where(r, people):
    assert r.delete("clients", people["Ana"]) is True
    assert r.delete("clients", people["Ana"]) is False
    assert r.delete_where("clients", {"school": "Riverside"}) == 2
    assert r.count("clients") == 3


def test_insert_ignore_returns_none_on_a_duplicate(r, people):
    """
    seed.py's INSERT OR IGNORE. It is only idempotent because a unique index
    exists — bookings(client_id, session_id) here.
    """
    cls = r.insert("classes", {"name": "C", "colour": "#87438E", "duration_hours": 1.0})
    sess = r.insert("sessions", {"class_id": cls, "starts_at": 1, "duration_hours": 1.0,
                                 "ends_at": 3601})
    doc = {"client_id": people["Ana"], "session_id": sess,
           "status": "booked", "created_at": db.now()}
    assert r.insert_ignore("bookings", doc) is not None
    assert r.insert_ignore("bookings", doc) is None
    assert r.count("bookings") == 1


def test_a_unique_clash_on_plain_insert_raises_duplicatekey(r, people):
    """Never sqlite3.IntegrityError in one deployment and pymongo's in another."""
    cls = r.insert("classes", {"name": "C", "colour": "#87438E", "duration_hours": 1.0})
    sess = r.insert("sessions", {"class_id": cls, "starts_at": 1, "duration_hours": 1.0,
                                 "ends_at": 3601})
    doc = {"client_id": people["Ana"], "session_id": sess,
           "status": "booked", "created_at": db.now()}
    r.insert("bookings", doc)
    with pytest.raises(data.DuplicateKey):
        r.insert("bookings", doc)


# ---------------------------------------------------------------- refusals

def test_an_unknown_operator_is_refused(r):
    with pytest.raises(FilterError, match="regex"):
        r.find("clients", {"name_en": {"regex": "^A"}})


def test_an_unknown_top_level_operator_is_refused(r):
    with pytest.raises(FilterError):
        r.find("clients", {"$where": "something"})


def test_a_field_name_that_is_not_a_column_is_refused(r):
    """Values bind as parameters; a column name cannot, so it is whitelisted."""
    with pytest.raises(FilterError):
        r.find("clients", {"name_en; DROP TABLE clients": 1})


# ---------------------------------------------------------------- transactions

def test_begin_commits_and_rolls_back(r):
    with r.begin():
        r.insert("clients", {"name_en": "Kept", "created_at": db.now(), "active": 1})
    assert r.count("clients") == 1

    with pytest.raises(ValueError):
        with r.begin():
            r.insert("clients", {"name_en": "Lost", "created_at": db.now(), "active": 1})
            raise ValueError
    assert r.count("clients") == 1


def test_begin_is_re_entrant(r):
    with r.begin():
        with r.begin():
            r.insert("clients", {"name_en": "Nested", "created_at": db.now(), "active": 1})
    assert r.count("clients") == 1


def test_the_repo_is_a_context_manager(conn):
    with data.connect() as r:
        assert r.count("clients") == 0
