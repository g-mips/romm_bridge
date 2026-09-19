#!/bin/sh

notify_user () {
    local MSG="$1"
    # Print to the log file
    printf "%s\n" "$MSG"
    # Send a desktop notification (expires in 3 seconds)
    notify-send -a "ROMM" -t 3000 "Retro Loader" "$MSG"
}
