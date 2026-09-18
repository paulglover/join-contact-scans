"""Reading, naming, ordering and validating the sections."""
import dataclasses
import os
import time

import numpy as np
import pytest

import dngfixture as fix
from joincontactscans import scan


# --- Filenames ------------------------------------------------------------- #
@pytest.mark.parametrize("name, roll, seq", [
    ("S0220-1.dng", "S0220", 1),
    ("S0220-10.dng", "S0220", 10),
    ("S0220-007.dng", "S0220", 7),
    # A roll id may contain hyphens of its own: the split is on the LAST one.
    ("2026-05-portra-3.dng", "2026-05-portra", 3),
])
def test_parse_section_name(name, roll, seq):
    assert scan.parse_section_name("/scans/" + name) == (roll, seq)


@pytest.mark.parametrize("name", [
    "S0220.dng",        # what this tool WRITES: no sequence, not an input
    "S0220-.dng",
    "S0220-a.dng",
    "S0220-1a.dng",
    "nohyphen.dng",
])
def test_parse_section_name_rejects(name):
    with pytest.raises(scan.SectionError):
        scan.parse_section_name("/scans/" + name)


def test_joined_output_is_not_a_section():
    """The reason a finished join can sit beside its sources: its name has no
    scan number, so a second run does not pick it up as an input."""
    assert scan.is_section_path("/scans/S0220-1.dng")
    assert not scan.is_section_path("/scans/S0220.dng")
    assert not scan.is_section_path("/scans/S0220-1.tif")


def test_collect_skips_non_sections_in_a_folder(tmp_path):
    fix.write_roll(tmp_path, count=2)
    (tmp_path / "S0220.dng").write_bytes(b"not a dng")
    (tmp_path / "notes.txt").write_text("hi")
    found = [p.rsplit("/", 1)[-1]
             for p in scan.collect_section_files([str(tmp_path)])]
    assert found == ["S0220-1.dng", "S0220-2.dng"]


def test_collect_deduplicates(tmp_path):
    paths, _ = fix.write_roll(tmp_path, count=2)
    both = [str(paths[0]), str(paths[0]), str(paths[1])]
    assert len(scan.collect_section_files(both)) == 2


def test_collect_missing_path_raises(tmp_path):
    with pytest.raises(scan.SectionError, match="no such file"):
        scan.collect_section_files([str(tmp_path / "nope.dng")])


# --- Reading --------------------------------------------------------------- #
def test_read_section(tmp_path):
    fix.write_section(tmp_path / "S0220-2.dng",
                      fix.section_pixels(9, 13, seed=1))
    s = scan.read_section(str(tmp_path / "S0220-2.dng"))
    assert (s.roll, s.seq, s.width, s.height) == ("S0220", 2, 13, 9)


def test_read_section_rejects_mosaiced(tmp_path):
    """A CFA DNG must not be joined: section two's mosaic phase depends on
    section one's height, and getting that wrong looks like a colour problem
    three steps later."""
    fix.write_section(tmp_path / "S0220-1.dng",
                      fix.section_pixels(4, 4, seed=1),
                      photometric=32803)          # CFA
    with pytest.raises(scan.SectionError, match="not a linear DNG"):
        scan.read_section(str(tmp_path / "S0220-1.dng"))


def test_read_section_rejects_unreadable(tmp_path):
    (tmp_path / "S0220-1.dng").write_bytes(b"nonsense")
    with pytest.raises(scan.SectionError, match="cannot be read"):
        scan.read_section(str(tmp_path / "S0220-1.dng"))


def test_open_plane_gives_the_pixels(tmp_path):
    pixels = fix.write_section(tmp_path / "S0220-1.dng",
                               fix.section_pixels(6, 5, seed=3))
    with scan.open_plane(str(tmp_path / "S0220-1.dng")) as plane:
        assert np.array_equal(np.asarray(plane), pixels)


# --- Ordering and validation ----------------------------------------------- #
def test_sections_stack_in_numeric_not_string_order(tmp_path):
    """Sorted as text, S0220-10 would land between 1 and 2 and silently
    interleave the sheet."""
    for i in (1, 2, 10, 11):
        fix.write_section(tmp_path / f"S0220-{i}.dng",
                          fix.section_pixels(3, 3, seed=i))
    sections = [scan.read_section(p)
                for p in scan.collect_section_files([str(tmp_path)])]
    rolls = scan.group_into_rolls(sections)
    assert [s.seq for s in rolls["S0220"]] == [1, 2, 10, 11]


