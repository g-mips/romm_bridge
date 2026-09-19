#!/bin/bash

. ${HOME}/.local/bin/romm_helpers/device_env.sh

DRY_RUN=0
TARGET_PLATFORM=""

while [[ $# -gt 0 ]]; do
    case $1 in
        -d|--dry-run)
            DRY_RUN=1
            shift # past argument
            ;;
        -p|--platform)
            TARGET_PLATFORM="$2"
            shift # past argument
            shift # past value
            ;;
        -h|--help)
            printf "Usage: %s [OPTIONS]\n" "$0"
            printf "Options:\n"
            printf "  -d, --dry-run          Simulate downloads to /tmp/romm_dry_run_bios\n"
            printf "  -p, --platform <slug>  Only download BIOS files for the specified platform (e.g., gba, psx)\n"
            exit 0
            ;;
        *)
            printf "Unknown option: %s\n" "$1"
            exit 1
            ;;
    esac
done

DEST_ROOT="$HOME"
if [ "$DRY_RUN" -eq 1 ]; then
    DEST_ROOT="/tmp/romm_dry_run_bios"
    printf "=================================================\n"
    printf "=== DRY RUN MODE ACTIVE =========================\n"
    printf "Files will be downloaded to: %s\n" "$DEST_ROOT"
    printf "=================================================\n\n"
    # Optional: Start with a clean slate for the dry run
    rm -rf "$DEST_ROOT"
else
    printf "========================================\n"
    printf "=== Dynamic ROMM Firmware Sync ========\n"
    printf "========================================\n\n"
fi

RETROARCH_SYSTEM_DIR="$DEST_ROOT/.config/retroarch/system"

# Master Dictionary: Map the extracted ROMM slug to the Local Directory
declare -A PLATFORM_DIRS=(
    # TODO: I need to review these. I'm not 100% sure these are all correct. Got this list somewhere without verifying them all

    # RetroArch Cores
    ["3do"]="$RETROARCH_SYSTEM_DIR"
    ["64dd"]="$RETROARCH_SYSTEM_DIR"
    ["acpc"]="$RETROARCH_SYSTEM_DIR"
    ["amiga"]="$RETROARCH_SYSTEM_DIR"
    ["arcade"]="$RETROARCH_SYSTEM_DIR"
    ["atari-st"]="$RETROARCH_SYSTEM_DIR"
    ["atari5200"]="$RETROARCH_SYSTEM_DIR"
    ["atari7800"]="$RETROARCH_SYSTEM_DIR"
    ["atari8bit"]="$RETROARCH_SYSTEM_DIR"
    ["colecovision"]="$RETROARCH_SYSTEM_DIR"
    ["enterprise"]="$RETROARCH_SYSTEM_DIR"
    ["fairchild-channel-f"]="$RETROARCH_SYSTEM_DIR"
    ["famicom"]="$RETROARCH_SYSTEM_DIR"
    ["gamegear"]="$RETROARCH_SYSTEM_DIR"
    ["gb"]="$RETROARCH_SYSTEM_DIR"
    ["gba"]="$RETROARCH_SYSTEM_DIR"
    ["gbc"]="$RETROARCH_SYSTEM_DIR"
    ["genesis"]="$RETROARCH_SYSTEM_DIR"
    ["intellivision"]="$RETROARCH_SYSTEM_DIR"
    ["j2me"]="$RETROARCH_SYSTEM_DIR"
    ["lynx"]="$RETROARCH_SYSTEM_DIR"
    ["mac"]="$RETROARCH_SYSTEM_DIR"
    ["msx"]="$RETROARCH_SYSTEM_DIR"
    ["msx2"]="$RETROARCH_SYSTEM_DIR"
    ["nds"]="$RETROARCH_SYSTEM_DIR"
    ["neo-geo-cd"]="$RETROARCH_SYSTEM_DIR"
    ["nes"]="$RETROARCH_SYSTEM_DIR"
    ["odyssey-2-slash-videopac-g7000"]="$RETROARCH_SYSTEM_DIR"
    ["pc-9800-series"]="$RETROARCH_SYSTEM_DIR"
    ["pc-fx"]="$RETROARCH_SYSTEM_DIR"
    ["pokemon-mini"]="$RETROARCH_SYSTEM_DIR"
    ["psx"]="$RETROARCH_SYSTEM_DIR"
    ["satellaview"]="$RETROARCH_SYSTEM_DIR"
    ["saturn"]="$RETROARCH_SYSTEM_DIR"
    ["segacd"]="$RETROARCH_SYSTEM_DIR"
    ["sharp-x68000"]="$RETROARCH_SYSTEM_DIR"
    ["sms"]="$RETROARCH_SYSTEM_DIR"
    ["snes"]="$RETROARCH_SYSTEM_DIR"
    ["sufami-turbo"]="$RETROARCH_SYSTEM_DIR"
    ["super-gb"]="$RETROARCH_SYSTEM_DIR"
    ["tg16"]="$RETROARCH_SYSTEM_DIR"
    ["tvc"]="$RETROARCH_SYSTEM_DIR"
    ["videopac-g7400"]="$RETROARCH_SYSTEM_DIR"
    ["x1"]="$RETROARCH_SYSTEM_DIR"
    ["zxs"]="$RETROARCH_SYSTEM_DIR"

    # RetroArch Exceptions. These are those that don't follow the normal flow.
    ["dc"]="$RETROARCH_SYSTEM_DIR/dc"
    ["ngc"]="$RETROARCH_SYSTEM_DIR/dolphin-emu/Sys"
    ["psp"]="$RETROARCH_SYSTEM_DIR/PPSSPP/system"
    ["philips-cd-i"]="$RETROARCH_SYSTEM_DIR/same_cdi/bios"

    # Special Source Ports
    ["doom"]="$RETROARCH_SYSTEM_DIR"
    ["scummvm"]="$RETROARCH_SYSTEM_DIR/scummvm"
    ["wolfenstein"]="$RETROARCH_SYSTEM_DIR"

    # Standalone Native Emulators
    # NOTE: Some of the emulators in the above lists could end up here.
    ["xbox"]="$DEST_ROOT/.local/share/xemu/xemu"
    ["ps2"]="$DEST_ROOT/.config/PCSX2/bios"
)

