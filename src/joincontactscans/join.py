"""
Planning and running the join.

Given the files the user named as one sheet's sections and the roll id to call
it by, this works out the order the sections stack in and where the joined file
goes, and then does the work.

Every problem is said at once
-----------------------------
Planning reads every section before it gives up, so a selection with two bad
files reports both in one message rather than one per attempt. Nothing is
joined unless everything can be: a sheet with a section left out is a
valid-looking file with a hole in it.

Nothing is overwritten by accident
----------------------------------
A joined file is only written where none exists, unless `force` says otherwise;
a sheet whose output is already there is skipped and reported, not silently
replaced. The output path is also checked against the inputs, so a roll id that
matches a section's name cannot eat its own source.

Every write goes to a temporary file beside the destination and is renamed over
it only once it has been verified. That is what makes `--force` safe: an
interrupted or failed join leaves the previous file exactly as it was, rather
than a truncated replacement, and the rename is atomic because the temporary
file is in the same directory.
"""
import os
from contextlib import ExitStack
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

from . import dng as dng_mod
from . import scan as scan_mod
from .scan import Section, SectionError

# Suffix for the in-progress file. It carries the process id so two runs over
# the same folder cannot collide, and it is not a section name, so a leftover
# from a killed run is never picked up as an input.
_PARTIAL_SUFFIX = ".joining-{pid}.tmp"


@dataclass
class Job:
    """One sheet's worth of work: its roll id, its sections in stacking order,
    and where the joined file goes."""
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
class JobResult:
    """What became of one job."""
    job: Job
    size: Optional[Tuple[int, int]] = None     # (height, width) as written
    verified: int = 0                          # sections checked pixel-for-pixel
    warnings: List[str] = field(default_factory=list)
    error: Optional[str] = None
    skipped: Optional[str] = None              # why nothing was written


def output_path(roll: str, sections: Sequence[Section],
                out_dir: Optional[str] = None) -> str:
    """Where the sheet is written: `ROLLID.dng`, beside its first section
    unless `out_dir` says otherwise."""
    directory = out_dir if out_dir else os.path.dirname(sections[0].path)
    return os.path.join(os.path.abspath(os.path.expanduser(directory)),
                        roll + dng_mod.OUTPUT_EXTENSION)


def plan_job(inputs: Sequence[str], roll_id: str,
             out_dir: Optional[str] = None) -> Job:
    """Work out the join. Raises SectionError, naming every problem found,
    when the files cannot be joined as one sheet."""
    roll = scan_mod.validate_roll_id(roll_id)
    paths = scan_mod.collect_section_files(inputs)

    sections: List[Section] = []
    problems: List[str] = []
    for path in paths:
        try:
            sections.append(scan_mod.read_section(path))
        except SectionError as e:
            problems.append(str(e))
    if problems:
        raise SectionError("\n".join(problems))

    warnings = scan_mod.validate_sections(sections)
    out = output_path(roll, sections, out_dir)
    for s in sections:
        if os.path.abspath(s.path) == out:
            raise SectionError(
                f"the sheet would be written to {s.name}, which is one of its "
                "own sections — choose another roll id or use --out")
    return Job(roll=roll, sections=sections, output=out, warnings=warnings)


def run_job(job: Job, force: bool = False, verify: bool = True,
            version: Optional[str] = None) -> JobResult:
    """Join one sheet. Never raises: the outcome, good or bad, is in the
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
                                     identifier=job.roll)
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
