"""
Sizes: a leotard comes in 34, 36 and 38, and a hairnet does not come in any.

The design under test is that those two are *one* case, not two. A product
with no sizes holds exactly one `product_stock` row whose `size` is NULL, so
a sale always names a size, the shelf count is always the sum of the rows,
and `products.stock` always has one writer. Most of what follows is about
the edges of that: a size that has been sold from, a quantity that goes to
zero, a sale put back on the shelf it came off.
"""

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


def _sized(client, sizes, name="Leotard", price=350.0):
    r = client.post("/api/products",
                    json={"name": name, "price": price, "sizes": sizes})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _shelf(client, pid):
    """The shelf as the product page shows it: {size or None: qty}."""
    return {s["size"]: s["qty"]
            for s in client.get(f"/api/products/{pid}").json()["sizes"]}


# --------------------------------------------------- the shelf

def test_a_sized_product_carries_a_row_per_size_and_their_total(client):
    pid = _sized(client, [{"size": 34, "qty": 2}, {"size": 36, "qty": 5},
                          {"size": 38, "qty": 0}])

    assert _shelf(client, pid) == {34.0: 2, 36.0: 5, 38.0: 0}
    assert client.get(f"/api/products/{pid}").json()["stock"] == 7, \
        "the card's figure is the sum of the shelf, written by one writer"


def test_a_product_with_no_sizes_still_has_a_shelf(client):
    """One row, size NULL. That uniformity is what keeps a hairnet and a
    leotard on the same code path."""
    pid = _sized(client, [])

    assert _shelf(client, pid) == {None: 0}


def test_a_plain_stock_figure_is_the_same_thing_as_one_sizeless_row(client):
    """The shorthand, resolved in one place so it cannot be a second source
    of truth."""
    r = client.post("/api/products", json={"name": "Hairnet", "stock": 20})
    pid = r.json()["id"]

    assert _shelf(client, pid) == {None: 20}
    assert client.get(f"/api/products/{pid}").json()["stock"] == 20


def test_giving_both_a_stock_figure_and_sizes_is_refused(client):
    r = client.post("/api/products", json={"name": "x", "stock": 3,
                                           "sizes": [{"size": 34, "qty": 1}]})
    assert r.status_code == 400
    assert "not both" in r.json()["detail"]


def test_sizes_are_numbers_and_halves_survive(client):
    """REAL, not INTEGER, for the same reason clients.age is: 34.5 rounded
    to 34 is the distinction the fitting was made on."""
    pid = _sized(client, [{"size": 34.5, "qty": 1}])

    assert _shelf(client, pid) == {34.5: 1}


def test_a_size_listed_twice_and_a_negative_amount_are_refused(client):
    assert client.post("/api/products", json={
        "name": "x", "sizes": [{"size": 34, "qty": 1},
                               {"size": 34, "qty": 2}]}).status_code == 400
    assert client.post("/api/products", json={
        "name": "x", "sizes": [{"size": 34, "qty": -1}]}).status_code == 400
    assert client.post("/api/products", json={
        "name": "x", "sizes": [{"size": 0, "qty": 1}]}).status_code == 400


def test_a_sizeless_row_beside_sizes_is_refused(client):
    """A product either comes in sizes or it does not. Both at once would
    leave a shelf nobody could describe at the counter."""
    r = client.post("/api/products", json={
        "name": "x", "sizes": [{"size": None, "qty": 1},
                               {"size": 34, "qty": 1}]})
    assert r.status_code == 400


# --------------------------------------------------- editing the shelf

def test_editing_adds_removes_and_restocks_sizes(client):
    pid = _sized(client, [{"size": 34, "qty": 2}, {"size": 36, "qty": 5}])

    r = client.put(f"/api/products/{pid}",
                   json={"name": "Leotard", "price": 350.0,
                         "sizes": [{"size": 36, "qty": 1},
                                   {"size": 40, "qty": 4}]})
    assert r.status_code == 200, r.text

    assert _shelf(client, pid) == {36.0: 1, 40.0: 4}
    assert client.get(f"/api/products/{pid}").json()["stock"] == 5


