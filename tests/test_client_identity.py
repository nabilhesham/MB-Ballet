"""
The mobile number is what identifies a client.

Two profiles for one person is not an untidiness problem. Their sessions,
their plans and their cards divide between the two records, so a card scans
against a balance that is half what they bought — and the half that is
missing is invisible, because the other profile looks perfectly healthy.

The seed has always merged the roster sheets on the number rather than on
the spelling of a name ("rodaina hesham" and "rodina hesham" are one
student). These tests are that same rule applied to a client typed in at
reception, which is where the duplicate actually gets created.
"""

from datetime import date

import pytest
from fastapi.testclient import TestClient

import access
import phones


# ---------------------------------------------------------------- the key
def test_one_number_written_three_ways_has_one_key():
    """
    Exactly the shapes the academy's own sheets hold: Excel ate the leading
    zero on one block, another block has it, and a third carries the country
    code. One person.
    """
    assert (phones.key("1129200365") == phones.key("01129200365")
            == phones.key("+201129200365") == "1129200365")


def test_punctuation_and_spacing_do_not_make_a_new_person():
    assert phones.key("0112 920 0365") == phones.key("011-2920-0365") == "1129200365"


def test_a_number_with_nothing_to_compare_has_no_key():
    for blank in (None, "", "   ", "n/a", "no phone"):
        assert phones.key(blank) is None


def test_a_short_number_is_compared_as_written():
    """
    Not a mobile, so there is no country code to strip. Comparing what is
    there can fail to match; it can never match the wrong person.
    """
    assert phones.key("12345") == "12345"
    assert phones.key("12345") != phones.key("0111112345")


# ---------------------------------------------------------------- the rule
def test_a_free_number_is_not_a_conflict(repo, academy):
    assert access.phone_conflict(repo, "01999888777") is None


def test_a_number_already_in_use_is_refused_by_name(repo, academy):
    msg = access.phone_conflict(repo, "01111111111")
    assert msg is not None
    assert "Dana Halim" in msg
    assert str(academy.dual) in msg


def test_the_same_number_written_differently_is_still_refused(repo, academy):
    """The whole point: a duplicate that does not look like one."""
    for written in ("1111111111", "+201111111111", "0111 111 1111"):
        assert access.phone_conflict(repo, written) is not None


def test_a_blank_number_is_never_a_conflict(repo, academy):
    """
    True of phone_conflict() alone, and exactly why phone_required() had to
    exist: with nothing to compare, two blanks are not duplicates of each
    other and never would be. The routes refuse a blank before they get
    here — see the route tests below.
    """
    for blank in (None, "", "   "):
        assert access.phone_conflict(repo, blank) is None


def test_an_archived_client_is_named_and_the_way_out_is_restore(repo, academy):
    """
    Saying only "already exists" would send reception looking for somebody
    they cannot find on the list, and the only way out of that is a second
    profile — the exact thing this refusal prevents.
    """
    msg = access.phone_conflict(repo, "01111111116")
    assert "Karim Nour" in msg
    assert "archived" in msg.lower()
    assert "restore" in msg.lower()


def test_a_client_is_not_a_duplicate_of_themselves(repo, academy):
    assert access.phone_conflict(
        repo, "01111111111", exclude_id=academy.dual) is None


def test_excluding_one_client_does_not_excuse_another(repo, academy):
    """Editing Farah's row to Dana's number is still Dana's number."""
    assert access.phone_conflict(
        repo, "01111111111", exclude_id=academy.solo_ballet) is not None


# ---------------------------------------------------------------- the routes
@pytest.fixture
def client(academy):
    import server
    with TestClient(server.app) as c:
        c.academy = academy
        yield c


def _new(name="Test Person", phone="01555000111"):
    """A number by default: a client without one is refused outright now."""
    return {"name_en": name, "phone": phone, "joined_on": date.today().isoformat()}


def test_creating_with_a_taken_number_is_refused(client):
    r = client.post("/api/clients", json=_new(phone="01111111111"))
    assert r.status_code == 409
    assert "Dana Halim" in r.json()["detail"]


