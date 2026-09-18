"""
Writing the joined contact-sheet scan as a linear DNG.

What the file is
----------------
The sections stacked top to bottom, in scan order, pixel for pixel. Nothing is
scaled, blended, aligned, feathered or colour-matched at the seams — the row
below the last row of section one is the first row of section two, unchanged.
That is the whole promise of this tool, and `verify_pixels` reads the finished
file back to check it was kept.

Everything else in the file is metadata saying what those numbers are.

Why a DNG and not a TIFF
------------------------
The sections arrive as linear DNG and the join is still raw data: scene-linear,
bounded by a black and a white level, with no white balance applied and no tone
curve baked in. Written as a TIFF that claim has nowhere to live, and every
converter treats the result as an already-rendered image — no raw white balance,
no exposure in stops ahead of the curve, and for a negative that means grading
the orange mask without the controls built for it.

`PhotometricInterpretation = 34892` (LinearRaw), three samples per pixel, sends
the file through the raw pipeline instead, where it belongs. This mirrors what
trichrome writes, for the same reason.

What is carried and what is added
---------------------------------
The policy is one sentence: **carry through everything the source states about
its own colour, and supply the linear-declaration defaults only for what it
omits.**

The sections already came from a real device with a real colour spec —
VueScan's DNGs carry ColorMatrix1, AsShotWhiteXY and UniqueCameraModel — and
stacking them changes none of it. So scan.py harvests those tags and this module
re-emits them untouched. That is the difference between this writer and
trichrome's: trichrome MUST fabricate a colour spec, because a three-light merge
is not colorimetric and no honest matrix exists for it. Here one exists, and the
scanner wrote it down.

What VueScan does not write, this module adds, because their absence is not
neutral — it is an invitation for the converter to supply its own:

* `BlackLevel = 0` and `WhiteLevel = 65535`. VueScan states the range as
  MinSampleValue/MaxSampleValue, which is a TIFF tag a DNG reader does not
  consult. Without the DNG tags a reader falls back to its own guess at the
  sensor's pedestal.
* `ProfileToneCurve` as the identity, (0, 0) -> (1, 1). Given a profile with no
  curve of its own, Adobe's SDK — and everything modelled on it — renders
  through its default: a contrasty S built for camera sensor data, applied here
  to a scan that has no business being toned. Declaring the identity leaves the
  slot filled so there is no default to fall back to.
* `DefaultBlackRender = None`. Left at Auto, a converter subtracts its own
  estimate of a black point. On a negative that estimate is dominated by the
  orange mask, so it is a grade being made for you out of the mask density.

`DNGVersion` is raised to 1.4.0.0 because `DefaultBlackRender` is a 1.4 tag,
while `DNGBackwardVersion` stays at 1.2.0.0: a 1.2 reader that has never heard
of that tag skips it and renders as it always would, which is a different
default and not a failure to read the file.

Only if the source states no colour matrix at all does this module fall back to
a fabricated one — the sRGB/Rec.709 primaries, with AsShotNeutral at (1, 1, 1).
That is a placeholder to grade from, not a measurement, and it is a last resort
rather than the normal path.

Compression and size
--------------------
DNG's lossless choices are uncompressed and lossless JPEG; ZIP/deflate is not
among them for 16-bit integer data, and libraw rejects such a file outright. So
this writes uncompressed, and the image is exactly `width x height x 6` bytes.

Uncompressed also means a joined sheet gets large, and a classic TIFF addresses
its data with 32-bit offsets. BigTIFF would lift that ceiling but is not valid
DNG, so a join that would not fit is refused before anything is written, with
the arithmetic in the message.

Memory
------
The sections are never held together in memory. `write_joined_dng` takes
array-likes — scan.py hands it read-only memory maps — and feeds tifffile one
strip at a time, so joining ten sections costs one strip, not ten gigabytes.
The thumbnail is built from a strided read of the same maps, touching about a
fiftieth of each file.

Layout
------
As the spec prescribes, and as trichrome writes: IFD0 holds a small sRGB-encoded
thumbnail (preview only — the one place a gamma is applied, and it touches no
image data), and the full-resolution linear image lives in a SubIFD.
"""
import os
from xml.sax.saxutils import escape as xml_escape
from typing import List, Optional, Sequence, Tuple

