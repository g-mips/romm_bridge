#!/bin/sh

. ${HOME}/.local/bin/romm_helpers/device_env.sh
. ${HOME}/.local/bin/romm_helpers/notify.sh

#############################################
# GENERIC
#############################################

register_device_if_needed() {
    local ID_FILE="${HOME}/.config/romm/device_id"

    # If we already have a saved UUID, load it and exit
    if [ -f "$ID_FILE" ]; then
        export DEVICE_ID=$(cat "$ID_FILE")
        return 0
    fi

    notify_user "=== Device not registered. Registering $HOSTNAME with ROMM..."

    # Ensure the config directory exists
    mkdir -p "$(dirname "$ID_FILE")"

    # Register and capture the JSON response
    # Using your confirmed working payload
    RESPONSE=$(curl -s -X POST -H "Authorization: Bearer $API_KEY" \
        -H "Content-Type: application/json" \
        -d "{
            \"name\": \"$HOSTNAME\",
            \"hostname\": \"$HOSTNAME\",
            \"platform\": \"linux\",
            \"client\": \"Emulation Station\",
            \"sync_mode\": \"api\"
        }" "${ROMM_URL}/api/devices")

    # Extract the device_id from the response
    NEW_UUID=$(echo "$RESPONSE" | jq -r '.device_id')

    if [ "$NEW_UUID" != "null" ] && [ -n "$NEW_UUID" ]; then
        echo "$NEW_UUID" > "$ID_FILE"
        DEVICE_ID="$NEW_UUID"
        notify_user "=== Success! Device ID $DEVICE_ID saved to $ID_FILE"
        return 0
    else
        notify_user "!!! Registration failed. Response: $RESPONSE"
        return 1
    fi
}

download_rom_save () {
    local ROM_ID=$1
    local RET_CODE=1
    local SAVE_LOC="/tmp/latest_save.tar.gz"

    rm -f $SAVE_LOC 2> /dev/null

    # Check for newer saves on ROMM
    # Query the saves API for this ROM_ID and find the latest one
    LATEST_SAVE_JSON=$(curl -s -H "Authorization: Bearer $API_KEY" \
        "$ROMM_URL/api/saves?rom_id=$ROM_ID&order_by=updated_at&order_dir=desc" | jq -r '.[0]')

    REMOTE_SAVE_ID=$(echo "$LATEST_SAVE_JSON" | jq -r '.id')
    REMOTE_DEVICE=$(echo "$LATEST_SAVE_JSON" | jq -r '.device_id')
    if [ "$REMOTE_SAVE_ID" != "null" ] && [ "$REMOTE_DEVICE" != "$DEVICE_ID" ]; then
        notify_user "=== Found a newer save from device: $REMOTE_DEVICE. Downloading...\n" ""

        # Download the save content (I decided to just zip/tar things up when things get pushed to ROMM)
        curl -L -H "Authorization: Bearer $API_KEY" \
            "$ROMM_URL/api/saves/$REMOTE_SAVE_ID/content" \
            -o "${SAVE_LOC}"
        [ $? -eq 0 ] && [ -f "${SAVE_LOC}" ] && RET_CODE=0
    fi

    return $RET_CODE
}

update_save_retroarch () {
    local MODE=$1       # 1 for Pre-game (Download), 0 for Post-game (Upload)
    local SAVE_DATA=$2
    local ROM_ID=$3
    local ROM_PATH=$4   # Pass the ROM_PATH in to extract the name
    local INDICATOR=/tmp/session_start
    local SAVE_DIR="$HOME/.config/retroarch/saves"

    # Get the filename without the path, then strip the extension
    local ROM_BASENAME=$(basename "$ROM_PATH")
    local ROM_NAME_NO_EXT="${ROM_BASENAME%.*}"

    # Use a wildcard to catch .srm, .sav, .bcr, .rtc, etc.
    local EXPECTED_PATTERN="${ROM_NAME_NO_EXT}.*"

    if [ "$MODE" -eq 1 ]; then
        # PRE GAME
        touch "$INDICATOR"
        if [ -f "${SAVE_DATA}" ]; then
            notify_user "=== Extracting remote RetroArch save..."
            mkdir -p "${SAVE_DIR}"
            tar -xzf "${SAVE_DATA}" -C "${SAVE_DIR}"
        fi
    else
        # POST GAME
        # Look for ANY file matching the ROM name that was updated
        UPDATED_FILES=$(find "$SAVE_DIR" -type f -name "$EXPECTED_PATTERN" -newer "$INDICATOR" | sed "s|^$SAVE_DIR/||")

        if [ -z "$UPDATED_FILES" ]; then
            notify_user "=== No save data changes detected for $ROM_NAME_NO_EXT."
        else
            notify_user "=== Detected updated save files:\n$UPDATED_FILES\nPackaging..."
            local UPLOAD_BUNDLE="/tmp/romm_ra_save.tar.gz"

            # Tar up all the exact files found
            echo "$UPDATED_FILES" | tar -czf "${UPLOAD_BUNDLE}" -C "$SAVE_DIR" -T -

            notify_user "=== Uploading to ROMM (ROM_ID: $ROM_ID)..."
            curl -s -X POST -H "Authorization: Bearer $API_KEY" \
                -F "saveFile=@${UPLOAD_BUNDLE}" \
                "${ROMM_URL}/api/saves?rom_id=${ROM_ID}&device_id=${DEVICE_ID}&overwrite=true" | jq -r '"=== Sync Successful: \( .file_name ) (\( .file_size_bytes ) bytes)"'
        fi
    fi
}

