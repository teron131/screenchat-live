on run argv
  set clickX to item 1 of argv as integer
  set clickY to item 2 of argv as integer
  tell application "System Events"
    click at {clickX, clickY}
  end tell
  return "ok"
end run
