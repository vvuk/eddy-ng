#!/usr/bin/env python3

import os
import sys
import argparse
import shutil
from pathlib import Path

IS_MAC = os.path.isdir("/System/Library")

SED_IN_PLACE_ARG = "-i ''" if IS_MAC else "-i"

# On Klipper, the python module is installed as a package directory in
# klippy/extras, and the firmware source is added to src/
KLIPPER_MODULE_DIR = "klippy/extras/probe_eddy_ng"
KLIPPER_FIRMWARE_FILE = "src/sensor_ldc1612_ng.c"
# Files that older versions installed individually into klippy/extras
OLD_KLIPPER_FILES = [
    "klippy/extras/probe_eddy_ng.py",
    "klippy/extras/ldc1612_ng.py",
    "klippy/extras/eddy_ng_bigfoot.py",
]
# Files and directories not to copy with --copy
COPY_IGNORE = shutil.ignore_patterns(".git", "__pycache__", "*.pyc", ".ruff_cache", ".beads")


def get_script_dir():
    return os.path.dirname(os.path.realpath(__file__))


def remove_path(path: str):
    if os.path.islink(path) or os.path.isfile(path):
        print(f"Removing {path}")
        os.remove(path)
    elif os.path.isdir(path):
        print(f"Removing {path}/")
        shutil.rmtree(path)


def uninstall_klipper(target_dir: str):
    for path in [KLIPPER_MODULE_DIR, KLIPPER_FIRMWARE_FILE] + OLD_KLIPPER_FILES:
        remove_path(os.path.join(target_dir, path))

    print("Unpatching src/Makefile...")
    makefile_path = os.path.join(target_dir, "src/Makefile")
    os.system(f"sed {SED_IN_PLACE_ARG} 's, sensor_ldc1612_ng.c,,' '{makefile_path}'")

    print("Unpatching klippy/extras/bed_mesh.py...")
    bed_mesh_path = os.path.join(target_dir, "klippy/extras/bed_mesh.py")
    os.system(
        f"sed {SED_IN_PLACE_ARG} 's,\"eddy\" in probe_name #eddy-ng,probe_name.startswith(\"probe_eddy_current\"),' '{bed_mesh_path}'"
    )
    return


def install_kalico(target_dir: str, uninstall: bool, copy: bool):
    if not uninstall:
        print("Congrats, you're running Kalico!")
        print("================================")

    python_module_path = os.path.join(target_dir, "klippy/plugins/probe_eddy_ng")
    firmware_module_path = os.path.join(target_dir, "src/extras/eddy-ng")

    old_module_path = os.path.join(target_dir, "klippy/extras/probe_eddy_ng.py")
    if os.path.islink(old_module_path) or os.path.isfile(old_module_path):
        print("Uninstalling old installation...")
        uninstall_klipper(target_dir)

    if os.path.exists(python_module_path) or os.path.islink(python_module_path):
        if not os.path.islink(python_module_path):
            print(f"{python_module_path} exists, but is not a symlink. Please remove it and try again.")
            sys.exit(1)
        os.unlink(python_module_path)

    if os.path.exists(firmware_module_path) or os.path.islink(firmware_module_path):
        if not os.path.islink(firmware_module_path):
            print(f"{firmware_module_path} exists, but is not a symlink. Please remove it and try again.")
            sys.exit(1)
        os.unlink(firmware_module_path)

    if uninstall:
        print("Removed firmware and plugin module links.")
        sys.exit(0)

    if copy:
        shutil.copytree(get_script_dir(), python_module_path, ignore=COPY_IGNORE)
        shutil.copytree(os.path.join(get_script_dir(), "eddy-ng"), firmware_module_path)
    else:
        os.symlink(get_script_dir(), python_module_path)
        os.symlink(os.path.join(get_script_dir(), "eddy-ng"), firmware_module_path)

    print("Installed links to firmware and plugin modules.")
    print("When rebuilding firmware, make sure to select eddy-ng")
    print("from the firmware extras in menuconfig.")
    print("(There's no need to run install again after eddy-ng updates.)")

def install_klipper(target_dir: str, uninstall: bool, copy: bool):
    if uninstall:
        print("Uninstalling files...")
        uninstall_klipper(target_dir)
        return

    # remove anything from a previous install, including the old per-file layout
    for path in [KLIPPER_MODULE_DIR, KLIPPER_FIRMWARE_FILE] + OLD_KLIPPER_FILES:
        remove_path(os.path.join(target_dir, path))

    print("Installing files...")
    for src_path, dest in (
        (get_script_dir(), KLIPPER_MODULE_DIR),
        (os.path.join(get_script_dir(), "eddy-ng/sensor_ldc1612_ng.c"), KLIPPER_FIRMWARE_FILE),
    ):
        dest_path = os.path.join(target_dir, dest)
        if copy:
            print(f"Copying {src_path} to {dest}")
            if os.path.isdir(src_path):
                shutil.copytree(src_path, dest_path, ignore=COPY_IGNORE)
            else:
                shutil.copyfile(src_path, dest_path)
        else:
            link_path = os.path.relpath(os.path.realpath(src_path), os.path.dirname(dest_path))
            print(f"Linking {dest} to {link_path}")
            os.symlink(link_path, dest_path)

    print("Patching src/Makefile...")
    makefile_path = os.path.join(target_dir, "src/Makefile")
    os.system(f"sed {SED_IN_PLACE_ARG} 's,sensor_ldc1612.c$,sensor_ldc1612.c sensor_ldc1612_ng.c,' '{makefile_path}'")

    # Klipper only allows BED_MESH_CALIBRATE METHOD=rapid_scan with probe_eddy_current
    print("Patching klippy/extras/bed_mesh.py...")
    bed_mesh_path = os.path.join(target_dir, "klippy/extras/bed_mesh.py")
    os.system(
        f"sed {SED_IN_PLACE_ARG} 's,probe_name.startswith(\"probe_eddy_current\"),\"eddy\" in probe_name #eddy-ng,' '{bed_mesh_path}'"
    )
    with open(bed_mesh_path) as f:
        if "#eddy-ng" not in f.read():
            print("Warning: couldn't patch bed_mesh.py; BED_MESH_CALIBRATE METHOD=rapid_scan won't be available with eddy-ng")


def main():
    parser = argparse.ArgumentParser(description="Install or uninstall components.")
    parser.add_argument("-u", "--uninstall", action="store_true", help="Uninstall files")
    parser.add_argument("--copy", action="store_true", help="Copy files instead of linking")
    parser.add_argument("target_dir", nargs="?", help="Target directory")

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

    if os.path.exists(os.path.join(target_dir, "klippy/extras/danger_options.py")):
        install_kalico(target_dir, uninstall, copy)
    else:
        install_klipper(target_dir, uninstall, copy)


if __name__ == "__main__":
    main()
