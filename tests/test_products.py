"""
The shop: products, sales, and the till figure that now knows about both.

A sale is deliberately built out of the same pieces a plan is — a `paid_on`
dated the day the money arrives, a nullable price so a thing nobody wrote a
price for is not reported as zero, and a figure that counts the unpriced
separately. What it adds is stock, which comes off the shelf on a sale and
goes back when the sale is removed.
"""

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

import access
import db


@pytest.fixture
def client(academy):
    import server
    with TestClient(server.app) as c:
        c.academy = academy
        yield c


def _shelf(repo, name, price, qty, sizes=None):
    """A product straight into the table, with the shelf row every product
    has. `set_product_sizes()` is how the row is made rather than a second
    insert here, because it is the one writer of `products.stock` -- see
    access._sync_stock()."""
    pid = repo.insert("products", {"name": name, "price": price, "stock": 0,
                                   "notes": None, "photo_path": None,
                                   "active": 1, "created_at": db.now()})
    access.set_product_sizes(repo, pid,
                             sizes if sizes is not None
                             else [{"size": None, "qty": qty}])
    return pid


def _product(client, **over):
    body = {"name": "Leotard", "price": 350.0, "stock": 5, "notes": None}
    body.update(over)
    r = client.post("/api/products", json=body)
    assert r.status_code == 200, r.text
    return r.json()["id"]


# --------------------------------------------------- the product itself

def test_a_product_is_created_listed_and_edited(client):
    pid = _product(client)

    listed = client.get("/api/products").json()
    assert [p["name"] for p in listed] == ["Leotard"]

    r = client.put(f"/api/products/{pid}",
                   json={"name": "Leotard", "price": 400.0, "stock": 6,
                         "notes": "navy"})
    assert r.status_code == 200
    p = client.get(f"/api/products/{pid}").json()
    assert (p["price"], p["stock"], p["notes"]) == (400.0, 6, "navy")


def test_a_product_with_no_price_is_a_real_state(client):
    """Nullable for the same reason a plan's price is: a thing nobody wrote
    a price for must not be reported as zero revenue."""
    pid = _product(client, price=None)
    assert client.get(f"/api/products/{pid}").json()["price"] is None


def test_a_nameless_or_negative_product_is_refused_in_words(client):
    assert client.post("/api/products", json={"name": "  "}).status_code == 400
    assert client.post("/api/products",
                       json={"name": "x", "stock": -1}).status_code == 400
    assert client.post("/api/products",
                       json={"name": "x", "price": -5}).status_code == 400


def test_the_same_refusals_apply_to_an_edit(client):
    """Refusing something at creation and allowing it a minute later leaves
    the state the refusal exists to prevent."""
    pid = _product(client)
    assert client.put(f"/api/products/{pid}",
                      json={"name": "", "stock": 1}).status_code == 400


# --------------------------------------------------- selling

def test_selling_takes_it_off_the_shelf_and_names_the_buyer(client):
    a = client.academy
    pid = _product(client, stock=5)

    r = client.post(f"/api/products/{pid}/sell",
                    json={"client_id": a.dual, "qty": 2})
    assert r.status_code == 200, r.text

    p = client.get(f"/api/products/{pid}").json()
    assert p["stock"] == 3
    assert p["sold"] == 2
    assert [s["client_id"] for s in p["sales"]] == [a.dual]
    assert p["sales"][0]["qty"] == 2


def test_the_price_is_copied_not_referenced(client):
    """Re-pricing a leotard must not rewrite what today's till took. The
    opposite of instructor pay, which is hours x rate at read time."""
    a = client.academy
    pid = _product(client, price=350.0, stock=5)
    client.post(f"/api/products/{pid}/sell", json={"client_id": a.dual, "qty": 1})

    client.put(f"/api/products/{pid}",
               json={"name": "Leotard", "price": 999.0, "stock": 4})

    sale = client.get(f"/api/products/{pid}").json()["sales"][0]
    assert sale["unit_price"] == 350.0


