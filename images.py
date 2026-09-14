"""
Every picture the app holds, kept in the database rather than on the disk.

Client photos, instructor photos and the printed member cards all used to be
files — `photos/client_00060_1788711822.png`, `cards/client_00060_ballet.png`
— with the path written into a column. That made them the one part of the
academy's record a backup of the database did not contain, and on the hosted
backend it was worse: the data lived on Atlas and the faces lived on
whichever laptop had done the uploading.

They are base64 text in an `images` row instead. Base64 rather than a BLOB
because the same rows have to live in MongoDB, and it is the one encoding
SQLite, BSON and the repository's plain dicts all carry with no per-backend
special case. It costs a third more space than raw bytes, which for a
few hundred photos is nothing next to having them in the backup.

**What is stored is not what is served.** A row holds the bytes; the app
holds a short URL (`/api/images/client_photo/60?v=...`) that a request
exchanges for them. Putting a data: URI in `photo_path` would have been
fewer moving parts and much worse: that column is returned in the clients
list, the dashboard's attention list and every kiosk scan, so a megabyte of
base64 would ride along with every row of every list that mentions a person.
"""

import base64
import io

from PIL import Image

CLIENT_PHOTO = "client_photo"
INSTRUCTOR_PHOTO = "instructor_photo"
CARD = "card"

# What a photo is stored at. The largest it is ever displayed is the kiosk's
# 150px square, so this is already generous; a phone's 4000px original is
# four megabytes of nothing anyone can see. It also keeps a document inside
# MongoDB's 16MB limit, which a raw upload genuinely threatens.
PHOTO_MAX = 900
PHOTO_QUALITY = 82


def store(repo, kind: str, owner_id: int, data: bytes, mime: str,
          variant: str = "", now: int = None) -> int:
    """
    Save one image, replacing whatever that owner had before.

    The replace is why this is not a plain insert: one photo per person and
    one card per class, so an upload or a reissue must overwrite rather than
    leave the old row behind for a `find` to pick between.
    """
    import db
    row = {"kind": kind, "owner_id": owner_id, "variant": variant,
           "mime": mime, "data": base64.b64encode(data).decode("ascii"),
           "updated_at": now or db.now()}
    existing = repo.find_one("images", {"kind": kind, "owner_id": owner_id,
                                        "variant": variant})
    if existing:
        repo.update("images", existing["id"], row)
        return existing["id"]
    return repo.insert("images", row)


def load(repo, kind: str, owner_id: int, variant: str = ""):
    """The bytes and their mime type, or (None, None)."""
    row = repo.find_one("images", {"kind": kind, "owner_id": owner_id,
                                   "variant": variant})
    if not row:
        return None, None
    return base64.b64decode(row["data"]), row["mime"]


def drop(repo, kind: str, owner_id: int, variant: str = None) -> None:
    """Forget an owner's images. `variant=None` means all of them."""
    flt = {"kind": kind, "owner_id": owner_id}
    if variant is not None:
        flt["variant"] = variant
    repo.delete_where("images", flt)


def url(kind: str, owner_id: int, variant: str = "", stamp: int = 0) -> str:
    """
    Where the app points an `<img src>` at it.

    The `?v=` is the same trick the card file needed and for the same reason:
    the URL is derived from who the image belongs to, so it does not change
    when the picture does, and the browser goes on showing the one it has.
    Stamping it with the update time changes the URL exactly when the bytes
    change.
    """
    q = f"?variant={variant}&v={stamp}" if variant else f"?v={stamp}"
    return f"/api/images/{kind}/{owner_id}{q}"


def shrink(data: bytes, mime: str):
    """
    A photo at a sane size, as (bytes, mime).

    Anything that is not a decodable image comes back untouched — the caller
    has already checked the extension, and refusing here would turn a photo
    Pillow merely dislikes into a failed upload.
    """
    try:
        im = Image.open(io.BytesIO(data))
        im.load()
    except Exception:
        return data, mime

    # EXIF orientation: a phone stores the sensor's idea of up and a tag
    # saying how to turn it. Stripping the tag without applying it is how an
    # upright portrait ends up sideways on the card.
    try:
        from PIL import ImageOps
        im = ImageOps.exif_transpose(im)
    except Exception:
        pass

    if max(im.size) > PHOTO_MAX:
        im.thumbnail((PHOTO_MAX, PHOTO_MAX), Image.LANCZOS)

    out = io.BytesIO()
    if im.mode in ("RGBA", "LA", "P"):
        im.convert("RGBA").save(out, "PNG", optimize=True)
        return out.getvalue(), "image/png"
    im.convert("RGB").save(out, "JPEG", quality=PHOTO_QUALITY, optimize=True)
    return out.getvalue(), "image/jpeg"


# ------------------------------------------------------------------ legacy
# Every install that existed before this change has real faces and real
# printed cards sitting in photos/ and cards/. Shipping the change without
# this would blank every client photo on the reception screen — the one
# element the kiosk deliberately makes the largest thing on it — and break
# the download link on every card already issued.
#
# It is idempotent by construction: a file is read only when there is no row
# for that owner yet, so it moves what is there on the first start after the
# upgrade and does nothing on every start after that. The files are left
# where they are rather than deleted; cleanup.sh is where removing them
# belongs, once someone has seen the photos are still on screen.
_LEGACY_PHOTO_KINDS = {"client": (CLIENT_PHOTO, "clients"),
                       "instructor": (INSTRUCTOR_PHOTO, "instructors")}


def _legacy_photos(folder):
    """(kind, table, owner_id, path) for each photo file, newest per owner."""
    import os
    import re
    best = {}
    for name in sorted(os.listdir(folder)):
        m = re.match(r"(client|instructor)_(\d+)_", name)
        if not m or m.group(1) not in _LEGACY_PHOTO_KINDS:
            continue
        kind, table = _LEGACY_PHOTO_KINDS[m.group(1)]
        owner = int(m.group(2))
        path = os.path.join(folder, name)
        # Several files can survive for one owner if a delete ever failed;
        # the timestamp is in the name, so the last one sorted is the newest.
        best[(kind, owner)] = (table, path)
    return [(k, t, o, p) for (k, o), (t, p) in best.items()]


def import_legacy_files(repo, app_dir: str) -> int:
    """Move photos/ and cards/ into the database. Returns how many moved."""
    import os
    import re
    import mimetypes

    moved = 0
    folder = os.path.join(app_dir, "photos")
    if os.path.isdir(folder):
        for kind, table, owner, path in _legacy_photos(folder):
            if repo.exists("images", {"kind": kind, "owner_id": owner,
                                      "variant": ""}):
                continue
            if not repo.exists(table, {"id": owner}):
                continue
            mime = mimetypes.guess_type(path)[0] or "image/jpeg"
            with open(path, "rb") as f:
                blob, mime = shrink(f.read(), mime)
            now = int(os.path.getmtime(path))
            store(repo, kind, owner, blob, mime, now=now)
            repo.update(table, owner, {"photo_path": url(kind, owner, stamp=now)})
            moved += 1

    folder = os.path.join(app_dir, "cards")
    if os.path.isdir(folder):
        for name in sorted(os.listdir(folder)):
            m = re.match(r"client_(\d+)_(.+)\.png$", name)
            if not m:
                continue
            owner, variant = int(m.group(1)), m.group(2)
            if repo.exists("images", {"kind": CARD, "owner_id": owner,
                                      "variant": variant}):
                continue
            path = os.path.join(folder, name)
            with open(path, "rb") as f:
                store(repo, CARD, owner, f.read(), "image/png",
                      variant=variant, now=int(os.path.getmtime(path)))
            moved += 1
    return moved
