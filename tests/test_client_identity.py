"""
What identifies a client: the mobile number AND the name, together.

Two profiles for one person is not an untidiness problem. Their sessions,
their plans and their cards divide between the two records, so a card scans
against a balance that is half what they bought — and the half that is
missing is invisible, because the other profile looks perfectly healthy.

But a shared mobile is not that. A parent enrols two children on one
number, which at a children's ballet academy is the ordinary case, so the
pair is what has to be unique rather than the number alone. The number is
still mandatory on its own — a client with none cannot be told apart from
the next client with none.
"""

from datetime import date

import pytest
from fastapi.testclient import TestClient

import access
import identity


# ---------------------------------------------------------------- the key
def test_one_number_written_three_ways_has_one_key():
    """
    Exactly the shapes the academy's own sheets hold: Excel ate the leading
    zero on one block, another block has it, and a third carries the country
    code. One person.
    """
    assert (identity.phone_key("1129200365") == identity.phone_key("01129200365")
            == identity.phone_key("+201129200365") == "1129200365")


def test_punctuation_and_spacing_do_not_make_a_new_person():
    assert identity.phone_key("0112 920 0365") == identity.phone_key("011-2920-0365") == "1129200365"


def test_a_number_with_nothing_to_compare_has_no_key():
    for blank in (None, "", "   ", "n/a", "no phone"):
        assert identity.phone_key(blank) is None


def test_a_short_number_is_compared_as_written():
    """
    Not a mobile, so there is no country code to strip. Comparing what is
    there can fail to match; it can never match the wrong person.
    """
    assert identity.phone_key("12345") == "12345"
    assert identity.phone_key("12345") != identity.phone_key("0111112345")


def test_a_name_is_folded_for_comparison():
    """One person typed three ways. Nothing cleverer than this, deliberately."""
    assert (identity.name_key("Dana Halim") == identity.name_key("dana  halim")
            == identity.name_key("  Dana HALIM ") == "dana halim")


def test_name_folding_does_not_guess():
    """
    Two spellings of one Arabic name are NOT folded together, and two real
    cousins are not merged. Reception can see both rows and decide.
    """
    assert identity.name_key("Mohamed Ali") != identity.name_key("Mohammed Ali")
    assert identity.name_key("Dana Halim") != identity.name_key("Dana Halima")


# ---------------------------------------------------------------- the rule
def test_a_free_number_is_not_a_conflict(repo, academy):
    assert access.duplicate_client(repo, "New Person", "01999888777") is None


def test_the_same_name_on_the_same_number_is_refused(repo, academy):
    msg = access.duplicate_client(repo, "Dana Halim", "01111111111")
    assert msg is not None
    assert "Dana Halim" in msg
    assert str(academy.dual) in msg


def test_a_different_name_on_the_same_number_is_allowed(repo, academy):
    """
    The whole point of the pair. Dana's little brother goes on the same
    mobile as Dana, because it is their parent's.
    """
    assert access.duplicate_client(repo, "Omar Halim", "01111111111") is None


def test_the_same_number_written_differently_is_still_refused(repo, academy):
    """A duplicate that does not look like one — as long as the name matches."""
    for written in ("1111111111", "+201111111111", "0111 111 1111"):
        assert access.duplicate_client(repo, "Dana Halim", written) is not None


def test_the_name_is_matched_folded_not_exactly(repo, academy):
    """Case and spacing are not a second person."""
    for written in ("dana halim", "DANA HALIM", "  Dana   Halim  "):
        assert access.duplicate_client(repo, written, "01111111111") is not None


def test_a_blank_number_is_never_a_conflict(repo, academy):
    """
    True of duplicate_client() alone, and exactly why phone_required() had
    to exist: with nothing to compare, two blanks are not duplicates of each
    other and never would be. The routes refuse a blank before they get
    here — see the route tests below.
    """
    for blank in (None, "", "   "):
        assert access.duplicate_client(repo, "Dana Halim", blank) is None


