(*
	Join Contact Sheet Scans — a droplet that hands a selection of scan
	sections to the joincontactscans command-line tool.

	Built as an .app (see build-app.sh) it becomes something Finder, digiKam or
	Path Finder can "Open With": select the sections of ONE contact sheet, open
	them with this, give the sheet its roll ID, and they are joined into one
	linear DNG named ROLLID.dng.

	WHAT IT ASKS
	------------
	Once the selection is gathered, one dialog asks for:

	  * the roll ID — the joined file's name, and its XMP dc:identifier. Asked
	    fresh every time, because it is different for every sheet. Join stays
	    disabled until one is typed.
	  * "Different output folder", and the folder. Unticked, the sheet is
	    written beside its first section. Both are remembered between runs, in
	    the app's own preferences (net.joincontactscans.droplet) rather than in
	    script properties: an applet that saves its properties rewrites its own
	    bundle, which breaks the signature build-app.sh applied. A new install
	    starts unticked, with no folder.

	WHY THIS APP STAYS OPEN, AND WHY IT WAITS A SECOND
	--------------------------------------------------
	LaunchServices does not deliver a multiple-file selection as one event. A
	four-file selection arrives as TWO `odoc` events about 25 milliseconds
	apart — in the case that prompted this, files 2, 3 and 4 first, then file 1
	on its own, on cold AND warm launches alike.

	A droplet that acts on each event as it arrives therefore runs twice, and
	the damage is silent: the first batch joins sections 2, 3 and 4 into a
	perfectly valid S0220.dng that is simply missing the top of the sheet, and
	the second batch fails with "only 1 section" — which reads like a complaint
	about the file it ignored rather than a warning about the file it wrote.

	So this is a STAY-OPEN applet. `on open` only accumulates; the `idle`
	handler waits for the deliveries to stop and then runs the tool ONCE on
	everything that arrived. One second of quiet is forty times the observed
	gap, and the app quits as soon as the batch is done.

	The same wait applies to a plain double-click: `on run` cannot know whether
	files are about to follow it, so it only raises a flag, and the idle handler
	shows the file chooser if nothing arrives.

	Beyond fewer than two files, a blank roll ID, or a ticked box with no
	folder, it does NO validation of its own. joincontactscans already checks that the files are
	linear DNGs, that there are at least two, that they agree on width, that
	the roll ID is a usable file name, and that nothing is about to be written
	over — and its messages are written to be read by a person. Duplicating any
	of that here would only create a second place for the rules to drift.

	Selection ORDER does not matter — and, as above, cannot be relied on
	anyway. Sections are stacked in filename order, numbers compared as numbers.

	NOTHING IS EVER DELETED and nothing is overwritten. A sheet whose joined
	file already exists is reported and skipped; pass --force by hand to
	replace one.
*)

use AppleScript version "2.5"
use framework "Foundation"
use framework "AppKit"
use scripting additions

-- Set this to an absolute path to skip auto-detection entirely.
property toolPath : ""

-- Where to look, in order, before falling back to asking a login shell.
property searchPaths : {"/opt/homebrew/bin/joincontactscans", "/usr/local/bin/joincontactscans", "/opt/local/bin/joincontactscans"}

-- Offer a "Show in Finder" button when a sheet is written.
property revealResult : true

-- How the batch is gathered. `idleSeconds` is how often the idle handler runs;
-- `quietTicks` is how many idle rounds with nothing new must pass before the
-- batch is considered complete. 0.5 x 2 = one second of quiet.
property idleSeconds : 0.5
property quietTicks : 2

-- Accumulated between deliveries. Reset on every launch as well as declared
-- empty, because an applet can persist property values into its own bundle.
property pendingItems : {}
property ticksSinceChange : 0
property awaitingChoice : false

-- The settings dialog's controls, held only while it is on screen so its
-- button actions can reach them. Always cleared afterwards: an applet cannot
-- save a property that still holds an Objective-C object when it quits.
property settingsCheckbox : missing value
property settingsPathField : missing value
property settingsChooseButton : missing value
property settingsJoinButton : missing value


on run
	-- A double-click, OR the moment before files arrive: it cannot be told
	-- apart yet, so only raise a flag and let idle decide.
	set pendingItems to {}
	set ticksSinceChange to 0
	set awaitingChoice to true
end run


on open droppedItems
	-- Dropped on the icon, or sent by another app's "Open With". Only
	-- accumulate: more of the same selection is probably milliseconds away.
	set awaitingChoice to false
	my absorbItems(droppedItems)
end open


