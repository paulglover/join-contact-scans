# Join Contact Sheet Scans.app

A droplet that hands a Finder selection to `joincontactscans`.

```bash
./build-app.sh                       # -> ~/Applications/Join Contact Sheet Scans.app
./build-app.sh /path/to/Some.app     # somewhere else
```

Then either:

- **select the sections** of a contact sheet in Finder, digiKam or Path Finder
  and *Open With → Join Contact Sheet Scans*, or
- **drop a whole scan folder** on the app to join every roll inside it, or
- **double-click** the app and pick the files.

Selection order does not matter — sections are stacked by the scan number in
their filename.

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
a roll starts at a section other than 1.

Source files are never touched, and nothing is overwritten: a roll whose joined
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
declares `.dng` files and folders, as `Viewer`/`Alternate` so the droplet never
displaces the normal handler for a DNG.

Editing `Info.plist` invalidates the ad-hoc signature `osacompile` applied, and
recent macOS refuses to launch an app whose signature does not match its
contents, so the script re-signs and then re-registers with LaunchServices —
which is why **Open With** sees the app without a logout.

It finishes by loading the compiled script and calling `buildCommand` directly,
as a self-test of the quoting without needing a screen.
