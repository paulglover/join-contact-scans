"""
Reading the sections of a contact-sheet scan.

A *section* is one pass of the scanner over part of a contact sheet, written by
VueScan (or anything else that produces linear DNG) as `ROLLID-SCANSEQ.dng`.
This module turns a pile of such files into ordered, validated groups, hands out
their pixels without pulling a gigabyte into memory to do it, and harvests the
metadata the joined file will carry.

Filename order, and why it is not string order
----------------------------------------------
The scan sequence decides the stacking order, and it is a NUMBER. Sorted as
text, `S0220-10` lands between `S0220-1` and `S0220-2`, which would silently
interleave a ten-section sheet. So the trailing field is parsed as an integer
and sorted as one; a roll whose sections are not consecutively numbered is
still joined, in numeric order, because a gap is a missing scan and not a
reason to refuse the ones that are there.

The split is on the LAST hyphen, so a roll id may contain hyphens of its own:
`2026-05-portra-3.dng` is section 3 of roll `2026-05-portra`.

What counts as joinable
-----------------------
Stacking two images vertically only means anything if they agree on everything
except height. This module requires, and says plainly when it does not get:

* a LinearRaw image (`PhotometricInterpretation = 34892`) — three samples per
  pixel, already demosaiced. A CFA (Bayer) DNG is rejected rather than joined,
  because the mosaic phase of section two depends on section one's height being
  even, and a file that silently got that wrong looks like a colour problem
  three steps later,
* 16-bit unsigned samples,
* the same pixel width as every other section of the roll.

Reading the pixels
------------------
`open_plane` memory-maps the image where the file allows it — uncompressed,
contiguous, and in the machine's byte order, which is the normal shape of a
scanner's linear DNG. Nothing is read until it is touched, so building a
thumbnail from every fiftieth row costs a fiftieth of the file rather than all
of it, and joining ten sections never holds more than one strip of any of them.
A file that cannot be mapped (compressed, or byte-swapped) falls back to a plain
read of that one section.

The metadata carried forward
----------------------------
`read_profile` harvests the first section's tags for `dng.write_joined_dng` to
re-emit. The policy is one sentence: **carry through everything the source
states about its own colour, and supply the linear-declaration defaults only for
what it omits.** So a VueScan file's ColorMatrix1, AsShotWhiteXY and
UniqueCameraModel arrive in the joined file untouched — they describe the
scanner that made these numbers, and the join does not change what the numbers
mean — while BlackLevel, WhiteLevel and the identity tone curve, which VueScan
does not write, are added by dng.py to stop a converter supplying its own.

Geometry tags are the exception, and the reason this module returns warnings.
`DefaultCropSize`, `ActiveArea` and friends are measured in pixels against a
height the join has just changed. Carried through unaltered they would crop the
joined file back to its first section. Where such a tag covers the whole section
frame it is re-stated against the joined height; where it describes a partial
crop it is dropped and said so, because extending someone else's crop is a guess
and a wrong guess here is invisible until the bottom of the sheet is missing.

The EXIF sub-IFD is deliberately not carried. A scanner's holds a capture date
and a colour-space code. The date is restated in IFD0 as `DateTimeOriginal` —
where TIFF/EP, and so DNG, lets it live, and where a converter reads capture
time — and as `DateTime`; `ColorSpace` means nothing in a LinearRaw file.
Moving the rest would mean rewriting a gigabyte to relocate two tags.
"""
import os
import re
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
import tifffile

# The extension this tool reads and writes.
SECTION_EXTENSION = ".dng"

# PhotometricInterpretation for demosaiced, camera-native RGB — the thing that
# makes a DNG "linear".
PHOTOMETRIC_LINEAR_RAW = 34892

# `ROLLID-SCANSEQ`, split on the last hyphen. The roll id may contain hyphens;
# the sequence may not, and must be digits.
_SECTION_NAME = re.compile(r"^(?P<roll>.+)-(?P<seq>\d+)$")

# TIFF data type -> the format character tifffile's `extratags` wants. Types are
# numbered by the TIFF 6.0 spec; RATIONAL and SRATIONAL are pairs, hence '2I'
# and '2i'.
_DATATYPE_FORMAT = {
    1: "B", 2: "s", 3: "H", 4: "I", 5: "2I", 6: "b",
    7: "B", 8: "h", 9: "i", 10: "2i", 11: "f", 12: "d",
}
_TYPE_RATIONAL = 5
_TYPE_SRATIONAL = 10

