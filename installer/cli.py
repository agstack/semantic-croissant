#!/usr/bin/env python3
"""
Interactive CLI Installer for Semantic Croissant on Linux.

Steps performed:
1. Git clone https://github.com/codata/semantic-croissant (or work inside an existing repo).
2. Download and unpack protected data dump archive with optional HTTP Basic authentication.
3. List available deployment profiles and prompt user to select one.
4. Build required Docker images and initialize environment.
5. Launch Docker Compose stack and run automated health checks and verification tests.
"""

import argparse
import base64
import getpass
import json
import os
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path


# ANSI Colors for clean terminal UI
class Colors:
    HEADER = "\033[95m"
    BLUE = "\033[94m"
    CYAN = "\033[96m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    BOLD = "\033[1m"
    RESET = "\033[0m"


def print_banner():
    banner = f"""
{Colors.CYAN}{Colors.BOLD}========================================================================
       SEMANTIC CROISSANT - AUTOMATED LINUX INSTALLER & DEPLOYER       
========================================================================{Colors.RESET}
"""
    print(banner)


def log_info(msg: str):
    print(f"{Colors.BLUE}[INFO]{Colors.RESET} {msg}")


def log_success(msg: str):
    print(f"{Colors.GREEN}[✓]{Colors.RESET} {msg}")


def log_warn(msg: str):
    print(f"{Colors.YELLOW}[WARN]{Colors.RESET} {msg}")


def log_error(msg: str):
    print(f"{Colors.RED}[ERROR]{Colors.RESET} {msg}", file=sys.stderr)


def ask_prompt(question: str, default: str = "", secret: bool = False) -> str:
    """Prompts the user for input with an optional default value or masked input."""
    if default:
        prompt_text = f"{Colors.BOLD}{question}{Colors.RESET} [{Colors.CYAN}{default}{Colors.RESET}]: "
    else:
        prompt_text = f"{Colors.BOLD}{question}{Colors.RESET}: "

    try:
        if secret:
            val = getpass.getpass(prompt_text).strip()
        else:
            val = input(prompt_text).strip()
    except (KeyboardInterrupt, EOFError):
        print("\n\nAborted by user.")
        sys.exit(130)

    return val if val else default


def check_prerequisites():
    """Verifies that Git, Docker, and Docker Compose are installed and accessible."""
    log_info("Checking system prerequisites...")
    missing = []
    for tool in ["git", "docker"]:
        if not shutil.which(tool):
            missing.append(tool)

    if missing:
        log_error(f"Missing required system dependencies: {', '.join(missing)}")
        log_error("Please install them before proceeding (e.g. 'sudo apt install git docker.io').")
        sys.exit(1)

    # Check docker compose functionality (v2 plugin or standalone)
    res = subprocess.run(["docker", "compose", "version"], capture_output=True, text=True)
    if res.returncode != 0:
        res2 = subprocess.run(["docker-compose", "version"], capture_output=True, text=True)
        if res2.returncode != 0:
            log_error("Docker Compose is required but neither 'docker compose' nor 'docker-compose' was found.")
            sys.exit(1)

    log_success("All system prerequisites found (git, docker, docker compose).")


def step_1_clone_repo(target_dir: str, repo_url: str = "https://github.com/codata/semantic-croissant") -> str:
    """Clones the repository if not already cloned in target_dir."""
    print(f"\n{Colors.BOLD}--- Step 1: Repository Setup ---{Colors.RESET}")
    abs_target = os.path.abspath(target_dir)

    # Check if target_dir is already a clone of semantic-croissant
    is_git_repo = os.path.isdir(os.path.join(abs_target, ".git"))
    has_compose = os.path.isfile(os.path.join(abs_target, "compose.yaml"))

    if is_git_repo and has_compose:
        log_info(f"Target directory '{abs_target}' already contains the Semantic Croissant repository.")
        use_existing = ask_prompt("Use existing repository in this folder? (y/n)", default="y").lower()
        if use_existing in ["y", "yes"]:
            return abs_target

    if os.path.exists(abs_target) and os.listdir(abs_target):
        # Target directory exists and is not empty
        new_dir = os.path.join(abs_target, "semantic-croissant")
        log_warn(f"Directory '{abs_target}' is not empty. Cloning into subfolder: '{new_dir}'")
        abs_target = new_dir

    log_info(f"Cloning {repo_url} into '{abs_target}'...")
    os.makedirs(abs_target, exist_ok=True)
    cmd = ["git", "clone", repo_url, abs_target]
    res = subprocess.run(cmd)
    if res.returncode != 0:
        log_error(f"Failed to clone repository from {repo_url}")
        sys.exit(res.returncode)

    log_success(f"Repository successfully cloned to {abs_target}")
    return abs_target


def step_2_download_dump(repo_dir: str):
    """
    Asks the user for a download URL, login, and password for the protected data dump,
    downloads the archive, and extracts it into the proper data/volume directories.
    """
    print(f"\n{Colors.BOLD}--- Step 2: Protected Data Dump Ingestion ---{Colors.RESET}")
    print("All required data can be restored from a protected dump archive (.tar.gz, .tgz, .zip, .tar).")
    has_dump = ask_prompt("Do you have a protected data dump URL to download? (y/n)", default="y").lower()

    if has_dump not in ["y", "yes"]:
        log_info("Skipping data dump download. Continuing with existing local data.")
        return

    download_url = ask_prompt("Enter the data dump download URL")
    while not download_url.startswith(("http://", "https://", "ftp://")):
        log_error("Invalid URL schema. Please provide a full URL (http:// or https://).")
        download_url = ask_prompt("Enter the data dump download URL")

    username = ask_prompt("Username for protected download (leave blank if none)", default="")
    password = ""
    if username:
        password = ask_prompt("Password", secret=True)

    dest_folder = ask_prompt(
        "Directory to unpack dump into (e.g. repo root, ./volumes, or data directory)",
        default=repo_dir
    )
    dest_folder = os.path.abspath(dest_folder)
    os.makedirs(dest_folder, exist_ok=True)

    # Local temporary download file
    parsed_path = urllib.parse.urlparse(download_url).path
    filename = os.path.basename(parsed_path) or "data_dump.tar.gz"
    local_archive_path = os.path.join(dest_folder, filename)

    log_info(f"Downloading data dump to '{local_archive_path}'...")

    # Build request with optional HTTP Basic Auth
    req = urllib.request.Request(download_url)
    if username:
        auth_str = f"{username}:{password}"
        encoded_auth = base64.b64encode(auth_str.encode("utf-8")).decode("ascii")
        req.add_header("Authorization", f"Basic {encoded_auth}")

    # Streaming download with progress bar
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            total_size = resp.headers.get("Content-Length")
            total_bytes = int(total_size) if total_size and total_size.isdigit() else 0
            downloaded = 0
            block_size = 65536
            t0 = time.time()

            with open(local_archive_path, "wb") as out_fp:
                while True:
                    chunk = resp.read(block_size)
                    if not chunk:
                        break
                    out_fp.write(chunk)
                    downloaded += len(chunk)
                    if total_bytes > 0:
                        pct = (downloaded / total_bytes) * 100.0
                        mb_down = downloaded / (1024 * 1024)
                        mb_tot = total_bytes / (1024 * 1024)
                        sys.stdout.write(f"\r  Downloading: {mb_down:.1f}MB / {mb_tot:.1f}MB ({pct:.1f}%)")
                    else:
                        mb_down = downloaded / (1024 * 1024)
                        sys.stdout.write(f"\r  Downloading: {mb_down:.1f}MB")
                    sys.stdout.flush()

            elapsed = time.time() - t0
            print(f"\n[+] Download completed in {elapsed:.1f}s.")
    except Exception as e:
        log_error(f"Download failed: {e}")
        retry = ask_prompt("Would you like to retry or skip? (retry/skip)", default="skip").lower()
        if retry == "retry":
            return step_2_download_dump(repo_dir)
        return

    # Extract the archive
    log_info(f"Extracting '{local_archive_path}' into '{dest_folder}'...")
    try:
        if tarfile.is_tarfile(local_archive_path):
            with tarfile.open(local_archive_path, "r:*") as tar:
                tar.extractall(path=dest_folder)
            log_success("Tar archive extracted successfully.")
        elif zipfile.is_zipfile(local_archive_path):
            with zipfile.ZipFile(local_archive_path, "r") as zf:
                zf.extractall(path=dest_folder)
            log_success("Zip archive extracted successfully.")
        else:
            log_warn(f"File '{filename}' does not appear to be a standard tar/zip archive. Left in place.")
    except Exception as e:
        log_error(f"Extraction error: {e}")

    # Ask if temporary downloaded archive should be kept
    keep = ask_prompt(f"Keep downloaded archive file '{filename}'? (y/n)", default="n").lower()
    if keep not in ["y", "yes"]:
        try:
            os.remove(local_archive_path)
            log_info(f"Removed temporary archive '{local_archive_path}'.")
        except OSError:
            pass


def step_3_select_profile(repo_dir: str) -> str:
    """Lists available environment profiles and prompts the user to select one."""
    print(f"\n{Colors.BOLD}--- Step 3: Deployment Profile Selection ---{Colors.RESET}")
    profiles_dir = os.path.join(repo_dir, "profiles")
    available_profiles = []

    if os.path.isdir(profiles_dir):
        for f in sorted(os.listdir(profiles_dir)):
            if f.endswith(".env"):
                available_profiles.append(f[:-4])

    if not available_profiles:
        available_profiles = ["croissant-live", "codata", "codata-dev", "ca4eosc", "cdif4eosc"]

    print("Available profiles found in repository:")
    for idx, p in enumerate(available_profiles, 1):
        print(f"  {Colors.CYAN}[{idx}]{Colors.RESET} {p}")

    default_profile = "croissant-live" if "croissant-live" in available_profiles else available_profiles[0]
    choice = ask_prompt(f"Select profile number or enter profile name", default=default_profile)

    if choice.isdigit() and 1 <= int(choice) <= len(available_profiles):
        selected_profile = available_profiles[int(choice) - 1]
    else:
        selected_profile = choice.strip()

    log_success(f"Selected profile: '{selected_profile}'")
    return selected_profile


def step_4_build_docker_images(repo_dir: str, profile_name: str):
    """Builds required Docker images and ensures volume permissions are initialized."""
    print(f"\n{Colors.BOLD}--- Step 4: Build Docker Images ---{Colors.RESET}")
    os.chdir(repo_dir)

    # 1. Build the API / MCP image for the profile
    api_image_name = f"api-{profile_name}"
    log_info(f"Building Docker image '{api_image_name}' from ./api/Dockerfile...")
    build_api_cmd = [
        "docker", "build",
        "-f", "api/Dockerfile",
        "-t", api_image_name,
        "."
    ]
    res_api = subprocess.run(build_api_cmd)
    if res_api.returncode != 0:
        log_error(f"Failed to build '{api_image_name}'")
        sys.exit(res_api.returncode)
    log_success(f"Built '{api_image_name}' successfully.")

    # 2. Build converters service if present
    converters_dir = os.path.join(repo_dir, "tools", "converters")
    if os.path.isdir(converters_dir):
        log_info("Building converters Docker image...")
        build_conv_cmd = [
            "docker", "build",
            "-f", "tools/converters/Dockerfile",
            "-t", f"{profile_name}-converters",
            "tools/converters"
        ]
        subprocess.run(build_conv_cmd)

    # 3. Pull required base images
    log_info("Pulling standard base images (Qlever, Elasticsearch, MinIO)...")
    base_images = [
        "docker.io/library/busybox",
        "ghcr.io/zazukoians/qlever-server:latest",
        "ghcr.io/zazukoians/qlever-ui:latest",
        "docker.elastic.co/elasticsearch/elasticsearch:8.14.3",
        "cgr.dev/chainguard/minio:latest"
    ]
    for img in base_images:
        subprocess.run(["docker", "pull", img])

    # 4. Run init service to ensure proper file permissions
    log_info("Initializing volume directory permissions via init service...")
    env_vars = os.environ.copy()
    env_vars["PROFILE_NAME"] = profile_name
    subprocess.run(
        ["docker", "compose", "--profile", profile_name, "up", "init"],
        env=env_vars
    )
    log_success("Docker images built and volume permissions set.")


def step_5_run_and_test(repo_dir: str, profile_name: str):
    """Starts the Docker Compose services and runs automated health checks."""
    print(f"\n{Colors.BOLD}--- Step 5: Run and Test Stack ---{Colors.RESET}")
    os.chdir(repo_dir)

    start_now = ask_prompt(f"Start Semantic Croissant stack for profile '{profile_name}' now? (y/n)", default="y").lower()
    if start_now not in ["y", "yes"]:
        log_info("Setup complete. You can start the stack anytime using:")
        print(f"  cd {repo_dir} && PROFILE_NAME={profile_name} docker compose --profile {profile_name} up -d")
        return

    env_vars = os.environ.copy()
    env_vars["PROFILE_NAME"] = profile_name

    log_info(f"Starting services with 'docker compose --profile {profile_name} up -d'...")
    res = subprocess.run(
        ["docker", "compose", "--profile", profile_name, "up", "-d"],
        env=env_vars
    )
    if res.returncode != 0:
        log_error("Docker Compose failed to start services.")
        sys.exit(res.returncode)

    log_info("Waiting 15 seconds for services to initialize...")
    time.sleep(15)

    # Read profile port configurations
    profile_env_file = os.path.join(repo_dir, "profiles", f"{profile_name}.env")
    ports = {
        "API": 7013,
        "MCP": 7070,
        "QLEVER": 7011,
        "ES": 9200,
    }

    if os.path.isfile(profile_env_file):
        with open(profile_env_file, "r") as pf:
            for line in pf:
                line = line.strip()
                if line.startswith("API_PORT="):
                    ports["API"] = int(line.split("=", 1)[1].strip())
                elif line.startswith("MCP_PORT="):
                    ports["MCP"] = int(line.split("=", 1)[1].strip())
                elif line.startswith("QLEVER_SERVER_PORT="):
                    ports["QLEVER"] = int(line.split("=", 1)[1].strip())
                elif line.startswith("ES_PORT="):
                    ports["ES"] = int(line.split("=", 1)[1].strip())

    print("\nRunning automated health checks:")

    # 1. Check API endpoint
    api_url = f"http://localhost:{ports['API']}/"
    try:
        req = urllib.request.Request(api_url)
        with urllib.request.urlopen(req, timeout=5) as r:
            log_success(f"API service is responding on port {ports['API']} (HTTP {r.status})")
    except Exception as e:
        log_warn(f"API service check on port {ports['API']} failed: {e}")

    # 2. Check MCP endpoint
    mcp_url = f"http://localhost:{ports['MCP']}/"
    try:
        req = urllib.request.Request(mcp_url)
        with urllib.request.urlopen(req, timeout=5) as r:
            log_success(f"MCP service is responding on port {ports['MCP']} (HTTP {r.status})")
    except Exception as e:
        log_warn(f"MCP service check on port {ports['MCP']} failed: {e}")

    # 3. Check QLever endpoint
    qlever_url = f"http://localhost:{ports['QLEVER']}/"
    try:
        req = urllib.request.Request(f"{qlever_url}?query=SELECT%20*%20WHERE%20%7B%20%3Fs%20%3Fp%20%3Fo%20%7D%20LIMIT%201")
        with urllib.request.urlopen(req, timeout=5) as r:
            log_success(f"QLever SPARQL service is responding on port {ports['QLEVER']} (HTTP {r.status})")
    except Exception as e:
        log_warn(f"QLever SPARQL service check on port {ports['QLEVER']} failed: {e}")

    # 4. Check Elasticsearch endpoint
    es_url = f"http://localhost:{ports['ES']}/"
    try:
        req = urllib.request.Request(es_url)
        with urllib.request.urlopen(req, timeout=5) as r:
            log_success(f"Elasticsearch service is responding on port {ports['ES']} (HTTP {r.status})")
    except Exception as e:
        log_warn(f"Elasticsearch service check on port {ports['ES']} failed: {e}")

    # 5. Run test suite if requested
    run_tests = ask_prompt("\nRun unit test suite (tests/test_ping.py)? (y/n)", default="y").lower()
    if run_tests in ["y", "yes"]:
        log_info("Running test_ping.py...")
        subprocess.run([sys.executable, "tests/test_ping.py"])

    print(f"\n{Colors.GREEN}{Colors.BOLD}========================================================================{Colors.RESET}")
    print(f"{Colors.GREEN}{Colors.BOLD}       SEMANTIC CROISSANT INSTALLATION COMPLETED SUCCESSFULLY!         {Colors.RESET}")
    print(f"{Colors.GREEN}{Colors.BOLD}========================================================================{Colors.RESET}")
    print(f"Profile:     {profile_name}")
    print(f"API:         http://localhost:{ports['API']}/")
    print(f"MCP SSE:     http://localhost:{ports['MCP']}/sse")
    print(f"QLever UI:   http://localhost:{ports.get('QLEVER', 7011) + 1}/")
    print(f"Manage:      cd {repo_dir} && PROFILE_NAME={profile_name} docker compose --profile {profile_name} ps\n")


def main():
    parser = argparse.ArgumentParser(
        description="Interactive CLI installation package for Semantic Croissant."
    )
    parser.add_argument(
        "--dir", "-d",
        default=".",
        help="Installation target directory (default: current directory '.')"
    )
    parser.add_argument(
        "--repo-url",
        default="https://github.com/codata/semantic-croissant",
        help="Git repository URL to clone"
    )
    args = parser.parse_args()

    print_banner()
    check_prerequisites()

    # Step 1: Clone or locate repository
    repo_dir = step_1_clone_repo(target_dir=args.dir, repo_url=args.repo_url)

    # Step 2: Download and unpack protected dump
    step_2_download_dump(repo_dir=repo_dir)

    # Step 3: Select profile
    profile_name = step_3_select_profile(repo_dir=repo_dir)

    # Step 4: Build Docker images
    step_4_build_docker_images(repo_dir=repo_dir, profile_name=profile_name)

    # Step 5: Run and test
    step_5_run_and_test(repo_dir=repo_dir, profile_name=profile_name)


if __name__ == "__main__":
    main()

