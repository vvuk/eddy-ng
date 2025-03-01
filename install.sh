#!/bin/bash

SCRIPT_DIR="$(dirname "$(realpath "$0")")"
FILES_TO_COPY=(
    "sensor_ldc1612_ng.c:src"
    "probe_eddy_ng.py:klippy/extras"
    "ldc1612_ng.py:klippy/extras"
)
PATCH_FILE="klipper.patch"

usage() {
    echo "Usage: $0 [-u|--uninstall] [--copy] [TARGET_DIR]"
    exit 1
}

UNINSTALL=0
COPY=0
TARGET_DIR=""

while [[ $# -gt 0 ]] ; do
    case "$1" in
        -u|--uninstall)
            UNINSTALL=1
            ;;
        --copy)
            COPY=1
            ;;
        -h|--help|--*)
            usage
            ;;
        *)
            [ ! -z "$TARGET_DIR" ] && usage
            TARGET_DIR="$1"
            ;;
    esac
    shift
done

if [ -z "$TARGET_DIR" ]; then
    if [ -d "$HOME/klipper" ]; then
        TARGET_DIR="$HOME/klipper"
    elif [ -d "$HOME/kalico" ]; then
        TARGET_DIR="$HOME/kalico"
    else
        echo "Error: No target directory provided and no default directories found."
        usage
    fi
fi

if [ ! -d "$TARGET_DIR" ]; then
    echo "Error: Target directory '$TARGET_DIR' does not exist."
    exit 1
fi

if [[ "$COPY" == "0" && -d "/System/Library" ]] ; then
    echo "Forcing copy on macOS"
    COPY=1
fi

NEEDS_REBUILD=0

if [ "$UNINSTALL" == "1" ]; then
    echo "Uninstalling files..."
    for file in "${FILES_TO_COPY[@]}"; do
        SRC_FILE="${SCRIPT_DIR}/${file%%:*}"
        DEST_DIR="$TARGET_DIR/${file#*:}"
        DEST_FILE="$DEST_DIR/$(basename "$SRC_FILE")"
        
        if [ -f "$DEST_FILE" ]; then
            echo "Removing $DEST_FILE"
            rm -f "$DEST_FILE"
        else
            echo "File $DEST_FILE does not exist. Skipping."
        fi
    done
    
    # Reverse the patch
    if grep -q "ldc1612_ng" "$TARGET_DIR/src/Makefile" ; then
        echo "Reversing patch..."
        (cd "$TARGET_DIR" && patch -p1 -R < "$SCRIPT_DIR/$PATCH_FILE")
    fi
else
    echo "Installing files..."
    for file in "${FILES_TO_COPY[@]}"; do
        SRC_FILE="${file%%:*}"
        SRC_PATH="${SCRIPT_DIR}/${SRC_FILE}"
        DEST_DIR="$TARGET_DIR/${file#*:}"
       
        if [ "$COPY" == "1" ] ; then
            cp -v "$SRC_FILE" "$DEST_DIR/"
        else
            LINKPATH="$(realpath $SRC_PATH --relative-to=$DEST_DIR)"
            ln -sfv "$LINKPATH" "$DEST_DIR/"
        fi
    done

    if ! grep -q "ldc1612_ng" "$TARGET_DIR/src/Makefile"; then
        echo "Applying patch..."
        (cd "$TARGET_DIR" && patch -p1 < "$SCRIPT_DIR/$PATCH_FILE")
    else
        echo "(Patch already applied, skipping.)"
    fi
fi

echo "Installation completed."

if [ "$NEEDS_REBUILD" == "1" ] ; then
    echo "Klipper firmware source was updated."
    echo "Please rebuild the Klipper firmware for your Eddy probe and reflash."
fi

