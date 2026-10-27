"""Small Pillow helpers that stay valid on Pillow 10+."""

from __future__ import annotations

from typing import Any, Optional, Tuple


def text_size(draw: Any, text: str, font: Optional[Any] = None) -> Tuple[int, int]:
    """Return ``(width, height)`` of ``text``.

    ``ImageDraw.textsize`` was removed in Pillow 10. ``textbbox`` is the
    replacement and is available on every Pillow this project supports.
    """
    kwargs = {"font": font} if font is not None else {}
    left, top, right, bottom = draw.textbbox((0, 0), text, **kwargs)
    return right - left, bottom - top


def lanczos():
    """The high-quality downscale filter formerly called ``Image.Resampling.LANCZOS``."""
    from PIL import Image

    resampling = getattr(Image, "Resampling", None)
    if resampling is not None:
        return resampling.LANCZOS
    return Image.LANCZOS
