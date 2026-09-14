"""/api/images/* — the bytes behind every `<img src>` in the app."""

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

import images
import repo as data

router = APIRouter()


@router.get("/api/images/{kind}/{owner_id}")
def get_image(kind: str, owner_id: int, variant: str = ""):
    """
    One stored image, decoded.

    404 rather than a placeholder when there is none: `<img>` with no src is
    what the UI already handles — Avatar falls back to initials, the kiosk to
    a monogram — and inventing a grey square here would take that decision
    away from the screen that knows what fits.
    """
    if kind not in (images.CLIENT_PHOTO, images.INSTRUCTOR_PHOTO, images.CARD):
        raise HTTPException(404, "no such image kind")
    repo = data.connect()
    try:
        blob, mime = images.load(repo, kind, owner_id, variant)
    finally:
        repo.close()
    if blob is None:
        raise HTTPException(404, "no image")
    # The ?v= stamp in the URL already changes when the bytes do, so this
    # could be cached hard. It is not, because server.py's cache_policy says
    # one thing for everything that is not a hashed asset and a second rule
    # here would be a second thing to keep true. Over localhost it costs
    # nothing; see CLAUDE.md.
    return Response(content=blob, media_type=mime)