def test_more_than_the_shelf_holds_is_refused_and_says_how_many(client):
    a = client.academy
    pid = _product(client, stock=2)

    r = client.post(f"/api/products/{pid}/sell",
                    json={"client_id": a.dual, "qty": 3})

    assert r.status_code == 400
    assert "only 2" in r.json()["detail"]
    assert client.get(f"/api/products/{pid}").json()["stock"] == 2


def test_none_is_not_a_quantity(client):
    a = client.academy
    pid = _product(client)
    assert client.post(f"/api/products/{pid}/sell",
                       json={"client_id": a.dual, "qty": 0}).status_code == 400


def test_a_sale_defaults_to_paid_today(academy):
    """A sale over the counter is paid as it happens — the common case, and
    the one a receptionist should not have to type."""
    repo = academy.repo
    pid = _shelf(repo, "Shoes", 200.0, 3)

    r = access.sell_product(repo, pid, academy.dual)

    assert r["ok"], r
    sale = repo.get("product_sales", r["sale_id"])
    assert sale["paid_on"] == date.today().isoformat()


def test_an_explicitly_empty_paid_on_is_unpaid(academy):
    """And falls out of every day's till rather than counting as zero in
    one, exactly as an unpaid plan does."""
    repo = academy.repo
    pid = _shelf(repo, "Shoes", 200.0, 3)

    r = access.sell_product(repo, pid, academy.dual, paid_on="")

    assert repo.get("product_sales", r["sale_id"])["paid_on"] is None
    today = date.today().isoformat()
    assert repo.shop_takings(today, access.next_day(today))["paid"] == 0


# --------------------------------------------------- removing a sale

def test_removing_a_sale_gives_back_the_stock_and_the_money(client):
    a = client.academy
    pid = _product(client, price=350.0, stock=5)
    sale = client.post(f"/api/products/{pid}/sell",
                       json={"client_id": a.dual, "qty": 2}).json()

    r = client.delete(f"/api/product-sales/{sale['sale_id']}")

    assert r.status_code == 200
    p = client.get(f"/api/products/{pid}").json()
    assert p["stock"] == 5
    assert p["sales"] == []


def test_removing_a_sale_twice_is_refused_rather_than_doubling_the_stock(client):
    a = client.academy
    pid = _product(client, stock=5)
    sale = client.post(f"/api/products/{pid}/sell",
                       json={"client_id": a.dual, "qty": 1}).json()
    client.delete(f"/api/product-sales/{sale['sale_id']}")

    assert client.delete(f"/api/product-sales/{sale['sale_id']}").status_code == 404
    assert client.get(f"/api/products/{pid}").json()["stock"] == 5


# --------------------------------------------------- archiving

def test_a_product_with_sales_is_archived_not_deleted(client):
    """A sale points at it, and the profile that lists that sale has to go
    on naming what was bought."""
    a = client.academy
    pid = _product(client)
    client.post(f"/api/products/{pid}/sell", json={"client_id": a.dual, "qty": 1})

    assert client.delete(f"/api/products/{pid}?hard=true").status_code == 409

    assert client.delete(f"/api/products/{pid}").status_code == 200
    assert client.get("/api/products").json() == []
    assert [p["id"] for p in
            client.get("/api/products?status=archived").json()] == [pid]
    # And the client still shows what they bought.
    bought = client.get(f"/api/clients/{a.dual}").json()["purchases"]
    assert [s["product_name"] for s in bought] == ["Leotard"]


def test_an_unsold_product_can_be_deleted_for_good(client):
    pid = _product(client)
    assert client.delete(f"/api/products/{pid}?hard=true").status_code == 200
    assert client.get(f"/api/products/{pid}").status_code == 404