import numpy as np
import tifffile

from .scan import PHOTOMETRIC_LINEAR_RAW, SourceProfile, open_plane

# Extension given to every file this tool writes.
OUTPUT_EXTENSION = ".dng"

# Stamped into Software so a joined file can be recognised as one — by this
# tool, when a folder is re-scanned, and by a person reading the metadata.
JOIN_MARKER = "JoinContactScans:vertical-join-linear-v1"

# DNG tag numbers used below, named so the extratags lists stay readable.
_TAG_XMP = 700
_TAG_DNG_VERSION = 50706
_TAG_DNG_BACKWARD_VERSION = 50707
_TAG_UNIQUE_CAMERA_MODEL = 50708
_TAG_BLACK_LEVEL_REPEAT_DIM = 50713
_TAG_BLACK_LEVEL = 50714
_TAG_WHITE_LEVEL = 50717
_TAG_COLOR_MATRIX_1 = 50721
_TAG_COLOR_MATRIX_2 = 50722
_TAG_AS_SHOT_NEUTRAL = 50728
_TAG_AS_SHOT_WHITE_XY = 50729
_TAG_CALIBRATION_ILLUMINANT_1 = 50778
_TAG_ORIGINAL_RAW_FILE_NAME = 50827
_TAG_PROFILE_TONE_CURVE = 50940
_TAG_FORWARD_MATRIX_1 = 50964
_TAG_DEFAULT_BLACK_RENDER = 51110

# CalibrationIlluminant code 21 is D65 — the white the fallback ColorMatrix1 is
# stated under, and the white sRGB primaries are defined against.
_ILLUMINANT_D65 = 21

# DefaultBlackRender code 1 is None; code 0, the default, is Auto.
_BLACK_RENDER_NONE = 1

# ProfileToneCurve as (in, out) pairs of 32-bit floats: the identity. Two
# control points is how the spec says "identity" — the first sample must be
# (0, 0) and the last (1, 1), and a cubic spline through nothing else is the
# straight line between them.
IDENTITY_TONE_CURVE = (0.0, 0.0, 1.0, 1.0)

# Fallback colour spec, used ONLY when the source states no matrix of its own.
# These are the standard sRGB/Rec.709 matrices, not a measurement of anything:
# ColorMatrix1 is XYZ(D65) -> linear sRGB, stated under D65 because that is the
# white sRGB primaries are defined against, and ForwardMatrix1 is linear sRGB ->
# XYZ(D50), Bradford-adapted because a forward matrix's output is D50 by
# definition (it feeds the profile connection space).
#
# The two are NOT inverses, and must not be: the spec defines them against
# different white points. ColorMatrix1 takes D65's XYZ onto (1, 1, 1), agreeing
# with AsShotNeutral; ForwardMatrix1 takes (1, 1, 1) onto D50's XYZ.
_SRGB_COLOR_MATRIX_1 = (
    3.2404542, -1.5371385, -0.4985314,
    -0.9692660, 1.8760108, 0.0415560,
    0.0556434, -0.2040259, 1.0572252,
)
_SRGB_FORWARD_MATRIX_1 = (
    0.4360747, 0.3850649, 0.1430804,
    0.2225045, 0.7168786, 0.0606169,
    0.0139322, 0.0971045, 0.7141733,
)

# What a file calls the "camera" that produced it when the source did not say.
# DNG requires the tag, and a name no profile database knows sends every reader
# to the embedded matrices, which is where the truth about such a file is.
FALLBACK_CAMERA_MODEL = "Joined contact sheet scan"

# Denominator for RATIONAL-encoded matrices: six decimal places, far finer than
# the matrices are meaningful to.
_RATIONAL_DEN = 1000000

# Longest edge of the embedded thumbnail, in pixels.
_THUMBNAIL_MAX_EDGE = 256

# Roughly how much image data to hand tifffile at a time. Big enough that the
# per-strip overhead disappears, small enough that peak memory does not depend
# on how tall the sheet is.
_STRIP_TARGET_BYTES = 4 << 20

