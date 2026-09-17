"""
Command-line interface.

    joincontactscans S0220-1.dng S0220-2.dng S0220-3.dng   # -> S0220.dng
    joincontactscans /Volumes/Files/Vuescan                # every roll in there
    joincontactscans ./scans --out ./joined
    joincontactscans ./scans --dry-run                     # show the plan only
    joincontactscans ./scans --force                       # replace existing

Sections are named `ROLLID-SCANSEQ.dng` and the joined sheet is `ROLLID.dng`,
written beside the sections unless `--out` says otherwise. Selection order does
not matter: sections are stacked by their scan number, so whatever order Finder
or a shell glob hands them over in, the sheet comes out the same.
"""
import argparse
import os
import sys
from typing import List

from . import __version__
from . import join as join_mod
from . import scan as scan_mod


def _fmt_sections(job: join_mod.Job) -> str:
    return " + ".join(s.name for s in job.sections)


def _report(summary: join_mod.Summary, plan: join_mod.Plan,
            dry_run: bool) -> int:
    """Print what happened and return the exit code. Written so the lines a
    person needs are on stdout in the order they would ask for them, and every
    line that means "something did not happen" is on stderr."""
    if dry_run:
        print(f"\nDry run — nothing written. {len(summary.written)} sheet(s) "
              "would be created.")
    else:
        print()
        for r in summary.written:
            h, w = r.size or (0, 0)
            print(f"wrote {r.job.output}  ({w}x{h}, uint16)")
            if r.verified:
                print(f"      verified {r.verified}/{len(r.job.sections)} "
                      "sections pixel-identical")

    # Said after the results: these are notes about files that WERE written,
    # and they read as notes rather than alarms once the result is on screen.
    for r in summary.written:
        for w in r.warnings:
            print(f"WARNING {r.job.roll}: {w}", file=sys.stderr)
    for r in summary.skipped:
        print(f"SKIPPED {r.job.roll}: {r.skipped}", file=sys.stderr)
    for r in summary.failures:
        print(f"FAILED {r.job.roll}: {r.error}", file=sys.stderr)
    for problem in plan.problems:
        print(f"PROBLEM {problem}", file=sys.stderr)

    if not dry_run:
        print(f"\n{len(summary.written)} joined, {len(summary.failures)} "
              f"failed, {len(summary.skipped)} skipped")
    return 1 if (summary.failures or summary.skipped or plan.problems) else 0


def cmd_join(args) -> int:
    plan = join_mod.plan_jobs(args.inputs, out_dir=args.out,
                              recursive=args.recursive)
    if not plan.jobs:
        for problem in plan.problems:
            print(f"PROBLEM {problem}", file=sys.stderr)
        raise ValueError("nothing to join")

    verify = "verifying pixels" if args.verify else "no pixel verification"
    print(f"{len(plan.jobs)} roll(s) · linear DNG · {verify}")
    for job in plan.jobs:
        print(f"  {job.roll}: {len(job.sections)} sections, "
              f"{job.width}x{job.height}  ->  {os.path.basename(job.output)}")
        print(f"    {_fmt_sections(job)}")

    def progress(i, total, job):
        print(f"[{i + 1}/{total}] {job.roll} …", flush=True)

    summary = join_mod.run_jobs(plan.jobs, force=args.force, verify=args.verify,
                                dry_run=args.dry_run, version=__version__,
                                progress_cb=None if args.dry_run else progress)
    return _report(summary, plan, args.dry_run)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="joincontactscans",
        description="Join the sections of a scanned contact sheet into one "
                    "linear DNG, stacked top to bottom in scan order.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__)
    p.add_argument("--version", action="version",
                   version=f"joincontactscans {__version__}")
    p.add_argument("inputs", nargs="+",
                   help="scan sections (ROLLID-SCANSEQ.dng), and/or folders "
                        "of them")
    p.add_argument("-r", "--recursive", action="store_true",
                   help="descend into subfolders of any input folder")
    p.add_argument("-o", "--out", metavar="DIR",
                   help="write the joined sheets here (default: beside each "
                        "roll's first section)")
    p.add_argument("-f", "--force", action="store_true",
                   help="replace an existing joined file instead of skipping "
                        "the roll")
    p.add_argument("-n", "--dry-run", action="store_true",
                   help="show the plan; read and write nothing")
    p.add_argument("--no-verify", dest="verify", action="store_false",
                   default=True,
                   help="skip reading the finished file back to confirm every "
                        "section is pixel-identical to its source")
    p.set_defaults(func=cmd_join)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (ValueError, scan_mod.SectionError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
