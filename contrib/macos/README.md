# Join Contact Sheet Scans.app

A droplet that hands a Finder selection to `joincontactscans`.

```bash
./build-app.sh                       # -> ~/Applications/Join Contact Sheet Scans.app
./build-app.sh /path/to/Some.app     # somewhere else
```

Then either:

- **select the sections** of one contact sheet in Finder, digiKam or Path
  Finder and *Open With → Join Contact Sheet Scans*, or
- **double-click** the app and pick the files.

Every file handed over is a section of the same sheet, and only `.dng` files
are accepted — folders are not. A selection of just one file is refused before
anything is asked.

Selection order does not matter — sections are stacked in filename order, with
numbers compared as numbers.

## What it asks

Once the selection is gathered, one dialog asks for:

- **Roll ID** — names the joined file (`ROLLID.dng`) and is written into its
  XMP `dc:identifier`. Asked fresh each time; **Join** stays disabled until one
  is typed.
- **Different output folder** — a checkbox, with a folder field and a
  **Choose…** button. Unticked, the sheet is written beside its first section;
  ticked, into the folder (which must then be filled in).

The checkbox and the folder are remembered between runs, saved when you click
**Join**. A new install starts unticked with an empty folder. They live in the
app's preferences, not in the script's properties — an applet that saves its
properties rewrites its own bundle and breaks the signature the build applied:

```bash
defaults read net.joincontactscans.droplet          # useOutputFolder, outputFolder
defaults delete net.joincontactscans.droplet        # back to the install defaults
```

## Why it pauses for a second first

LaunchServices does not hand over a multiple-file selection as a single event.
A four-file selection arrives as **two** `odoc` events about **25 milliseconds**
apart, and not in order — measured here as files 2, 3, 4 followed by file 1, on
cold and warm launches alike.

A droplet that acts on each event as it arrives therefore runs twice, and the
damage is silent: the first batch joins sections 2, 3 and 4 into a perfectly
valid `S0220.dng` that is simply missing the top of the sheet, and the second
batch fails with "only 1 section" — which reads like a complaint about the file
it ignored rather than a warning about the file it just wrote.

So the app is built **stay-open** (`osacompile -s`). Its `open` handler only
accumulates; an `idle` handler waits one second for the deliveries to stop, runs
the tool once on everything that arrived, and quits. The same wait applies to a
plain double-click, because `on run` cannot know whether files are about to
follow it.

`build-app.sh` self-tests this: it feeds the compiled script two separate
deliveries and checks they come back as one batch of four.

The command-line tool has its own backstop for the same failure — it warns when
a sheet starts at a section other than 1.

Source files are never touched, and nothing is overwritten: a sheet whose joined
file already exists is reported and skipped. The droplet shows whatever the tool
said, and offers **Show in Finder** for the first sheet it wrote.

## If it says it cannot find `joincontactscans`

`do shell script` runs with a bare `PATH` (`/usr/bin:/bin:/usr/sbin:/sbin`),
which is never where a pip-installed tool lives. The droplet tries, in order:
the `toolPath` property, the usual package-manager prefixes, then a **login**
shell — which is what finds it inside a venv, pyenv or conda environment.

If none of those work, open `join-contact-scans.applescript`, set

```applescript
property toolPath : "/full/path/to/joincontactscans"
```

and run `build-app.sh` again.

## Notes on the build script

Most of it is the `Info.plist` declaration. `osacompile` alone produces an app
that runs but that no file can be opened *with* — an app only appears in **Open
With** if its `Info.plist` declares document types it handles. The script
declares `.dng` files, as `Viewer`/`Alternate` so the droplet never
displaces the normal handler for a DNG.

Editing `Info.plist` invalidates the ad-hoc signature `osacompile` applied, and
recent macOS refuses to launch an app whose signature does not match its
contents, so the script re-signs and then re-registers with LaunchServices —
which is why **Open With** sees the app without a logout.

It finishes by loading the compiled script and calling its handlers directly —
`buildCommand` for the quoting of paths, roll ID and output folder,
`selectionProblem` and `settingsProblem` for what is refused, and the batch
gathering — as self-tests that need no screen. The dialog itself is not
exercised there.
