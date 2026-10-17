"""
What the shop sold in a date range, named.

The product page answers "who bought this one" and the client profile
answers "what did she buy"; neither answers "what went over the counter
last month", which is the question stock-taking and the month's figures are
actually asked of.

It is dated by `paid_on` — the same column TAKEN TODAY reads — so a day
here and a day in the till are the same day's money. The consequence, which
the screen states rather than hiding, is that an undated sale is in no
range at all.
"""

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(academy):
    import server
    with TestClient(server.app) as c:
        c.academy = academy
        yield c


def _product(client, name="Leotard", price=350.0, sizes=None):
    r = client.post("/api/products", json={
        "name": name, "price": price,
        "sizes": sizes if sizes is not None else [{"size": None, "qty": 50}]})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _sell(client, pid, who, **over):
    body = {"client_id": who, "qty": 1}
    body.update(over)
    r = client.post(f"/api/products/{pid}/sell", json=body)
    assert r.status_code == 200, r.text
    return r.json()["sale_id"]


def _history(client, **params):
    q = "&".join(f"{k}={v}" for k, v in params.items() if v is not None)
    r = client.get(f"/api/products/history{'?' + q if q else ''}")
    assert r.status_code == 200, r.text
    return r.json()


TODAY = date.today().isoformat()
YDAY = (date.today() - timedelta(days=1)).isoformat()
LAST_MONTH = (date.today() - timedelta(days=40)).isoformat()


def test_it_names_the_item_the_buyer_the_day_the_size_and_the_money(client):
    a = client.academy
    pid = _product(client, sizes=[{"size": 36, "qty": 5}])
    _sell(client, pid, a.dual, qty=2, size=36, paid_on=TODAY)

    sale = _history(client)["sales"][0]

    assert sale["product_name"] == "Leotard"
    assert sale["client_name"]
    assert sale["paid_on"] == TODAY
    assert sale["size"] == 36.0
    assert sale["qty"] == 2
    assert sale["unit_price"] == 350.0


def test_the_range_is_inclusive_at_both_ends(client):
    """Which is what a receptionist typing two dates means by "between" —
    the same rule the Clients list's joining range follows."""
    a = client.academy
    pid = _product(client)
    _sell(client, pid, a.dual, paid_on=YDAY)
    _sell(client, pid, a.dual, paid_on=TODAY)

    assert len(_history(client, start=YDAY, end=TODAY)["sales"]) == 2
    assert len(_history(client, start=TODAY, end=TODAY)["sales"]) == 1
    assert len(_history(client, start=YDAY, end=YDAY)["sales"]) == 1


def test_either_end_works_alone(client):
    """"Everything since September" is a real question."""
    a = client.academy
    pid = _product(client)
    _sell(client, pid, a.dual, paid_on=LAST_MONTH)
    _sell(client, pid, a.dual, paid_on=TODAY)

    assert len(_history(client, start=TODAY)["sales"]) == 1
    assert len(_history(client, end=YDAY)["sales"]) == 1
    assert len(_history(client)["sales"]) == 2


def test_the_totals_count_the_unpriced_rather_than_adding_them_as_zero(client):
    """The same treatment an unpriced plan gets on the dashboard."""
    a = client.academy
    priced = _product(client, price=350.0)
    free = _product(client, name="Hairnet", price=None)
    _sell(client, priced, a.dual, qty=2, paid_on=TODAY)
    _sell(client, free, a.dual, qty=3, paid_on=TODAY)

    out = _history(client, start=TODAY, end=TODAY)

    assert out["paid"] == 700.0
    assert out["items"] == 5
    assert out["unpriced"] == 1


def test_an_undated_sale_is_in_no_range(client):
    """It has no day to fall in. The screen says so rather than letting it
    vanish silently — it is still on the profile and the product page."""
    a = client.academy
    pid = _product(client)
    _sell(client, pid, a.dual, paid_on="")

    assert _history(client)["sales"] == []
    assert _history(client, start="2000-01-01", end="2999-01-01")["sales"] == []
    assert client.get(f"/api/products/{pid}").json()["sales"], \
        "it is still on the product's own page"


def test_history_is_reachable_and_not_swallowed_by_the_id_route(client):
    """`/api/products/{pid}` takes an int, so declared first it would match
    this path and answer 422 about a path parameter — a route that exists
    and cannot be reached."""
    r = client.get("/api/products/history")

    assert r.status_code == 200
    assert "sales" in r.json()


def test_a_bound_that_is_not_a_date_is_refused_in_words(client):
    r = client.get("/api/products/history?start=last+tuesday")

    assert r.status_code == 400
    assert r.json()["detail"] == "Pick a day, or clear the date filter."


def test_a_removed_sale_leaves_the_history(client):
    """Deleting a sale is the one deletion the shop has, precisely because
    what it leaves behind is a wrong number in a day's takings."""
    a = client.academy
    pid = _product(client)
    sale = _sell(client, pid, a.dual, paid_on=TODAY)
    assert len(_history(client)["sales"]) == 1

    client.delete(f"/api/product-sales/{sale}")

    assert _history(client)["sales"] == []
