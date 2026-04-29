on run argv
  set appName to item 1 of argv
  set windowTitle to item 2 of argv
  set windowIndexText to item 3 of argv

  tell application appName to activate
  delay 0.1
  tell application "System Events"
    tell process appName
      set frontmost to true
      if windowTitle is not "" then
        set matchingWindows to windows whose name is windowTitle
        if (count of matchingWindows) is 0 then error "No window titled `" & windowTitle & "` found for `" & appName & "`."
        perform action "AXRaise" of item 1 of matchingWindows
      else if windowIndexText is not "" then
        set windowIndex to windowIndexText as integer
        if (count of windows) < windowIndex then error "Window index " & windowIndexText & " not found for `" & appName & "`."
        perform action "AXRaise" of window windowIndex
      end if
    end tell
  end tell
  return "ok"
end run