def test_group_separates_rolls(tmp_path):
    fix.write_roll(tmp_path, roll="S0220", count=2)
    fix.write_roll(tmp_path, roll="S0221", count=3)
    sections = [scan.read_section(p)
                for p in scan.collect_section_files([str(tmp_path)])]
    rolls = scan.group_into_rolls(sections)
    assert {r: len(v) for r, v in rolls.items()} == {"S0220": 2, "S0221": 3}


def test_validate_rejects_a_lone_section(tmp_path):
    paths, _ = fix.write_roll(tmp_path, count=1)
    sections = [scan.read_section(str(p)) for p in paths]
    with pytest.raises(scan.SectionError, match="at least two"):
        scan.validate_roll(sections)


def test_validate_rejects_mismatched_width(tmp_path):
    fix.write_section(tmp_path / "S0220-1.dng", fix.section_pixels(4, 11, 1))
    fix.write_section(tmp_path / "S0220-2.dng", fix.section_pixels(4, 12, 2))
    sections = [scan.read_section(str(tmp_path / f"S0220-{i}.dng"))
                for i in (1, 2)]
    with pytest.raises(scan.SectionError, match="pixels wide"):
        scan.validate_roll(sections)


def test_validate_rejects_duplicate_sequence(tmp_path):
    """Two files claiming the same place in the stack leave the order
    ambiguous, and quietly picking one would be a guess about the sheet."""
    paths, _ = fix.write_roll(tmp_path, count=2)
    first = scan.read_section(str(paths[0]))
    twin = dataclasses.replace(first, path=str(paths[1]))
    with pytest.raises(scan.SectionError, match="two section 1s"):
        scan.validate_roll([first, twin])


def test_validate_warns_but_allows_a_gap(tmp_path):
    """A missing scan number is a missing scan, not a reason to refuse the
    ones that are there."""
    for i in (1, 2, 4):
        fix.write_section(tmp_path / f"S0220-{i}.dng",
                          fix.section_pixels(3, 3, seed=i))
    sections = [scan.read_section(p)
                for p in scan.collect_section_files([str(tmp_path)])]
    warnings = scan.validate_roll(sections)
    assert any("not consecutive" in w for w in warnings)


def test_validate_warns_on_divergent_colour(tmp_path):
    fix.write_section(tmp_path / "S0220-1.dng", fix.section_pixels(3, 3, 1))
    fix.write_section(tmp_path / "S0220-2.dng", fix.section_pixels(3, 3, 2),
                      ifd0=[t for t in fix.VUESCAN_IFD0 if t[0] != 50708]
                      + [(50708, "s", 0, "Some Other Scanner", True)])
    sections = [scan.read_section(str(tmp_path / f"S0220-{i}.dng"))
                for i in (1, 2)]
    warnings = scan.validate_roll(sections)
    assert any("different colour metadata" in w for w in warnings)


# --- The metadata carried forward ------------------------------------------ #
def test_profile_carries_vuescan_colour(tmp_path):
    fix.write_section(tmp_path / "S0220-1.dng", fix.section_pixels(4, 4, 1))
    profile = scan.read_profile(str(tmp_path / "S0220-1.dng"), 4, 12)
    carried = {code: value for code, _f, _c, value, _w in profile.ifd0}
    assert carried[50721] == fix.VUESCAN_COLOR_MATRIX_1
    assert carried[50729] == fix.VUESCAN_AS_SHOT_WHITE_XY
    assert carried[50708] == "Epson Perfection4490"
    assert profile.resolution == (1200.0, 1200.0)
    assert not profile.warnings


def test_profile_lifts_the_capture_date_out_of_exif(tmp_path):
    """VueScan puts the date in the EXIF sub-IFD. The joined file states it in
    IFD0, where a converter looks for it, so the sheet still sorts by when it
    was scanned."""
    fix.write_section(tmp_path / "S0220-1.dng", fix.section_pixels(4, 4, 1),
                      datetime_digitized="2026:09:17 13:01:06")
    profile = scan.read_profile(str(tmp_path / "S0220-1.dng"), 4, 12)
    carried = {code: value for code, _f, _c, value, _w in profile.ifd0}
    assert carried[306] == "2026:09:17 13:01:06"
    assert carried[36867] == "2026:09:17 13:01:06"