#############################################
# Sony PlayStation 1
#############################################

start_rom_psx () {
    local ROM_PATH=$1
    local ROM_ID=$2
    local SAVE_DATA_PATH="/tmp/romm_ra_save.tar.gz"

    # Download save from ROMM before launching
    update_save_retroarch 1 "${SAVE_DATA_PATH}" "$ROM_ID" "$ROM_PATH"

    notify_user "=== Launching RetroArch (PlayStation 1 / SwanStation)...\n"
    retroarch -f -L /usr/lib/libretro/swanstation_libretro.so "$ROM_PATH"

    # Upload save to ROMM after exiting
    update_save_retroarch 0 "" "$ROM_ID" "$ROM_PATH"
}

#############################################
# PS2
#############################################

update_save_ps2 () {
    local MODE=$1        # 1 for Pre-game (Download), 0 for Post-game (Upload)
    local SAVE_DATA=$2   # Path to tarball (for download mode)
    local ROM_ID=$3      # Pass this in so we can upload
    local INDICATOR=/tmp/session_start
    local SAVE_DIR="$HOME/.config/PCSX2/memcards/ROMM_SAVE_FOLDER_0001.ps2"

    if [ "$MODE" -eq 1 ]; then
        # PRE GAME
        touch "$INDICATOR"
        if [ -f "${SAVE_DATA}" ]; then
            notify_user "=== Extracting remote save to memory card..."
            mkdir -p "${SAVE_DIR}"
            tar -xzf "${SAVE_DATA}" -C "${SAVE_DIR}"
        fi
    else
        # POST GAME
        # Look for changes
        UPDATED_FOLDER=$(find "$SAVE_DIR" -type f -newer "$INDICATOR" ! -path "*/BADATA-SYSTEM/*" ! -name "_pcsx2_*" | \
            sed "s|^$SAVE_DIR/||" | \
            cut -d'/' -f1 | \
            sort -u | \
            head -n 1)

        if [ -z "$UPDATED_FOLDER" ]; then
            notify_user "=== No save data changes detected."
        else
            notify_user "=== Detected changes in: $UPDATED_FOLDER. Packaging..."
            local UPLOAD_BUNDLE="/tmp/romm_save.tar.gz"
            tar -czf "${UPLOAD_BUNDLE}" -C "$SAVE_DIR" "$UPDATED_FOLDER"

            notify_user "=== Uploading to ROMM (ROM_ID: $ROM_ID)..."
            curl -s -X POST -H "Authorization: Bearer $API_KEY" \
                -F "saveFile=@${UPLOAD_BUNDLE}" \
                "${ROMM_URL}/api/saves?rom_id=${ROM_ID}&device_id=${DEVICE_ID}&overwrite=true" | jq -r '"=== Sync Successful: \( .file_name ) (\( .file_size_bytes ) bytes)"'
        fi
    fi
}

start_rom_ps2 () {
    local ROM_PATH=$1
    local ROM_ID=$2
    local SAVE_DATA_PATH=$3

    # Pass ROM_ID into the update function
    update_save_ps2 1 "${SAVE_DATA_PATH}" "$ROM_ID"

    # Launch Emulator
    notify_user "=== Launching PCSX2..."
    pcsx2 -batch -fullscreen "$ROM_PATH"

    # Pass ROM_ID again for the upload
    update_save_ps2 0 "" "$ROM_ID"
}

#############################################
# NES / FAMICOM
#############################################

