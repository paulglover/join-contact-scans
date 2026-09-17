"""
joincontactscans — join the sections of a scanned contact sheet into one linear
DNG.

A contact sheet wider or taller than the scanner's bed comes off it in sections:
`S0220-1.dng`, `S0220-2.dng`, and so on. This stacks them top to bottom in scan
order and writes one `S0220.dng` — a linear DNG whose pixels are, section for
section, byte-for-byte the pixels that went in, and which says so: it reads the
finished file back and checks before reporting success.

The joined file carries the scanner's own colour metadata forward untouched, and
adds the tags a linear DNG needs so that a raw converter renders the sheet as
the data says rather than as its defaults would prefer. See dng.py for what is
carried, what is added, and why.

Public API:

    from joincontactscans import plan_jobs, run_jobs
"""
__version__ = "0.1.0"

from .dng import (JOIN_MARKER, OUTPUT_EXTENSION, carries_join_marker,
                  read_joined_dng, verify_pixels, verify_structure,
                  write_joined_dng)
from .join import (Job, JobResult, Plan, Summary, output_path, plan_jobs,
                   run_job, run_jobs)
from .scan import (PHOTOMETRIC_LINEAR_RAW, SECTION_EXTENSION, Section,
                   SectionError, SourceProfile, collect_section_files,
                   group_into_rolls, is_section_path, open_plane,
                   parse_section_name, read_profile, read_section,
                   validate_roll)

__all__ = [
    "__version__",
    "JOIN_MARKER", "OUTPUT_EXTENSION", "carries_join_marker",
    "read_joined_dng", "verify_pixels", "verify_structure", "write_joined_dng",
    "Job", "JobResult", "Plan", "Summary", "output_path", "plan_jobs",
    "run_job", "run_jobs",
    "PHOTOMETRIC_LINEAR_RAW", "SECTION_EXTENSION", "Section", "SectionError",
    "SourceProfile", "collect_section_files", "group_into_rolls",
    "is_section_path", "open_plane", "parse_section_name", "read_profile",
    "read_section", "validate_roll",
]
