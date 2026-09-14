"""
Photos and cards, now that they are rows rather than files.

The thing under test is not "can bytes go in and come out" — it is the two
properties the move had to preserve. A picture has to *change* when it is
replaced, which is what the `?v=` stamp in the URL is for; and an install
that already has faces in photos/ and cards printed in cards/ has to arrive
at the new version still showing them.
"""

import io
import os

import pytest
from fastapi.testclient import TestClient
from PIL import Image

import images


def png(size=(40, 30), colour=(200, 120, 180)) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", size, colour).save(out, "PNG")
    return out.getvalue()


@pytest.fixture
def client(academy):
    import server
    with TestClient(server.app) as c:
        c.academy = academy
        yield c


# ---------------------------------------------------------------- storage

def test_an_image_comes_back_as_it_went_in(repo):
    blob = png()
    images.store(repo, images.CLIENT_PHOTO, 7, blob, "image/png")
    assert images.load(repo, images.CLIENT_PHOTO, 7) == (blob, "image/png")


def test_storing_again_replaces_rather_than_accumulates(repo):
    images.store(repo, images.CLIENT_PHOTO, 7, png(), "image/png")
    second = png(colour=(10, 10, 10))
    images.store(repo, images.CLIENT_PHOTO, 7, second, "image/png")
    assert repo.count("images", {"kind": images.CLIENT_PHOTO, "owner_id": 7}) == 1
    assert images.load(repo, images.CLIENT_PHOTO, 7)[0] == second


def test_a_variant_is_its_own_image(repo):
    """One card per class, so the class has to be part of the identity."""
    images.store(repo, images.CARD, 7, png(), "image/png", variant="ballet")
    images.store(repo, images.CARD, 7, png(colour=(1, 2, 3)), "image/png",
                 variant="flexibility")
    assert repo.count("images", {"kind": images.CARD, "owner_id": 7}) == 2
    assert images.load(repo, images.CARD, 7, "ballet")[0] \
        != images.load(repo, images.CARD, 7, "flexibility")[0]


def test_nothing_stored_reads_as_nothing(repo):
    assert images.load(repo, images.CLIENT_PHOTO, 7) == (None, None)


def test_a_phone_sized_photo_is_cut_down(repo):
    """A 4000px original is four megabytes of what nobody can see, and two
    of them would put a MongoDB document near its 16MB limit."""
    big = io.BytesIO()
    Image.new("RGB", (3000, 2200), (20, 180, 90)).save(big, "JPEG")
    blob, mime = images.shrink(big.getvalue(), "image/jpeg")
    assert max(Image.open(io.BytesIO(blob)).size) == images.PHOTO_MAX
    assert len(blob) < len(big.getvalue())


def test_something_that_is_not_an_image_is_left_alone(repo):
    """The caller already checked the extension; refusing here would turn a
    photo Pillow merely dislikes into a failed upload."""
    assert images.shrink(b"not an image", "image/jpeg") == (b"not an image", "image/jpeg")


# ---------------------------------------------------------------- serving

def test_the_endpoint_serves_the_bytes(client):
    blob = png()
    images.store(client.academy.repo, images.CLIENT_PHOTO, 7, blob, "image/png")
    r = client.get("/api/images/client_photo/7")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert r.content == blob


def test_an_absent_image_is_a_404_not_a_placeholder(client):
    """Avatar falls back to initials and the kiosk to a monogram; inventing a
    grey square here would take that decision away from the screen."""
    assert client.get("/api/images/client_photo/999").status_code == 404


def test_an_unknown_kind_is_refused(client):
    assert client.get("/api/images/passport/1").status_code == 404


def test_a_re_uploaded_photo_gets_a_new_url(client, monkeypatch):
    """
    The URL is derived from who the photo belongs to, so it does not change
    when the picture does — which is how a re-upload used to leave the
    browser showing the one it already had, looking like it never saved. The
    clock is driven here because db.now() counts whole seconds and a test
    does both uploads inside one.
    """
    import db
    cid = client.academy.dual
    monkeypatch.setattr(db, "now", lambda: 1_700_000_000)
    first = client.post(f"/api/clients/{cid}/photo",
                        files={"file": ("a.png", png(), "image/png")}).json()
    monkeypatch.setattr(db, "now", lambda: 1_700_000_060)
    second = client.post(f"/api/clients/{cid}/photo",
                         files={"file": ("b.png", png(colour=(0, 0, 0)), "image/png")}).json()
    assert first["photo_path"] != second["photo_path"]
    assert first["photo_path"].split("v=")[0] == second["photo_path"].split("v=")[0]
    assert client.get(second["photo_path"]).status_code == 200


def test_the_profile_points_at_the_stored_photo(client):
    """Not byte-for-byte: an upload goes through shrink() on the way in."""
    cid = client.academy.dual
    client.post(f"/api/clients/{cid}/photo",
                files={"file": ("a.png", png(size=(60, 45)), "image/png")})
    path = client.get(f"/api/clients/{cid}").json()["photo_path"]
    r = client.get(path)
    assert r.status_code == 200
    assert Image.open(io.BytesIO(r.content)).size == (60, 45)


# ---------------------------------------------------------------- upgrading

def test_photos_and_cards_already_on_disk_are_carried_over(repo, academy, tmp_path):
    """
    The regression that matters most on the day this ships: an academy that
    has been running has real faces in photos/ and real printed cards in
    cards/, and losing them would blank the largest element on the kiosk.
    """
    cid = academy.dual
    os.makedirs(tmp_path / "photos", exist_ok=True)
    os.makedirs(tmp_path / "cards", exist_ok=True)
    (tmp_path / "photos" / f"client_{cid:05d}_1700000000.png").write_bytes(png())
    (tmp_path / "cards" / f"client_{cid:05d}_ballet.png").write_bytes(png(colour=(9, 9, 9)))

    assert images.import_legacy_files(repo, str(tmp_path)) == 2
    assert images.load(repo, images.CLIENT_PHOTO, cid)[0] is not None
    assert images.load(repo, images.CARD, cid, "ballet")[0] == png(colour=(9, 9, 9))
    # And the column now points at the app rather than at the disk.
    assert repo.get("clients", cid)["photo_path"].startswith("/api/images/")


def test_carrying_them_over_happens_once(repo, academy, tmp_path):
    """It runs on every start, so a second pass must not re-read the file —
    least of all over a photo uploaded since."""
    cid = academy.dual
    os.makedirs(tmp_path / "photos", exist_ok=True)
    (tmp_path / "photos" / f"client_{cid:05d}_1700000000.png").write_bytes(png())
    images.import_legacy_files(repo, str(tmp_path))

    newer = png(colour=(3, 3, 3))
    images.store(repo, images.CLIENT_PHOTO, cid, newer, "image/png")
    assert images.import_legacy_files(repo, str(tmp_path)) == 0
    assert images.load(repo, images.CLIENT_PHOTO, cid)[0] == newer


def test_a_photo_belonging_to_nobody_is_skipped(repo, tmp_path):
    """A file left behind by a client who was hard-deleted."""
    os.makedirs(tmp_path / "photos", exist_ok=True)
    (tmp_path / "photos" / "client_09999_1700000000.png").write_bytes(png())
    assert images.import_legacy_files(repo, str(tmp_path)) == 0


def test_no_folders_at_all_is_not_an_error(repo, tmp_path):
    """The ordinary case on every start after the first, and on a fresh
    install that never had either folder."""
    assert images.import_legacy_files(repo, str(tmp_path)) == 0
