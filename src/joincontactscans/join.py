"""
Planning and running the joins.

Given a pile of paths — files, folders, or whatever a Finder selection handed
over — this works out which sections belong to which roll, what each roll's
joined file should be called, and then does the work.

One bad roll does not stop the others
-------------------------------------
A selection of six rolls where one has a section missing should still produce
five joined sheets and one clear complaint. So planning collects problems
instead of raising on the first one: a file that turns out not to be a linear
DNG, a roll with only one section, a roll whose sections disagree on width.
Each becomes a line in `Plan.problems`, the rest of the rolls become jobs, and
the CLI reports both. The exceptions are the problems that make planning itself
meaningless — nothing to work on, or a path that does not exist — which do
raise.

Nothing is overwritten by accident
----------------------------------
A joined file is only written where none exists, unless `force` says otherwise;
a roll whose output is already there is skipped and reported, not silently
replaced. The output path is also checked against the roll's own inputs, so a
roll id that collides with a section name cannot eat its own source.

Every write goes to a temporary file beside the destination and is renamed over
it only once it has been verified. That is what makes `--force` safe: an
interrupted or failed join leaves the previous file exactly as it was, rather
than a truncated replacement, and the rename is atomic because the temporary
file is in the same directory.
"""
import os
from contextlib import ExitStack
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from . import dng as dng_mod
from . import scan as scan_mod
from .scan import Section, SectionError

# Suffix for the in-progress file. It carries the process id so two runs over
# the same folder cannot collide, and it is not a section name, so a leftover
# from a killed run is never picked up as an input.
_PARTIAL_SUFFIX = ".joining-{pid}.tmp"


@dataclass
class Job:
    """One roll's worth of work: its sections, in order, and where the joined
    file goes."""
    roll: str
    sections: List[Section]
    output: str
    warnings: List[str] = field(default_factory=list)

    @property
    def width(self) -> int:
        return self.sections[0].width

    @property
    def height(self) -> int:
        """Rows in the joined image."""
        return sum(s.height for s in self.sections)

    @property
    def sources(self) -> List[str]:
        return [s.path for s in self.sections]


@dataclass
class Plan:
    """What `plan_jobs` worked out: the rolls that can be joined, and readable
    complaints about the ones that cannot."""
    jobs: List[Job] = field(default_factory=list)
    problems: List[str] = field(default_factory=list)


@dataclass
class JobResult:
    """What became of one job."""
    job: Job
    size: Optional[Tuple[int, int]] = None     # (height, width) as written
    verified: int = 0                          # sections checked pixel-for-pixel
    warnings: List[str] = field(default_factory=list)
    error: Optional[str] = None
    skipped: Optional[str] = None              # why nothing was written


@dataclass
class Summary:
    written: List[JobResult] = field(default_factory=list)
    skipped: List[JobResult] = field(default_factory=list)
    failures: List[JobResult] = field(default_factory=list)


def output_path(roll: str, sections: Sequence[Section],
                out_dir: Optional[str] = None) -> str:
    """Where roll `roll` is written: `ROLLID.dng`, beside its first section
    unless `out_dir` says otherwise."""
    directory = out_dir if out_dir else os.path.dirname(sections[0].path)
    return os.path.join(os.path.abspath(os.path.expanduser(directory)),
                        roll + dng_mod.OUTPUT_EXTENSION)


