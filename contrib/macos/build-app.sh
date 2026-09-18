#!/bin/bash
#
# Compile join-contact-scans.applescript into an .app bundle that Finder and
# digiKam will offer under "Open With" for DNG files.
#
#   ./build-app.sh                      -> ~/Applications/Join Contact Sheet Scans.app
#   ./build-app.sh /path/to/Some.app    -> there instead
#
# osacompile alone produces an app that runs, but that no file can be opened
# WITH: an app only appears in Open With if its Info.plist declares document
# types it handles. Most of this script is that declaration.
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
source_script="$here/join-contact-scans.applescript"
app="${1:-$HOME/Applications/Join Contact Sheet Scans.app}"
plist="$app/Contents/Info.plist"
plistbuddy=/usr/libexec/PlistBuddy

echo "Building $app"
rm -rf "$app"
mkdir -p "$(dirname "$app")"
# -s makes it a STAY-OPEN applet, which is what gives it an idle handler.
# LaunchServices splits a multi-file selection across several `odoc` events
# (measured ~25ms apart), and the app has to gather them before it acts —
# otherwise it runs once per fragment and joins a partial sheet. See the
# script's own header.
osacompile -s -o "$app" "$source_script"

# Set a key whether or not osacompile already wrote one.
plist_set() {   # key type value...
    local key=$1 type=$2
    shift 2
    $plistbuddy -c "Set :$key $*" "$plist" 2>/dev/null \
        || $plistbuddy -c "Add :$key $type $*" "$plist"
}

# --- Identity ------------------------------------------------------------- #
plist_set CFBundleIdentifier string net.joincontactscans.droplet
plist_set CFBundleName string Join Contact Sheet Scans
plist_set NSHighResolutionCapable bool true

# Reaching Finder for the "Show in Finder" button is an Automation request, and
# macOS kills an app that makes one without a stated reason.
plist_set NSAppleEventsUsageDescription string \
    Join Contact Sheet Scans reveals the files it wrote in Finder.

# --- Document types: what makes "Open With" offer this app ---------------- #
# DNG files only. Every file handed over is a section of one sheet, so a folder
# is not something the tool accepts, and the app does not offer to open one.
$plistbuddy -c "Delete :CFBundleDocumentTypes" "$plist" 2>/dev/null || true
$plistbuddy -c "Add :CFBundleDocumentTypes array" "$plist"

$plistbuddy -c "Add :CFBundleDocumentTypes:0 dict" "$plist"
$plistbuddy -c "Add :CFBundleDocumentTypes:0:CFBundleTypeName string 'DNG scan section'" "$plist"
# Viewer, not Editor: the app never writes over what it is given.
$plistbuddy -c "Add :CFBundleDocumentTypes:0:CFBundleTypeRole string Viewer" "$plist"
# Alternate, not Owner: being in the Open With list is the whole point, but
# nothing here should displace the user's normal handler for a DNG.
$plistbuddy -c "Add :CFBundleDocumentTypes:0:LSHandlerRank string Alternate" "$plist"
$plistbuddy -c "Add :CFBundleDocumentTypes:0:LSItemContentTypes array" "$plist"
i=0
for uti in com.adobe.raw-image public.camera-raw-image public.image; do
    $plistbuddy -c "Add :CFBundleDocumentTypes:0:LSItemContentTypes:$i string $uti" "$plist"
    i=$((i + 1))
done
$plistbuddy -c "Add :CFBundleDocumentTypes:0:CFBundleTypeExtensions array" "$plist"
$plistbuddy -c "Add :CFBundleDocumentTypes:0:CFBundleTypeExtensions:0 string dng" "$plist"

# --- Re-sign ------------------------------------------------------------- #
# osacompile ad-hoc signs the bundle; editing Info.plist afterwards invalidates
# that signature, and recent macOS refuses to launch an app whose signature does
# not match its contents. Re-sign now that the edits are done.
codesign --force --sign - "$app" >/dev/null 2>&1 \
    || echo "warning: could not re-sign the bundle" >&2

# --- Register, so Open With sees it without a logout ---------------------- #
lsregister=/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister
[ -x "$lsregister" ] && "$lsregister" -f "$app" || true
touch "$app"

# --- Self-tests: the logic, without needing a screen ---------------------- #
scpt="$app/Contents/Resources/Scripts/main.scpt"
fail=0

check() {   # label expected actual
    if [ "$3" != "$2" ]; then
        echo "SELF-TEST FAILED: $1" >&2
        echo "  got:      $3" >&2
        echo "  expected: $2" >&2
        fail=1
    fi
}

# The command builder: quoting, including a path with a space, and a roll ID
# that would read as an option if it were not attached to its flag.
built=$(osascript \
    -e "set s to load script POSIX file \"$scpt\"" \
    -e 'tell s to buildCommand("/usr/bin/true", "S0220", "", {"/a b/S0220-1.dng", "/S0220-2.dng"})')
check "command quoting" \
    "'/usr/bin/true' '--roll-id=S0220' '/a b/S0220-1.dng' '/S0220-2.dng' 2>&1" "$built"

built=$(osascript \
    -e "set s to load script POSIX file \"$scpt\"" \
    -e 'tell s to buildCommand("/usr/bin/true", "-it'"'"'s", "/out dir", {"/S0220-1.dng"})')
check "roll ID and output folder" \
    "'/usr/bin/true' '--roll-id=-it'\\''s' '--out=/out dir' '/S0220-1.dng' 2>&1" "$built"

# What the settings dialog refuses before it lets the join run.
problems=$(osascript \
    -e "set s to load script POSIX file \"$scpt\"" \
    -e 'tell s to set a to settingsProblem("", false, "")' \
    -e 'tell s to set b to settingsProblem("S0220", true, "")' \
    -e 'tell s to set c to settingsProblem("S0220", false, "")' \
    -e 'tell s to set d to settingsProblem("S0220", true, "/out")' \
    -e '((a is not "") as text) & "/" & ((b is not "") as text) & "/" & ((c is "") as text) & "/" & ((d is "") as text)')
check "blank roll ID and missing folder are refused" \
    "true/true/true/true" "$problems"

selection=$(osascript \
    -e "set s to load script POSIX file \"$scpt\"" \
    -e 'tell s to ((selectionProblem(1) is not "") as text) & "/" & ((selectionProblem(2) is "") as text)')
check "a single file is refused before the dialog" "true/true" "$selection"

trim=$(osascript \
    -e "set s to load script POSIX file \"$scpt\"" \
    -e 'tell s to trimmed("  S0220 " & tab & linefeed)')
check "whitespace around a setting is trimmed" "S0220" "$trim"

# The coalescing this app exists to do: two deliveries, one batch of four, and
# an empty accumulator afterwards so the next selection starts clean.
batch=$(osascript \
    -e "set s to load script POSIX file \"$scpt\"" \
    -e 'tell s to absorbItems({"b", "c", "d"})' \
    -e 'tell s to absorbItems({"a"})' \
    -e 'tell s to set taken to takeBatch()' \
    -e 'tell s to ((count of taken) as text) & "/" & (absorbItems({}) as text)')
check "split deliveries are gathered into one batch" "4/0" "$batch"

[ "$fail" -eq 0 ] || exit 1

echo "Built and registered. Open With: .dng files."
echo "Self-tests passed."