on idle
	if (count of pendingItems) > 0 then
		set ticksSinceChange to ticksSinceChange + 1
		if ticksSinceChange < quietTicks then return idleSeconds
		my joinItems(my takeBatch())
		quit
		return idleSeconds
	end if

	if awaitingChoice then
		set ticksSinceChange to ticksSinceChange + 1
		if ticksSinceChange < quietTicks then return idleSeconds
		-- Nothing followed the launch, so it really was a double-click.
		set awaitingChoice to false
		try
			set chosen to choose file with prompt ¬
				"Choose the sections of one contact sheet to join." ¬
				of type {"com.adobe.raw-image", "dng"} ¬
				with multiple selections allowed
		on error number -128
			quit
			return idleSeconds
		end try
		my joinItems(chosen)
		quit
	end if
	return idleSeconds
end idle


on absorbItems(newItems)
	(* Add a delivery to the batch and restart the quiet countdown. Separated
	   from the event handler so build-app.sh can exercise the coalescing that
	   this whole app exists to do, without needing a screen. *)
	set pendingItems to pendingItems & newItems
	set ticksSinceChange to 0
	return count of pendingItems
end absorbItems


on takeBatch()
	(* Hand over everything accumulated so far and start again empty. *)
	set theBatch to pendingItems
	set pendingItems to {}
	set ticksSinceChange to 0
	return theBatch
end takeBatch


on joinItems(theItems)
	if (count of theItems) is 0 then return

	set problem to my selectionProblem(count of theItems)
	if problem is not "" then
		activate
		display alert "Not enough to join" message problem as warning
		return
	end if

	set foundTool to my findTool()
	if foundTool is missing value then return

	set posixPaths to {}
	repeat with anItem in theItems
		set end of posixPaths to POSIX path of anItem
	end repeat

	set theSettings to my askSettings(count of posixPaths)
	if theSettings is missing value then return
	set {rollId, outDir} to theSettings

	try
		display notification "Joining " & (count of posixPaths) & " sections as " & ¬
			rollId & "…" with title "Join Contact Sheet Scans"
	end try

	set theCommand to my buildCommand(foundTool, rollId, outDir, posixPaths)
	try
		set theOutput to do shell script theCommand
	on error errorMessage number errorNumber
		if errorNumber is -128 then return
		-- stderr is folded into stdout, so a non-zero exit carries the tool's
		-- own message. That covers the ordinary "already exists, skipped" case
		-- as well as a real failure, so the output is worth showing either way
		-- rather than collapsing to an alert with no detail.
		my report(errorMessage, "Join Contact Sheet Scans — not everything was written")
		return
	end try

	my report(theOutput, "Join Contact Sheet Scans")
end joinItems


on buildCommand(aToolPath, rollId, outDir, posixPaths)
	(* The shell command, with every argument quoted. `outDir` is "" for
	   "beside the first section". The options are written as --opt=value, so a
	   roll ID that starts with a hyphen is still a value and not an option.
	   Kept separate from the UI so it can be exercised without a screen;
	   build-app.sh calls it directly as a self-test after compiling. *)
	set theCommand to quoted form of aToolPath
	set theCommand to theCommand & " " & quoted form of ("--roll-id=" & rollId)
	if outDir is not "" then ¬
		set theCommand to theCommand & " " & quoted form of ("--out=" & outDir)
	repeat with aPath in posixPaths
		set theCommand to theCommand & " " & quoted form of (aPath as text)
	end repeat
	return theCommand & " 2>&1"
end buildCommand


on selectionProblem(itemCount)
	(* Why a selection of `itemCount` files cannot be joined, or "" when it can.
	   Said before the settings dialog, so nobody types a roll ID for a join
	   that was never going to run. The tool says the same, but only after. *)
	if itemCount < 2 then return "Only " & itemCount & ¬
		" file was selected. Select all the sections of the sheet — at least two — and open them together."
	return ""
end selectionProblem


on askSettings(fileCount)
	(* The roll ID and output folder for this join, as {rollId, outDir} with
	   outDir "" for "beside the first section" — or missing value if the user
	   cancelled. The output-folder choice is saved only when the user clicks
	   Join, so cancelling leaves the previous choice as it was. *)
	set {useOutDir, outDir} to my loadSettings()
	set rollId to ""
	set problem to ""
	repeat
		set theAnswer to my settingsDialog(fileCount, rollId, useOutDir, outDir, problem)
		if theAnswer is missing value then return missing value
		set {rollId, useOutDir, outDir} to theAnswer
		set problem to my settingsProblem(rollId, useOutDir, outDir)
		if problem is "" then exit repeat
	end repeat
	my saveSettings(useOutDir, outDir)
	if not useOutDir then set outDir to ""
	return {rollId, outDir}
