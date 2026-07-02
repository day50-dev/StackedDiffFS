#!/usr/bin/env python3
"""StackedFS Command-Line Interface."""

import sys
import argparse
from pathlib import Path

from .fuse import mount, unmount
from .layers import load_layers, load_layers_from_json


def main():
    parser = argparse.ArgumentParser(
        prog="stackedfs",
        description="StackedFS - A layered FUSE filesystem with pre/post hook layers"
    )

    parser.add_argument("-l", "--layer", action="append", default=[],
                        help="Layer Python file (can be specified multiple times)")
    parser.add_argument("-f", "--file", type=str, default=None,
                        help="JSON file with list of layer paths")

    subparsers = parser.add_subparsers(dest="command", help="Sub-command")

    # mount
    mount_parser = subparsers.add_parser("mount", help="Mount the layered filesystem")
    mount_parser.add_argument("source", help="Source directory to mirror")
    mount_parser.add_argument("mount_point", help="Mount point (destination)")
    mount_parser.add_argument("--foreground", "-f", action="store_true",
                              help="Run in foreground")
    mount_parser.add_argument("--debug", "-d", action="store_true",
                              help="Enable FUSE debug output")
    mount_parser.add_argument("-l", "--layer", action="append", default=[],
                              help="Layer Python file")
    mount_parser.add_argument("--file", type=str, default=None,
                              help="JSON file with list of layer paths")

    # unmount
    unmount_parser = subparsers.add_parser("unmount", help="Unmount a filesystem")
    unmount_parser.add_argument("mount_point", help="Mount point to unmount")

    args = parser.parse_args()

    # Collect layers: command-level first, then top-level
    layer_specs = list(args.layer) if args.layer else []
    if hasattr(args, 'file') and args.file:
        layer_specs = []
        try:
            layers = load_layers_from_json(args.file)
            print(f"Loaded {len(layers)} layer(s) from {args.file}")
            for l in layers:
                print(f"  - {l.name}")
            if args.command != "mount":
                return
        except Exception as e:
            print(f"Error loading layers from {args.file}: {e}", file=sys.stderr)
            sys.exit(1)

    if args.command == "mount":
        if not layer_specs:
            print("Error: no layers specified (use -l or --file)", file=sys.stderr)
            sys.exit(1)
        try:
            mount(args.source, args.mount_point, layer_specs,
                  foreground=args.foreground, debug=args.debug)
        except Exception as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)

    elif args.command == "unmount":
        try:
            unmount(args.mount_point)
        except Exception as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)

    else:
        # No subcommand: just list/validate layers
        if not layer_specs and not args.file:
            parser.print_help()
            sys.exit(1)

        try:
            layers = load_layers(layer_specs)
            print(f"Loaded {len(layers)} layer(s):")
            for l in layers:
                print(f"  - {l.name}")
            print("Layers validated successfully.")
        except Exception as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()
