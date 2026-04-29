on cleanText(valueText)
  set AppleScript's text item delimiters to tab
  set parts to text items of valueText
  set AppleScript's text item delimiters to " "
  set cleaned to parts as text
  set AppleScript's text item delimiters to ""
  return cleaned
end cleanText

set output to ""
tell application "System Events"
  set visibleProcesses to application processes whose visible is true
  repeat with proc in visibleProcesses
    set appName to name of proc as text
    try
      set windowCount to count windows of proc
      repeat with windowIndex from 1 to windowCount
        set currentWindow to window windowIndex of proc
        set windowTitle to my cleanText(name of currentWindow as text)
        set windowPosition to position of currentWindow
        set windowSize to size of currentWindow
        set output to output & appName & tab & windowIndex & tab & windowTitle & tab & (item 1 of windowPosition) & tab & (item 2 of windowPosition) & tab & (item 1 of windowSize) & tab & (item 2 of windowSize) & linefeed
      end repeat
    end try
  end repeat
end tell
return output