# --- IFD0: what the file says about its colour and its camera -------------- #
# Everything here is re-emitted verbatim. These tags describe the scanner and
# the meaning of its numbers, neither of which stacking sections changes.
_CARRY_IFD0 = (
    270,    # ImageDescription
    271,    # Make
    272,    # Model
    274,    # Orientation
    285,    # PageName            (VueScan: "Transparency" / "Reflective")
    306,    # DateTime
    315,    # Artist
    33432,  # Copyright
    50708,  # UniqueCameraModel
    50709,  # LocalizedCameraModel
    50721,  # ColorMatrix1
    50722,  # ColorMatrix2
    50723,  # CameraCalibration1
    50724,  # CameraCalibration2
    50725,  # ReductionMatrix1
    50726,  # ReductionMatrix2
    50727,  # AnalogBalance
    50728,  # AsShotNeutral       (spec: this or AsShotWhiteXY, never both)
    50729,  # AsShotWhiteXY
    50730,  # BaselineExposure
    50731,  # BaselineNoise
    50732,  # BaselineSharpness
    50734,  # LinearResponseLimit
    50735,  # CameraSerialNumber
    50736,  # LensInfo
    50739,  # ShadowScale
    50778,  # CalibrationIlluminant1
    50779,  # CalibrationIlluminant2
    50931,  # CameraCalibrationSignature
    50932,  # ProfileCalibrationSignature
    50936,  # ProfileName
    50937,  # ProfileHueSatMapDims
    50938,  # ProfileHueSatMapData1
    50939,  # ProfileHueSatMapData2
    50940,  # ProfileToneCurve    (carried if stated; dng.py adds identity if not)
    50941,  # ProfileEmbedPolicy
    50942,  # ProfileCopyright
    50964,  # ForwardMatrix1
    50965,  # ForwardMatrix2
    50981,  # ProfileLookTableDims
    50982,  # ProfileLookTableData
    51110,  # DefaultBlackRender  (carried if stated; dng.py adds None if not)
)

# --- The raw IFD: what the file says about its sample values ---------------- #
# Levels, not geometry. None of these depend on how tall the image is.
_CARRY_RAW = (
    50713,  # BlackLevelRepeatDim
    50714,  # BlackLevel
    50715,  # BlackLevelDeltaH    (per COLUMN — unchanged by stacking)
    50717,  # WhiteLevel
    50733,  # BayerGreenSplit
    50738,  # AntiAliasStrength
    50780,  # BestQualityScale
    50718,  # DefaultScale        (a ratio, not a pixel count)
)

# --- Geometry: tags measured against a height the join changes -------------- #
# Handled, not carried. See the module docstring.
_FRAME_TAGS = {
    50719: "DefaultCropOrigin",   # (x, y)
    50720: "DefaultCropSize",     # (w, h)
    50829: "ActiveArea",          # (top, left, bottom, right)
}
_DROP_TAGS = {
    50716: "BlackLevelDeltaV",    # one value per ROW
    50830: "MaskedAreas",         # rectangles in pixels
    51125: "DefaultUserCrop",     # fractions of the frame
}

# Tags whose disagreement between sections would mean the pixels of different
# sections do not mean the same thing. Compared; a difference is warned about,
# never fatal — the user chose these files.
_COLOUR_KEY = (50708, 50721, 50722, 50728, 50729, 50778, 50779, 50964)


class SectionError(ValueError):
    """A file cannot be used as a section, with a message meant for a person."""


@dataclass(frozen=True)
class Section:
    """One input scan: where it is, where it belongs in the stack, and the shape
    of the image inside it."""
    path: str
    roll: str
    seq: int
    width: int
    height: int
    colour_key: Tuple = ()

    @property
    def name(self) -> str:
        return os.path.basename(self.path)


