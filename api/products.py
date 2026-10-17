"""
/api/products/* — the shop.

The second thing money arrives for, and until there was a products table it
was money the dashboard's till figure could not see. A product is the
offering; a `product_sales` row is one purchase by one client, and it is
deliberately built out of the same pieces a plan is — see access.sell_product()
and the shop tables in db.py.
"""

import os
from datetime import date
from typing import List, Optional

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel

import access
import db
import images
import repo as data


router = APIRouter()


class SizeIn(BaseModel):
    # None is "this product has no sizes" — the same NULL product_stock.size
    # carries. REAL, not int: shoe sizes come in halves. See
    # access.set_product_sizes().
    size: Optional[float] = None
    qty: int = 0


class ProductIn(BaseModel):
    name: str
    price: Optional[float] = None
    notes: Optional[str] = None
    # How many of each size are on the shelf. `products.stock` is the sum of
    # these and has exactly one writer (access._sync_stock) -- see the
    # products table in db.py. Left out entirely means "leave the shelf
    # alone", which is what an edit that only renames or re-prices wants;
    # `[]` means "no sizes", which is a real answer and the one a hairnet
    # gives.
    sizes: Optional[List[SizeIn]] = None
    # Shorthand for a product with no sizes: `stock: 20` is exactly
    # `sizes: [{"size": null, "qty": 20}]`, which is what a hairnet would
    # otherwise have to be written as. Not a second source of truth -- it is
    # translated in `_sizes()` before anything is written, so there is still
    # one writer -- and giving both at once is refused rather than one of
    # them quietly winning.
    stock: Optional[int] = None


class SaleIn(BaseModel):
    client_id: int
    qty: int = 1
    # Which shelf it came off. None is "no size", which is the only thing a
    # sizeless product has; a product that does come in sizes refuses a sale
    # that does not name one, rather than guessing which shelf to take it
    # off. See access.sell_product().
    size: Optional[float] = None
    # Left out entirely means today — a sale over the counter is paid as it
    # happens. An empty string is unpaid, and falls out of every day's till.
    # See access.sell_product().
    paid_on: Optional[str] = None
    notes: Optional[str] = None


def _sizes(body: ProductIn):
    """
    The sizes as access.set_product_sizes() wants them: plain dicts, and
    `[]` for a product with no sizes (which it turns into the one NULL row).

    The `stock` shorthand is resolved here and nowhere else, which is what
    keeps it from being a second source of truth.
    """
    if body.sizes is not None:
        return [{"size": s.size, "qty": s.qty} for s in body.sizes]
    if body.stock is not None:
        return [{"size": None, "qty": body.stock}]
    return []


def _checked(body: ProductIn) -> None:
    """
    The single copy of the refusals, so creating and editing cannot drift —
    the same split `api/appointments.py` uses, and for the same reason:
    refusing something at creation and allowing it a minute later leaves the
    state the refusal exists to prevent.
    """
    if not (body.name or "").strip():
        raise HTTPException(400, "A product needs a name.")
    if body.price is not None and body.price < 0:
        raise HTTPException(400, "A price cannot be negative.")
    if body.sizes is not None and body.stock is not None:
        raise HTTPException(400, "Give the sizes or a plain stock figure, "
                                 "not both — a plain figure is the same "
                                 "thing as one row with no size.")
    # The quantities and the sizes themselves are checked in
    # access.set_product_sizes(), which is the single copy of those refusals
    # for the same reason this function is the single copy of these: the
    # desk's own paths reach it without coming through here.