start_rom_nes () {
    local ROM_PATH=$1
    local ROM_ID=$2
    local SAVE_DATA_PATH=$3

    update_save_retroarch 1 "${SAVE_DATA_PATH}" "$ROM_ID" "$ROM_PATH"

    notify_user "=== Launching RetroArch (NES/FAMICOM)..."
    retroarch -f -L /usr/lib/libretro/mesen_libretro.so "$ROM_PATH"

    # Pass ROM_ID again for the upload
    update_save_retroarch 0 "" "$ROM_ID" "$ROM_PATH"
}

start_rom_famicom () {
    start_rom_nes "$@"
}

#############################################
# SNES
#############################################

start_rom_snes () {
    local ROM_PATH=$1
    local ROM_ID=$2
    local SAVE_DATA_PATH=$3

    update_save_retroarch 1 "${SAVE_DATA_PATH}" "$ROM_ID" "$ROM_PATH"

    notify_user "=== Launching RetroArch (SNES)...\n"
    retroarch -f -L /usr/lib/libretro/snes9x_libretro.so "$ROM_PATH"

    update_save_retroarch 0 "" "$ROM_ID" "$ROM_PATH"
}

#############################################
# Game Boy (GB)
#############################################

start_rom_gb () {
    local ROM_PATH=$1
    local ROM_ID=$2
    local SAVE_DATA_PATH=$3

    update_save_retroarch 1 "${SAVE_DATA_PATH}" "$ROM_ID" "$ROM_PATH"

    notify_user "=== Launching RetroArch (Game Boy / SameBoy)...\n"
    retroarch -f -L /usr/lib/libretro/sameboy_libretro.so "$ROM_PATH"

    update_save_retroarch 0 "" "$ROM_ID" "$ROM_PATH"
}

#############################################
# Game Boy Color (GBC)
#############################################

start_rom_gbc () {
    local ROM_PATH=$1
    local ROM_ID=$2
    local SAVE_DATA_PATH=$3

    update_save_retroarch 1 "${SAVE_DATA_PATH}" "$ROM_ID" "$ROM_PATH"

    notify_user "=== Launching RetroArch (Game Boy Color / SameBoy)...\n"
    retroarch -f -L /usr/lib/libretro/sameboy_libretro.so "$ROM_PATH"

    update_save_retroarch 0 "" "$ROM_ID" "$ROM_PATH"
}

#############################################
# Nintendo Game Boy Advance
#############################################

start_rom_gba () {
    local ROM_PATH=$1
    local ROM_ID=$2
    local SAVE_DATA_PATH="/tmp/romm_ra_save.tar.gz"

    # Download save from ROMM before launching
    update_save_retroarch 1 "${SAVE_DATA_PATH}" "$ROM_ID" "$ROM_PATH"

    notify_user "=== Launching RetroArch (Game Boy Advance / mGBA)...\n"
    retroarch -f -L /usr/lib/libretro/mgba_libretro.so "$ROM_PATH"

    # Upload save to ROMM after exiting
    update_save_retroarch 0 "" "$ROM_ID" "$ROM_PATH"
}

#############################################
# Nintendo GameCube
#############################################

start_rom_ngc () {
    local ROM_PATH=$1
    local ROM_ID=$2
    local SAVE_DATA_PATH="/tmp/romm_ra_save.tar.gz"

    # TODO: I need to get the gamecube stuff working better.
    #       This follows a different file layout than normal so it just isn't going
    #       to be as simple as some of the other retroarch stuff.
    # Download save from ROMM before launching
    #update_save_retroarch 1 "${SAVE_DATA_PATH}" "$ROM_ID" "$ROM_PATH"

    notify_user "=== Launching RetroArch (GameCube / Dolphin)...\n"
    dolphin-emu -b -e "$ROM_PATH"
    #retroarch -f -L /usr/lib/libretro/dolphin_libretro.so "$ROM_PATH"

    # Upload save to ROMM after exiting
    #update_save_retroarch 0 "" "$ROM_ID" "$ROM_PATH"
}

#############################################
# Nintendo Wii
#############################################

start_rom_wii () {
    local ROM_PATH=$1
    local ROM_ID=$2
    local SAVE_DATA_PATH="/tmp/romm_wii_save.tar.gz"

    # TODO: I need to get the wii stuff working better. See gamecube
    # Save sync temporarily bypassed
    # update_save_wii 1 "${SAVE_DATA_PATH}" "$ROM_ID"

    printf "=== Launching Standalone Dolphin (Wii)...\n"
    dolphin-emu -b -e "$ROM_PATH"

    # Save sync temporarily bypassed
    # update_save_wii 0 "${SAVE_DATA_PATH}" "$ROM_ID"
}

