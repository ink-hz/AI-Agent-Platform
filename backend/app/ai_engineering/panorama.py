"""Compatibility facade for the original panorama imports."""

import hashlib
import json

from .exports import HEIGHT, WIDTH
from .exports import render_png as _png
from .exports import render_svg as _svg
from .seed import PANORAMA_SEED

__all__ = ["HEIGHT", "PANORAMA", "WIDTH", "content_hash", "render_png", "render_svg"]

PANORAMA = PANORAMA_SEED


def content_hash():
    return hashlib.sha256(
        json.dumps(
            PANORAMA, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()


def render_svg(data=None):
    return _svg(data or PANORAMA)


def render_png(data=None):
    return _png(data or PANORAMA)
