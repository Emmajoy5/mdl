"""Store uploaded photos in less space without a visible loss.

Phone cameras save large files: 3 to 6 MB for a 12 megapixel photo and 15 MB or
more for 48 or 50 megapixels. A plate or a Gram stain needs far less. compact()
does three things to a photo before it is stored:

1. Turns it the right way up. Phones often save the picture sideways with a
   note saying which way to turn it. Browsers follow the note, the analysis did
   not, so a painted area or a background marker could land in the wrong place.
   The pixels are turned once, here, and the note is dropped.
2. Caps the long side at MAX_SIDE pixels. At 4096 pixels a 90 mm plate is
   about 45 pixels per millimetre, far finer than the analysis (which works on
   a 1024 pixel crop) or the eye on a screen needs.
3. Saves it as a JPEG at quality 90 with full colour resolution (4:4:4). The
   usual shortcut of halving the colour resolution (4:2:0) is not used: it
   blurs the colour at the edges of small colonies and changed the colony
   coverage the analysis measured. The colour profile is kept so colours stay
   true; everything else in the file's metadata, including the phone's GPS
   location, is removed.

Photos that are already small, the right way up and carry no location are left
exactly as they are. A photo with transparency stays a PNG. A lab can turn all
of this off (Admin, "Photo storage") to keep every file as uploaded.
"""
import io

try:
    from PIL import Image, ImageOps
except ImportError:  # pragma: no cover - Pillow is a hard dependency of the app
    Image = ImageOps = None

MAX_SIDE = 4096               # pixels, long side
JPEG_QUALITY = 90
SHRINK_ABOVE_BYTES = 1_000_000  # files under this are kept unless they need turning
MIN_SAVING = 0.85             # replace a file only if the new one is under 85% of it
ORIENTATION_TAG = 274
GPS_TAG = 34853


def compact(data, ext):
    """Return (stored_bytes, stored_ext, info) for an uploaded picture.

    data is the file as uploaded and ext its lower-case extension (".jpg").
    info says what was done: changed, turned, resized, had_location,
    original_bytes, stored_bytes, original_size and stored_size (w, h).
    Raises nothing for a readable image; the caller checks it is one first."""
    im = Image.open(io.BytesIO(data))
    im.load()
    original_size = im.size
    try:
        exif = im.getexif()
    except Exception:
        exif = {}
    orientation = exif.get(ORIENTATION_TAG, 1) or 1
    had_location = GPS_TAG in exif
    icc = im.info.get("icc_profile")
    alpha = im.mode in ("RGBA", "LA", "PA") or (im.mode == "P" and "transparency" in im.info)

    info = dict(changed=False, turned=orientation not in (1, None), resized=False,
                had_location=had_location, original_bytes=len(data), stored_bytes=len(data),
                original_size=original_size, stored_size=original_size)
    big = len(data) > SHRINK_ABOVE_BYTES or max(im.size) > MAX_SIDE
    if not (big or info["turned"] or had_location):
        return data, ext, info

    im = ImageOps.exif_transpose(im) if info["turned"] else im
    if max(im.size) > MAX_SIDE:
        im = im.copy()
        im.thumbnail((MAX_SIDE, MAX_SIDE), Image.LANCZOS)
        info["resized"] = True

    out = io.BytesIO()
    if alpha:
        im.save(out, format="PNG", optimize=True)
        new_ext = ".png"
    else:
        if im.mode not in ("RGB", "L"):
            im = im.convert("RGB")
        kw = dict(format="JPEG", quality=JPEG_QUALITY, subsampling=0, optimize=True, progressive=True)
        if icc:
            kw["icc_profile"] = icc
        im.save(out, **kw)
        new_ext = ".jpg"
    new = out.getvalue()
    # A file that only needed shrinking is replaced only for a real saving, so a
    # photo that is already compact is never encoded again (each JPEG pass loses
    # a little).
    if len(new) > MIN_SAVING * len(data) and not (info["turned"] or had_location or info["resized"]):
        return data, ext, info
    info.update(changed=True, stored_bytes=len(new), stored_size=im.size)
    return new, new_ext, info


def describe(info):
    """A short note for the page: 'Saved 4.5 MB as 1.6 MB, turned upright.'"""
    if not info or not info.get("changed"):
        return ""
    mb = lambda b: f"{b / 1e6:.1f} MB" if b >= 100_000 else f"{b / 1e3:.0f} KB"
    bits = [f"{mb(info['original_bytes'])} stored as {mb(info['stored_bytes'])}"]
    if info.get("turned"):
        bits.append("turned upright")
    if info.get("had_location"):
        bits.append("location removed")
    return ", ".join(bits)
