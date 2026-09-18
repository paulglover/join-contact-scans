# joincontactscans

[![CI](https://github.com/paulglover/join-contact-scans/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/paulglover/join-contact-scans/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/paulglover/join-contact-scans?label=release)](https://github.com/paulglover/join-contact-scans/releases)

Join the sections of a scanned contact sheet into one linear DNG.

A contact sheet bigger than the scanner's bed comes off it in sections —
`S0220-1.dng`, `S0220-2.dng`, `S0220-3.dng`, `S0220-4.dng`. Give this those
files and the sheet's roll id, and it stacks them top to bottom and writes one
`S0220.dng`.

The pixels are the pixels. Nothing is scaled, blended, aligned, feathered or
colour-matched at the seams: the row below the last row of section one is the
first row of section two, unchanged. The tool reads the finished file back and
checks that, section by section, before it reports success.

```
$ joincontactscans --roll-id S0220 /Volumes/Files/Vuescan/S0220-*.dng
S0220: 4 sections, 9442x12800 · linear DNG · verifying pixels
  S0220-1.dng + S0220-2.dng + S0220-3.dng + S0220-4.dng
  -> /Volumes/Files/Vuescan/S0220.dng

wrote /Volumes/Files/Vuescan/S0220.dng  (9442x12800, uint16)
      verified 4/4 sections pixel-identical
```

## Install

```bash
git clone https://github.com/paulglover/join-contact-scans.git
cd join-contact-scans
pip install -e .
```

Needs Python 3.9+, `numpy` and `tifffile`. No scanner, no Adobe SDK, no
exiftool.

## Use

```bash
joincontactscans -i S0220 S0220-1.dng S0220-2.dng S0220-3.dng   # -> S0220.dng, beside them
joincontactscans -i S0220 scans/S0220-*.dng --out ./joined
joincontactscans -i S0220 scans/S0220-*.dng --dry-run           # show the plan only
joincontactscans -i S0220 scans/S0220-*.dng --force             # replace an existing sheet
joincontactscans -i S0220 scans/S0220-*.dng --no-verify         # skip the readback
```

| | |
|---|---|
| `-i`, `--roll-id ROLLID` | **required.** The sheet's roll id: the joined file is `ROLLID.dng`, and the id is written into its XMP `dc:identifier` |
| `-o`, `--out DIR` | write the joined sheet here (default: beside its first section) |
| `-f`, `--force` | replace an existing joined file instead of skipping the sheet |
| `-n`, `--dry-run` | show the plan; read and write nothing |
| `--no-verify` | skip reading the finished file back to confirm every section is pixel-identical |

### Inputs

Every file named is a section of **one** sheet. At least two, no upper limit.
Each must be a `.dng` file; folders are refused, as is anything else, and all of
the refusals are listed together rather than one per attempt.

Nothing about a file's name decides which sheet it belongs to — that is what
the roll id is for — but the names do decide the **order**. Sections are
stacked in filename order with the numbers compared as numbers: sorted as
text, `S0220-10` would land between `S0220-1` and `S0220-2` and silently
interleave a ten-section sheet. So selection order does not matter: whatever
order Finder or a shell glob hands the files over in, the sheet comes out the
same.

When every name ends in a scan number (`…-N.dng`), a gap in the numbers, or a
sheet that does not start at 1, is warned about.

### The roll id

The roll id names the output — `ROLLID.dng` — and is written into the file's
XMP as `dc:identifier`. Whitespace around it is trimmed; a blank one is refused,
as is one containing `/` (it is a file name, not a path — use `--out` for the
folder). A roll id that would put the sheet on top of one of its own sections is
refused too.

### Nothing is deleted, nothing is overwritten

Source files are never touched. A sheet whose joined file already exists is
reported and skipped, not replaced; `--force` replaces it.

Every write goes to a temporary file beside the destination and is renamed over
it only once it has been verified. That is what makes `--force` safe: a failed
or interrupted join leaves the previous file exactly as it was, rather than a
truncated replacement.

### What stops a join, and what is only a warning

Things that stop it: fewer than two sections, sections that disagree on width,
a file that is not a linear DNG, a folder or non-DNG file among the inputs, a
blank roll id. Things that are only warnings: a gap in the scan numbers, a
sheet that starts at something other than section 1, sections whose colour
metadata disagrees.

The "starts at section 2" check matters more than it looks. A run of sections
2, 3, 4 is perfectly consecutive, so only this check catches it — and what it
catches is a valid-looking sheet quietly missing its top.

## What trichrome has to do with it

[`trichrome`](https://github.com/paulglover/trichrome) comes up throughout what
follows, so: it is the companion tool, doing a separate job — merging
red/green/blue-light RAW triplets into a linear DNG. This one stacks several
sections of one sheet. What they share is the output and the way it is reached:
the same file layout, the same linear-declaration tags, and the same kind of
macOS droplet for opening a selection from the Finder. Sheets joined here and
frames merged there land in a converter looking like siblings, which is the
point — the grading you work out for one applies to the other.

Colour is where they part company, and the next section says why.

## What the output file is

A linear DNG: `PhotometricInterpretation = 34892` (LinearRaw), three 16-bit
samples per pixel, uncompressed, with the full-resolution image in a SubIFD and
a small sRGB-encoded preview in IFD0 — the layout the DNG spec prescribes, and
the one `trichrome` writes.

DNG rather than TIFF because the join is still raw data. Every converter treats
a TIFF as an already-rendered image: no raw white balance, no camera profile, and
exposure applied after the tone curve rather than before it. For a negative that
means grading the orange mask without the controls built for it. A linear DNG is
ingested through the raw pipeline instead — white balance as a Kelvin/tint pair,
exposure in stops ahead of the curve.

### What is carried, and what is added

The policy is one sentence: **carry through everything the source states about
its own colour, and supply the linear-declaration defaults only for what it
omits.**

The sections came from a real device with a real colour spec, and stacking them
changes none of it. VueScan's `ColorMatrix1`, `AsShotWhiteXY`,
`UniqueCameraModel`, `Make`, `Model` and the scan DPI are re-emitted untouched,
along with the capture date. The joined sheet's `DateTimeOriginal` (and
`DateTime`, if the source states none) is the first section's capture time —
lifted out of the EXIF sub-IFD into IFD0, where a converter looks for it, so the
sheet sorts by when it was scanned. A first section that records no date at all
falls back to its file modification time.

The roll id (`S0220`) is written into the file's XMP as `dc:identifier`, so the
sheet can still be identified after a catalogue renames it.

This is where the tool differs from trichrome. Trichrome *must* fabricate a
colour spec, because a three-light merge is not colorimetric and no honest
matrix exists for it. Here one exists and the scanner wrote it down. (For
VueScan the two agree anyway: its `ColorMatrix1` is exactly the sRGB/Rec.709
matrix under D65, which is the convention trichrome assumes.)

What VueScan does not write, the tool adds, because their absence is not neutral
— it is an invitation for the converter to supply its own:

- **`BlackLevel = 0`, `WhiteLevel = 65535`.** VueScan states its range as
  `MinSampleValue`/`MaxSampleValue`, TIFF tags a DNG reader does not consult.
  Without the DNG tags a reader falls back to guessing a sensor pedestal.
- **`ProfileToneCurve` = identity.** Given a profile with no curve of its own,
  Adobe's SDK — and everything modelled on it — renders through its default: a
  contrasty S built for camera sensor data, applied to a scan that has no
  business being toned.
- **`DefaultBlackRender = None`.** Left at Auto, a converter subtracts its own
  estimate of a black point. On a negative that estimate is dominated by the
  orange mask, so it is a grade being made for you out of the mask density.

`DNGVersion` is 1.4.0.0 because `DefaultBlackRender` is a 1.4 tag;
`DNGBackwardVersion` stays 1.2.0.0, because a 1.2 reader that has never heard of
that tag skips it and renders as it always would.

Only if a source states no colour matrix at all does the tool fall back to a
fabricated one — sRGB primaries, `AsShotNeutral` at (1, 1, 1). That is a
placeholder to grade from, not a measurement.

### Geometry tags

`DefaultCropSize`, `ActiveArea`, `DefaultCropOrigin` and friends are measured in
pixels against a height the join has just changed. Carried through unaltered they
would crop the joined sheet back to its first section.

Where such a tag covers the whole section frame, it is re-stated against the
joined height. Where it describes a partial crop it is dropped, with a warning —
extending someone else's crop is a guess, and a wrong guess here is invisible
until the bottom of the sheet is missing. Tags that are inherently per-row
(`BlackLevelDeltaV`) or frame-relative (`DefaultUserCrop`) are dropped the same
way.

The scan DPI is the one piece of geometry that always survives intact: it is what
tells a converter how big the sheet physically is.

### Size

Uncompressed, the image is exactly `width × height × 6` bytes — a four-section
9442 × 3200 sheet is 692 MiB. DNG's lossless choices are uncompressed and
lossless JPEG; ZIP/deflate is not among them for 16-bit integer data, and libraw
rejects such a file outright. Adobe's DNG Converter will losslessly recompress
one if the size matters.

A classic TIFF addresses its data with 32-bit offsets. BigTIFF would lift that
ceiling but is not valid DNG, so a join that would not fit is refused before
anything is written, with the arithmetic in the message.

## Memory

The sections are never held together in memory. Each is memory-mapped read-only
and fed to the writer one strip at a time, so joining ten sections costs one
strip rather than ten gigabytes. The preview is built from a strided read of the
same maps, touching about a fiftieth of each file. A four-section, 692 MiB join
takes about 2.5 seconds including the full pixel readback.

## Open from the Finder

`contrib/macos/build-app.sh` compiles a droplet that Finder, digiKam and Path
Finder will offer under **Open With**, the same way Trichrome Merge works:

```bash
contrib/macos/build-app.sh          # -> ~/Applications/Join Contact Sheet Scans.app
```

Select one sheet's sections and open them with it. It asks for the roll id —
blank is not accepted — and, optionally, a different output folder:

- **Roll ID** — asked fresh every time.
- **Different output folder**, and the folder — remembered between runs. A new
  install starts unticked, with no folder, which writes the sheet beside its
  first section.

The app waits about a second before it starts. That is deliberate:
LaunchServices does not deliver a multiple-file selection as one event — a
four-file selection arrives as two, roughly 25 ms apart, and not in order. A
droplet that acted on each event as it arrived would run twice and join a
partial sheet. This one gathers the deliveries first. See
`contrib/macos/README.md`.

## Tests

```bash
pip install -e '.[dev]'
pytest
```

## Licence

MIT.