@dataclass
class SourceProfile:
    """The first section's metadata, decomposed into what `dng.write_joined_dng`
    needs to re-emit it.

    `ifd0` and `raw` are tifffile `extratags` tuples, ready to write. `codes`
    is every tag code the source stated, so the writer can tell "the source has
    no WhiteLevel, add one" from "the source said WhiteLevel is 16383, keep
    it". `warnings` are the geometry tags that had to be dropped."""
    ifd0: List[tuple] = field(default_factory=list)
    raw: List[tuple] = field(default_factory=list)
    codes: set = field(default_factory=set)
    resolution: Optional[Tuple[float, float]] = None
    resolution_unit: int = 2
    thumbnail_resolution: Optional[Tuple[float, float]] = None
    software: Optional[str] = None
    warnings: List[str] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Filenames
# --------------------------------------------------------------------------- #
def parse_section_name(path: str) -> Tuple[str, int]:
    """`(roll, seq)` from a `ROLLID-SCANSEQ.dng` path. Raises SectionError when
    the name does not carry a trailing numeric sequence — including for a file
    this tool wrote, whose name is `ROLLID.dng` with no sequence at all, which
    is what keeps a finished join from being picked up as an input to the
    next one."""
    stem = os.path.splitext(os.path.basename(path))[0]
    m = _SECTION_NAME.match(stem)
    if not m:
        raise SectionError(
            f"{os.path.basename(path)}: not named ROLLID-SCANSEQ{SECTION_EXTENSION} "
            f"(expected a trailing scan number, as in S0220-1{SECTION_EXTENSION})")
    return m.group("roll"), int(m.group("seq"))


def is_section_path(path: str) -> bool:
    """True when `path` looks like a section of a roll: a `.dng` whose name ends
    in a scan number. Cheap — it reads the name, not the file."""
    if os.path.splitext(str(path))[1].lower() != SECTION_EXTENSION:
        return False
    try:
        parse_section_name(str(path))
    except SectionError:
        return False
    return True


def collect_section_files(inputs: Sequence[str],
                          recursive: bool = False) -> List[str]:
    """Every section-shaped `.dng` under `inputs`, which may be files or
    folders.

    A file named explicitly is taken as given — if the user points at it, a
    complaint about its name belongs in the error message, not in a silent
    skip. A file merely FOUND inside a folder is filtered by name, so dropping a
    whole scan folder on the droplet picks up `S0220-1.dng` and leaves the
    joined `S0220.dng` beside it alone."""
    found: List[str] = []
    for item in inputs:
        item = os.path.abspath(os.path.expanduser(str(item)))
        if os.path.isdir(item):
            walker = os.walk(item) if recursive else [
                (item, [], sorted(os.listdir(item)))]
            for root, _dirs, names in walker:
                found.extend(os.path.join(root, n) for n in sorted(names)
                             if is_section_path(os.path.join(root, n)))
        elif os.path.exists(item):
            found.append(item)
        else:
            raise SectionError(f"no such file or folder: {item}")
    # Stable, de-duplicated: the same file named twice is the same section.
    return sorted(dict.fromkeys(found))


# --------------------------------------------------------------------------- #
# Reading a section
# --------------------------------------------------------------------------- #
def _raw_page(tf: tifffile.TiffFile):
    """The page holding the LinearRaw image, or None.

    Checked in IFD0 as well as the SubIFDs: the spec's layout puts a thumbnail
    in IFD0 and the image in a SubIFD, which is what VueScan writes, but a
    writer that puts the full image in IFD0 has still produced a linear DNG and
    there is no reason not to read it."""
    for page in tf.pages:
        candidates = [page]
        candidates.extend(page.pages or ())
        for cand in candidates:
            cand = cand.aspage() if hasattr(cand, "aspage") else cand
            photometric = cand.tags.get("PhotometricInterpretation")
            if (photometric is not None
                    and int(photometric.value) == PHOTOMETRIC_LINEAR_RAW):
                return cand
    return None


