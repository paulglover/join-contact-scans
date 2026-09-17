"""
Synthetic linear DNGs to test against.

`write_section` produces a file shaped like the ones VueScan writes — a small
RGB thumbnail in IFD0, a 16-bit LinearRaw image in a SubIFD, and VueScan's own
choice of colour tags — so the tests exercise the same code paths the real
scans do. `extra_ifd0` and `extra_raw` let a test add or withhold a tag and
check what the writer does about it.
"""
import numpy as np
import tifffile

# VueScan's ColorMatrix1: the sRGB/Rec.709 XYZ(D65) -> RGB matrix, as
# SRATIONAL numerator/denominator pairs.
VUESCAN_COLOR_MATRIX_1 = (
    3240625, 1000000, -1537208, 1000000, -498629, 1000000,
    -968931, 1000000, 1875756, 1000000, 41518, 1000000,
    55710, 1000000, -204021, 1000000, 1056996, 1000000,
)
# VueScan's AsShotWhiteXY: D65.
VUESCAN_AS_SHOT_WHITE_XY = (3127, 10000, 3290, 10000)

VUESCAN_IFD0 = (
    (50706, "B", 4, (1, 1, 0, 0), True),                 # DNGVersion 1.1
    (50708, "s", 0, "Epson Perfection4490", True),       # UniqueCameraModel
    (271, "s", 0, "Epson", True),                        # Make
    (272, "s", 0, "Perfection4490", True),               # Model
    (285, "s", 0, "Transparency", True),                 # PageName
    (50721, "2i", 9, VUESCAN_COLOR_MATRIX_1, True),      # ColorMatrix1
    (50729, "2I", 2, VUESCAN_AS_SHOT_WHITE_XY, True),    # AsShotWhiteXY
    (50731, "2I", 1, (64, 1), True),                     # BaselineNoise
)

# MinSampleValue/MaxSampleValue, which is how VueScan states its range — TIFF
# tags a DNG reader does not consult, which is why the writer adds BlackLevel
# and WhiteLevel of its own.
VUESCAN_RAW = (
    (280, "H", 3, (0, 0, 0), False),
    (281, "H", 3, (65535, 65535, 65535), False),
)


def section_pixels(height, width, seed):
    """Deterministic noise, so a test can assert on exact bytes."""
    return np.random.default_rng(seed).integers(
        0, 65536, (height, width, 3), dtype=np.uint16)


def write_section(path, pixels, extra_ifd0=(), extra_raw=(),
                  ifd0=VUESCAN_IFD0, raw=VUESCAN_RAW, software="VueScan 9 a64",
                  resolution=(1200, 1200), datetime_digitized=None,
                  photometric=34892):
    """Write `pixels` as a VueScan-shaped linear DNG at `path`. Returns
    `pixels`, so a test can write and remember in one line."""
    ifd0_tags = list(ifd0) + list(extra_ifd0)
    if datetime_digitized is not None:
        # DateTimeDigitized, loose in IFD0 rather than inside a real EXIF
        # sub-IFD — tifffile cannot write a nested IFD, and read_profile looks
        # in both places precisely because writers differ about where they
        # leave it. The real VueScan files put it in the EXIF IFD.
        ifd0_tags.append((36868, "s", 0, datetime_digitized, True))
    # A CFA image is one sample per pixel: a caller testing the mosaic
    # rejection passes photometric=32803 and gets the plane flattened to 2-D,
    # which is the only shape that is a valid CFA image.
    if photometric == 32803 and pixels.ndim == 3:
        pixels = pixels[..., 0]
    thumb = np.zeros((8, 8, 3), np.uint8)
    with tifffile.TiffWriter(str(path)) as tw:
        tw.write(thumb, photometric="rgb", compression=None, subfiletype=1,
                 subifds=1, metadata=None, software=software,
                 resolution=(32, 32), resolutionunit=2, extratags=ifd0_tags)
        tw.write(pixels, photometric=photometric, planarconfig="contig",
                 compression=None, subfiletype=0, rowsperstrip=1,
                 metadata=None, resolution=resolution, resolutionunit=2,
                 extratags=list(raw) + list(extra_raw))
    return pixels


def write_roll(directory, roll="S0220", count=3, height=7, width=11,
               **kwargs):
    """A whole roll of sections. Returns `(paths, joined)` — the section paths
    in scan order, and the array they should join into."""
    paths, blocks = [], []
    for i in range(1, count + 1):
        pixels = section_pixels(height, width, seed=1000 + i)
        path = directory / f"{roll}-{i}.dng"
        write_section(path, pixels, **kwargs)
        paths.append(path)
        blocks.append(pixels)
    return paths, np.concatenate(blocks, axis=0)


def subifd_tags(path):
    """`{code: value}` of the LinearRaw page's tags."""
    with tifffile.TiffFile(str(path)) as tf:
        page = tf.pages[0].pages[0].aspage()
        return {t.code: t.value for t in page.tags}


def ifd0_tags(path):
    """`{code: value}` of IFD0's tags."""
    with tifffile.TiffFile(str(path)) as tf:
        return {t.code: t.value for t in tf.pages[0].tags}
