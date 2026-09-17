"""Writing the joined DNG: the pixels, and the tags that say what they are."""
import numpy as np
import pytest
import tifffile

import dngfixture as fix
from joincontactscans import dng, scan


def _join(tmp_path, **kwargs):
    """Write a roll, join it, and return `(output, expected_pixels)`."""
    paths, joined = fix.write_roll(tmp_path, **kwargs)
    out = tmp_path / "S0220.dng"
    sections = [scan.read_section(str(p)) for p in paths]
    profile = scan.read_profile(str(paths[0]), sections[0].height,
                                sum(s.height for s in sections))
    planes = [_pixels(p) for p in paths]
    dng.write_joined_dng(str(out), planes, profile,
                         sources=[str(p) for p in paths])
    return out, joined


def _pixels(path):
    with scan.open_plane(str(path)) as plane:
        return np.array(plane)


# --- The one real claim ----------------------------------------------------- #
def test_pixels_are_identical_section_for_section(tmp_path):
    out, joined = _join(tmp_path, count=4, height=7, width=11)
    assert np.array_equal(dng.read_joined_dng(str(out)), joined)


@pytest.mark.parametrize("height", [1, 2, 63, 64, 65])
def test_pixels_survive_any_section_height(tmp_path, height):
    """Strips are the OUTPUT image's grid and blocks cross section boundaries,
    so a section height that is not a multiple of the strip size must not be
    padded to fit one."""
    out, joined = _join(tmp_path, count=3, height=height, width=5)
    assert np.array_equal(dng.read_joined_dng(str(out)), joined)


def test_verify_pixels_passes_on_a_good_file(tmp_path):
    paths, _ = fix.write_roll(tmp_path, count=3)
    out, _ = _join(tmp_path, count=3)
    assert dng.verify_pixels(str(out), [str(p) for p in paths]) == 3


def test_verify_pixels_catches_a_corrupted_image(tmp_path):
    """The guarantee is checked, not assumed: flip one sample and the check
    must find it and say where."""
    paths, _ = fix.write_roll(tmp_path, count=3, height=5, width=5)
    out, _ = _join(tmp_path, count=3, height=5, width=5)
    with tifffile.TiffFile(str(out)) as tf:
        offset = tf.pages[0].pages[0].aspage().dataoffsets[0]
    with open(out, "r+b") as fh:            # row 7 of the join: section 2
        fh.seek(offset + (7 * 5 * 3 + 2) * 2)
        fh.write(b"\xff\xff")
    with pytest.raises(dng.JoinError, match="section 2"):
        dng.verify_pixels(str(out), [str(p) for p in paths])


def test_verify_structure_rejects_a_wrong_size(tmp_path):
    out, _ = _join(tmp_path, count=3, height=7, width=11)
    dng.verify_structure(str(out), expect_shape=(21, 11))
    with pytest.raises(dng.JoinError, match="expected"):
        dng.verify_structure(str(out), expect_shape=(22, 11))


# --- What the file says it is ---------------------------------------------- #
def test_output_is_a_linear_dng(tmp_path):
    out, _ = _join(tmp_path, count=2)
    raw = fix.subifd_tags(out)
    assert raw[262] == scan.PHOTOMETRIC_LINEAR_RAW
    assert raw[258] == (16, 16, 16)
    assert raw[277] == 3
    assert raw[284] == 1                    # chunky, as DNG requires
    assert raw[259] == 1                    # uncompressed
    assert fix.ifd0_tags(out)[254] == 1     # IFD0 is the thumbnail
    assert raw[254] == 0                    # the SubIFD is the image


def test_carries_the_scanners_colour_untouched(tmp_path):
    """The sections came from a real device with a real colour spec, and
    stacking them changes none of it."""
    out, _ = _join(tmp_path, count=2)
    tags = fix.ifd0_tags(out)
    assert tags[50721] == fix.VUESCAN_COLOR_MATRIX_1
    assert tags[50729] == fix.VUESCAN_AS_SHOT_WHITE_XY
    assert tags[50708] == "Epson Perfection4490"
    assert tags[271] == "Epson"
    # AsShotWhiteXY was stated, so no AsShotNeutral is invented: the spec
    # allows one or the other, never both.
    assert 50728 not in tags


def test_adds_what_vuescan_leaves_unstated(tmp_path):
    """Their absence is not neutral — it is an invitation for the converter to
    supply its own."""
    out, _ = _join(tmp_path, count=2)
    raw, tags = fix.subifd_tags(out), fix.ifd0_tags(out)
    assert raw[50714] == (0, 0, 0)                  # BlackLevel
    assert raw[50717] == (65535, 65535, 65535)      # WhiteLevel
    assert raw[50713] == (1, 1)                     # BlackLevelRepeatDim
    assert tuple(tags[50940]) == dng.IDENTITY_TONE_CURVE
    assert tags[51110] == 1                         # DefaultBlackRender: None
    assert bytes(tags[50706]) == b"\x01\x04\x00\x00"    # DNGVersion 1.4
    assert bytes(tags[50707]) == b"\x01\x02\x00\x00"    # backward 1.2