def read_section(path: str) -> Section:
    """Inspect `path` and describe it as a Section. Raises SectionError with a
    readable reason when the file is not a joinable linear DNG."""
    path = os.path.abspath(os.path.expanduser(str(path)))
    roll, seq = parse_section_name(path)
    name = os.path.basename(path)
    try:
        with tifffile.TiffFile(os.path.normpath(path)) as tf:
            page = _raw_page(tf)
            if page is None:
                raise SectionError(
                    f"{name}: no LinearRaw image — this is not a linear DNG. "
                    "A mosaiced (Bayer) DNG cannot be joined; convert it to "
                    "linear first.")
            shape, dtype = tuple(page.shape), np.dtype(page.dtype)
            key = tuple(_tag_value(tf.pages[0].tags.get(c)) for c in _COLOUR_KEY)
    except SectionError:
        raise
    except Exception as e:
        raise SectionError(f"{name}: cannot be read as a DNG: {e}") from e

    if len(shape) != 3 or shape[2] != 3:
        raise SectionError(f"{name}: LinearRaw image is {shape}, expected "
                           "(height, width, 3)")
    if dtype != np.uint16:
        raise SectionError(f"{name}: samples are {dtype}, expected uint16")
    if shape[0] <= 0 or shape[1] <= 0:
        raise SectionError(f"{name}: image is empty ({shape[0]}x{shape[1]})")
    return Section(path=path, roll=roll, seq=seq, width=int(shape[1]),
                   height=int(shape[0]), colour_key=key)


@contextmanager
def open_plane(path: str):
    """Yield the (H, W, 3) uint16 LinearRaw image of `path` as an array-like.

    Memory-mapped when the file permits, so a caller that touches every
    fiftieth row reads a fiftieth of the file. The map is read-only and is
    released when the block exits."""
    with tifffile.TiffFile(os.path.normpath(str(path))) as tf:
        page = _raw_page(tf)
        if page is None:
            raise SectionError(f"{os.path.basename(path)}: no LinearRaw image")
        if page.is_memmappable:
            # is_memmappable already accounts for byte order, so page.dtype is
            # safe to hand to numpy as-is.
            plane = np.memmap(os.path.normpath(str(path)), dtype=page.dtype,
                              mode="r", offset=int(page.dataoffsets[0]),
                              shape=tuple(page.shape))
            try:
                yield plane
            finally:
                del plane
        else:
            # Compressed or byte-swapped: one section in memory is the price.
            yield page.asarray()


# --------------------------------------------------------------------------- #
# Grouping and validation
# --------------------------------------------------------------------------- #
def group_into_rolls(sections: Iterable[Section]) -> Dict[str, List[Section]]:
    """Sections grouped by roll id, each list in scan order. Rolls come back in
    the order their first section was named."""
    rolls: Dict[str, List[Section]] = {}
    for s in sections:
        rolls.setdefault(s.roll, []).append(s)
    return {roll: sorted(items, key=lambda s: s.seq)
            for roll, items in rolls.items()}


def validate_roll(sections: Sequence[Section]) -> List[str]:
    """Check a roll's sections can be stacked. Raises SectionError when they
    cannot; returns warnings — things worth knowing that are not reasons to
    stop — when they can."""
    if len(sections) < 2:
        raise SectionError(
            f"roll {sections[0].roll!r} has only {len(sections)} section "
            f"({sections[0].name}) — a join needs at least two")

    seen: Dict[int, Section] = {}
    for s in sections:
        if s.seq in seen:
            raise SectionError(
                f"roll {s.roll!r} has two section {s.seq}s: "
                f"{seen[s.seq].name} and {s.name}")
        seen[s.seq] = s

    first = sections[0]
    for s in sections[1:]:
        if s.width != first.width:
            raise SectionError(
                f"roll {s.roll!r}: {s.name} is {s.width} pixels wide but "
                f"{first.name} is {first.width} — sections of one sheet must "
                "be scanned at the same width")

    warnings: List[str] = []
    expected = list(range(first.seq, first.seq + len(sections)))
    if [s.seq for s in sections] != expected:
        got = ", ".join(str(s.seq) for s in sections)
        warnings.append(f"scan numbers are not consecutive ({got}) — joining "
                        "in numeric order anyway")
    # Missing the FIRST section is the one gap the consecutiveness check above
    # cannot see: 2, 3, 4 is a perfectly consecutive run. It is also the gap
    # that produces a valid-looking sheet with the top quietly absent, so it is
    # worth saying out loud even though numbering from something other than 1
    # is a legitimate thing to do.
    elif first.seq > 1:
        warnings.append(
            f"roll starts at section {first.seq}, not 1 — if section 1 was "
            "meant to be included, this sheet is missing its top")
    odd = [s for s in sections if s.colour_key != first.colour_key]
    if odd:
        warnings.append(
            f"{', '.join(s.name for s in odd)} "
            f"{'has' if len(odd) == 1 else 'have'} different colour metadata "
            f"from {first.name}; the joined file carries {first.name}'s")
    return warnings


