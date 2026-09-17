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
# Folders as well as files: dropping a whole scan folder is the easy way to
# join every roll in it at once.
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

$plistbuddy -c "Add :CFBundleDocumentTypes:1 dict" "$plist"
$plistbuddy -c "Add :CFBundleDocumentTypes:1:CFBundleTypeName string 'Folder of scans'" "$plist"
$plistbuddy -c "Add :CFBundleDocumentTypes:1:CFBundleTypeRole string Viewer" "$plist"
$plistbuddy -c "Add :CFBundleDocumentTypes:1:LSHandlerRank string Alternate" "$plist"
$plistbuddy -c "Add :CFBundleDocumentTypes:1:LSItemContentTypes array" "$plist"
$plistbuddy -c "Add :CFBundleDocumentTypes:1:LSItemContentTypes:0 string public.folder" "$plist"

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

# The command builder: quoting, including a path with a space.
built=$(osascript \
    -e "set s to load script POSIX file \"$scpt\"" \
    -e 'tell s to buildCommand("/usr/bin/true", {"/a b/S0220-1.dng", "/S0220-2.dng"})')
check "command quoting" \
    "'/usr/bin/true' '/a b/S0220-1.dng' '/S0220-2.dng' 2>&1" "$built"

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

echo "Built and registered. Open With: .dng files, and folders of them."
echo "Self-tests passed."