#######
# NOTE: I don't think I have throughly tested much of the Atari systems yet.
# In fact, I am pretty sure one of the systems doesn't work yet at all. Can't
# remember which.
#######

#############################################
# Atari 2600
#############################################

start_rom_atari2600 () {
    local ROM_PATH=$1
    local ROM_ID=$2
    local SAVE_DATA_PATH=$3

    update_save_retroarch 1 "${SAVE_DATA_PATH}" "$ROM_ID" "$ROM_PATH"

    notify_user "=== Launching RetroArch (Atari 2600 / Stella)...\n"
    retroarch -f -L /usr/lib/libretro/stella_libretro.so "$ROM_PATH"

    update_save_retroarch 0 "" "$ROM_ID" "$ROM_PATH"
}

#############################################
# Atari 5200
#############################################

start_rom_atari5200 () {
    local ROM_PATH=$1
    local ROM_ID=$2
    local SAVE_DATA_PATH=$3

    update_save_retroarch 1 "${SAVE_DATA_PATH}" "$ROM_ID" "$ROM_PATH"

    notify_user "=== Launching RetroArch (Atari 5200 / a5200)...\n"
    retroarch -f -L /usr/lib/libretro/a5200_libretro.so "$ROM_PATH"

    update_save_retroarch 0 "" "$ROM_ID" "$ROM_PATH"
}

#############################################
# Atari 7800
#############################################

start_rom_atari7800 () {
    local ROM_PATH=$1
    local ROM_ID=$2
    local SAVE_DATA_PATH=$3

    update_save_retroarch 1 "${SAVE_DATA_PATH}" "$ROM_ID" "$ROM_PATH"

    notify_user "=== Launching RetroArch (Atari 7800 / ProSystem)...\n"
    retroarch -f -L /usr/lib/libretro/prosystem_libretro.so "$ROM_PATH"

    update_save_retroarch 0 "" "$ROM_ID" "$ROM_PATH"
}

#############################################
# Sega Genesis / Mega Drive
#############################################

start_rom_genesis () {
    local ROM_PATH=$1
    local ROM_ID=$2
    local SAVE_DATA_PATH=$3

    update_save_retroarch 1 "${SAVE_DATA_PATH}" "$ROM_ID" "$ROM_PATH"

    notify_user "=== Launching RetroArch (Sega Genesis / Genesis Plus GX)...\n"
    retroarch -f -L /usr/lib/libretro/genesis_plus_gx_libretro.so "$ROM_PATH"

    update_save_retroarch 0 "" "$ROM_ID" "$ROM_PATH"
}

#############################################
# Sega 32X
#############################################

start_rom_sega32 () {
    local ROM_PATH=$1
    local ROM_ID=$2
    local SAVE_DATA_PATH="/tmp/romm_ra_save.tar.gz"

    update_save_retroarch 1 "${SAVE_DATA_PATH}" "$ROM_ID" "$ROM_PATH"

    notify_user "=== Launching RetroArch (Sega 32X / PicoDrive)...\n"
    retroarch -f -L /usr/lib/libretro/picodrive_libretro.so "$ROM_PATH"

    update_save_retroarch 0 "" "$ROM_ID" "$ROM_PATH"
}

#############################################
# Sega Master System (SMS)
#############################################

start_rom_sms () {
    local ROM_PATH=$1
    local ROM_ID=$2
    local SAVE_DATA_PATH=$3

    update_save_retroarch 1 "${SAVE_DATA_PATH}" "$ROM_ID" "$ROM_PATH"

    notify_user "=== Launching RetroArch (Sega Master System / Genesis Plus GX)...\n"
    retroarch -f -L /usr/lib/libretro/genesis_plus_gx_libretro.so "$ROM_PATH"

    update_save_retroarch 0 "" "$ROM_ID" "$ROM_PATH"
}

#############################################
# Sega Game Gear
#############################################

start_rom_gamegear () {
    local ROM_PATH=$1
    local ROM_ID=$2
    local SAVE_DATA_PATH=$3

    update_save_retroarch 1 "${SAVE_DATA_PATH}" "$ROM_ID" "$ROM_PATH"

    notify_user "=== Launching RetroArch (Sega Game Gear / Genesis Plus GX)...\n"
    retroarch -f -L /usr/lib/libretro/genesis_plus_gx_libretro.so "$ROM_PATH"

    update_save_retroarch 0 "" "$ROM_ID" "$ROM_PATH"
}

#############################################
# Sega Saturn
#############################################