def test_the_refusal_holds_however_the_number_is_typed(client):
    r = client.post("/api/clients", json=_new(phone="+20 111 111 1111"))
    assert r.status_code == 409


def test_a_free_number_still_creates(client):
    r = client.post("/api/clients", json=_new(phone="01999888777"))
    assert r.status_code == 200
    assert r.json()["id"]


def test_a_client_cannot_be_created_without_a_number(client):
    """
    The number is the identity, so there is no client without one. Two
    clients with no number are not duplicates of each other — nothing to
    compare — which is the hole requiring it closes.
    """
    for body in ({"name_en": "No Phone"},
                 {"name_en": "No Phone", "phone": ""},
                 {"name_en": "No Phone", "phone": "   "}):
        r = client.post("/api/clients", json=body)
        assert r.status_code == 400
        assert "required" in r.json()["detail"]


def test_a_placeholder_is_not_a_number(client):
    """
    A required field that accepts "n/a" is not required in any sense that
    matters: it carries no identity, and several clients could hold the same
    placeholder without any of them conflicting.
    """
    for junk in ("n/a", "-", "none", "0", "123", "0111"):
        r = client.post("/api/clients", json=_new(phone=junk))
        assert r.status_code == 400, junk
        assert "does not look like" in r.json()["detail"]


def test_a_real_number_of_any_shape_is_accepted(client):
    """
    The shape check has to be loose enough for a real number written any of
    the ways reception writes one, including a foreign one. A distinct
    number per shape, because the same number written two ways is one
    number — which is the next test.
    """
    for i, good in enumerate(("01012345671", "0101 234 5672", "+201012345673",
                              "+44 7700 900123", "1012345674")):
        r = client.post("/api/clients", json=_new(f"Person {i}", good))
        assert r.status_code == 200, good


def test_the_shape_check_does_not_let_a_duplicate_through(client):
    """
    The two rules run in order and both apply: a well-shaped number that
    somebody already holds is still refused, whichever way it is written.
    """
    assert client.post("/api/clients",
                       json=_new("First", "01012345675")).status_code == 200
    r = client.post("/api/clients", json=_new("Second", "+20 101 234 5675"))
    assert r.status_code == 409
    assert "First" in r.json()["detail"]


def test_a_number_cannot_be_cleared_by_an_edit(client):
    """
    Otherwise the requirement lasts exactly as long as it takes to press
    Edit, and the client is back to having no identity.
    """
    cid = client.academy.solo_ballet
    r = client.put(f"/api/clients/{cid}", json={"name_en": "Farah Adel", "phone": ""})
    assert r.status_code == 400
    assert client.get(f"/api/clients/{cid}").json()["phone"] == "01111111112"


def test_nothing_is_written_when_the_number_is_refused(client):
    before = len(client.get("/api/clients").json())
    client.post("/api/clients", json=_new(phone="01111111111"))
    assert len(client.get("/api/clients").json()) == before


def test_editing_a_number_onto_someone_else_is_refused(client):
    """
    Half a rule is no rule: refusing at creation and then allowing the
    number to be typed over somebody else's a minute later leaves exactly
    the state the refusal exists to prevent.
    """
    cid = client.academy.solo_ballet
    r = client.put(f"/api/clients/{cid}",
                   json={"name_en": "Farah Adel", "phone": "01111111111"})
    assert r.status_code == 409
    assert "Dana Halim" in r.json()["detail"]
    # And the row is untouched.
    assert client.get(f"/api/clients/{cid}").json()["phone"] == "01111111112"


def test_editing_a_client_keeping_their_own_number_is_fine(client):
    cid = client.academy.solo_ballet
    r = client.put(f"/api/clients/{cid}",
                   json={"name_en": "Farah Adel Renamed", "phone": "01111111112"})
    assert r.status_code == 200
    assert client.get(f"/api/clients/{cid}").json()["name_en"] == "Farah Adel Renamed"


def test_editing_a_client_to_a_free_number_is_fine(client):
    cid = client.academy.solo_ballet
    r = client.put(f"/api/clients/{cid}",
                   json={"name_en": "Farah Adel", "phone": "01999000111"})
    assert r.status_code == 200