# Ask ROMM for the master list of all firmwares
FIRMWARE_LIST=$(curl -s -H "Authorization: Bearer $API_KEY" "${ROMM_URL}/api/firmware")

if [ -z "$FIRMWARE_LIST" ]; then
    printf "Error: Could not retrieve firmware list from ROMM.\n"
    exit 1
fi

# Parse the JSON.
# Split "bios/gamegear" by "/" and take the second part to get the pure platform slug.
PARSED_DATA=$(echo "$FIRMWARE_LIST" | jq -r '.[] | "\(.id)|\(.file_name)|\(.file_path | split("/")[1])"')

# Loop through every firmware on the server
echo "$PARSED_DATA" | while IFS="|" read -r FILE_ID FILE_NAME PLATFORM_SLUG; do

    # If a specific platform was requested and this isn't it, skip immediately.
    if [ -n "$TARGET_PLATFORM" ] && [ "$PLATFORM_SLUG" != "$TARGET_PLATFORM" ]; then
        continue
    fi

    # Look up the target directory from our dictionary
    TARGET_DIR="${PLATFORM_DIRS[$PLATFORM_SLUG]}"

    # If the platform isn't in our dictionary, skip it
    if [ -z "$TARGET_DIR" ]; then
        continue
    fi

    TARGET_PATH="$TARGET_DIR/$FILE_NAME"

    # Check if we already downloaded it
    if [ -f "$TARGET_PATH" ]; then
        printf "[ OK ] %s already exists.\n" "$FILE_NAME"
        continue
    fi

    printf "[ !! ] Missing %s (Platform: %s). Downloading...\n" "$FILE_NAME" "$PLATFORM_SLUG"

    # Create the directory if it doesn't exist
    mkdir -p "$TARGET_DIR"

    # URL encode the filename just in case
    ENCODED_NAME=$(printf %s "$FILE_NAME" | jq -sRr @uri)

    # Now we actually download the file
    curl -# -L -H "Authorization: Bearer $API_KEY" -o "$TARGET_PATH" \
        "${ROMM_URL}/api/firmware/$FILE_ID/content/$ENCODED_NAME"

    if [ -s "$TARGET_PATH" ]; then
        printf "       -> Installed to %s\n" "$TARGET_PATH"
    else
        printf "       -> ERROR: Download failed for %s\n" "$FILE_NAME"
        rm -f "$TARGET_PATH"
    fi
done

printf "\n=== Firmware Sync Complete! ===\n"
