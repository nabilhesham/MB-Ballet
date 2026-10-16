"""
/api/products/* — the shop.

The second thing money arrives for, and until there was a products table it
was money the dashboard's till figure could not see. A product is the
offering; a `product_sales` row is one purchase by one client, and it is
deliberately built out of the same pieces a plan is — see access.sell_product()
and the shop tables in db.py.
"""

import os
from typing import Optional

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel

import access
import db
import images
import repo as data


router = APIRouter()


class ProductIn(BaseModel):
    name: str
    price: Optional[float] = None
    stock: int = 0
    notes: Optional[str] = None


class SaleIn(BaseModel):
    client_id: int
    qty: int = 1
    # Left out entirely means today — a sale over the counter is paid as it
    # happens. An empty string is unpaid, and falls out of every day's till.
    # See access.sell_product().
    paid_on: Optional[str] = None
    notes: Optional[str] = None


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
    if body.stock < 0:
        raise HTTPException(400, "Stock cannot be negative. Zero is "
                                 "'none left', which is a real answer.")


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
        return repo.find("products",
                         {"active": 0 if status == "archived" else 1},
                         sort=[("name", 1)])
    finally:
        repo.close()


@router.post("/api/products")
def create_product(body: ProductIn):
    _checked(body)
    repo = data.connect()
    try:
        pid = repo.insert("products", {
            "name": body.name.strip(), "price": body.price,
            "stock": body.stock, "notes": body.notes,
            "photo_path": None, "active": 1, "created_at": db.now()})
        return {"id": pid}
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
        if not repo.update("products", pid, {
                "name": body.name.strip(), "price": body.price,
                "stock": body.stock, "notes": body.notes}):
            raise HTTPException(404, "no such product")
        return {"ok": True}
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
                                paid_on=body.paid_on, notes=body.notes)
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
