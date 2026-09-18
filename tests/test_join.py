"""Planning the join, and the rules about what gets written over."""
import os

import numpy as np
import pytest

import dngfixture as fix
from joincontactscans import dng, join, scan


def _plan(paths, roll="S0220", **kwargs):
    return join.plan_job([str(p) for p in paths], roll, **kwargs)


# --- Planning --------------------------------------------------------------- #
def test_output_is_the_roll_id_beside_the_first_section(tmp_path):
    paths, _ = fix.write_roll(tmp_path, count=2)
    job = _plan(paths, "Portra 400")
    assert job.roll == "Portra 400"
    assert job.output == str(tmp_path / "Portra 400.dng")


def test_roll_id_is_trimmed(tmp_path):
    paths, _ = fix.write_roll(tmp_path, count=2)
    assert _plan(paths, "  S0221 ").output == str(tmp_path / "S0221.dng")


def test_blank_roll_id_is_refused(tmp_path):
    paths, _ = fix.write_roll(tmp_path, count=2)
    with pytest.raises(scan.SectionError, match="blank"):
        _plan(paths, " ")


def test_out_dir_moves_the_output(tmp_path):
    paths, _ = fix.write_roll(tmp_path, count=2)
    elsewhere = tmp_path / "joined"
    job = _plan(paths, out_dir=str(elsewhere))
    assert job.output == str(elsewhere / "S0220.dng")


def test_every_file_named_is_a_section_of_the_sheet(tmp_path):
    """Nothing about the names decides which sheet a file is part of."""
    fix.write_section(tmp_path / "A-1.dng", fix.section_pixels(3, 5, 1))
    fix.write_section(tmp_path / "B-7.dng", fix.section_pixels(3, 5, 2))
    fix.write_section(tmp_path / "loose.dng", fix.section_pixels(3, 5, 3))
    job = _plan(tmp_path.glob("*.dng"), "sheet")
    assert [s.name for s in job.sections] == ["A-1.dng", "B-7.dng",
                                              "loose.dng"]


def test_selection_order_does_not_matter(tmp_path):
    """Whatever order Finder hands the files over in, the sheet comes out the
    same."""
    paths, _ = fix.write_roll(tmp_path, count=4)
    job = _plan([paths[2], paths[0], paths[3], paths[1]])
    assert [s.seq for s in job.sections] == [1, 2, 3, 4]


def test_every_unreadable_section_is_named(tmp_path):
    paths, _ = fix.write_roll(tmp_path, count=2)
    (tmp_path / "S0220-3.dng").write_bytes(b"nonsense")
    (tmp_path / "S0220-4.dng").write_bytes(b"nonsense")
    with pytest.raises(scan.SectionError) as e:
        _plan(sorted(tmp_path.glob("*.dng")))
    assert "S0220-3.dng" in str(e.value) and "S0220-4.dng" in str(e.value)


def test_a_lone_section_is_refused(tmp_path):
    paths, _ = fix.write_roll(tmp_path, count=1)
    with pytest.raises(scan.SectionError, match="at least two"):
        _plan(paths)


def test_refuses_to_write_over_an_input(tmp_path):
    """Roll id "S0220-1" would be written to S0220-1.dng, which is one of the
    sheet's own sections."""
    paths, _ = fix.write_roll(tmp_path, count=2)
    with pytest.raises(scan.SectionError, match="one of its own sections"):
        _plan(paths, "S0220-1")
    assert (tmp_path / "S0220-1.dng").exists()


# --- Running ---------------------------------------------------------------- #
def test_run_writes_and_verifies(tmp_path):
    paths, joined = fix.write_roll(tmp_path, count=3)
    result = join.run_job(_plan(paths))
    assert result.error is None and result.skipped is None
    assert result.verified == 3
    assert np.array_equal(dng.read_joined_dng(result.job.output), joined)


def test_the_roll_id_is_the_xmp_identifier(tmp_path):
    paths, _ = fix.write_roll(tmp_path, count=2)
    result = join.run_job(_plan(paths, "Portra.400"))
    xmp = bytes(fix.ifd0_tags(tmp_path / "Portra.400.dng")[700]).decode()
    assert "<dc:identifier>Portra.400</dc:identifier>" in xmp
    assert result.error is None


def test_existing_output_is_skipped_not_replaced(tmp_path):
    paths, _ = fix.write_roll(tmp_path, count=2)
    job = _plan(paths)
    (tmp_path / "S0220.dng").write_bytes(b"precious")
    result = join.run_job(job)
    assert "already exists" in result.skipped
    assert (tmp_path / "S0220.dng").read_bytes() == b"precious"


def test_force_replaces_it(tmp_path):
    paths, joined = fix.write_roll(tmp_path, count=2)
    job = _plan(paths)
    (tmp_path / "S0220.dng").write_bytes(b"stale")
    result = join.run_job(job, force=True)
    assert result.error is None and result.skipped is None
    assert np.array_equal(dng.read_joined_dng(str(tmp_path / "S0220.dng")),
                          joined)


def test_a_failed_join_leaves_the_previous_file_alone(tmp_path):
    """The rename is the last step, so an interrupted or failed join leaves the
    previous file exactly as it was rather than a truncated replacement."""
    paths, _ = fix.write_roll(tmp_path, count=2)
    job = _plan(paths)
    (tmp_path / "S0220.dng").write_bytes(b"precious")

    job.sections[1] = type(job.sections[1])(
        path=str(tmp_path / "gone.dng"), seq=2,
        width=job.sections[1].width, height=job.sections[1].height)
    result = join.run_job(job, force=True)

    assert result.error is not None
    assert (tmp_path / "S0220.dng").read_bytes() == b"precious"
    leftovers = [n for n in os.listdir(tmp_path) if n.endswith(".tmp")]
    assert not leftovers


def test_a_missing_out_dir_is_created(tmp_path):
    paths, _ = fix.write_roll(tmp_path, count=2)
    out = tmp_path / "joined" / "deeper"
    result = join.run_job(_plan(paths, out_dir=str(out)))
    assert result.error is None
    assert (out / "S0220.dng").exists()


def test_warnings_reach_the_result(tmp_path):
    paths = []
    for i in (1, 2, 4):
        paths.append(tmp_path / f"S0220-{i}.dng")
        fix.write_section(paths[-1], fix.section_pixels(3, 3, seed=i))
    result = join.run_job(_plan(paths))
    assert any("not consecutive" in w for w in result.warnings)