def test_an_archived_client_is_named_and_the_way_out_is_restore(repo, academy):
    """
    Saying only "already exists" would send reception looking for somebody
    they cannot find on the list, and the only way out of that is a second
    profile — the exact thing this refusal prevents.
    """
    msg = access.duplicate_client(repo, "Karim Nour", "01111111116")
    assert "Karim Nour" in msg
    assert "archived" in msg.lower()
    assert "restore" in msg.lower()


def test_a_client_is_not_a_duplicate_of_themselves(repo, academy):
    assert access.duplicate_client(
        repo, "Dana Halim", "01111111111", exclude_id=academy.dual) is None


def test_excluding_one_client_does_not_excuse_another(repo, academy):
    """Renaming Farah to Dana on Dana's number is still Dana."""
    assert access.duplicate_client(
        repo, "Dana Halim", "01111111111",
        exclude_id=academy.solo_ballet) is not None


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


def test_creating_the_same_person_twice_is_refused(client):
    r = client.post("/api/clients", json=_new("Dana Halim", "01111111111"))
    assert r.status_code == 409
    assert "Dana Halim" in r.json()["detail"]


def test_the_refusal_holds_however_the_number_is_typed(client):
    r = client.post("/api/clients", json=_new("Dana Halim", "+20 111 111 1111"))
    assert r.status_code == 409


def test_a_sibling_on_the_same_number_is_created(client):
    """
    The relaxation, end to end: Dana's brother shares their parent's mobile
    and is a second client, not a refused duplicate.
    """
    r = client.post("/api/clients", json=_new("Omar Halim", "01111111111"))
    assert r.status_code == 200, r.json()
    assert r.json()["id"]


def test_a_whole_family_can_share_one_number(client):
    """Three children on one parent's mobile is an ordinary enrolment."""
    for who in ("Sibling One", "Sibling Two", "Sibling Three"):
        assert client.post(
            "/api/clients", json=_new(who, "01777000111")).status_code == 200


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
    the same name already holds is still refused, whichever way it is
    written.
    """
    assert client.post("/api/clients",
                       json=_new("Nour Said", "01012345675")).status_code == 200
    r = client.post("/api/clients", json=_new("nour  said", "+20 101 234 5675"))
    assert r.status_code == 409
    assert "Nour Said" in r.json()["detail"]


def test_a_number_cannot_be_cleared_by_an_edit(client):
    """
    Otherwise the requirement lasts exactly as long as it takes to press
    Edit, and the client is back to having no identity.
    """
    cid = client.academy.solo_ballet
    r = client.put(f"/api/clients/{cid}", json={"name_en": "Farah Adel", "phone": ""})
    assert r.status_code == 400
    assert client.get(f"/api/clients/{cid}").json()["phone"] == "01111111112"


def test_nothing_is_written_when_the_pair_is_refused(client):
    before = len(client.get("/api/clients").json())
    client.post("/api/clients", json=_new("Dana Halim", "01111111111"))
    assert len(client.get("/api/clients").json()) == before


def test_editing_into_an_existing_person_is_refused(client):
    """
    Half a rule is no rule: refusing at creation and then allowing the pair
    to be typed over somebody else's a minute later leaves exactly the state
    the refusal exists to prevent.
    """
    cid = client.academy.solo_ballet
    r = client.put(f"/api/clients/{cid}",
                   json={"name_en": "Dana Halim", "phone": "01111111111"})
    assert r.status_code == 409
    assert "Dana Halim" in r.json()["detail"]
    # And the row is untouched.
    assert client.get(f"/api/clients/{cid}").json()["phone"] == "01111111112"


def test_editing_onto_someone_elses_number_is_allowed(client):
    """
    Farah moving to the number Dana uses is a family sharing a mobile, not a
    duplicate — she keeps her own name.
    """
    cid = client.academy.solo_ballet
    r = client.put(f"/api/clients/{cid}",
                   json={"name_en": "Farah Adel", "phone": "01111111111"})
    assert r.status_code == 200
    assert client.get(f"/api/clients/{cid}").json()["phone"] == "01111111111"


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