# --------------------------------------------------------------------------- #
# Harvesting the metadata to carry forward
# --------------------------------------------------------------------------- #
def _tag_value(tag):
    """A tag's value as plain Python, or None when the tag is absent. Enums
    become ints, numpy scalars become Python scalars, arrays become tuples —
    anything that can be compared and re-written without dragging tifffile's
    types along."""
    if tag is None:
        return None
    value = tag.value
    if isinstance(value, (bytes, bytearray)):
        return tuple(value)
    if isinstance(value, (str, dict)):
        # A nested IFD (EXIF, GPS) comes back as a dict of its own tags. Handed
        # on whole: nothing re-emits one, but read_profile reads the capture
        # date out of it.
        return value
    if isinstance(value, (int, float)):        # covers IntEnum
        return int(value) if isinstance(value, int) else float(value)
    if hasattr(value, "item") and getattr(value, "ndim", None) == 0:
        return value.item()
    try:
        return tuple(np.asarray(value).reshape(-1).tolist())
    except Exception:
        return value


def _numbers(tag) -> List[float]:
    """A tag's values as plain numbers, with RATIONAL pairs divided out, so a
    geometry tag can be compared against a pixel count whichever type it was
    written as."""
    raw = _tag_value(tag)
    values = list(raw) if isinstance(raw, tuple) else [raw]
    if tag.dtype in (_TYPE_RATIONAL, _TYPE_SRATIONAL):
        return [(n / d if d else 0.0) for n, d in zip(values[::2], values[1::2])]
    return [float(v) for v in values]


def _reemit(tag, writeonce: bool) -> Optional[tuple]:
    """A tifffile `extratags` entry that writes `tag` out again as it was read.
    None when the tag's type is one this cannot round-trip."""
    fmt = _DATATYPE_FORMAT.get(int(tag.dtype))
    if fmt is None:
        return None
    value = _tag_value(tag)
    if value is None:
        return None
    if fmt == "s":
        # tifffile appends the NUL itself; count 0 lets it size the tag.
        return (tag.code, "s", 0, str(value), writeonce)
    return (tag.code, fmt, int(tag.count), value, writeonce)


def _reemit_numbers(tag, values: Sequence[float], writeonce: bool) -> tuple:
    """`tag` re-emitted with new numbers, in the type it originally had — so a
    RATIONAL DefaultCropSize stays RATIONAL and a SHORT one stays SHORT."""
    fmt = _DATATYPE_FORMAT[int(tag.dtype)]
    if tag.dtype in (_TYPE_RATIONAL, _TYPE_SRATIONAL):
        out: List[int] = []
        for v in values:
            out.extend((int(round(v)), 1))
        return (tag.code, fmt, len(values), tuple(out), writeonce)
    return (tag.code, fmt, len(values),
            tuple(int(round(v)) for v in values), writeonce)


def _carry_frame_tag(tag, name: str, width: int, section_height: int,
                     joined_height: int, warnings: List[str]) -> Optional[tuple]:
    """A geometry tag re-stated against the joined height, or None (with a
    warning) when it describes a partial crop that cannot honestly be extended.

    The test is whether the tag covers the whole section frame. If it does, the
    same statement about the joined image is the same statement with a bigger
    height, and making it keeps the joined file's default rendering matching its
    sections'. If it does not, the source cropped to something inside one
    section, and no rule turns that into a crop of four."""
    values = _numbers(tag)
    if tag.code == 50719:                       # DefaultCropOrigin (x, y)
        if len(values) == 2 and values == [0.0, 0.0]:
            return _reemit_numbers(tag, [0.0, 0.0], False)
    elif tag.code == 50720:                     # DefaultCropSize (w, h)
        if len(values) == 2 and values == [float(width), float(section_height)]:
            return _reemit_numbers(tag, [float(width), float(joined_height)],
                                   False)
    elif tag.code == 50829:                     # ActiveArea (t, l, b, r)
        if (len(values) == 4
                and values == [0.0, 0.0, float(section_height), float(width)]):
            return _reemit_numbers(
                tag, [0.0, 0.0, float(joined_height), float(width)], False)
    warnings.append(
        f"dropped {name}: it describes a crop inside one section "
        "({}), which cannot be restated for the joined image".format(
            ", ".join(f"{v:g}" for v in values)))
    return None