def plan_jobs(inputs: Sequence[str], out_dir: Optional[str] = None,
              recursive: bool = False) -> Plan:
    """Work out what to join. Raises SectionError only when there is nothing to
    plan; everything else comes back in `Plan.problems`."""
    paths = scan_mod.collect_section_files(inputs, recursive=recursive)
    if not paths:
        raise SectionError(
            "no scan sections found. Sections are named "
            f"ROLLID-SCANSEQ{scan_mod.SECTION_EXTENSION}, as in "
            f"S0220-1{scan_mod.SECTION_EXTENSION}")

    plan = Plan()
    sections: List[Section] = []
    for path in paths:
        try:
            sections.append(scan_mod.read_section(path))
        except SectionError as e:
            plan.problems.append(str(e))

    rolls: Dict[str, List[Section]] = scan_mod.group_into_rolls(sections)
    # Every section in the whole plan, not just the roll being placed: with one
    # roll's sections named `S0220-1-1.dng` and another's `S0220-1.dng`, the
    # first roll's output lands on the second roll's input. Checking only a
    # roll's own sections would miss that and destroy a source.
    inputs = {os.path.abspath(s.path): s for s in sections}
    for roll in sorted(rolls):
        items = rolls[roll]
        try:
            warnings = scan_mod.validate_roll(items)
        except SectionError as e:
            plan.problems.append(str(e))
            continue
        out = output_path(roll, items, out_dir)
        clash = inputs.get(out)
        if clash is not None:
            plan.problems.append(
                f"roll {roll!r} would be written to {clash.name}, which is a "
                f"section of roll {clash.roll!r} — rename it or use --out")
            continue
        plan.jobs.append(Job(roll=roll, sections=items, output=out,
                             warnings=warnings))
    return plan


def run_job(job: Job, force: bool = False, verify: bool = True,
            version: Optional[str] = None) -> JobResult:
    """Join one roll. Never raises: the outcome, good or bad, is in the
    result."""
    result = JobResult(job=job, warnings=list(job.warnings))
    if os.path.exists(job.output) and not force:
        result.skipped = (f"{os.path.basename(job.output)} already exists — "
                          "not overwriting it (use --force to replace it)")
        return result

    directory = os.path.dirname(job.output) or "."
    try:
        os.makedirs(directory, exist_ok=True)
    except OSError as e:
        result.error = f"cannot create {directory}: {e}"
        return result

    partial = job.output + _PARTIAL_SUFFIX.format(pid=os.getpid())
    try:
        with ExitStack() as stack:
            planes = [stack.enter_context(scan_mod.open_plane(s.path))
                      for s in job.sections]
            profile = scan_mod.read_profile(job.sections[0].path,
                                            job.sections[0].height, job.height)
            result.warnings.extend(profile.warnings)
            dng_mod.write_joined_dng(partial, planes, profile,
                                     sources=job.sources, version=version,
                                     name=os.path.basename(job.output))
        # Outside the ExitStack: the maps of the sources are closed, and the
        # verification below opens the written file fresh, as a reader would.
        dng_mod.verify_structure(partial, expect_shape=(job.height, job.width))
        if verify:
            result.verified = dng_mod.verify_pixels(partial, job.sources)
        os.replace(partial, job.output)
        result.size = (job.height, job.width)
    except Exception as e:
        result.error = str(e)
        # The destination has not been touched — the rename is the last step —
        # so all that needs cleaning up is the temporary file.
        try:
            if os.path.exists(partial):
                os.remove(partial)
        except OSError as cleanup:
            result.warnings.append(
                f"could not remove the incomplete {os.path.basename(partial)}: "
                f"{cleanup}")
    return result


def run_jobs(jobs: Sequence[Job], force: bool = False, verify: bool = True,
             dry_run: bool = False, version: Optional[str] = None,
             progress_cb: Optional[Callable[[int, int, Job], None]] = None
             ) -> Summary:
    """Run every job, sorting the results. A dry run reports what each job would
    do and reads nothing."""
    summary = Summary()
    total = len(jobs)
    for i, job in enumerate(jobs):
        if progress_cb is not None:
            progress_cb(i, total, job)
        if dry_run:
            summary.written.append(JobResult(job=job,
                                             size=(job.height, job.width),
                                             warnings=list(job.warnings)))
            continue
        result = run_job(job, force=force, verify=verify, version=version)
        if result.error is not None:
            summary.failures.append(result)
        elif result.skipped is not None:
            summary.skipped.append(result)
        else:
            summary.written.append(result)
    return summary
