#!/usr/bin/env python3

import os
import sys
import argparse
import shutil
from pathlib import Path

IS_MAC = os.path.isdir("/System/Library")


def get_script_dir():
    return os.path.dirname(os.path.realpath(__file__))


def install_kalico(target_dir: str, uninstall: bool, copy: bool):
    print("Congrats, you're running Kalico!")

    python_module_path = os.path.join(target_dir, "klippy/plugins/probe_eddy_ng")
    firmware_module_path = os.path.join(target_dir, "src/extras/eddy-ng")

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
        shutil.copytree(get_script_dir(), python_module_path)
        shutil.copytree(os.path.join(get_script_dir(), "eddy-ng"), firmware_module_path)
    else:
        os.symlink(get_script_dir(), python_module_path)
        os.symlink(os.path.join(get_script_dir(), "eddy-ng"), firmware_module_path)

    print("Installed links to firmware and plugin modules.")
    print("When rebuilding firmware, make sure to select eddy-ng")
    print("from the firmware extras in menuconfig.")
    print("(There's no need to run install again after eddy-ng updates.)")


def install_klipper(target_dir: str, uninstall: bool, copy: bool):
    FILES_TO_COPY = {
        "eddy-ng/sensor_ldc1612_ng.c": "src",
        "probe_eddy_ng.py": "klippy/extras",
        "ldc1612_ng.py": "klippy/extras"
    }

    sed_in_place_arg = "-i ''" if IS_MAC else "-i"

    if uninstall:
        print("Uninstalling files...")
        for src_file, dest_dir in FILES_TO_COPY.items():
            dest_path = os.path.join(target_dir, dest_dir)
            dest_file = os.path.join(dest_path, os.path.basename(src_file))
            if os.path.isfile(dest_file):
                print(f"Removing {dest_file}")
                os.remove(dest_file)
            else:
                print(f"File {dest_file} does not exist. Skipping.")

        print("Unpatching src/Makefile...")
        makefile_path = os.path.join(target_dir, "src/Makefile")
        os.system(f"sed {sed_in_place_arg} 's, sensor_ldc1612_ng.c,,' '{makefile_path}'")

        print("Unpatching klippy/extras/bed-mesh.py...")
        bed_mesh_path = os.path.join(target_dir, "klippy/extras/bed_mesh.py")
        os.system(
            f"sed {sed_in_place_arg} 's,\"eddy\" in probe_name #eddy-ng,probe_name.startswith(\"probe_eddy_current\"),' '{bed_mesh_path}'"
        )
        return

    print("Installing files...")
    for src_file, dest_dir in FILES_TO_COPY.items():
        src_path = os.path.join(get_script_dir(), src_file)
        dest_path = os.path.join(target_dir, dest_dir)
        dest_file = os.path.join(dest_path, os.path.basename(src_file))

        if copy:
            print(f"Copying {src_file} to {dest_dir}/")
            shutil.copyfile(src_file, dest_file)
        else:
            link_path = os.path.relpath(os.path.realpath(src_path), dest_path)
            print(f"Linking {link_path} to {dest_dir}/")
            if os.path.islink(dest_file) or os.path.exists(dest_file):
                os.remove(dest_file)
            os.symlink(link_path, dest_file)

    print("Patching src/Makefile...")
    makefile_path = os.path.join(target_dir, "src/Makefile")
    os.system(f"sed {sed_in_place_arg} 's,sensor_ldc1612.c$,sensor_ldc1612.c sensor_ldc1612_ng.c,' '{makefile_path}'")

    print("Patching klippy/extras/bed-mesh.py...")
    bed_mesh_path = os.path.join(target_dir, "klippy/extras/bed_mesh.py")
    os.system(
        f"sed {sed_in_place_arg} 's,probe_name.startswith(\"probe_eddy_current\"),\"eddy\" in probe_name #eddy-ng,' '{bed_mesh_path}'"
    )


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
