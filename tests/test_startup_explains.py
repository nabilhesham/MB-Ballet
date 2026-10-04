"""
What reception is shown when the database cannot be reached at startup.

The failure this covers has already happened twice, in two different ways,
and both times the symptom was eighty lines of pymongo ServerDescription
objects on the one screen where a stack trace is worst: the window closes,
and whatever was printed is all anyone gets.

`server.py`'s `_connect_or_explain()` is supposed to replace that with three
sentences naming the three real causes. For a year it could not, because it
guarded `data.connect()` -- which builds a MongoClient and touches no
network at all. The first round trip was `init_schema()` on the next line,
*outside* the handler. That is what `ping()` exists for, and what these hold.
"""

import pytest

pytestmark = pytest.mark.sqlite_only   # about startup, not about a backend

import config                                           # noqa: E402
import repo as data                                     # noqa: E402
import server                                           # noqa: E402


class Dead:
    """A repo that connects happily and cannot be reached."""

    def __init__(self, boom):
        self.boom = boom
        self.schema_calls = 0

    def ping(self):
        raise self.boom

    def init_schema(self):
        self.schema_calls += 1


@pytest.fixture
def unreachable(monkeypatch):
    """`connect()` succeeding and the first round trip failing, on mongo."""
    monkeypatch.setattr(config, "backend", lambda: config.MONGO)
    monkeypatch.setattr(config, "describe", lambda: "MongoDB  mb_ballet at h")

    def arrange(boom):
        dead = Dead(boom)
        monkeypatch.setattr(data, "connect", lambda *a, **kw: dead)
        return dead

    return arrange


def test_the_first_round_trip_is_one_the_handler_owns(unreachable, capsys):
    """
    The bug itself: a connection that fails must fail *inside* the try, not
    on the next statement. `init_schema()` must never be what finds out.
    """
    dead = unreachable(RuntimeError("No replica set members found yet"))
    with pytest.raises(RuntimeError):
        server._connect_or_explain()
    assert dead.schema_calls == 0
    assert "could not be reached" in capsys.readouterr().out


def test_no_answer_sends_them_to_the_access_list(unreachable, capsys):
    """
    The commonest cause by far, and the one nobody guesses: the Atlas access
    list is per-network, so a laptop that worked yesterday stops working in
    another building with nothing having changed on it.
    """
    unreachable(RuntimeError(
        "ServerSelectionTimeoutError: No replica set members found yet, "
        "Timeout: 20.0s, Topology Description: <TopologyDescription ..."))
    with pytest.raises(RuntimeError):
        server._connect_or_explain()
    out = capsys.readouterr().out
    assert "Network Access" in out
    assert "internet" in out


def test_a_certificate_failure_blames_the_build_not_the_laptop(unreachable,
                                                              capsys):
    """It is a fault in the build -- a missing CA bundle -- and saying so is
    what stops somebody rebuilding the network instead."""
    unreachable(RuntimeError("[SSL: CERTIFICATE_VERIFY_FAILED] certificate "
                             "verify failed: unable to get local issuer"))
    with pytest.raises(RuntimeError):
        server._connect_or_explain()
    out = capsys.readouterr().out
    assert "fault in the build" in out
    assert "Network Access" not in out


def test_a_lookup_failure_names_the_direct_uri_form(unreachable, capsys):
    """Some networks filter the DNS SRV lookup a mongodb+srv:// URI needs;
    the direct host form is the answer and is in .env.example."""
    unreachable(RuntimeError("The DNS query name does not exist: "
                             "_mongodb._tcp.cluster0.example.mongodb.net."))
    with pytest.raises(RuntimeError):
        server._connect_or_explain()
    assert "mongodb://host1" in capsys.readouterr().out


def test_the_dump_does_not_come_back_as_the_last_word(unreachable, capsys):
    """
    uvicorn prints whatever escapes a lifespan handler *after* everything
    the handler printed, so a chained cause would scroll the explanation off
    the terminal and leave the wall as the final thing on screen. `from
    None` is what keeps the readable sentence last.
    """
    unreachable(RuntimeError("Topology Description: <TopologyDescription id"))
    with pytest.raises(RuntimeError) as got:
        server._connect_or_explain()
    assert got.value.__cause__ is None
    assert "Topology Description" not in str(got.value)


def test_the_detail_is_written_where_the_message_says_it_is(unreachable,
                                                           capsys, tmp_path,
                                                           monkeypatch):
    """
    uvicorn catches a lifespan failure, logs "Application startup failed" and
    returns normally -- so nothing above it raises and run_app.py's error.log
    was never written for this one case, while the message said it was.
    """
    monkeypatch.setattr(server, "APP_DIR", str(tmp_path))
    unreachable(RuntimeError("boom-at-startup"))
    with pytest.raises(RuntimeError):
        server._connect_or_explain()
    log = tmp_path / "error.log"
    assert log.exists()
    assert "boom-at-startup" in log.read_text()
    assert str(log) in capsys.readouterr().out


def test_sqlite_failures_are_left_alone(monkeypatch):
    """The three sentences are about Atlas. A local file that cannot be
    opened is a different fault and must not be dressed up as this one."""
    monkeypatch.setattr(config, "backend", lambda: config.SQLITE)

    def boom(*a, **kw):
        raise OSError("unable to open database file")

    monkeypatch.setattr(data, "connect", boom)
    with pytest.raises(OSError):
        server._connect_or_explain()
