(*
	Join Contact Sheet Scans — a droplet that hands a selection of scan
	sections to the joincontactscans command-line tool.

	Built as an .app (see build-app.sh) it becomes something Finder, digiKam or
	Path Finder can "Open With": select the sections of a contact sheet, open
	them with this, and each roll is joined into one linear DNG.

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

	It deliberately does NO validation of its own. joincontactscans already
	checks that the files are linear DNGs, that a roll has at least two
	sections, that they agree on width, and that nothing is about to be written
	over — and its messages are written to be read by a person. Duplicating any
	of that here would only create a second place for the rules to drift.

	Selection ORDER does not matter — and, as above, cannot be relied on
	anyway. Sections are stacked by the scan number in their filename.

	NOTHING IS EVER DELETED and nothing is overwritten. A roll whose joined file
	already exists is reported and skipped; pass --force by hand to replace one.
*)

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
				"Choose the scan sections to join — ROLLID-SCANSEQ.dng." ¬
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

	set foundTool to my findTool()
	if foundTool is missing value then return

	set posixPaths to {}
	repeat with anItem in theItems
		set end of posixPaths to POSIX path of anItem
	end repeat

	try
		display notification "Joining " & (count of posixPaths) & " item(s)…" ¬
			with title "Join Contact Sheet Scans"
	end try

	set theCommand to my buildCommand(foundTool, posixPaths)
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


on buildCommand(aToolPath, posixPaths)
	(* The shell command, with every path quoted. Kept separate from the UI so
	   it can be exercised without a screen; build-app.sh calls it directly as a
	   self-test after compiling. *)
	set theCommand to quoted form of aToolPath
	repeat with aPath in posixPaths
		set theCommand to theCommand & " " & quoted form of (aPath as text)
	end repeat
	return theCommand & " 2>&1"
end buildCommand


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
