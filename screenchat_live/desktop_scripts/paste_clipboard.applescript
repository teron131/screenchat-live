on run argv
  tell application "System Events"
    delay 0.05
    keystroke "v" using command down
  end tell
  return "ok"
end run
