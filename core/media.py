"""Helpers for photos and video that staff upload.

iPhones save photos as HEIC. Windows computers and most browsers cannot show
those, so we turn them into ordinary JPEGs the moment they are uploaded.
"""
import io
import os

from django.core.files.base import ContentFile

# Pillow is imported only when a HEIC photo arrives, so a missing package can never
# stop the whole site from starting.
HEIF_READY = False

HEIF_EXTENSIONS = (".heic", ".heif")


def prepare_upload(f):
    """Return (file_to_save, None), or (None, reason) if the file cannot be used.

    Everything except HEIC/HEIF passes through untouched. HEIC becomes a JPEG
    (same pixels, right way up) with a .jpg name.
    """
    ext = os.path.splitext(f.name)[1].lower()
    if ext not in HEIF_EXTENSIONS:
        return f, None
    global HEIF_READY
    try:
        from PIL import Image, ImageOps

        try:  # lets Pillow read HEIC/HEIF
            from pillow_heif import register_heif_opener

            register_heif_opener()
            HEIF_READY = True
        except ImportError:
            HEIF_READY = False
        img = Image.open(f)
        img = ImageOps.exif_transpose(img)
        if img.mode != "RGB":
            img = img.convert("RGB")
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=90, optimize=True)
    except Exception:
        if not HEIF_READY:
            return None, "HEIC support is not installed on the server (add pillow and pillow-heif to requirements.txt)"
        return None, "could not read this photo"
    base = os.path.splitext(os.path.basename(f.name))[0] or "photo"
    return ContentFile(buf.getvalue(), name=f"{base}.jpg"), None
