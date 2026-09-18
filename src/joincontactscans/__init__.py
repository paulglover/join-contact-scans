"""
joincontactscans — join the sections of a scanned contact sheet into one linear
DNG.

A contact sheet wider or taller than the scanner's bed comes off it in sections:
`S0220-1.dng`, `S0220-2.dng`, and so on. Given those files and a roll id, this
stacks them top to bottom in filename order and writes one `ROLLID.dng` — a
linear DNG whose pixels are, section for section, byte-for-byte the pixels that
went in, and which says so: it reads the finished file back and checks before
reporting success.

The joined file carries the scanner's own colour metadata forward untouched, and
adds the tags a linear DNG needs so that a raw converter renders the sheet as
the data says rather than as its defaults would prefer. See dng.py for what is
carried, what is added, and why.

Public API:

    from joincontactscans import plan_job, run_job

    result = run_job(plan_job(["S0220-1.dng", "S0220-2.dng"], "S0220"))
"""
__version__ = "0.3.0"

from .dng import (JOIN_MARKER, OUTPUT_EXTENSION, carries_join_marker,
                  read_joined_dng, verify_pixels, verify_structure,
                  write_joined_dng)
from .join import Job, JobResult, output_path, plan_job, run_job
from .scan import (PHOTOMETRIC_LINEAR_RAW, SECTION_EXTENSION, Section,
                   SectionError, SourceProfile, collect_section_files,
                   open_plane, read_profile, read_section, scan_number,
                   validate_roll_id, validate_sections)

__all__ = [
    "__version__",
    "JOIN_MARKER", "OUTPUT_EXTENSION", "carries_join_marker",
    "read_joined_dng", "verify_pixels", "verify_structure", "write_joined_dng",
    "Job", "JobResult", "output_path", "plan_job", "run_job",
    "PHOTOMETRIC_LINEAR_RAW", "SECTION_EXTENSION", "Section", "SectionError",
    "SourceProfile", "collect_section_files", "open_plane", "read_profile",
    "read_section", "scan_number", "validate_roll_id", "validate_sections",
]