def test_does_not_override_what_the_source_stated(tmp_path):
    """Carry through everything the source states; supply a default only for
    what it omits."""
    paths, _ = fix.write_roll(tmp_path, count=2,
                              extra_raw=[(50714, "H", 3, (512, 512, 512), False),
                                         (50717, "I", 3, (16383,) * 3, False)])
    out = tmp_path / "S0220.dng"
    sections = [scan.read_section(str(p)) for p in paths]
    profile = scan.read_profile(str(paths[0]), sections[0].height,
                                sum(s.height for s in sections))
    dng.write_joined_dng(str(out), [_pixels(p) for p in paths], profile,
                         sources=[str(p) for p in paths])
    raw = fix.subifd_tags(out)
    assert raw[50714] == (512, 512, 512)
    assert raw[50717] == (16383, 16383, 16383)


def test_fabricates_a_colour_spec_only_as_a_last_resort(tmp_path):
    """A source that states no matrix at all gets the sRGB placeholder and a
    neutral of (1, 1, 1) — enough for a converter to open the file and grade
    from, and not a measurement of anything."""
    bare = [t for t in fix.VUESCAN_IFD0 if t[0] not in (50721, 50729, 50708)]
    paths, _ = fix.write_roll(tmp_path, count=2, ifd0=bare)
    out = tmp_path / "S0220.dng"
    sections = [scan.read_section(str(p)) for p in paths]
    profile = scan.read_profile(str(paths[0]), sections[0].height,
                                sum(s.height for s in sections))
    dng.write_joined_dng(str(out), [_pixels(p) for p in paths], profile,
                         sources=[str(p) for p in paths])
    tags = fix.ifd0_tags(out)
    assert 50721 in tags and 50964 in tags      # ColorMatrix1, ForwardMatrix1
    assert tags[50778] == 21                    # CalibrationIlluminant1: D65
    assert tags[50728] == (1000000, 1000000) * 3    # AsShotNeutral (1, 1, 1)
    assert tags[50708] == dng.FALLBACK_CAMERA_MODEL


def test_keeps_the_scanner_dpi(tmp_path):
    """The one piece of geometry that must survive intact: it is what tells a
    converter how big the sheet physically is."""
    out, _ = _join(tmp_path, count=4, height=7, width=11)
    assert fix.subifd_tags(out)[282] == (1200, 1)
    assert fix.subifd_tags(out)[283] == (1200, 1)


def test_stamps_a_recognisable_marker(tmp_path):
    out, _ = _join(tmp_path, count=2)
    assert dng.carries_join_marker(str(out))
    assert "Joined from S0220-1.dng" in fix.ifd0_tags(out)[270]
    assert fix.ifd0_tags(out)[50827] == "S0220-1.dng"


# --- The thumbnail ---------------------------------------------------------- #
def test_thumbnail_samples_the_join_not_each_section(tmp_path):
    """A thumbnail pixel is a sample of the JOIN on a regular grid, so for each
    section it takes the rows whose position in the join falls on that grid —
    not row 0 of every section."""
    height, width, count = 40, 30, 3
    paths, joined = fix.write_roll(tmp_path, count=count, height=height,
                                   width=width)
    planes = [_pixels(p) for p in paths]
    step = dng.thumbnail_step(width, height * count)
    built = dng.build_thumbnail(planes, step)
    whole = dng.build_thumbnail([joined], step)
    assert np.array_equal(built, whole)


def test_thumbnail_fits_the_limit(tmp_path):
    out, _ = _join(tmp_path, count=4, height=400, width=300)
    with tifffile.TiffFile(str(out)) as tf:
        assert max(tf.pages[0].shape[:2]) <= 256


def test_thumbnail_dpi_is_derived_from_the_image(tmp_path):
    """The preview is a different size from the source's, so the source's DPI
    describes the wrong picture; dividing the image's by the stride that built
    the preview keeps it describing a sheet of the right physical size."""
    out, _ = _join(tmp_path, count=4, height=400, width=300)
    step = dng.thumbnail_step(300, 1600)
    num, den = fix.ifd0_tags(out)[282]
    assert num / den == pytest.approx(1200 / step, rel=1e-6)


# --- Refusals --------------------------------------------------------------- #
def test_refuses_mismatched_widths(tmp_path):
    planes = [fix.section_pixels(4, 5, 1), fix.section_pixels(4, 6, 2)]
    with pytest.raises(dng.JoinError, match="different widths"):
        dng.write_joined_dng(str(tmp_path / "x.dng"), planes,
                             scan.SourceProfile())


def test_refuses_a_join_past_the_tiff_offset_limit(tmp_path, monkeypatch):
    """BigTIFF would lift the ceiling but is not valid DNG, so a join that
    would not fit is refused before anything is written."""
    monkeypatch.setattr(dng, "_MAX_TIFF_BYTES", 1000)
    planes = [fix.section_pixels(40, 40, 1), fix.section_pixels(40, 40, 2)]
    with pytest.raises(dng.JoinError, match="32-bit offsets"):
        dng.write_joined_dng(str(tmp_path / "x.dng"), planes,
                             scan.SourceProfile())
    assert not (tmp_path / "x.dng").exists()