@router.get("/api/products")
def list_products(status: str = None):
    """
    The shop list. `status=archived` reads the other half, exactly as the
    three other archived lists do.

    **The search box is client-side here, unlike the Clients list's.** The
    academy sells a dozen things, so the whole list arrives in one round
    trip and a server-side `q` would be a second source of truth filtering a
    different set of rows from the one on screen — which is the exact
    reasoning that gave the Clients list the *opposite* answer: that one
    searches 254 names through `/api/clients?q=`, so its box has to be the
    server's.
    """
    repo = data.connect()
    try:
        rows = repo.find("products",
                         {"active": 0 if status == "archived" else 1},
                         sort=[("name", 1)])
        # The shelf, for the whole list in **one** more round trip rather
        # than one per product. The sell modal picks a product and then needs
        # its sizes immediately, so sending them with the list is what keeps
        # that a choice rather than a fetch. `products.stock` is still the
        # total on the card -- see db.py.
        shelves = {}
        for sh in repo.find("product_stock"):
            shelves.setdefault(sh["product_id"], []).append(sh)
        for r in rows:
            r["sizes"] = sorted(
                shelves.get(r["id"], []),
                key=lambda x: (x["size"] is not None, x["size"] or 0, x["id"]))
        return rows
    finally:
        repo.close()


@router.get("/api/products/history")
def purchase_history(start: str = None, end: str = None):
    """
    Everything sold in a date range, named: the product, the size, who
    bought it, when, and what it came to.

    **Declared above `/api/products/{pid}` on purpose.** That route takes an
    `int`, so FastAPI would match this path against it first and answer a
    422 about a path parameter -- a route that exists and cannot be reached,
    which is the kind of thing that looks like a frontend bug for an hour.

    **Dated by `paid_on`, the same column TAKEN TODAY reads**, so a day in
    this list and a day in the till are the same day's money. The cost,
    which the screen says rather than hiding: a sale nobody has dated yet
    has no day to fall in and is in no range at all. It is still on the
    client's profile and on the product's own page, which is where an
    unpaid sale is chased from.

    Both bounds are **inclusive**, because that is what a receptionist
    typing two dates into a form means by "between" -- the same rule the
    Clients list's joining range follows. `end` is pushed to the day after
    for the half-open window the port wants.
    """
    for value in (start, end):
        if value is None:
            continue
        try:
            date.fromisoformat(value)
        except (TypeError, ValueError):
            raise HTTPException(400, "Pick a day, or clear the date filter.")

    repo = data.connect()
    try:
        # No bound given is the whole history rather than nothing: a range
        # with one end open is a real question ("everything since September"),
        # and the ISO strings sort, so the extremes are plain sentinels.
        rows = repo.purchases_between(start or "0000-01-01",
                                      access.next_day(end) if end
                                      else "9999-12-31")
        return {"start": start, "end": end, "sales": rows,
                "items": sum(r["qty"] or 0 for r in rows),
                "paid": round(sum((r["qty"] or 0) * (r["unit_price"] or 0)
                                  for r in rows), 2),
                # Counted, never folded in as zero -- the same treatment an
                # unpriced plan gets on the dashboard.
                "unpriced": sum(1 for r in rows if r["unit_price"] is None)}
    finally:
        repo.close()


@router.post("/api/products")
def create_product(body: ProductIn):
    """
    A new product, with its shelf.

    **Even a product with no sizes gets a shelf row**, the single NULL one
    that means "no sizes" — so there is never a product the rest of the app
    cannot sell, and never a second code path for the sizeless case. See
    access.set_product_sizes().
    """
    _checked(body)
    repo = data.connect()
    try:
        with repo.begin():
            pid = repo.insert("products", {
                "name": body.name.strip(), "price": body.price,
                "stock": 0, "notes": body.notes,
                "photo_path": None, "active": 1, "created_at": db.now()})
            r = access.set_product_sizes(repo, pid, _sizes(body))
            if not r["ok"]:
                raise HTTPException(400, r["error"])
        return {"id": pid, "stock": r["stock"], "sizes": r["sizes"]}
    finally:
        repo.close()


