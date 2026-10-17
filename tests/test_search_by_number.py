"""
Finding a client by the number reception is holding.

The search matches the stored text, and the stored text is whatever was
written down. The academy's own sheets hold one student three ways —
`1129200365` (Excel ate the leading zero), `01129200365` and
`+201129200365` — so typing the number off a card found one of the three
and missed the same person entered either other way.

`identity.phone_key()` is the app's own answer to exactly that, and this is
it applied to the one place it was not: `identity.search_needle()` is the
second needle, ORed beside the plain match so a name goes on working.

It is in `search_clients()` rather than in any one screen, because the
Clients page, the kiosk's name lookup and the shop's sell modal all go
through it — a second search with its own idea of a match would eventually
disagree with the first.
"""

import pytest
from fastapi.testclient import TestClient

import identity


@pytest.fixture
def client(academy):
    import server
    with TestClient(server.app) as c:
        c.academy = academy
        yield c


def _add(repo, name, phone):
    import db
    return repo.insert("clients", {"name_en": name, "phone": phone,
                                   "created_at": db.now(), "active": 1})


def _names(client, q):
    return sorted(r["name_en"] for r in client.get(f"/api/clients?q={q}").json())


# ---------------------------------------------------- the needle itself

def test_the_needle_is_the_last_ten_digits_of_a_real_number():
    assert identity.search_needle("01129200365") == "1129200365"
    assert identity.search_needle("+20 112 920 0365") == "1129200365"


def test_it_is_nothing_for_a_query_that_is_not_a_number():
    """A name, and a prefix somebody is still typing — the plain match
    already covers both, so an ordinary search costs nothing extra."""
    assert identity.search_needle("Dana") is None
    assert identity.search_needle("0112") is None
    assert identity.search_needle("") is None
    assert identity.search_needle(None) is None


def test_it_is_nothing_when_it_would_repeat_the_plain_match(): 
    assert identity.search_needle("1129200365") is None


# ---------------------------------------------------- through the search

def test_one_number_typed_finds_the_same_person_stored_three_ways(client):
    repo = client.academy.repo
    _add(repo, "Excel Ate It", "1129200365")
    _add(repo, "As Dialled", "01129200365")
    _add(repo, "With Country Code", "+201129200365")

    assert _names(client, "01129200365") == [
        "As Dialled", "Excel Ate It", "With Country Code"]
    assert _names(client, "%2B201129200365") == [
        "As Dialled", "Excel Ate It", "With Country Code"]


def test_a_name_search_is_untouched(client):
    repo = client.academy.repo
    _add(repo, "Zuzu Hany", "01129200365")

    assert _names(client, "Zuzu") == ["Zuzu Hany"]


def test_a_part_typed_number_still_matches_as_written(client):
    """Fewer than MIN_DIGITS is a prefix, and the plain match is what
    answers it — this must not start matching everybody."""
    repo = client.academy.repo
    _add(repo, "Zuzu Hany", "01129200365")

    assert _names(client, "0112") == ["Zuzu Hany"]
    assert "Zuzu Hany" not in _names(client, "0999")


def test_the_kiosk_lookup_and_the_shop_use_the_same_search(client):
    """All three screens go through /api/clients?q=, which is the whole
    reason this went into search_clients() and not into one of them."""
    repo = client.academy.repo
    _add(repo, "Kinda Ahmed", "+201280227955")

    assert _names(client, "01280227955") == ["Kinda Ahmed"]