# A classic TIFF addresses its data with 32-bit offsets. The margin covers the
# header, the thumbnail and the strip offset/bytecount tables, which for a tall
# uncompressed image run to a few hundred kilobytes.
_MAX_TIFF_BYTES = (1 << 32) - (16 << 20)


class JoinError(IOError):
    """Writing or verifying the joined DNG failed, with a message for a
    person."""


def _rational(values, signed: bool = True) -> Tuple[int, ...]:
    """Flatten a matrix into the (numerator, denominator, ...) pairs a
    RATIONAL/SRATIONAL tag is written as."""
    out: List[int] = []
    for v in np.asarray(values, dtype=float).reshape(-1):
        out.extend((int(round(v * _RATIONAL_DEN)), _RATIONAL_DEN))
    return tuple(out)


def thumbnail_step(width: int, height: int) -> int:
    """The row/column stride that brings `height x width` under the thumbnail's
    longest-edge limit.

    Ceiling division: flooring would leave a step that still overshoots
    (800 // 256 = 3, and 800/3 is 267 pixels)."""
    longest = max(int(width), int(height))
    return max(1, -(-longest // _THUMBNAIL_MAX_EDGE))


def build_thumbnail(planes: Sequence, step: int) -> np.ndarray:
    """A small 8-bit sRGB-encoded preview of the joined image, for IFD0.

    Built without ever forming the joined image. A thumbnail pixel is a sample
    of the join on a regular grid of stride `step`, so for each section this
    takes the rows whose position IN THE JOIN falls on that grid — which is not
    row 0 of every section, but `(-offset) % step`. Getting that wrong would
    not corrupt anything, it would just make the preview's seams land a few
    rows off; doing it right costs one modulo.

    This is the ONLY place in the tool where a transfer curve is applied, and it
    exists so file browsers and the converter's import grid show something
    recognisable instead of the dark linear data. It never touches the image in
    the SubIFD."""
    pieces, offset = [], 0
    for plane in planes:
        start = (-offset) % step
        if start < plane.shape[0]:
            pieces.append(np.asarray(plane[start::step, ::step]))
        offset += plane.shape[0]
    if not pieces:
        return np.zeros((1, 1, 3), np.uint8)
    linear = np.concatenate(pieces, axis=0).astype(np.float32) / 65535.0
    encoded = np.where(linear <= 0.0031308, linear * 12.92,
                       1.055 * np.power(np.clip(linear, 0.0, None), 1 / 2.4)
                       - 0.055)
    return np.clip(encoded * 255.0 + 0.5, 0, 255).astype(np.uint8)


def _row_blocks(planes: Sequence, rows_per_strip: int):
    """Yield the join's rows in blocks of exactly `rows_per_strip` (the last
    block short), reading each section only as the blocks reach it.

    Blocks deliberately cross section boundaries rather than restarting at each
    one: the strip grid belongs to the output image, and a section whose height
    is not a multiple of the strip size must not be padded to fit it."""
    buf: List[np.ndarray] = []
    have = 0
    for plane in planes:
        i, height = 0, plane.shape[0]
        while i < height:
            take = min(rows_per_strip - have, height - i)
            buf.append(np.asarray(plane[i:i + take]))
            have += take
            i += take
            if have == rows_per_strip:
                yield buf[0] if len(buf) == 1 else np.concatenate(buf, axis=0)
                buf, have = [], 0
    if have:
        yield buf[0] if len(buf) == 1 else np.concatenate(buf, axis=0)


def software_tag(version: Optional[str] = None) -> str:
    """The Software tag value stamped into a written DNG: the join marker plus
    this tool's identity."""
    from . import __version__
    return f"{JOIN_MARKER} (joincontactscans {version or __version__})"


def carries_join_marker(path) -> bool:
    """True when `path` is a TIFF-structured file this tool wrote. Reads only
    the header; an unreadable or unmarked file returns False."""
    try:
        with tifffile.TiffFile(os.path.normpath(str(path))) as tf:
            tag = tf.pages[0].tags.get("Software")
            value = tag.value if tag is not None else ""
        return isinstance(value, str) and JOIN_MARKER in value
    except Exception:
        return False


def xmp_packet(identifier: str) -> bytes:
    """A minimal XMP packet stating `identifier` as dc:identifier — the name
    the joined sheet was written under, so a catalogue that has renamed the
    file can still say what it was called."""
    return (
        '<?xpacket begin="\ufeff" id="W5M0MpCehiHzreSzNTczkc9d"?>\n'
        '<x:xmpmeta xmlns:x="adobe:ns:meta/">\n'
        ' <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">\n'
        '  <rdf:Description rdf:about=""'
        ' xmlns:dc="http://purl.org/dc/elements/1.1/">\n'
        f'   <dc:identifier>{xml_escape(identifier)}</dc:identifier>\n'
        '  </rdf:Description>\n'
        ' </rdf:RDF>\n'
        '</x:xmpmeta>\n'
        '<?xpacket end="w"?>'
    ).encode("utf-8")


def _added_tags(profile: SourceProfile, sources: Sequence[str],
                name: Optional[str] = None
                ) -> Tuple[List[tuple], List[tuple]]:
    """The `(ifd0, raw)` extratags this module contributes: the version stamp,
    the provenance, and a linear-declaration default for each tag the source
    left unstated. See the module docstring."""
    have = profile.codes
    ifd0: List[tuple] = [
        (_TAG_DNG_VERSION, "B", 4, (1, 4, 0, 0), True),
        (_TAG_DNG_BACKWARD_VERSION, "B", 4, (1, 2, 0, 0), True),
    ]
    if _TAG_UNIQUE_CAMERA_MODEL not in have:
        ifd0.append((_TAG_UNIQUE_CAMERA_MODEL, "s", 0, FALLBACK_CAMERA_MODEL,
                     True))
    # A colour spec is fabricated only when the source states none at all.
    if _TAG_COLOR_MATRIX_1 not in have and _TAG_COLOR_MATRIX_2 not in have:
        ifd0.extend([
            (_TAG_COLOR_MATRIX_1, "2i", 9, _rational(_SRGB_COLOR_MATRIX_1),
             True),
            (_TAG_FORWARD_MATRIX_1, "2i", 9, _rational(_SRGB_FORWARD_MATRIX_1),
             True),
            (_TAG_CALIBRATION_ILLUMINANT_1, "H", 1, _ILLUMINANT_D65, True),
        ])
    # AsShotNeutral and AsShotWhiteXY are alternatives; the spec forbids both.
    if _TAG_AS_SHOT_NEUTRAL not in have and _TAG_AS_SHOT_WHITE_XY not in have:
        ifd0.append((_TAG_AS_SHOT_NEUTRAL, "2I", 3,
                     _rational((1.0, 1.0, 1.0)), True))
    if _TAG_PROFILE_TONE_CURVE not in have:
        ifd0.append((_TAG_PROFILE_TONE_CURVE, "f", len(IDENTITY_TONE_CURVE),
                     IDENTITY_TONE_CURVE, True))
    if _TAG_DEFAULT_BLACK_RENDER not in have:
        ifd0.append((_TAG_DEFAULT_BLACK_RENDER, "I", 1, _BLACK_RENDER_NONE,
                     True))
    if _TAG_ORIGINAL_RAW_FILE_NAME not in have and sources:
        ifd0.append((_TAG_ORIGINAL_RAW_FILE_NAME, "s", 0,
                     os.path.basename(sources[0]), True))
    # ImageDescription is left alone. Whatever the source put there is carried
    # through like any other IFD0 tag, and where the source left it empty it
    # stays empty: it is a field for whoever owns the image to write in, and a
    # sheet arriving with a sentence this tool made up would displace that.
    # What the file was joined from is already recorded, in Software (the join
    # marker) and OriginalRawFileName.
    #
    # XMP carries the name the sheet was written under, without its
    # extension, as dc:identifier. The sources' own XMP is not carried (it is
    # not in scan._CARRY_IFD0), so this packet is the whole of the file's XMP.
    if name:
        packet = xmp_packet(os.path.splitext(name)[0])
        ifd0.append((_TAG_XMP, "B", len(packet), packet, True))

    raw: List[tuple] = []
    if _TAG_BLACK_LEVEL_REPEAT_DIM not in have:
        raw.append((_TAG_BLACK_LEVEL_REPEAT_DIM, "H", 2, (1, 1), False))
    if _TAG_BLACK_LEVEL not in have:
        raw.append((_TAG_BLACK_LEVEL, "H", 3, (0, 0, 0), False))
    if _TAG_WHITE_LEVEL not in have:
        raw.append((_TAG_WHITE_LEVEL, "I", 3, (65535, 65535, 65535), False))
    return ifd0, raw


def write_joined_dng(path: str, planes: Sequence, profile: SourceProfile,
                     sources: Sequence[str] = (),
                     version: Optional[str] = None,
                     name: Optional[str] = None) -> None:
    """Write `planes` — (H, W, 3) uint16 array-likes, in stacking order — to
    `path` as one uncompressed linear DNG. Raises JoinError on failure.

    The pixels written are exactly the pixels read, in order, unchanged.
    `profile` is the first section's metadata (scan.read_profile) and `sources`
    the section paths, used for provenance. `name` is the file name the sheet
    will finally have, stated as XMP dc:identifier; it defaults to the
    basename of `path`, and is given separately for a caller that writes to a
    temporary file and renames it."""
    if not planes:
        raise JoinError("nothing to write: no sections")
    widths = {int(p.shape[1]) for p in planes}
    if len(widths) != 1:
        raise JoinError(f"sections have different widths: "
                        f"{sorted(widths)}")
    for p in planes:
        if np.dtype(p.dtype) != np.uint16 or len(p.shape) != 3 or p.shape[2] != 3:
            raise JoinError(f"expected (H, W, 3) uint16 sections, got "
                            f"shape={tuple(p.shape)} dtype={p.dtype}")

    width = widths.pop()
    height = int(sum(p.shape[0] for p in planes))
    row_bytes = width * 3 * 2
    total = height * row_bytes
    if total > _MAX_TIFF_BYTES:
        raise JoinError(
            f"joined image would be {width}x{height} = "
            f"{total / (1 << 30):.2f} GiB of uncompressed data, past the 4 GiB "
            "a DNG's 32-bit offsets can address. Join fewer sections, or scan "
            "at a lower resolution.")

    rows_per_strip = max(1, min(height, _STRIP_TARGET_BYTES // max(1, row_bytes)))
    ifd0_added, raw_added = _added_tags(
        profile, sources, name or os.path.basename(path))
    ifd0_tags = list(profile.ifd0) + ifd0_added
    raw_tags = list(profile.raw) + raw_added
    step = thumbnail_step(width, height)
    thumb = build_thumbnail(planes, step)
    # Stated on both pages. Left unset, tifffile stamps the raw page with its
    # own "tifffile.py", which would have the file crediting its image data to
    # the library that laid out the bytes.
    software = software_tag(version)

    # Resolution cannot go through extratags — tifffile owns tags 282/283/296
    # and drops any attempt to set them that way.
    #
    # The thumbnail's DPI is DERIVED from the image's rather than carried from
    # the source's thumbnail: this preview is a different size from that one, so
    # the source's figure describes the wrong picture. Dividing the image
    # resolution by the stride that built the preview is what makes it describe
    # a sheet of the right physical size.
    thumb_res = profile.thumbnail_resolution
    if profile.resolution is not None:
        thumb_res = (profile.resolution[0] / step, profile.resolution[1] / step)

    try:
        with tifffile.TiffWriter(os.path.normpath(path)) as tw:
            # metadata=None drops tifffile's own {"shape": ...}
            # ImageDescription: a tifffile convention, meaningless to a DNG
            # reader, and it would sit in the slot the source's description
            # goes.
            tw.write(thumb, photometric="rgb", compression=None, subfiletype=1,
                     subifds=1, extratags=ifd0_tags, metadata=None,
                     software=software, resolution=thumb_res,
                     resolutionunit=profile.resolution_unit)
            # planarconfig is stated rather than inferred: 34892 is not a
            # photometric tifffile treats as having samples, so without it the
            # (H, W, 3) data is written as H pages of W x 3 grey instead of one
            # RGB image — and DNG requires chunky data anyway.
            tw.write(_row_blocks(planes, rows_per_strip),
                     shape=(height, width, 3), dtype=np.uint16,
                     photometric=PHOTOMETRIC_LINEAR_RAW, planarconfig="contig",
                     compression=None, subfiletype=0,
                     rowsperstrip=rows_per_strip, extratags=raw_tags,
                     metadata=None, software=software,
                     resolution=profile.resolution,
                     resolutionunit=profile.resolution_unit)
    except JoinError:
        raise
    except Exception as e:
        raise JoinError(f"failed to write {path}: {e}") from e


def verify_structure(path: str,
                     expect_shape: Optional[Tuple[int, int]] = None) -> None:
    """Confirm `path` is a real, non-empty uint16 LinearRaw RGB DNG of the
    expected (H, W). Raises JoinError on any mismatch.

    The thumbnail in IFD0 is deliberately not what gets checked: a file whose
    preview survived but whose image did not must fail."""
    from .scan import _raw_page
    if not os.path.exists(path) or os.path.getsize(path) <= 0:
        raise JoinError(f"joined DNG not written or empty: {path}")
    with tifffile.TiffFile(os.path.normpath(path)) as tf:
        if _TAG_DNG_VERSION not in tf.pages[0].tags:
            raise JoinError(f"file carries no DNGVersion tag: {path}")
        page = _raw_page(tf)
        if page is None:
            raise JoinError(f"joined DNG has no LinearRaw image: {path}")
        shape, dtype = tuple(page.shape), np.dtype(page.dtype)
    if (dtype != np.uint16 or len(shape) != 3 or shape[2] != 3
            or shape[0] <= 0 or shape[1] <= 0):
        raise JoinError(f"joined DNG failed verification "
                        f"(shape={shape}, dtype={dtype}): {path}")
    if expect_shape is not None and tuple(shape[:2]) != tuple(expect_shape):
        raise JoinError(f"joined DNG is {shape[1]}x{shape[0]}, expected "
                        f"{expect_shape[1]}x{expect_shape[0]}: {path}")


def verify_pixels(path: str, sources: Sequence[str],
                  rows_per_block: int = 256) -> int:
    """Read `path` back and confirm every section of it is byte-for-byte the
    source it came from. Returns the number of sections checked; raises
    JoinError naming the section and the first differing row otherwise.

    This is the tool's one real claim, so it is checked rather than assumed.
    Both files are memory-mapped and walked in blocks, so the cost is one
    sequential read of each and a bounded amount of memory — not a second copy
    of the join."""
    with open_plane(path) as joined:
        offset = 0
        for index, source in enumerate(sources, start=1):
            with open_plane(source) as src:
                height = int(src.shape[0])
                if offset + height > joined.shape[0]:
                    raise JoinError(
                        f"joined DNG is too short for {os.path.basename(source)}: "
                        f"needs rows {offset}-{offset + height - 1} of "
                        f"{joined.shape[0]}")
                for i in range(0, height, rows_per_block):
                    n = min(rows_per_block, height - i)
                    a = np.asarray(joined[offset + i:offset + i + n])
                    b = np.asarray(src[i:i + n])
                    if not np.array_equal(a, b):
                        bad = int(np.argmax(np.any(a != b, axis=(1, 2))))
                        raise JoinError(
                            f"section {index} ({os.path.basename(source)}) does "
                            f"not match the joined file: first difference at "
                            f"its row {i + bad} (joined row {offset + i + bad})")
                offset += height
        if offset != joined.shape[0]:
            raise JoinError(
                f"joined DNG has {joined.shape[0]} rows but its sections "
                f"account for {offset}")
    return len(sources)


def read_joined_dng(path: str) -> np.ndarray:
    """The (H, W, 3) uint16 linear image out of a DNG this tool wrote — the
    SubIFD, not the thumbnail. Reads it all into memory; mostly useful for
    tests and for checking a small file."""
    with open_plane(path) as plane:
        return np.array(plane)