def test_an_archived_product_cannot_be_sold(academy):
    repo = academy.repo
    pid = repo.insert("products", {"name": "Gone", "price": 1.0, "stock": 9,
                                   "notes": None, "photo_path": None,
                                   "active": 0, "created_at": db.now()})
    assert access.sell_product(repo, pid, academy.dual)["ok"] is False


# --------------------------------------------------- the client's profile

def test_the_profile_lists_what_they_bought_and_what_it_cost(client):
    a = client.academy
    shoes = _product(client, name="Shoes", price=200.0, stock=4)
    client.post(f"/api/products/{shoes}/sell", json={"client_id": a.dual, "qty": 2})

    c = client.get(f"/api/clients/{a.dual}").json()

    assert [s["product_name"] for s in c["purchases"]] == ["Shoes"]
    assert c["spent_on_products"] == 400.0


# --------------------------------------------------- the till

def test_the_till_is_plans_plus_the_shop(client):
    a = client.academy
    today = date.today().isoformat()
    pid = _product(client, price=350.0, stock=5)
    client.post(f"/api/products/{pid}/sell",
                json={"client_id": a.dual, "qty": 2, "paid_on": today})

    s = client.get("/api/dashboard").json()["stats"]

    assert s["day_shop_paid"] == 700.0
    assert s["day_shop_items"] == 2
    assert s["day_income"] == round(s["day_plan_paid"] + 700.0, 2)


def test_yesterdays_sale_is_not_in_todays_till(client):
    a = client.academy
    yday = (date.today() - timedelta(days=1)).isoformat()
    pid = _product(client, price=350.0, stock=5)
    client.post(f"/api/products/{pid}/sell",
                json={"client_id": a.dual, "qty": 1, "paid_on": yday})

    s = client.get("/api/dashboard").json()["stats"]
    assert s["day_shop_paid"] == 0

    # And it is in yesterday's, which is what the day filter is for.
    assert client.get(f"/api/dashboard/till?on={yday}").json()["shop_paid"] == 350.0


def test_an_unpriced_sale_is_counted_and_not_added(client):
    a = client.academy
    pid = _product(client, price=None, stock=5)
    client.post(f"/api/products/{pid}/sell", json={"client_id": a.dual, "qty": 3})

    s = client.get("/api/dashboard").json()["stats"]

    assert s["day_shop_paid"] == 0
    assert s["day_shop_items"] == 3
    assert s["day_shop_unpriced"] == 1


def test_the_till_drilldown_names_both_halves(client):
    a = client.academy
    today = date.today().isoformat()
    pid = _product(client, price=350.0, stock=5)
    client.post(f"/api/products/{pid}/sell",
                json={"client_id": a.dual, "qty": 1, "paid_on": today})

    d = client.get("/api/dashboard/till").json()

    sale = [s for s in d["products"] if s["product_id"] == pid][0]
    assert sale["client_name"] and sale["product_name"] == "Leotard"
    assert sale["qty"] == 1 and sale["unit_price"] == 350.0
    # The plan half names the client and the class it was bought for.
    for p in d["plans"]:
        assert p["client_name"]
        assert "class_name" in p
    assert d["paid"] == round(d["plan_paid"] + d["shop_paid"], 2)


def test_the_drilldown_refuses_a_bound_that_is_not_a_date(client):
    r = client.get("/api/dashboard/till?on=soon")
    assert r.status_code == 400
    assert "Pick a day" in r.json()["detail"]


def test_removing_a_sale_takes_it_out_of_the_till(client):
    a = client.academy
    pid = _product(client, price=350.0, stock=5)
    sale = client.post(f"/api/products/{pid}/sell",
                       json={"client_id": a.dual, "qty": 2}).json()
    before = client.get("/api/dashboard").json()["stats"]["day_income"]

    client.delete(f"/api/product-sales/{sale['sale_id']}")

    after = client.get("/api/dashboard").json()["stats"]["day_income"]
    assert before - after == 700.0
