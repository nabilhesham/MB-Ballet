"""
migrate_to_mongo.py's SQLite side, which is the half that can be tested
without a MongoDB server.

Its one job before anything moves is to be able to *read* a database older
than itself. The academy's own file could not be read at all: it predates the
`images` table, so counting the rows to move died on `no such table: images`
before a single document had been written.
"""

import os
import sqlite3

import pytest

import config
import images

pytestmark = pytest.mark.sqlite_only

OLD_SCHEMA = """
CREATE TABLE clients (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name_en    TEXT NOT NULL,
    photo_path TEXT,
    active     INTEGER NOT NULL DEFAULT 1,
    created_at INTEGER NOT NULL DEFAULT 0);
CREATE TABLE credentials (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id  INTEGER NOT NULL,
    token      TEXT NOT NULL,
    kind       TEXT NOT NULL DEFAULT 'card',
    issued_at  INTEGER NOT NULL DEFAULT 0,
    revoked_at INTEGER);
INSERT INTO clients (name_en, photo_path) VALUES ('Salma', '/photos/client_00001.jpg');
INSERT INTO credentials (client_id, token) VALUES (1, 'AEAA');
"""


@pytest.fixture
def old_db(tmp_path, monkeypatch):
    """A database with no images table and none of the newer columns."""
    path = str(tmp_path / "academy.db")
    conn = sqlite3.connect(path)
    conn.executescript(OLD_SCHEMA)
    conn.commit()
    conn.close()
    monkeypatch.setenv("MB_SQLITE_PATH", path)
    return tmp_path


def png(colour=(200, 120, 180)):
    import io

    from PIL import Image
    out = io.BytesIO()
    Image.new("RGB", (40, 30), colour).save(out, "PNG")
    return out.getvalue()


def test_the_source_opens_even_though_it_predates_the_images_table(old_db):
    """The exact traceback this replaces: no such table: images."""
    import migrate_to_mongo

    source = migrate_to_mongo.sqlite_repo()
    try:
        assert source.count("images") == 0
        # Every collection the migration walks, not just the one that failed.
        for coll in migrate_to_mongo.ORDER:
            source.count(coll)
    finally:
        source.close()


def test_it_reads_the_pictures_in_from_beside_that_database(old_db):
    """
    Rows are the only thing that travels, so a `photo_path` pointing at a
    file has to become a row before the copy, not after — and the folder it
    looks in is the one holding this database, whatever MB_DB_BACKEND says.
    """
    import migrate_to_mongo

    os.makedirs(old_db / "photos")
    (old_db / "photos" / "client_00001.jpg").write_bytes(png())
    os.makedirs(old_db / "cards")
    (old_db / "cards" / "client_00001_ballet.png").write_bytes(png((9, 9, 9)))

    assert images.pending_legacy_files(config.legacy_media_dir()) == 2

    source = migrate_to_mongo.sqlite_repo()
    try:
        assert images.import_legacy_files(source, config.legacy_media_dir()) == 2
        assert source.count("images") == 2
        assert source.get("clients", 1)["photo_path"].startswith("/api/images/")
        # Idempotent: the migration can be re-run, and is.
        assert images.import_legacy_files(source, config.legacy_media_dir()) == 0
    finally:
        source.close()


def test_what_it_warns_about_is_a_picture_that_exists_nowhere(old_db):
    """
    Not a refusal. A photo never taken or a card whose PNG was deleted cannot
    be produced by any amount of importing — only by reissuing or
    re-uploading — so blocking the migration on it would be a refusal with no
    way to clear it.
    """
    import migrate_to_mongo

    source = migrate_to_mongo.sqlite_repo()
    try:
        faces, cards = migrate_to_mongo.pictures_with_no_row(source)
        assert faces == 1, "photo_path still points at /photos/"
        assert cards == 1, "one live credential, no card row"
    finally:
        source.close()