def read_profile(path: str, section_height: int,
                 joined_height: int) -> SourceProfile:
    """Harvest the metadata the joined file should carry from `path`, the
    roll's first section.

    Never raises: a source whose metadata cannot be read still has pixels worth
    joining, and the joined file is a valid linear DNG on dng.py's own tags
    alone. Anything that could not be carried comes back in `warnings`."""
    profile = SourceProfile()
    try:
        with tifffile.TiffFile(os.path.normpath(str(path))) as tf:
            ifd0 = tf.pages[0]
            page = _raw_page(tf) or ifd0
            profile.software = _tag_value(ifd0.tags.get(305))

            for code in _CARRY_IFD0:
                tag = ifd0.tags.get(code)
                entry = _reemit(tag, True) if tag is not None else None
                if entry is not None:
                    profile.ifd0.append(entry)
                    profile.codes.add(code)

            # The joined sheet's capture time is the first section's. It is
            # stated as DateTimeOriginal, the tag a converter shows as capture
            # time, and — where the source states no DateTime of its own — as
            # DateTime too, so the sheet sorts by when it was scanned either
            # way. See `_capture_time` for where it is looked for.
            when = _capture_time(ifd0, path)
            if when is not None:
                profile.ifd0.append((36867, "s", 0, when, True))
                profile.codes.add(36867)
                if 306 not in profile.codes:
                    profile.ifd0.append((306, "s", 0, when, True))
                    profile.codes.add(306)

            for code in _CARRY_RAW:
                tag = page.tags.get(code)
                entry = _reemit(tag, False) if tag is not None else None
                if entry is not None:
                    profile.raw.append(entry)
                    profile.codes.add(code)

            for code, name in _FRAME_TAGS.items():
                tag = page.tags.get(code)
                if tag is None:
                    continue
                entry = _carry_frame_tag(tag, name, int(page.shape[1]),
                                         section_height, joined_height,
                                         profile.warnings)
                if entry is not None:
                    profile.raw.append(entry)
                    profile.codes.add(code)

            for code, name in _DROP_TAGS.items():
                if page.tags.get(code) is not None:
                    profile.warnings.append(
                        f"dropped {name}: it is measured per row or against the "
                        "frame, and the join changes both")

            profile.resolution = _resolution(page)
            profile.thumbnail_resolution = _resolution(ifd0)
            unit = _tag_value(page.tags.get(296)) or _tag_value(ifd0.tags.get(296))
            if unit:
                profile.resolution_unit = int(unit)
    except Exception as e:
        profile.warnings.append(
            f"no metadata carried from {os.path.basename(str(path))}: {e}")
    return profile


def _capture_time(ifd0, path: str) -> Optional[str]:
    """When the section at `path` was scanned, as an EXIF date string
    ("YYYY:MM:DD HH:MM:SS"), or None if nothing says.

    What the file states about itself comes first: DateTimeOriginal, then
    DateTimeDigitized, each looked for inside the EXIF sub-IFD and loose in
    IFD0 because writers differ about where they leave it (VueScan uses the
    EXIF IFD), then IFD0's DateTime. Only a file that states none of these
    falls back to its modification time, which a copy may have reset."""
    exif = _tag_value(ifd0.tags.get(34665))
    exif = exif if isinstance(exif, dict) else {}
    for key, code in (("DateTimeOriginal", 36867),
                      ("DateTimeDigitized", 36868),
                      (None, 306)):
        when = exif.get(key) if key else None
        if not isinstance(when, str) or not when.strip():
            when = _tag_value(ifd0.tags.get(code))
        if isinstance(when, str) and when.strip():
            return when.strip()
    try:
        mtime = os.path.getmtime(os.path.normpath(str(path)))
    except OSError:
        return None
    return time.strftime("%Y:%m:%d %H:%M:%S", time.localtime(mtime))


def _resolution(page) -> Optional[Tuple[float, float]]:
    """A page's (x, y) resolution as floats, or None. Scanner DPI is the one
    piece of geometry that must survive the join intact — it is what tells a
    converter how big the sheet physically is."""
    x, y = page.tags.get(282), page.tags.get(283)
    if x is None or y is None:
        return None
    try:
        return (_numbers(x)[0], _numbers(y)[0])
    except Exception:
        return None