end askSettings


on settingsProblem(rollId, useOutDir, outDir)
	(* Why these settings cannot be used, or "" when they can. Only what the
	   dialog alone can know; everything else is joincontactscans's to say. *)
	if rollId is "" then return "Enter a roll ID — it names the joined sheet."
	if useOutDir and outDir is "" then ¬
		return "Choose an output folder, or untick “Different output folder”."
	return ""
end settingsProblem


on loadSettings()
	(* {useOutDir, outDir} as last saved; unticked and empty on a new install. *)
	set theDefaults to current application's NSUserDefaults's standardUserDefaults()
	set useOutDir to (theDefaults's boolForKey:"useOutputFolder") as boolean
	set outDir to theDefaults's stringForKey:"outputFolder"
	if outDir is missing value then return {useOutDir, ""}
	return {useOutDir, outDir as text}
end loadSettings


on saveSettings(useOutDir, outDir)
	set theDefaults to current application's NSUserDefaults's standardUserDefaults()
	theDefaults's setBool:useOutDir forKey:"useOutputFolder"
	theDefaults's setObject:outDir forKey:"outputFolder"
end saveSettings


on settingsDialog(fileCount, rollId, useOutDir, outDir, problem)
	(* One alert holding every setting: a roll ID field, the checkbox, and the
	   folder with a Choose… button. Returns {rollId, useOutDir, outDir},
	   trimmed, or missing value for Cancel. `problem`, when not "", is shown in
	   place of the usual explanation — it is why the dialog is being asked
	   again. *)
	set theView to current application's NSView's alloc()'s initWithFrame:{{0, 0}, {380, 100}}

	set rollLabel to current application's NSTextField's labelWithString:"Roll ID:"
	rollLabel's setFrame:{{0, 75}, {60, 20}}
	set rollField to current application's NSTextField's alloc()'s initWithFrame:{{64, 72}, {316, 24}}
	rollField's setStringValue:rollId
	rollField's setDelegate:me

	set settingsCheckbox to current application's NSButton's checkboxWithTitle:"Different output folder" target:me action:"outputFolderToggled:"
	settingsCheckbox's setFrame:{{0, 40}, {380, 20}}
	if useOutDir then
		settingsCheckbox's setState:1
	else
		settingsCheckbox's setState:0
	end if

	set settingsPathField to current application's NSTextField's alloc()'s initWithFrame:{{0, 6}, {284, 24}}
	settingsPathField's setStringValue:outDir
	settingsPathField's setPlaceholderString:"Output folder"
	settingsPathField's setUsesSingleLineMode:true
	(settingsPathField's cell())'s setLineBreakMode:(current application's NSLineBreakByTruncatingHead)
	set settingsChooseButton to current application's NSButton's buttonWithTitle:"Choose…" target:me action:"chooseOutputFolder:"
	settingsChooseButton's setFrame:{{290, 2}, {90, 32}}
	my syncOutputFolderControls()

	repeat with aControl in {rollLabel, rollField, settingsCheckbox, settingsPathField, settingsChooseButton}
		(theView's addSubview:aControl)
	end repeat

	set theAlert to current application's NSAlert's alloc()'s init()
	theAlert's setMessageText:("Join " & fileCount & " sections into one sheet")
	if problem is "" then
		theAlert's setInformativeText:"The joined sheet is named after its roll ID. Unless a different output folder is chosen, it is written beside the first section."
	else
		theAlert's setInformativeText:problem
	end if
	theAlert's setAccessoryView:theView
	set settingsJoinButton to theAlert's addButtonWithTitle:"Join"
	theAlert's addButtonWithTitle:"Cancel"
	my syncJoinButton(rollField)
	theAlert's layout()
	(theAlert's |window|())'s setInitialFirstResponder:rollField

	activate
	set theResponse to (theAlert's runModal()) as integer
	set theAnswer to {my trimmed(rollField's stringValue()), ¬
		((settingsCheckbox's state()) as integer) is 1, ¬
		my trimmed(settingsPathField's stringValue())}

	set settingsCheckbox to missing value
	set settingsPathField to missing value
	set settingsChooseButton to missing value
	set settingsJoinButton to missing value

	if theResponse is not (current application's NSAlertFirstButtonReturn) as integer then ¬
		return missing value
	return theAnswer
end settingsDialog


on controlTextDidChange:aNotification
	(* The roll ID field's delegate: re-checked on every keystroke. *)
	my syncJoinButton(aNotification's object())
end controlTextDidChange:


on syncJoinButton(rollField)
	(* Join is only clickable once there is a roll ID. Whitespace alone does
	   not count, since it is trimmed away. *)
	settingsJoinButton's setEnabled:((my trimmed(rollField's stringValue())) is not "")
end syncJoinButton


on outputFolderToggled:sender
	my syncOutputFolderControls()
end outputFolderToggled:


on syncOutputFolderControls()
	(* The folder is only editable while the box is ticked. It is kept, not
	   cleared, when the box is unticked, so ticking it again restores it. *)
	set isOn to ((settingsCheckbox's state()) as integer) is 1
	settingsPathField's setEnabled:isOn
	settingsChooseButton's setEnabled:isOn
end syncOutputFolderControls


on chooseOutputFolder:sender
	set thePanel to current application's NSOpenPanel's openPanel()
	thePanel's setCanChooseFiles:false
	thePanel's setCanChooseDirectories:true
	thePanel's setCanCreateDirectories:true
	thePanel's setAllowsMultipleSelection:false
	thePanel's setPrompt:"Choose"
	thePanel's setMessage:"Choose the folder to write joined sheets into."
	set startPath to my trimmed(settingsPathField's stringValue())
	if startPath is not "" then ¬
		thePanel's setDirectoryURL:(current application's NSURL's fileURLWithPath:((current application's NSString's stringWithString:startPath)'s stringByExpandingTildeInPath()))
	if ((thePanel's runModal()) as integer) is (current application's NSModalResponseOK) as integer then
		settingsPathField's setStringValue:((thePanel's |URL|())'s |path|())
	end if
end chooseOutputFolder:


on trimmed(theText)
	(* theText, an AppleScript or Cocoa string, without surrounding whitespace. *)
	set theString to current application's NSString's stringWithString:theText
	return (theString's stringByTrimmingCharactersInSet:(current application's NSCharacterSet's whitespaceAndNewlineCharacterSet())) as text
end trimmed


on findTool()
	(* `do shell script` runs with a bare PATH (/usr/bin:/bin:/usr/sbin:/sbin),
	   which is never where this is installed. So: the configured path, then the
	   usual package-manager prefixes, then a LOGIN shell — which is what finds
	   it inside a venv, pyenv or conda environment. *)
	if toolPath is not "" then
		if my isExecutable(toolPath) then return toolPath
	end if
	repeat with aPath in searchPaths
		if my isExecutable(aPath as text) then return (aPath as text)
	end repeat
	try
		set found to do shell script "$SHELL -lc 'command -v joincontactscans' 2>/dev/null"
		if found is not "" then return found
	end try

	display alert "joincontactscans not found" message ¬
		"This droplet could not find the joincontactscans command." & return & return & ¬
		"Install it with `pip install -e .` in the join-contact-scans " & ¬
		"checkout, then either put it on your login shell's PATH or open this " & ¬
		"script and set the toolPath property to its absolute path." as critical
	return missing value
end findTool


on isExecutable(aPath)
	try
		do shell script "test -x " & quoted form of aPath
		return true
	on error
		return false
	end try
end isExecutable


on report(theOutput, theTitle)
	set writtenPaths to my writtenPathsFrom(theOutput)
	set theButtons to {"OK"}
	if revealResult and (count of writtenPaths) > 0 then ¬
		set theButtons to {"Show in Finder", "OK"}

	set theButton to button returned of (display dialog theOutput ¬
		buttons theButtons default button (item -1 of theButtons) ¬
		with title theTitle)

	if theButton is "Show in Finder" then
		tell application "Finder"
			reveal (POSIX file (item 1 of writtenPaths) as alias)
			activate
		end tell
	end if
end report


on writtenPathsFrom(theOutput)
	(* joincontactscans reports each result as `wrote /some/path  (WxH, uint16)`.
	   tests/test_cli.py pins that line's shape, because this parses it. *)
	set thePaths to {}
	repeat with aLine in paragraphs of theOutput
		set aLine to aLine as text
		if aLine starts with "wrote " then
			set aPath to text 7 thru -1 of aLine
			set savedDelimiters to AppleScript's text item delimiters
			set AppleScript's text item delimiters to "  ("
			set aPath to text item 1 of aPath
			set AppleScript's text item delimiters to savedDelimiters
			set end of thePaths to aPath
		end if
	end repeat
	return thePaths
end writtenPathsFrom
