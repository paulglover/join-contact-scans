# joincontactscans

Join the sections of a scanned contact sheet into one linear DNG.

A contact sheet bigger than the scanner's bed comes off it in sections —
`S0220-1.dng`, `S0220-2.dng`, `S0220-3.dng`, `S0220-4.dng`. This stacks them top
to bottom in scan order and writes one `S0220.dng`.

The pixels are the pixels. Nothing is scaled, blended, aligned, feathered or
colour-matched at the seams: the row below the last row of section one is the
first row of section two, unchanged. The tool reads the finished file back and
checks that, section by section, before it reports success.

```
$ joincontactscans /Volumes/Files/Vuescan/S0220-*.dng
1 roll(s) · linear DNG · verifying pixels
  S0220: 4 sections, 9442x12800  ->  S0220.dng
    S0220-1.dng + S0220-2.dng + S0220-3.dng + S0220-4.dng
[1/1] S0220 …

wrote /Volumes/Files/Vuescan/S0220.dng  (9442x12800, uint16)
      verified 4/4 sections pixel-identical

1 joined, 0 failed, 0 skipped
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
joincontactscans S0220-1.dng S0220-2.dng S0220-3.dng   # -> S0220.dng, beside them
joincontactscans /Volumes/Files/Vuescan                # every roll in the folder
joincontactscans ./scans --out ./joined
joincontactscans ./scans --dry-run                     # show the plan only
joincontactscans ./scans --force                       # replace an existing sheet
joincontactscans ./scans --no-verify                   # skip the readback
```

| | |
|---|---|
| `-o`, `--out DIR` | write the joined sheets here (default: beside each roll's first section) |
| `-r`, `--recursive` | descend into subfolders of any input folder |
| `-f`, `--force` | replace an existing joined file instead of skipping the roll |
| `-n`, `--dry-run` | show the plan; read and write nothing |
| `--no-verify` | skip reading the finished file back to confirm every section is pixel-identical |

### Naming

Sections are `ROLLID-SCANSEQ.dng`; the joined sheet is `ROLLID.dng`. The split
is on the **last** hyphen, so a roll id may contain hyphens of its own —
`2026-05-portra-3.dng` is section 3 of roll `2026-05-portra`.

The scan sequence is a **number** and is sorted as one. Sorted as text,
`S0220-10` would land between `S0220-1` and `S0220-2` and silently interleave a
ten-section sheet. So selection order does not matter: whatever order Finder or
a shell glob hands the files over in, the sheet comes out the same.

The joined file's name has no scan number, which is what lets it sit beside its
sources — running the tool over the same folder again picks up the sections and
leaves the finished sheet alone.

### Nothing is deleted, nothing is overwritten

Source files are never touched. A roll whose joined file already exists is
reported and skipped, not replaced; `--force` replaces it.

Every write goes to a temporary file beside the destination and is renamed over
it only once it has been verified. That is what makes `--force` safe: a failed
or interrupted join leaves the previous file exactly as it was, rather than a
truncated replacement.

### One bad roll does not stop the others

A selection of six rolls where one has a section missing produces five joined
sheets and one clear complaint:

```
$ joincontactscans ./scans
...
wrote /scans/S0220.dng  (9442x12800, uint16)
      verified 4/4 sections pixel-identical

1 joined, 0 failed, 0 skipped
PROBLEM roll 'S0221' has only 1 section (S0221-1.dng) — a join needs at least two
```

Things that stop a roll: fewer than two sections, sections that disagree on
width, two files claiming the same scan number, a file that is not a linear DNG.
Things that are only warnings: a gap in the scan numbers, a roll that starts at
something other than section 1, sections whose colour metadata disagrees.

That last pair matters more than it looks. A run of sections 2, 3, 4 is
perfectly consecutive, so only the "starts at section 2" check catches it — and
what it catches is a valid-looking sheet quietly missing its top.

## What trichrome has to do with it

[`trichrome`](https://github.com/paulglover/trichrome) comes up throughout what
follows, so: it is the companion tool, doing a separate job — merging
red/green/blue-light RAW triplets into a linear DNG. This one stacks several
sections of one sheet. What they share is the
output and the way it is reached: the same file layout, the same
linear-declaration tags, and the same kind of macOS droplet for opening a
selection from the Finder. Sheets joined here and frames merged there land in a
converter looking like siblings, which is the point — the grading you work out
for one applies to the other.

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
along with the capture date — lifted out of the EXIF sub-IFD into IFD0, where a
converter looks for it, so the sheet still sorts by when it was scanned.

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

Select a sheet's sections and open them with it, or drop a whole scan folder on
it to join every roll inside.

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