@router.get("/api/products/{pid}")
def get_product(pid: int):
    """One product with everyone who has bought it — the page's whole
    payload in two round trips."""
    repo = data.connect()
    try:
        p = repo.get("products", pid)
        if p is None:
            raise HTTPException(404, "no such product")
        p["sales"] = repo.product_buyers(pid)
        p["sold"] = sum(s["qty"] or 0 for s in p["sales"])
        p["sizes"] = access.stock_rows(repo, pid)
        return p
    finally:
        repo.close()


@router.put("/api/products/{pid}")
def update_product(pid: int, body: ProductIn):
    """
    Name, price, stock and notes.

    **Changing the price does not touch a sale already written down.** Each
    sale copied the price at the moment it happened, which is what keeps a
    past day's till honest — see access.sell_product().
    """
    _checked(body)
    repo = data.connect()
    try:
        with repo.begin():
            if not repo.update("products", pid, {
                    "name": body.name.strip(), "price": body.price,
                    "notes": body.notes}):
                raise HTTPException(404, "no such product")
            # `sizes` left out means "leave the shelf alone" -- an edit that
            # only renames or re-prices should not have to restate what is in
            # the box. `[]` is a real answer and means "no sizes".
            if body.sizes is None and body.stock is None:
                return {"ok": True}
            r = access.set_product_sizes(repo, pid, _sizes(body))
            if not r["ok"]:
                raise HTTPException(400, r["error"])
        return {"ok": True, "stock": r["stock"], "sizes": r["sizes"],
                "kept": r["kept"]}
    finally:
        repo.close()


@router.post("/api/products/{pid}/photo")
async def upload_photo(pid: int, file: UploadFile = File(...)):
    """Stored in the database, exactly as a client's and an instructor's
    are — see images.py."""
    ext = os.path.splitext(file.filename or "")[1].lower() or ".jpg"
    if ext not in (".jpg", ".jpeg", ".png", ".webp"):
        raise HTTPException(400, "use jpg, png or webp")
    blob, mime = images.shrink(await file.read(),
                               file.content_type or "image/jpeg")
    repo = data.connect()
    try:
        now = db.now()
        with repo.begin():
            images.store(repo, images.PRODUCT_PHOTO, pid, blob, mime, now=now)
            url = images.url(images.PRODUCT_PHOTO, pid, stamp=now)
            repo.update("products", pid, {"photo_path": url})
        return {"photo_path": url}
    finally:
        repo.close()


@router.post("/api/products/{pid}/unarchive")
def unarchive_product(pid: int):
    repo = data.connect()
    try:
        if not repo.update("products", pid, {"active": 1}):
            raise HTTPException(404, "no such product")
        return {"ok": True}
    finally:
        repo.close()


@router.delete("/api/products/{pid}")
def archive_product(pid: int, hard: bool = False):
    repo = data.connect()
    try:
        r = access.archive_product(repo, pid, hard=hard)
        if not r["ok"]:
            raise HTTPException(409 if "sale" in r["error"] else 404,
                                r["error"])
        return r
    finally:
        repo.close()


@router.post("/api/products/{pid}/sell")
def sell(pid: int, body: SaleIn):
    """Sell one or more to a client. Refuses rather than going negative —
    the stock figure is what reception reads off the shelf."""
    repo = data.connect()
    try:
        r = access.sell_product(repo, pid, body.client_id, qty=body.qty,
                                paid_on=body.paid_on, notes=body.notes,
                                size=body.size)
        if not r["ok"]:
            raise HTTPException(400, r["error"])
        return r
    finally:
        repo.close()


@router.delete("/api/product-sales/{sale_id}")
def unsell(sale_id: int):
    """
    Remove a sale: the stock comes back and the money leaves that day's
    till. Not under /api/products/{pid} because the caller that uses it most
    is the client's own profile, which knows the sale and not the product.
    """
    repo = data.connect()
    try:
        r = access.unsell_product(repo, sale_id)
        if not r["ok"]:
            raise HTTPException(404, r["error"])
        return r
    finally:
        repo.close()
