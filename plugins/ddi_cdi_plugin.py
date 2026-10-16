#!/usr/bin/env python3
"""
ddi_cdi_plugin.py - Semantic Croissant plugin for DDI-CDI metadata conversion.
Relies on tools/DDICDI_generator_gen2 and convertors/ddi-cdi.py.
"""

import os
import sys
import argparse
import subprocess

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS_DIR = os.path.join(REPO_ROOT, "tools")
DDICDI_TOOL_DIR = os.path.join(TOOLS_DIR, "DDICDI_generator_gen2")
CONVERTOR_SCRIPT = os.path.join(REPO_ROOT, "convertors", "ddi-cdi.py")


def check_tool_installation():
    """Verify that tools/DDICDI_generator_gen2 is present and valid."""
    if not os.path.isdir(DDICDI_TOOL_DIR):
        raise FileNotFoundError(
            f"Error: DDICDI_generator_gen2 tool is not available in '{TOOLS_DIR}'.\n"
            f"Expected directory: {DDICDI_TOOL_DIR}\n"
            f"Please ensure it is installed under 'tools/DDICDI_generator_gen2'."
        )

    # Check for core project files
    pyproject_file = os.path.join(DDICDI_TOOL_DIR, "pyproject.toml")
    docker_file = os.path.join(DDICDI_TOOL_DIR, "Dockerfile")
    ddicdi_pkg = os.path.join(DDICDI_TOOL_DIR, "ddicdi")

    if not (os.path.exists(pyproject_file) or os.path.exists(docker_file) or os.path.isdir(ddicdi_pkg)):
        raise RuntimeError(
            f"Error: DDICDI_generator_gen2 tool directory '{DDICDI_TOOL_DIR}' is incomplete or uninstalled.\n"
            f"Missing essential files (pyproject.toml, Dockerfile, or ddicdi package)."
        )

    return True


def run_conversion(args_list):
    """Run convertors/ddi-cdi.py with provided arguments."""
    check_tool_installation()

    if not os.path.isfile(CONVERTOR_SCRIPT):
        raise FileNotFoundError(f"Error: Convertor script not found at {CONVERTOR_SCRIPT}")

    cmd = [sys.executable, CONVERTOR_SCRIPT] + args_list
    result = subprocess.run(cmd)
    return result.returncode


def main():
    parser = argparse.ArgumentParser(
        description="Semantic Croissant DDI-CDI Plugin: Convert tabular data to DDI-CDI JSON-LD"
    )
    parser.add_argument(
        "--url",
        default="https://gist.githubusercontent.com/nofurtherinformation/2d1017aa32e0163165c00afccff82dbd/raw/4976b4a890ca7f43049e5656b989c3652bede005/gdp_per_capita.csv",
        help="URL of the CSV file to convert",
    )
    parser.add_argument("--file", help="Local file path to convert")
    parser.add_argument("--api-url", help="Base URL of DDI-CDI Studio API")
    parser.add_argument("--api-key", help="API Key for X-API-Key header")
    parser.add_argument("--max-rows", type=int, default=5, help="Number of rows to include (default: 5)")
    parser.add_argument("--process-all-rows", action="store_true", help="Process all rows")
    parser.add_argument("--include-statistics", action="store_true", help="Include summary statistics in output")
    parser.add_argument("--output", "-o", help="Save result to file")
    parser.add_argument("--check-status", action="store_true", help="Check tool installation and API health")

    args = parser.parse_args()

    try:
        check_tool_installation()
        print(f"✓ Tool DDICDI_generator_gen2 is available and installed at: {DDICDI_TOOL_DIR}")
    except (FileNotFoundError, RuntimeError) as e:
        print(f"\n[DDI-CDI PLUGIN ERROR]\n{e}\n", file=sys.stderr)
        sys.exit(1)

    forward_args = []
    if args.check_status:
        forward_args.append("--check-info")
    else:
        if args.file:
            forward_args.extend(["--file", args.file])
        else:
            forward_args.extend(["--url", args.url])

        if args.api_url:
            forward_args.extend(["--api-url", args.api_url])
        if args.api_key:
            forward_args.extend(["--api-key", args.api_key])
        if args.max_rows != 5:
            forward_args.extend(["--max-rows", str(args.max_rows)])
        if args.process_all_rows:
            forward_args.append("--process-all-rows")
        if args.include_statistics:
            forward_args.append("--include-statistics")
        if args.output:
            forward_args.extend(["--output", args.output])

    sys.exit(run_conversion(forward_args))


if __name__ == "__main__":
    main()