start_rom_saturn () {
    local ROM_PATH=$1
    local ROM_ID=$2
    local SAVE_DATA_PATH=$3

    update_save_retroarch 1 "${SAVE_DATA_PATH}" "$ROM_ID" "$ROM_PATH"

    notify_user "=== Launching RetroArch (Sega Saturn / Beetle Saturn)...\n"
    retroarch -f -L /usr/lib/libretro/mednafen_saturn_libretro.so "$ROM_PATH"

    update_save_retroarch 0 "" "$ROM_ID" "$ROM_PATH"
}

#############################################
# Nintendo 64
#############################################

start_rom_n64 () {
    local ROM_PATH=$1
    local ROM_ID=$2
    local SAVE_DATA_PATH=$3

    update_save_retroarch 1 "${SAVE_DATA_PATH}" "$ROM_ID" "$ROM_PATH"

    notify_user "=== Launching RetroArch (Nintendo 64 / Mupen64Plus-Next)...\n"
    retroarch -f -L /usr/lib/libretro/mupen64plus_next_libretro.so "$ROM_PATH"

    update_save_retroarch 0 "" "$ROM_ID" "$ROM_PATH"
}

#############################################
# Nintendo DS
#############################################

start_rom_nds () {
    local ROM_PATH=$1
    local ROM_ID=$2
    local SAVE_DATA_PATH=$3

    update_save_retroarch 1 "${SAVE_DATA_PATH}" "$ROM_ID" "$ROM_PATH"

    notify_user "=== Launching RetroArch (Nintendo DS / melonDS)...\n"
    retroarch -f -L /usr/lib/libretro/melonds_libretro.so "$ROM_PATH"

    update_save_retroarch 0 "" "$ROM_ID" "$ROM_PATH"
}

#############################################
# Nintendo 3DS
#############################################

start_rom_3ds () {
    local ROM_PATH=$1
    local ROM_ID=$2
    local SAVE_DATA_PATH="/tmp/romm_ra_save.tar.gz"

    update_save_retroarch 1 "${SAVE_DATA_PATH}" "$ROM_ID" "$ROM_PATH"

    printf "=== Launching RetroArch (Nintendo 3DS / Azahar)...\n"
    retroarch -f -L /usr/lib/libretro/azahar_libretro.so "$ROM_PATH"

    update_save_retroarch 0 "" "$ROM_ID" "$ROM_PATH"
}

#############################################
# Original Xbox
#############################################

start_rom_xbox () {
    local ROM_PATH=$1
    local ROM_ID=$2
    # TODO: Upload save data
    # We ignore the save data path entirely for xemu

    notify_user "=== Launching standalone xemu (Original Xbox)...\n"

    # Launch xemu directly, load the disc, and go fullscreen
    prime-run xemu -dvd_path "$ROM_PATH" -full-screen
}

#############################################
# Philips CD-i
#############################################

start_rom_philips-cd-i () {
    local ROM_PATH=$1
    local ROM_ID=$2
    local SAVE_DATA_PATH="/tmp/romm_ra_save.tar.gz"

    update_save_retroarch 1 "${SAVE_DATA_PATH}" "$ROM_ID" "$ROM_PATH"

    notify_user "=== Launching RetroArch (Philips CD-i / same_cdi)...\n"
    retroarch -f -L /usr/lib/libretro/same_cdi_libretro.so "$ROM_PATH"

    update_save_retroarch 0 "" "$ROM_ID" "$ROM_PATH"
}

#############################################
# TurboGrafx-16 / PC Engine
#############################################

start_rom_tg16 () {
    local ROM_PATH=$1
    local ROM_ID=$2
    local SAVE_DATA_PATH="/tmp/romm_ra_save.tar.gz"

    update_save_retroarch 1 "${SAVE_DATA_PATH}" "$ROM_ID" "$ROM_PATH"

    notify_user "=== Launching RetroArch (TurboGrafx-16 / Beetle PCE Fast)...\n"
    retroarch -f -L /usr/lib/libretro/mednafen_pce_fast_libretro.so "$ROM_PATH"

    update_save_retroarch 0 "" "$ROM_ID" "$ROM_PATH"
}

#############################################
# Sony PlayStation Portable
#############################################

start_rom_psp () {
    local ROM_PATH=$1
    local ROM_ID=$2
    local SAVE_DATA_PATH="/tmp/romm_ra_save.tar.gz"

    update_save_retroarch 1 "${SAVE_DATA_PATH}" "$ROM_ID" "$ROM_PATH"

    notify_user "=== Launching RetroArch (PlayStation Portable / PPSSPP)...\n"
    retroarch -f -L /usr/lib/libretro/ppsspp_libretro.so "$ROM_PATH"

    update_save_retroarch 0 "" "$ROM_ID" "$ROM_PATH"
}
