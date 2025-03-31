#!/usr/bin/env python3

import os
import sys
import argparse
import subprocess
import platform
from pathlib import Path

def get_script_dir():
    return os.path.dirname(os.path.realpath(__file__))

FILES_TO_COPY = [
    "sensor_ldc1612_ng.c:src",
    "probe_eddy_ng.py:klippy/extras",
    "ldc1612_ng.py:klippy/extras"
]
PATCH_FILE = "klipper.patch"

def main():
    parser = argparse.ArgumentParser(description='Install or uninstall Klipper components.')
    parser.add_argument('-u', '--uninstall', action='store_true', help='Uninstall files')
    parser.add_argument('--copy', action='store_true', help='Copy files instead of linking')
    parser.add_argument('target_dir', nargs='?', help='Target directory')
    
    args = parser.parse_args()
    
    uninstall = args.uninstall
    copy = args.copy
    target_dir = args.target_dir
    
    # If no target directory provided, try defaults
    if not target_dir:
        home_dir = str(Path.home())
        if os.path.isdir(os.path.join(home_dir, "klipper")):
            target_dir = os.path.join(home_dir, "klipper")
        elif os.path.isdir(os.path.join(home_dir, "kalico")):
            target_dir = os.path.join(home_dir, "kalico")
        else:
            print("Error: No target directory provided and no default directories found.")
            parser.print_help()
            sys.exit(1)
    
    if not os.path.isdir(target_dir):
        print(f"Error: Target directory '{target_dir}' does not exist.")
        sys.exit(1)
    
    # Force copy on macOS
    if not copy and os.path.isdir("/System/Library"):
        print("Forcing copy on macOS")
        copy = True
    
    needs_rebuild = False
    
    if uninstall:
        print("Uninstalling files...")
        for file_entry in FILES_TO_COPY:
            src_file = file_entry.split(':')[0]
            dest_dir = os.path.join(target_dir, file_entry.split(':')[1])
            dest_file = os.path.join(dest_dir, os.path.basename(src_file))
            
            if os.path.isfile(dest_file):
                print(f"Removing {dest_file}")
                os.remove(dest_file)
            else:
                print(f"File {dest_file} does not exist. Skipping.")
        
        # Reverse the patch
        makefile_path = os.path.join(target_dir, "src/Makefile")
        if os.path.isfile(makefile_path):
            with open(makefile_path, 'r') as f:
                if "ldc1612_ng" in f.read():
                    print("Reversing patch...")
                    patch_file = os.path.join(get_script_dir(), PATCH_FILE)
                    subprocess.run(["patch", "-p1", "-R"], cwd=target_dir, stdin=open(patch_file, 'r'), check=True)
    else:
        print("Installing files...")
        for file_entry in FILES_TO_COPY:
            src_file = file_entry.split(':')[0]
            src_path = os.path.join(get_script_dir(), src_file)
            dest_dir = os.path.join(target_dir, file_entry.split(':')[1])
            
            if copy:
                print(f"Copying {src_file} to {dest_dir}/")
                subprocess.run(["cp", src_file, f"{dest_dir}/"], check=True)
            else:
                link_path = os.path.relpath(os.path.realpath(src_path), dest_dir)
                print(f"Linking {link_path} to {dest_dir}/")
                dest_file = os.path.join(dest_dir, os.path.basename(src_file))
                if os.path.islink(dest_file) or os.path.exists(dest_file):
                    os.remove(dest_file)
                os.symlink(link_path, dest_file)
        
        # Apply patch if not already applied
        makefile_path = os.path.join(target_dir, "src/Makefile")
        if os.path.isfile(makefile_path):
            with open(makefile_path, 'r') as f:
                if "ldc1612_ng" not in f.read():
                    print("Applying patch...")
                    patch_file = os.path.join(get_script_dir(), PATCH_FILE)
                    subprocess.run(["patch", "-p1"], cwd=target_dir, stdin=open(patch_file, 'r'), check=True)
                else:
                    print("(Patch already applied, skipping.)")
    
    print("Installation completed.")
    
    if needs_rebuild:
        print("Klipper firmware source was updated.")
        print("Please rebuild the Klipper firmware for your Eddy probe and reflash.")

if __name__ == "__main__":
    main()