def test_an_edit_that_leaves_sizes_out_leaves_the_shelf_alone(client):
    """Renaming or re-pricing should not have to restate what is in the
    box."""
    pid = _sized(client, [{"size": 34, "qty": 2}])

    client.put(f"/api/products/{pid}", json={"name": "Leotard", "price": 500.0})

    p = client.get(f"/api/products/{pid}").json()
    assert (p["price"], p["stock"]) == (500.0, 2)
    assert _shelf(client, pid) == {34.0: 2}


def test_a_size_with_sales_behind_it_is_kept_at_zero_and_named(client):
    """Removing it would leave a sale pointing at a shelf that is not there,
    and unselling would have nowhere to put the thing back."""
    a = client.academy
    pid = _sized(client, [{"size": 34, "qty": 2}, {"size": 36, "qty": 2}])
    assert client.post(f"/api/products/{pid}/sell",
                       json={"client_id": a.dual, "size": 34}).status_code == 200

    r = client.put(f"/api/products/{pid}",
                   json={"name": "Leotard", "price": 350.0,
                         "sizes": [{"size": 36, "qty": 2}]})
    assert r.status_code == 200, r.text

    assert _shelf(client, pid) == {34.0: 0, 36.0: 2}
    assert r.json()["kept"] and "34" in r.json()["kept"][0]
    assert client.get(f"/api/products/{pid}").json()["stock"] == 2


def test_a_size_with_no_sales_is_removed_outright(client):
    pid = _sized(client, [{"size": 34, "qty": 2}, {"size": 36, "qty": 2}])

    r = client.put(f"/api/products/{pid}",
                   json={"name": "Leotard", "price": 350.0,
                         "sizes": [{"size": 36, "qty": 2}]})

    assert _shelf(client, pid) == {36.0: 2}
    assert r.json()["kept"] == []


def test_a_quantity_below_what_has_been_sold_is_allowed(client):
    """Stock is what is in the box now; a sale is a receipt for something
    that left it. Four in the box after selling six is a fact."""
    a = client.academy
    pid = _sized(client, [{"size": 34, "qty": 6}])
    client.post(f"/api/products/{pid}/sell",
                json={"client_id": a.dual, "size": 34, "qty": 6})

    r = client.put(f"/api/products/{pid}",
                   json={"name": "Leotard", "price": 350.0,
                         "sizes": [{"size": 34, "qty": 4}]})

    assert r.status_code == 200, r.text
    assert _shelf(client, pid) == {34.0: 4}


def test_a_product_can_gain_sizes_and_lose_them_again(client):
    pid = _sized(client, [])

    client.put(f"/api/products/{pid}",
               json={"name": "Leotard", "sizes": [{"size": 34, "qty": 3}]})
    assert _shelf(client, pid) == {34.0: 3}

    client.put(f"/api/products/{pid}", json={"name": "Leotard", "sizes": []})
    assert _shelf(client, pid) == {None: 0}


# --------------------------------------------------- selling a size

def test_selling_a_size_comes_off_that_size_only(client):
    a = client.academy
    pid = _sized(client, [{"size": 34, "qty": 2}, {"size": 36, "qty": 5}])

    r = client.post(f"/api/products/{pid}/sell",
                    json={"client_id": a.dual, "qty": 2, "size": 36})
    assert r.status_code == 200, r.text

    assert _shelf(client, pid) == {34.0: 2, 36.0: 3}
    assert r.json()["stock"] == 5
    assert r.json()["size_left"] == 3


def test_a_sale_that_names_no_size_is_refused_with_the_sizes_in_it(client):
    """Guessing would take it off the wrong shelf, and the receptionist is
    the one holding the garment."""
    a = client.academy
    pid = _sized(client, [{"size": 34, "qty": 2}, {"size": 36, "qty": 5}])

    r = client.post(f"/api/products/{pid}/sell", json={"client_id": a.dual})

    assert r.status_code == 400
    assert "which size" in r.json()["detail"]
    assert "34, 36" in r.json()["detail"]


def test_a_size_the_product_does_not_come_in_is_refused(client):
    a = client.academy
    pid = _sized(client, [{"size": 34, "qty": 2}])

    r = client.post(f"/api/products/{pid}/sell",
                    json={"client_id": a.dual, "size": 99})

    assert r.status_code == 400
    assert "does not come in size 99" in r.json()["detail"]


