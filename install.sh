#!/bin/bash

SCRIPT_DIR="$(dirname "$(realpath "$0")")"
FILES_TO_COPY=(
    "sensor_ldc1612_ng.c:src"
    "probe_eddy_ng.py:klippy/extras"
    "ldc1612_ng.py:klippy/extras"
)
PATCH_FILE="klipper.patch"

usage() {
    echo "Usage: $0 [--uninstall] [TARGET_DIR]"
    exit 1
}

if [ "$1" == "--help" ]; then
    usage
fi

UNINSTALL=false
if [ "$1" == "--uninstall" ]; then
    UNINSTALL=true
    shift
fi

TARGET_DIR="$1"
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

NEEDS_REBUILD=0

if [ "$UNINSTALL" == "true" ]; then
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
    if [ -f "$TARGET_DIR/klippy/extras/bed_mesh.py" ]; then
        echo "Reversing patch..."
        (cd "$TARGET_DIR" && patch -p1 -R < "$SCRIPT_DIR/$PATCH_FILE")
    fi
else
    echo "Installing files..."
    for file in "${FILES_TO_COPY[@]}"; do
        SRC_FILE="${file%%:*}"
        SRC_PATH="${SCRIPT_DIR}/${SRC_FILE}"
        DEST_DIR="$TARGET_DIR/${file#*:}"
        DEST_PATH="${DEST_DIR}/${SRC_FILE}"
        
        if [ -f "$DEST_PATH" -a "$SRC_FILE" == "sensor_ldc1612_ng.c" ] &&
            ! cmp -s "$SRC_PATH" "$DEST_PATH" ;
        then
            NEEDS_REBUILD=1
        fi

        echo "Copying $SRC_FILE to $DEST_DIR"
        cp "$SRC_FILE" "$DEST_DIR/"
    done
    
    BED_MESH_FILE="$TARGET_DIR/klippy/extras/bed_mesh.py"
    if [ -f "$BED_MESH_FILE" ] && ! grep -q "eddy-ng patched" "$BED_MESH_FILE"; then
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

