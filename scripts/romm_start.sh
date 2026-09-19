#!/bin/sh

. ${HOME}/.local/bin/romm_helpers/notify.sh
. ${HOME}/.local/bin/romm_helpers/device_env.sh
. ${HOME}/.local/bin/romm_helpers/start_rom.sh

register_device_if_needed || exit 1

PLATFORM=$1
ROM=$2

[ $# -lt 2 ] && notify_user "Usage: $0 [platform] [rom]" && exit 1

FILENAME=$(basename "$ROM")
SEARCH_TERM="$FILENAME"

if [ -z "$PLATFORM" ] || [ -z "$ROM" ] || [ ! -f "$ROM" ]; then
    notify_user "Failed to start up '$ROM'." >&2
    exit 1
fi

LOCAL_ROM_DIR="$HOME/.local/share/romm_bridge/roms"
TRUE_ROM="${LOCAL_ROM_DIR}/${PLATFORM}/$(basename "$ROM")"

PLATFORM_ID=$(curl -s -H "Authorization: Bearer $API_KEY" ${ROMM_URL}/api/platforms | jq -r '.[] | select(.slug == "'$PLATFORM'") | .id')
if [ -z "$PLATFORM_ID" ]; then
    notify_user "No platform ID was found for $PLATFORM"
    exit 1
fi

# Get the list of file IDs and their actual names from the ROMM search
# NOTE: we use .items[0] assuming the first result is our best match
# There is the chance that it isn't I suppose. But probably not.
SEARCH_RESULT=$(curl -s -G --data-urlencode "platform_ids=${PLATFORM_ID}" --data-urlencode "search_term=${SEARCH_TERM}" \
    -H "Authorization: Bearer $API_KEY" \
    "${ROMM_URL}/api/roms" | jq -r '.items[0]')

ROM_ID=$(echo $SEARCH_RESULT | jq -r '.id')
if [ -z "$ROM_ID" ]; then
    notify_user "No ROM ID was found in ROMM. Error loading ROM: $(basename "$ROM")"
    exit 1
fi

if [ ! -f "$TRUE_ROM" ]; then
    notify_user "=== ROM is not downloaded. Searching ROMM for: $(basename "$ROM")"

    mkdir -p $LOCAL_ROM_DIR/$PLATFORM

    FILE_DATA=$(echo $SEARCH_RESULT | jq -r '.files[] | "\(.id)|\(.file_name)"')
    if [ -z "$FILE_DATA" ]; then
        notify_user "Error: No files found for $SEARCH_TERM"
        exit 1
    fi

    # Loop through each file found in the ROMM entry
    echo "$FILE_DATA" | while IFS="|" read -r FILE_ID FILE_NAME; do
        # Define local path (e.g., ~/.local/share/romm/roms/psx/Silent Hill (USA).bin)
        TARGET_PATH="$LOCAL_ROM_DIR/$PLATFORM/$FILE_NAME"

        # Create the directory if it doesn't exist
        mkdir -p "$(dirname "$TARGET_PATH")"

        notify_user "==> Downloading: $FILE_NAME (ID: $FILE_ID)"

        # Hit the specific content endpoint
        # URL structure: /api/roms/{id}/files/content/{file_name}
        ENCODED_NAME=$(printf %s "$FILE_NAME" | jq -sRr @uri)
        curl -# -L -H "Authorization: Bearer $API_KEY" -o "$TARGET_PATH" \
            "${ROMM_URL}/api/roms/$FILE_ID/files/content/$ENCODED_NAME"
    done
fi

if [ -f "$TRUE_ROM" ]; then
    download_rom_save "$ROM_ID"
    [ $? -eq 0 ] && SAVE_DATA="/tmp/latest_save.tar.gz" || SAVE_DATA=

    START_ROM_FUNC="$(type -t start_rom_${PLATFORM})"
    if [ "$START_ROM_FUNC" = "function" ]; then
        start_rom_${PLATFORM} "$TRUE_ROM" "$ROM_ID" "$SAVE_DATA"
    else
        notify_user "Unsupported platform: $PLATFORM" >&2
        exit 1
    fi
else
    notify_user "Error: Download failed or file not found at $TRUE_ROM" >&2
    exit 1
fi