def test_more_than_that_size_holds_is_refused_even_when_others_have_some(client):
    """The old refusal counted the whole shelf, which would have sold a 34
    out of the 36 pile."""
    a = client.academy
    pid = _sized(client, [{"size": 34, "qty": 1}, {"size": 36, "qty": 9}])

    r = client.post(f"/api/products/{pid}/sell",
                    json={"client_id": a.dual, "qty": 3, "size": 34})

    assert r.status_code == 400
    assert "only 1 Leotard in 34 left" in r.json()["detail"]


def test_a_sizeless_product_sells_with_no_size_named(client):
    a = client.academy
    pid = _sized(client, [], name="Hairnet", price=20.0)
    client.put(f"/api/products/{pid}",
               json={"name": "Hairnet", "price": 20.0, "stock": 10})

    r = client.post(f"/api/products/{pid}/sell",
                    json={"client_id": a.dual, "qty": 2})

    assert r.status_code == 200, r.text
    assert _shelf(client, pid) == {None: 8}


# --------------------------------------------------- putting it back

def test_removing_a_sale_puts_it_back_on_the_size_it_came_off(client):
    a = client.academy
    pid = _sized(client, [{"size": 34, "qty": 2}, {"size": 36, "qty": 5}])
    sale = client.post(f"/api/products/{pid}/sell",
                       json={"client_id": a.dual, "qty": 2,
                             "size": 36}).json()["sale_id"]

    assert client.delete(f"/api/product-sales/{sale}").status_code == 200

    assert _shelf(client, pid) == {34.0: 2, 36.0: 5}


def test_the_sale_records_its_size_so_a_re_price_cannot_rewrite_it(client):
    """Like unit_price, the size on a sale is a receipt. Changing which
    sizes the product comes in next term must not rewrite what was handed
    over last month."""
    a = client.academy
    pid = _sized(client, [{"size": 34, "qty": 2}])
    client.post(f"/api/products/{pid}/sell",
                json={"client_id": a.dual, "size": 34})

    client.put(f"/api/products/{pid}",
               json={"name": "Leotard", "sizes": [{"size": 40, "qty": 1}]})

    bought = client.get(f"/api/clients/{a.dual}").json()["purchases"]
    assert [b["size"] for b in bought if b["product_id"] == pid] == [34.0]


def test_a_sale_whose_size_has_gone_comes_back_as_its_own_row(client):
    """set_product_sizes() keeps a sold-from size, so this is a shelf
    removed before that rule existed. Inventing the number onto another size
    would be worse than a size nobody stocks showing one in it."""
    a = client.academy
    pid = _sized(client, [{"size": 34, "qty": 1}, {"size": 36, "qty": 1}])
    sale = client.post(f"/api/products/{pid}/sell",
                       json={"client_id": a.dual, "size": 34}).json()["sale_id"]
    repo = a.repo
    gone = [r for r in access.stock_rows(repo, pid) if r["size"] == 34]
    repo.delete("product_stock", gone[0]["id"])

    assert client.delete(f"/api/product-sales/{sale}").status_code == 200

    assert _shelf(client, pid) == {34.0: 1, 36.0: 1}


# --------------------------------------------------- the upgrade

def test_a_product_from_before_sizes_is_given_a_shelf_at_startup(academy):
    """A startup step rather than a db.migrate() one: migrate() only runs
    against SQLite and the academy's database may be on Atlas. Idempotent by
    construction, the same shape images.import_legacy_files() has."""
    repo = academy.repo
    pid = repo.insert("products", {"name": "Old shoes", "price": 200.0,
                                   "stock": 7, "notes": None,
                                   "photo_path": None, "active": 1,
                                   "created_at": db.now()})

    assert access.ensure_stock_rows(repo) == 1
    rows = access.stock_rows(repo, pid)
    assert [(r["size"], r["qty"]) for r in rows] == [(None, 7)]

    assert access.ensure_stock_rows(repo) == 0, "it must find nothing twice"


def test_a_hard_delete_takes_the_shelf_with_it(client):
    """The rows are not history -- they are how many of a thing that no
    longer exists are in a box."""
    pid = _sized(client, [{"size": 34, "qty": 2}])
    repo = client.academy.repo

    assert client.delete(f"/api/products/{pid}?hard=true").status_code == 200

    assert repo.find("product_stock", {"product_id": pid}) == []