def test_profile_keeps_a_stated_datetime_beside_the_capture_time(tmp_path):
    """DateTimeOriginal is the capture time; a DateTime the source states is
    its own and is carried as it was."""
    fix.write_section(tmp_path / "S0220-1.dng", fix.section_pixels(4, 4, 1),
                      extra_ifd0=[(306, "s", 0, "2026:09:18 09:00:00", True)],
                      datetime_digitized="2026:09:17 13:01:06")
    profile = scan.read_profile(str(tmp_path / "S0220-1.dng"), 4, 12)
    carried = {code: value for code, _f, _c, value, _w in profile.ifd0}
    assert carried[306] == "2026:09:18 09:00:00"
    assert carried[36867] == "2026:09:17 13:01:06"
    assert [code for code, *_ in profile.ifd0].count(306) == 1


def test_profile_falls_back_to_the_file_time_for_capture_time(tmp_path):
    """A section that records no date of its own is dated by its file."""
    path = tmp_path / "S0220-1.dng"
    fix.write_section(path, fix.section_pixels(4, 4, 1))
    when = time.mktime((2026, 9, 17, 13, 1, 6, 0, 0, -1))
    os.utime(path, (when, when))
    profile = scan.read_profile(str(path), 4, 12)
    carried = {code: value for code, _f, _c, value, _w in profile.ifd0}
    assert carried[36867] == "2026:09:17 13:01:06"
    assert carried[306] == "2026:09:17 13:01:06"


def test_profile_restates_a_full_frame_crop_against_the_join(tmp_path):
    """DefaultCropSize covering the whole section is the same statement about
    the joined image with a bigger height."""
    fix.write_section(tmp_path / "S0220-1.dng", fix.section_pixels(4, 6, 1),
                      extra_raw=[(50720, "I", 2, (6, 4), False),
                                 (50719, "I", 2, (0, 0), False)])
    profile = scan.read_profile(str(tmp_path / "S0220-1.dng"), 4, 12)
    carried = {code: value for code, _f, _c, value, _w in profile.raw}
    assert carried[50720] == (6, 12)     # width kept, height is the join's
    assert carried[50719] == (0, 0)
    assert not profile.warnings


def test_profile_drops_a_partial_crop_and_says_so(tmp_path):
    """Extending someone else's crop is a guess, and a wrong guess here is
    invisible until the bottom of the sheet is missing."""
    fix.write_section(tmp_path / "S0220-1.dng", fix.section_pixels(4, 6, 1),
                      extra_raw=[(50720, "I", 2, (5, 3), False)])
    profile = scan.read_profile(str(tmp_path / "S0220-1.dng"), 4, 12)
    carried = {code for code, *_ in profile.raw}
    assert 50720 not in carried
    assert any("DefaultCropSize" in w for w in profile.warnings)


def test_profile_drops_per_row_black_levels(tmp_path):
    fix.write_section(tmp_path / "S0220-1.dng", fix.section_pixels(4, 6, 1),
                      extra_raw=[(50716, "H", 4, (1, 2, 3, 4), False)])
    profile = scan.read_profile(str(tmp_path / "S0220-1.dng"), 4, 12)
    assert any("BlackLevelDeltaV" in w for w in profile.warnings)


def test_profile_never_raises_on_a_bad_file(tmp_path):
    """The pixels are the part that cannot be reconstructed; no metadata
    problem is worth losing them over."""
    (tmp_path / "S0220-1.dng").write_bytes(b"nonsense")
    profile = scan.read_profile(str(tmp_path / "S0220-1.dng"), 4, 12)
    assert profile.warnings and not profile.ifd0


def test_validate_warns_when_section_one_is_absent(tmp_path):
    """The gap the consecutiveness check cannot see: 2, 3, 4 is a perfectly
    consecutive run, and joining it yields a valid-looking sheet quietly
    missing its top. This is what a droplet handed a split selection produced
    before it learned to gather the deliveries."""
    for i in (2, 3, 4):
        fix.write_section(tmp_path / f"S0220-{i}.dng",
                          fix.section_pixels(3, 3, seed=i))
    sections = [scan.read_section(p)
                for p in scan.collect_section_files([str(tmp_path)])]
    warnings = scan.validate_roll(sections)
    assert any("missing its top" in w for w in warnings)


def test_validate_is_quiet_about_a_complete_roll(tmp_path):
    for i in (1, 2, 3):
        fix.write_section(tmp_path / f"S0220-{i}.dng",
                          fix.section_pixels(3, 3, seed=i))
    sections = [scan.read_section(p)
                for p in scan.collect_section_files([str(tmp_path)])]
    assert scan.validate_roll(sections) == []
