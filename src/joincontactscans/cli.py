"""
Command-line interface.

    joincontactscans -i S0220 S0220-1.dng S0220-2.dng S0220-3.dng  # -> S0220.dng
    joincontactscans -i S0220 scans/S0220-*.dng --out ./joined
    joincontactscans -i S0220 scans/S0220-*.dng --dry-run   # show the plan only
    joincontactscans -i S0220 scans/S0220-*.dng --force     # replace existing

Every file named is a section of the one sheet, and the sheet is written as
`ROLLID.dng` beside the first section unless `--out` says otherwise. Selection
order does not matter: sections are stacked in filename order, with numbers
compared as numbers, so whatever order Finder or a shell glob hands them over
in, the sheet comes out the same.
"""
import argparse
import sys

from . import __version__
from . import join as join_mod
from . import scan as scan_mod


def _fmt_sections(job: join_mod.Job) -> str:
    return " + ".join(s.name for s in job.sections)



def _report(result: join_mod.JobResult, dry_run: bool) -> int:
    """Print what happened and return the exit code. Written so the lines a
    person needs are on stdout in the order they would ask for them, and every
    line that means "something did not happen" is on stderr."""
    job = result.job
    if dry_run:
        print("\nDry run — nothing written.")
    elif result.size is not None:
        h, w = result.size
        print(f"\nwrote {job.output}  ({w}x{h}, uint16)")
        if result.verified:
            print(f"      verified {result.verified}/{len(job.sections)} "
                  "sections pixel-identical")

    # Said after the result: these are notes about a file that WAS written,
    # and they read as notes rather than alarms once the result is on screen.
    for w in result.warnings:
        print(f"WARNING {job.roll}: {w}", file=sys.stderr)
    if result.skipped is not None:
        print(f"SKIPPED {job.roll}: {result.skipped}", file=sys.stderr)
    if result.error is not None:
        print(f"FAILED {job.roll}: {result.error}", file=sys.stderr)
    return 1 if (result.error or result.skipped) else 0


def cmd_join(args) -> int:
    job = join_mod.plan_job(args.inputs, args.roll_id, out_dir=args.out)

    verify = "verifying pixels" if args.verify else "no pixel verification"
    print(f"{job.roll}: {len(job.sections)} sections, {job.width}x{job.height}"
          f" · linear DNG · {verify}")
    print(f"  {_fmt_sections(job)}")
    print(f"  -> {job.output}")

    if args.dry_run:
        result = join_mod.JobResult(job=job, warnings=list(job.warnings))
    else:
        result = join_mod.run_job(job, force=args.force, verify=args.verify,
                                  version=__version__)
    return _report(result, args.dry_run)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="joincontactscans",
        description="Join the sections of a scanned contact sheet into one "
                    "linear DNG, stacked top to bottom in scan order.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__)
    p.add_argument("--version", action="version",
                   version=f"joincontactscans {__version__}")
    p.add_argument("inputs", nargs="+", metavar="SECTION.dng",
                   help="the sections of one contact sheet, at least two")
    p.add_argument("-i", "--roll-id", required=True, metavar="ROLLID",
                   help="the sheet's roll id: the joined file is ROLLID.dng, "
                        "and the id is written into its XMP dc:identifier")
    p.add_argument("-o", "--out", metavar="DIR",
                   help="write the joined sheet here (default: beside its "
                        "first section)")
    p.add_argument("-f", "--force", action="store_true",
                   help="replace an existing joined file instead of skipping "
                        "the sheet")
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
        # Several problems come as several lines; each gets one of its own.
        lines = str(e).splitlines() or [""]
        if len(lines) == 1:
            print(f"error: {lines[0]}", file=sys.stderr)
        else:
            print("error:", *(f"  {l}" for l in lines), sep="\n",
                  file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
