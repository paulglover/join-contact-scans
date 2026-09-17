"""Planning the jobs, and the rules about what gets written over."""
import os

import numpy as np
import pytest

import dngfixture as fix
from joincontactscans import dng, join, scan


# --- Planning --------------------------------------------------------------- #
def test_plans_one_job_per_roll(tmp_path):
    fix.write_roll(tmp_path, roll="S0220", count=3)
    fix.write_roll(tmp_path, roll="S0221", count=2)
    plan = join.plan_jobs([str(tmp_path)])
    assert [(j.roll, len(j.sections)) for j in plan.jobs] == [
        ("S0220", 3), ("S0221", 2)]
    assert not plan.problems


def test_output_is_the_roll_id_beside_the_sections(tmp_path):
    fix.write_roll(tmp_path, roll="S0220", count=2)
    job = join.plan_jobs([str(tmp_path)]).jobs[0]
    assert job.output == str(tmp_path / "S0220.dng")


def test_out_dir_moves_the_output(tmp_path):
    fix.write_roll(tmp_path, roll="S0220", count=2)
    elsewhere = tmp_path / "joined"
    job = join.plan_jobs([str(tmp_path)], out_dir=str(elsewhere)).jobs[0]
    assert job.output == str(elsewhere / "S0220.dng")


def test_selection_order_does_not_matter(tmp_path):
    """Whatever order Finder hands the files over in, the sheet comes out the
    same."""
    paths, _ = fix.write_roll(tmp_path, count=4)
    shuffled = [str(p) for p in (paths[2], paths[0], paths[3], paths[1])]
    job = join.plan_jobs(shuffled).jobs[0]
    assert [s.seq for s in job.sections] == [1, 2, 3, 4]


def test_one_bad_roll_does_not_stop_the_others(tmp_path):
    """Five joined sheets and one clear complaint beats nothing and one
    complaint."""
    fix.write_roll(tmp_path, roll="S0220", count=3)
    fix.write_roll(tmp_path, roll="S0221", count=1)     # too few to join
    plan = join.plan_jobs([str(tmp_path)])
    assert [j.roll for j in plan.jobs] == ["S0220"]
    assert any("S0221" in p for p in plan.problems)


def test_nothing_to_plan_raises(tmp_path):
    with pytest.raises(scan.SectionError, match="no scan sections"):
        join.plan_jobs([str(tmp_path)])


def test_refuses_to_write_over_any_input(tmp_path):
    """Roll "S0220-1" would be written to S0220-1.dng, which is a section of
    roll "S0220". Checking only a roll's OWN sections would miss that and
    destroy a source."""
    fix.write_section(tmp_path / "S0220-1-1.dng", fix.section_pixels(4, 4, 1))
    fix.write_section(tmp_path / "S0220-1-2.dng", fix.section_pixels(4, 4, 2))
    fix.write_section(tmp_path / "S0220-1.dng", fix.section_pixels(4, 4, 3))
    fix.write_section(tmp_path / "S0220-2.dng", fix.section_pixels(4, 4, 4))
    plan = join.plan_jobs([str(tmp_path)])
    assert any("is a section of roll" in p for p in plan.problems)
    # ... and the roll that does not collide is still planned.
    assert [j.roll for j in plan.jobs] == ["S0220"]
    assert (tmp_path / "S0220-1.dng").exists()


# --- Running ---------------------------------------------------------------- #
def test_run_writes_and_verifies(tmp_path):
    paths, joined = fix.write_roll(tmp_path, count=3)
    plan = join.plan_jobs([str(tmp_path)])
    summary = join.run_jobs(plan.jobs)
    assert len(summary.written) == 1 and not summary.failures
    result = summary.written[0]
    assert result.verified == 3
    assert np.array_equal(dng.read_joined_dng(result.job.output), joined)


def test_dry_run_writes_nothing(tmp_path):
    fix.write_roll(tmp_path, count=2)
    plan = join.plan_jobs([str(tmp_path)])
    join.run_jobs(plan.jobs, dry_run=True)
    assert not (tmp_path / "S0220.dng").exists()


def test_existing_output_is_skipped_not_replaced(tmp_path):
    fix.write_roll(tmp_path, count=2)
    plan = join.plan_jobs([str(tmp_path)])
    (tmp_path / "S0220.dng").write_bytes(b"precious")
    summary = join.run_jobs(plan.jobs)
    assert not summary.written and len(summary.skipped) == 1
    assert "already exists" in summary.skipped[0].skipped
    assert (tmp_path / "S0220.dng").read_bytes() == b"precious"


def test_force_replaces_it(tmp_path):
    paths, joined = fix.write_roll(tmp_path, count=2)
    plan = join.plan_jobs([str(tmp_path)])
    (tmp_path / "S0220.dng").write_bytes(b"stale")
    summary = join.run_jobs(plan.jobs, force=True)
    assert len(summary.written) == 1
    assert np.array_equal(dng.read_joined_dng(str(tmp_path / "S0220.dng")),
                          joined)


def test_a_failed_join_leaves_the_previous_file_alone(tmp_path):
    """The rename is the last step, so an interrupted or failed join leaves the
    previous file exactly as it was rather than a truncated replacement."""
    fix.write_roll(tmp_path, count=2)
    plan = join.plan_jobs([str(tmp_path)])
    (tmp_path / "S0220.dng").write_bytes(b"precious")

    job = plan.jobs[0]
    job.sections[1] = type(job.sections[1])(
        path=str(tmp_path / "gone.dng"), roll="S0220", seq=2,
        width=job.sections[1].width, height=job.sections[1].height)
    result = join.run_job(job, force=True)

    assert result.error is not None
    assert (tmp_path / "S0220.dng").read_bytes() == b"precious"
    leftovers = [n for n in os.listdir(tmp_path) if n.endswith(".tmp")]
    assert not leftovers


def test_a_missing_out_dir_is_created(tmp_path):
    fix.write_roll(tmp_path, count=2)
    out = tmp_path / "joined" / "deeper"
    plan = join.plan_jobs([str(tmp_path)], out_dir=str(out))
    summary = join.run_jobs(plan.jobs)
    assert len(summary.written) == 1
    assert (out / "S0220.dng").exists()


def test_warnings_reach_the_result(tmp_path):
    for i in (1, 2, 4):
        fix.write_section(tmp_path / f"S0220-{i}.dng",
                          fix.section_pixels(3, 3, seed=i))
    summary = join.run_jobs(join.plan_jobs([str(tmp_path)]).jobs)
    assert any("not consecutive" in w
               for w in summary.written[0].warnings)


def test_a_joined_file_is_not_an_input_next_time(tmp_path):
    """The joined sheet sits beside its sources; running again must not try to
    fold it into itself."""
    fix.write_roll(tmp_path, count=2)
    join.run_jobs(join.plan_jobs([str(tmp_path)]).jobs)
    assert (tmp_path / "S0220.dng").exists()
    second = join.plan_jobs([str(tmp_path)])
    assert [j.roll for j in second.jobs] == ["S0220"]
    assert [s.name for s in second.jobs[0].sections] == [
        "S0220-1.dng", "S0220-2.dng"]
