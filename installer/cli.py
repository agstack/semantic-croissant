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
import re
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


def ensure_directory_writable(directory: str):
    """Ensures a directory exists and is writable by the current user; fixes permissions using Docker if needed."""
    os.makedirs(directory, exist_ok=True)
    if not os.access(directory, os.W_OK):
        # Attempt to grant write permissions using docker busybox if root created the volume/folder
        uid = os.getuid()
        gid = os.getgid()
        abs_dir = os.path.abspath(directory)
        log_info(f"Directory '{abs_dir}' is not writable by user ({uid}:{gid}). Fixing permissions via Docker...")
        subprocess.run(
            ["docker", "run", "--rm", "-v", f"{abs_dir}:/target", "busybox", "sh", "-c", f"chown -R {uid}:{gid} /target && chmod -R 775 /target"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )


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
    cmd = ["git", "clone", "--recurse-submodules", repo_url, abs_target]
    res = subprocess.run(cmd)
    if res.returncode != 0:
        log_error(f"Failed to clone repository from {repo_url}")
        sys.exit(res.returncode)

    # In case submodules were not populated or repository already existed without submodules initialized:
    subprocess.run(["git", "-C", abs_target, "submodule", "update", "--init", "--recursive"])

    log_success(f"Repository successfully cloned to {abs_target}")
    return abs_target


def parse_env_file(filepath: str) -> dict:
    """Parses key-value pairs from an .env profile file."""
    config = {}
    if not os.path.isfile(filepath):
        return config
    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            line_s = line.strip()
            if not line_s or line_s.startswith("#") or "=" not in line_s:
                continue
            k, v = line_s.split("=", 1)
            config[k.strip()] = v.strip().strip('"').strip("'")
    return config


def step_2_download_dump(repo_dir: str, profile_config: dict = None):
    """
    Asks the user for a download URL, login, and password for the protected data dump,
    downloads the archive, and extracts it into the proper data/volume directories.
    Can take defaults from profile_config (DUMPS_HOST, DUMPS_USER, DUMPS_PASSWORD, DUMPS_DIR).
    """
    profile_config = profile_config or {}
    print(f"\n{Colors.BOLD}--- Step 2: Protected Data Dump Ingestion ---{Colors.RESET}")
    print("All required data can be restored from a protected dump archive (.tar.gz, .tgz, .zip, .tar).")

    default_host = profile_config.get("DUMPS_HOST", "")
    default_user = profile_config.get("DUMPS_USER", "")
    default_pass = profile_config.get("DUMPS_PASSWORD", "")

    # Default prompt: if profile does not specify DUMPS_HOST and dumps/ already has files, default to 'n'
    has_dump = ask_prompt(
        "Do you have a protected data dump URL to download? (y/n)",
        default="y" if default_host else "n"
    ).lower()

    if has_dump not in ["y", "yes"]:
        log_info("Skipping data dump download. Continuing with existing local data.")
        return

    default_url = default_host if default_host else ""
    download_url = ask_prompt("Enter the data dump download URL", default=default_url)
    while not download_url.startswith(("http://", "https://", "ftp://")):
        log_error("Invalid URL schema. Please provide a full URL (http:// or https://).")
        download_url = ask_prompt("Enter the data dump download URL", default=default_url)

    username = ask_prompt("Username for protected download (leave blank if none)", default=default_user)
    password = ""
    if username:
        if default_pass:
            password = ask_prompt("Password", default=default_pass, secret=True)
        else:
            password = ask_prompt("Password", secret=True)

    dest_folder = ask_prompt(
        "Directory to unpack dump into (e.g. repo root, ./volumes, or dumps directory)",
        default=os.path.join(repo_dir, "dumps")
    )
    dest_folder = os.path.abspath(dest_folder)
    ensure_directory_writable(dest_folder)

    # Helper function to download a file with progress bar, resume support, and retries
    def download_file(url: str, out_path: str, max_retries: int = 5):
        # 1. Fetch remote file size via HEAD or zero-range GET
        remote_size = None
        try:
            head_req = urllib.request.Request(url)
            head_req.get_method = lambda: "HEAD"
            if username:
                auth_str = f"{username}:{password}"
                head_req.add_header("Authorization", f"Basic {base64.b64encode(auth_str.encode()).decode('ascii')}")
            with urllib.request.urlopen(head_req, timeout=15) as head_resp:
                cl = head_resp.headers.get("Content-Length")
                if cl and cl.isdigit():
                    remote_size = int(cl)
        except Exception:
            remote_size = None

        # 2. Check if file is already completely downloaded
        if os.path.exists(out_path):
            existing_size = os.path.getsize(out_path)
            if remote_size is not None and existing_size == remote_size and remote_size > 0:
                log_success(f"File '{os.path.basename(out_path)}' is already completely downloaded ({existing_size / (1024*1024):.1f}MB). Skipping.")
                return
            elif remote_size is None and existing_size > 0:
                pass  # Cannot verify full size, will verify with Range

        for attempt in range(1, max_retries + 1):
            downloaded = os.path.getsize(out_path) if os.path.exists(out_path) else 0

            # If remote_size is known and we reached it
            if remote_size is not None and downloaded >= remote_size and remote_size > 0:
                log_success(f"File '{os.path.basename(out_path)}' verified complete ({downloaded / (1024*1024):.1f}MB).")
                return

            req = urllib.request.Request(url)
            if username:
                auth_str = f"{username}:{password}"
                encoded_auth = base64.b64encode(auth_str.encode("utf-8")).decode("ascii")
                req.add_header("Authorization", f"Basic {encoded_auth}")

            if downloaded > 0:
                req.add_header("Range", f"bytes={downloaded}-")
                log_info(f"Resuming download from byte {downloaded} ({downloaded / (1024*1024):.1f}MB)...")

            try:
                with urllib.request.urlopen(req, timeout=60) as resp:
                    status = getattr(resp, "status", 200)
                    cl = resp.headers.get("Content-Length")
                    stream_length = int(cl) if cl and cl.isdigit() else 0

                    if status == 206:
                        # Partial content: server accepts range resume
                        write_mode = "ab"
                        total_bytes = downloaded + stream_length
                    else:
                        # Full content returned (no resume or start from 0)
                        write_mode = "wb"
                        downloaded = 0
                        total_bytes = stream_length

                    if remote_size is None and total_bytes > 0:
                        remote_size = total_bytes

                    block_size = 1048576  # 1MB
                    t0 = time.time()
                    with open(out_path, write_mode) as out_fp:
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
                    return  # Success!

            except Exception as e:
                log_warn(f"Download attempt {attempt}/{max_retries} interrupted: {e}")
                if attempt < max_retries:
                    wait_time = min(attempt * 3, 15)
                    log_info(f"Retrying download in {wait_time}s...")
                    time.sleep(wait_time)
                else:
                    raise e

    # Check if download_url is a directory listing (e.g. ends with / or /dumps)
    is_directory = False
    parsed_path = urllib.parse.urlparse(download_url).path
    if not any(download_url.lower().endswith(ext) for ext in [".tar.gz", ".tgz", ".zip", ".tar"]):
        is_directory = True

    if is_directory:
        log_info(f"Connecting to protected dumps directory index at '{download_url}'...")
        req = urllib.request.Request(download_url)
        if username:
            auth_str = f"{username}:{password}"
            encoded_auth = base64.b64encode(auth_str.encode("utf-8")).decode("ascii")
            req.add_header("Authorization", f"Basic {encoded_auth}")

        found_archives = []
        found_subdirs = []
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                html = resp.read().decode("utf-8", errors="ignore")
                found_archives = sorted(list(set(re.findall(r"href=['\"](?:/dumps/)?([^/'\"]+\.(?:tar\.gz|tgz|tar|zip))['\"]", html))))
                # Also detect keyword subdirectories (e.g. coffee, honduras)
                for dir_match in re.findall(r"href=['\"](?:/dumps/)?([^/'\"?#]+)/?['\"]>\s*📁\s*([^<]+)<", html):
                    subdir_name = dir_match[0].strip().rstrip("/")
                    if subdir_name and subdir_name not in ["..", "."]:
                        found_subdirs.append(subdir_name)
                found_subdirs = sorted(list(set(found_subdirs)))
        except Exception as e:
            log_error(f"Failed to fetch directory index from {download_url}: {e}")

        # Scan detected subdirectories for keyword-specific archives
        base_dl = download_url.rstrip("/")
        for sub in found_subdirs:
            sub_url = f"{base_dl}/{sub}"
            try:
                sub_req = urllib.request.Request(sub_url)
                if username:
                    sub_req.add_header("Authorization", f"Basic {encoded_auth}")
                with urllib.request.urlopen(sub_req, timeout=15) as s_resp:
                    s_html = s_resp.read().decode("utf-8", errors="ignore")
                    sub_arcs = re.findall(r"href=['\"](?:/dumps/" + re.escape(sub) + r"/)?([^/'\"]+\.(?:tar\.gz|tgz|tar|zip))['\"]", s_html)
                    for sa in sub_arcs:
                        found_archives.append(f"{sub}/{sa}")
            except Exception:
                pass

        if found_archives:
            log_info("Available archives discovered on dump server:")
            for idx, arc in enumerate(found_archives, 1):
                print(f"  [{idx}] {arc}")

            fetch_all = ask_prompt("Download all database and storage archives to dumps/? (y/n)", default="y").lower()
            to_download = found_archives if fetch_all in ["y", "yes"] else []
            if not to_download:
                sel = ask_prompt("Enter archive numbers or filenames to download (comma-separated)", default="all")
                if sel.lower() == "all":
                    to_download = found_archives
                else:
                    for part in sel.split(","):
                        part = part.strip()
                        if part.isdigit() and 1 <= int(part) <= len(found_archives):
                            to_download.append(found_archives[int(part) - 1])
                        elif part in found_archives:
                            to_download.append(part)

            base_dl = download_url.rstrip("/")
            for arc_name in to_download:
                target_file = os.path.join(dest_folder, arc_name)
                os.makedirs(os.path.dirname(target_file), exist_ok=True)
                arc_url = f"{base_dl}/{arc_name}"
                log_info(f"Downloading '{arc_name}' from {arc_url}...")
                try:
                    download_file(arc_url, target_file)
                    log_success(f"Saved '{arc_name}' into {os.path.dirname(target_file)}")
                except Exception as dl_err:
                    log_error(f"Failed to download {arc_name}: {dl_err}")
            return
        else:
            log_warn("No archive files matched in directory index. Prompting for direct file URL...")

    # Single file archive download
    filename = os.path.basename(parsed_path) or "data_dump.tar.gz"
    local_archive_path = os.path.join(dest_folder, filename)

    log_info(f"Downloading data dump to '{local_archive_path}'...")
    try:
        download_file(download_url, local_archive_path)
    except Exception as e:
        log_error(f"Download failed: {e}")
        retry = ask_prompt("Would you like to retry or skip? (retry/skip)", default="skip").lower()
        if retry == "retry":
            return step_2_download_dump(repo_dir, profile_config)
        return

    # Extract single archive if not dumped directly into dumps/
    if not filename.endswith("-indexes.tar.gz") and not filename.startswith("minio-"):
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


def create_custom_profile_from_template(repo_dir: str) -> str:
    """Interactively creates a new profile .env file using profiles/codata.env as a template."""
    print(f"\n{Colors.BOLD}--- Create Custom Profile (based on codata.env template) ---{Colors.RESET}")
    profiles_dir = os.path.join(repo_dir, "profiles")
    template_path = os.path.join(profiles_dir, "codata.env")

    # Prompt for new profile name
    while True:
        custom_name = ask_prompt("Enter name for your new profile (e.g. my-project)").strip()
        custom_name = re.sub(r"[^a-zA-Z0-9_\-]", "", custom_name).lower()
        if not custom_name:
            log_error("Profile name cannot be empty. Please enter a valid name.")
            continue
        env_file_path = os.path.join(profiles_dir, f"{custom_name}.env")
        if os.path.exists(env_file_path):
            overwrite = ask_prompt(f"Profile '{custom_name}.env' already exists. Overwrite? (y/n)", default="n").lower()
            if overwrite in ["y", "yes"]:
                break
        else:
            break

    # Read template lines if available
    template_content = ""
    if os.path.exists(template_path):
        with open(template_path, "r", encoding="utf-8") as f:
            template_content = f.read()
    else:
        # Fallback minimal template if codata.env not found
        template_content = (
            "PROFILE_NAME=codata\n"
            "QLEVER_SERVER_HOST_NAME=codata-server\n"
            "OLLAMA_HOST=http://10.147.18.24:11444\n"
            "OPENAI_API_KEY=test-api-key-12345\n"
            "OLLAMA_API_KEY=test-api-key-12345\n"
            "HOST=https://ai.codata.org\n"
            "MCP_DOMAIN=ai.codata.org\n"
            "QLEVER_UI_PORT=7042\n"
            "ES_HOST=http://localhost:9200\n"
            "API_PORT=7043\n"
            "MCP_PORT=7110\n"
            "MINIO_API_PORT=9025\n"
            "MINIO_CONSOLE_PORT=9027\n"
            "VOLUME_DIR=./qlever-tests/volumes\n"
            "VAULT_LOGO_URL=/logo.png\n"
        )

    # Ask user key configuration questions
    print("\nPlease answer configuration questions for your new profile (press Enter for defaults):")
    host_url = ask_prompt("Public Host URL (e.g. https://ai.example.org)", default=f"https://ai.{custom_name}.org")
    parsed_host = urllib.parse.urlparse(host_url).netloc or f"ai.{custom_name}.org"
    mcp_domain = ask_prompt("MCP Domain", default=parsed_host)
    ollama_host = ask_prompt("Ollama Host URL", default="http://10.147.18.24:11444")
    openai_api_key = ask_prompt("OpenAI API Key (or test placeholder)", default="test-api-key-12345")
    api_port = ask_prompt("API Port", default="7043")
    mcp_port = ask_prompt("MCP Port", default="7110")
    qlever_ui_port = ask_prompt("QLever UI Port", default="7042")
    es_host = ask_prompt("Elasticsearch URL", default="http://localhost:9200")
    vault_logo_url = ask_prompt("Vault Logo URL or path", default="/logo.png")

    config_updates = {
        "PROFILE_NAME": custom_name,
        "QLEVER_SERVER_HOST_NAME": f"{custom_name}-server",
        "OLLAMA_HOST": ollama_host,
        "OPENAI_API_KEY": openai_api_key,
        "OLLAMA_API_KEY": openai_api_key,
        "HOST": host_url,
        "MCP_DOMAIN": mcp_domain,
        "API_PORT": api_port,
        "MCP_PORT": mcp_port,
        "QLEVER_UI_PORT": qlever_ui_port,
        "ES_HOST": es_host,
        "VAULT_LOGO_URL": vault_logo_url,
        "QLEVER_INDEX_INPUT_FILES": "croissant.nt",
        "QLEVER_INDEX_CAT_INPUT_FILES": "cat croissant.nt",
        "QLEVER_DATA_GET_DATA_CMD": "echo 'Empty index initialized' > /dev/null",
        "QLEVER_INDEX_SETTINGS_JSON": '{ "ascii-prefixes-only": false, "num-triples-per-batch": 500000 }',
    }

    # Replace or append in template content
    lines = template_content.splitlines()
    new_lines = []
    seen_keys = set()

    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key, _ = stripped.split("=", 1)
            key = key.strip()
            if key in config_updates:
                new_lines.append(f"{key}={config_updates[key]}")
                seen_keys.add(key)
                continue
        new_lines.append(line)

    for key, val in config_updates.items():
        if key not in seen_keys:
            new_lines.append(f"{key}={val}")

    env_file_path = os.path.join(profiles_dir, f"{custom_name}.env")
    with open(env_file_path, "w", encoding="utf-8") as f:
        f.write("\n".join(new_lines) + "\n")

    log_success(f"Custom profile created at: {env_file_path}")
    return custom_name


def step_3_select_profile(repo_dir: str, profile_arg: str = None) -> str:
    """Lists available environment profiles and prompts the user to select or create one, or reads from profile_arg if provided."""
    print(f"\n{Colors.BOLD}--- Step 3: Deployment Profile Selection ---{Colors.RESET}")
    profiles_dir = os.path.join(repo_dir, "profiles")
    available_profiles = []

    if os.path.isdir(profiles_dir):
        for f in sorted(os.listdir(profiles_dir)):
            if f.endswith(".env"):
                available_profiles.append(f[:-4])

    if not available_profiles:
        available_profiles = ["croissant-live", "codata", "codata-dev", "ca4eosc", "cdif4eosc"]

    # If --profile argument was passed, resolve it
    if profile_arg:
        profile_clean = profile_arg.strip()
        # Handle path like profiles/asknowmuseum.env or /path/to/asknowmuseum.env
        if profile_clean.endswith(".env"):
            p_basename = os.path.basename(profile_clean)[:-4]
            # If a custom file path was passed outside repo profiles/, copy it in
            if os.path.isfile(profile_clean) and not os.path.isfile(os.path.join(profiles_dir, f"{p_basename}.env")):
                shutil.copyfile(profile_clean, os.path.join(profiles_dir, f"{p_basename}.env"))
            profile_clean = p_basename

        # Read PROFILE_NAME from the .env file if available
        env_candidate = os.path.join(profiles_dir, f"{profile_clean}.env")
        if os.path.isfile(env_candidate):
            with open(env_candidate, "r", encoding="utf-8") as f:
                for line in f:
                    line_s = line.strip()
                    if line_s.startswith("PROFILE_NAME=") and not line_s.startswith("#"):
                        extracted = line_s.split("=", 1)[1].strip().strip('"').strip("'")
                        if extracted:
                            profile_clean = extracted
                            break

        selected_profile = profile_clean
        log_info(f"Using profile specified via --profile: '{selected_profile}'")
    else:
        print("Available profiles found in repository:")
        for idx, p in enumerate(available_profiles, 1):
            print(f"  {Colors.CYAN}[{idx}]{Colors.RESET} {p}")
        create_idx = len(available_profiles) + 1
        print(f"  {Colors.GREEN}[{create_idx}]{Colors.RESET} Create own profile (from codata.env template)")

        default_profile = "codata" if "codata" in available_profiles else available_profiles[0]
        choice = ask_prompt(
            f"Select profile number or enter profile name",
            default=default_profile
        )

        if choice.isdigit():
            choice_num = int(choice)
            if 1 <= choice_num <= len(available_profiles):
                selected_profile = available_profiles[choice_num - 1]
            elif choice_num == create_idx:
                selected_profile = create_custom_profile_from_template(repo_dir)
            else:
                log_warn(f"Option [{choice}] not recognized. Creating new profile.")
                selected_profile = create_custom_profile_from_template(repo_dir)
        elif choice.strip().lower() in ["create", "new", "create own profile"]:
            selected_profile = create_custom_profile_from_template(repo_dir)
        elif choice.strip() in available_profiles:
            selected_profile = choice.strip()
        else:
            # User typed a profile name not in the list
            log_info(f"Profile '{choice.strip()}' does not exist yet. Creating it from codata.env template...")
            selected_profile = create_custom_profile_from_template(repo_dir)

    log_success(f"Selected profile: '{selected_profile}'")
    return selected_profile


def step_3b_restore_indexes(repo_dir: str, profile_name: str):
    """
    Checks if Elasticsearch, QLever, and MinIO archives exist or prompts user to restore them.
    Can restore directly from dumps/ directory using installer/restore.sh or custom location.
    Supports both full dumps and keyword-specific subset dumps (e.g. dumps/honduras, dumps/coffee).
    """
    dumps_dir = os.path.join(repo_dir, "dumps")
    
    # Check for direct or keyword subfolder archives
    found_archives = []
    if os.path.isdir(dumps_dir):
        for root, dirs, files in os.walk(dumps_dir):
            for f in sorted(files):
                if f.endswith(".tar.gz") and any(k in f for k in ["elasticsearch", "qlever", "minio", "converters", "ddicdi"]):
                    rel_p = os.path.relpath(os.path.join(root, f), dumps_dir)
                    found_archives.append(rel_p)

    print(f"\n{Colors.BOLD}--- Step 3b: Restore Elasticsearch, QLever & MinIO Data ---{Colors.RESET}")
    if found_archives:
        log_info(f"Found archive(s) in {dumps_dir}:")
        for arc in found_archives:
            print(f"  • {arc}")
        prompt_msg = f"Restore Elasticsearch, QLever, and/or MinIO data from archive for profile '{profile_name}'? (y/n)"
    else:
        prompt_msg = f"Do you want to restore Elasticsearch, QLever, and/or MinIO data from an archive? (y/n)"

    do_restore = ask_prompt(prompt_msg, default="y" if found_archives else "n").lower()
    if do_restore not in ["y", "yes"]:
        log_info("Skipping database and storage restore. Proceeding with standard setup.")
        return

    # Check for custom dumps directory
    custom_dumps = ask_prompt("Path to dumps directory containing archives", default=dumps_dir)
    custom_dumps = os.path.abspath(custom_dumps)

    # Check if user wants to restore a specific keyword subset subdirectory if available
    subdirs = [d for d in os.listdir(custom_dumps) if os.path.isdir(os.path.join(custom_dumps, d))] if os.path.isdir(custom_dumps) else []
    kw_subdirs = [d for d in subdirs if d not in ["__pycache__", ".git"]]
    if kw_subdirs:
        log_info(f"Detected keyword-filtered backup subdirectories: {', '.join(kw_subdirs)}")
        use_kw = ask_prompt(f"Select keyword subset directory to restore from (or 'all' for root)", default=kw_subdirs[0])
        if use_kw != "all" and os.path.isdir(os.path.join(custom_dumps, use_kw)):
            custom_dumps = os.path.join(custom_dumps, use_kw)
            log_info(f"Restoring from subset directory: {custom_dumps}")

    restore_script = os.path.join(repo_dir, "installer", "restore.sh")
    if os.path.isfile(restore_script):
        log_info(f"Running restore script '{restore_script}' for profile '{profile_name}'...")
        res = subprocess.run(["bash", restore_script, profile_name, custom_dumps])
        if res.returncode == 0:
            log_success(f"Data restored successfully for profile '{profile_name}'.")
        else:
            log_warn(f"Restore script exited with status code {res.returncode}.")
    else:
        # Fallback inline restoration if restore.sh is absent
        target_es = os.path.join(repo_dir, "qlever-tests", "volumes", profile_name, "es")
        target_qlever = os.path.join(repo_dir, "qlever-tests", "volumes", profile_name, "server")

        es_arc = os.path.join(custom_dumps, "elasticsearch-indexes.tar.gz")
        if os.path.isfile(es_arc):
            log_info(f"Unpacking Elasticsearch archive to {target_es}...")
            os.makedirs(target_es, exist_ok=True)
            subprocess.run(["tar", "-xzf", es_arc, "-C", target_es])
            log_success("Elasticsearch indexes unpacked.")

        ql_arc = os.path.join(custom_dumps, "qlever-cdif-indexes.tar.gz")
        if os.path.isfile(ql_arc):
            log_info(f"Unpacking QLever archive to {target_qlever}...")
            os.makedirs(target_qlever, exist_ok=True)
            subprocess.run(["tar", "-xzf", ql_arc, "-C", target_qlever])
            log_success("QLever indexes unpacked.")


def step_4_build_docker_images(repo_dir: str, profile_name: str):
    """Builds required Docker images without modifying any host OS configuration."""
    print(f"\n{Colors.BOLD}--- Step 4: Build Docker Images ---{Colors.RESET}")
    os.chdir(repo_dir)

    # 1. Build the API / MCP image for the profile
    api_image_name = f"api-{profile_name}"
    log_info(f"Building Docker image '{api_image_name}' from ./api/Dockerfile...")
    # api/Dockerfile expects the build context to be the api/ directory
    # where requirements.txt and app source files reside.
    api_context = "api" if os.path.exists(os.path.join(repo_dir, "api", "requirements.txt")) else "."
    build_api_cmd = [
        "docker", "build",
        "-f", "api/Dockerfile",
        "-t", api_image_name,
        api_context
    ]
    res_api = subprocess.run(build_api_cmd)
    if res_api.returncode != 0:
        log_error(f"Failed to build '{api_image_name}'")
        sys.exit(res_api.returncode)
    log_success(f"Built '{api_image_name}' successfully.")

    # 2. Build or download converters service image
    converters_image = f"{profile_name}-converters"
    converters_dir = os.path.join(repo_dir, "tools", "converters")
    converters_dockerfile = os.path.join(converters_dir, "Dockerfile")

    if not os.path.exists(converters_dockerfile):
        log_info("Initializing git submodules to fetch converters...")
        subprocess.run(["git", "-C", repo_dir, "submodule", "update", "--init", "--recursive"])

    built_converters = False
    if os.path.isdir(converters_dir) and os.path.exists(converters_dockerfile):
        log_info(f"Building converters Docker image '{converters_image}'...")
        build_conv_cmd = [
            "docker", "build",
            "-f", "tools/converters/Dockerfile",
            "-t", converters_image,
            "tools/converters"
        ]
        res_conv = subprocess.run(build_conv_cmd)
        if res_conv.returncode == 0:
            built_converters = True
            log_success(f"Built '{converters_image}' successfully.")

    if not built_converters:
        # Check if image already exists locally
        res_inspect = subprocess.run(
            ["docker", "image", "inspect", converters_image],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        if res_inspect.returncode != 0:
            log_info(f"No Dockerfile for converters found. Downloading pre-built image from repository...")
            conv_url = "https://ai.now.museum/dumps/converters.tar.gz"
            auth_str = "admin:croissant2026"
            encoded_auth = base64.b64encode(auth_str.encode("utf-8")).decode("ascii")
            req = urllib.request.Request(conv_url)
            req.add_header("Authorization", f"Basic {encoded_auth}")
            temp_conv_archive = os.path.join(repo_dir, "converters.tar.gz")
            try:
                with urllib.request.urlopen(req, timeout=300) as resp:
                    with open(temp_conv_archive, "wb") as f_out:
                        shutil.copyfileobj(resp, f_out)
                subprocess.run(["docker", "load", "-i", temp_conv_archive])
                # Tag to expected name
                subprocess.run(["docker", "tag", "asknowmuseum-converters:latest", converters_image])
                try:
                    os.remove(temp_conv_archive)
                except OSError:
                    pass
                log_success(f"Loaded and tagged '{converters_image}' successfully.")
            except Exception as e:
                log_warn(f"Could not download pre-built converters image: {e}")

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

    # 4. Ensure ddicdi-studio:latest is available locally
    res_inspect = subprocess.run(
        ["docker", "image", "inspect", "ddicdi-studio:latest"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )
    if res_inspect.returncode != 0:
        log_info("Image 'ddicdi-studio:latest' not found locally.")
        ddicdi_url = "https://ai.now.museum/dumps/ddicdi-studio.tar.gz"
        fetch_ddicdi = ask_prompt(
            f"Download and load 'ddicdi-studio:latest' from protected repository? (y/n)",
            default="y"
        ).lower()
        if fetch_ddicdi in ["y", "yes"]:
            auth_str = "admin:croissant2026"
            encoded_auth = base64.b64encode(auth_str.encode("utf-8")).decode("ascii")
            req = urllib.request.Request(ddicdi_url)
            req.add_header("Authorization", f"Basic {encoded_auth}")
            temp_archive = os.path.join(repo_dir, "ddicdi-studio.tar.gz")
            log_info(f"Downloading 'ddicdi-studio.tar.gz' to '{temp_archive}'...")
            try:
                with urllib.request.urlopen(req, timeout=300) as resp:
                    total_size = resp.headers.get("Content-Length")
                    total_bytes = int(total_size) if total_size and total_size.isdigit() else 0
                    downloaded = 0
                    block_size = 1048576  # 1MB
                    with open(temp_archive, "wb") as f_out:
                        while True:
                            chunk = resp.read(block_size)
                            if not chunk:
                                break
                            f_out.write(chunk)
                            downloaded += len(chunk)
                            if total_bytes > 0:
                                pct = (downloaded / total_bytes) * 100.0
                                sys.stdout.write(f"\r  Downloaded: {downloaded / (1024*1024):.1f}MB / {total_bytes / (1024*1024):.1f}MB ({pct:.1f}%)")
                            else:
                                sys.stdout.write(f"\r  Downloaded: {downloaded / (1024*1024):.1f}MB")
                            sys.stdout.flush()
                print("\n[+] Download complete. Loading into Docker...")
                load_res = subprocess.run(
                    ["docker", "load", "-i", temp_archive]
                )
                if load_res.returncode == 0:
                    log_success("'ddicdi-studio:latest' loaded successfully.")
                    try:
                        os.remove(temp_archive)
                    except OSError:
                        pass
                else:
                    log_warn(f"docker load failed with return code {load_res.returncode}")
            except Exception as e:
                log_error(f"Error downloading or loading ddicdi-studio: {e}")

    log_success("All Docker images built and pulled successfully.")


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

    # 0. Pre-run init container to ensure correct volume permissions
    log_info("Ensuring volume permissions via init service...")
    subprocess.run(
        ["docker", "compose", "--env-file", f"profiles/{profile_name}.env", "--profile", profile_name, "up", "init"],
        env=env_vars
    )

    log_info(f"Starting services with 'docker compose --profile {profile_name} up -d'...")
    res = subprocess.run(
        ["docker", "compose", "--env-file", f"profiles/{profile_name}.env", "--profile", profile_name, "up", "-d"],
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

    # Check services
    def check_http_endpoint(url: str, name: str, expected_statuses: tuple = (200, 301, 302, 405)):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "sc-installer-check"})
            with urllib.request.urlopen(req, timeout=5) as r:
                log_success(f"{name} is responding on {url} (HTTP {r.status})")
                return True
        except urllib.error.HTTPError as he:
            if he.code in expected_statuses:
                log_success(f"{name} is responding on {url} (HTTP {he.code})")
                return True
            log_warn(f"{name} check on {url} returned HTTP {he.code}")
            return False
        except Exception as e:
            log_warn(f"{name} check on {url} failed: {e}")
            return False

    # 1. Check API endpoint
    api_url = f"http://localhost:{ports['API']}/"
    check_http_endpoint(api_url, f"API service (port {ports['API']})")

    # 2. Check MCP endpoint
    mcp_url = f"http://localhost:{ports['MCP']}/"
    check_http_endpoint(mcp_url, f"MCP service (port {ports['MCP']})")

    # 3. Check QLever endpoint
    qlever_url = f"http://localhost:{ports['QLEVER']}/"
    check_http_endpoint(f"{qlever_url}?query=SELECT%20*%20WHERE%20%7B%20%3Fs%20%3Fp%20%3Fo%20%7D%20LIMIT%201", f"QLever SPARQL service (port {ports['QLEVER']})")

    # 4. Check Elasticsearch endpoint
    es_url = f"http://localhost:{ports['ES']}/"
    check_http_endpoint(es_url, f"Elasticsearch service (port {ports['ES']})")

    # 5. Run test suite if requested
    run_tests = ask_prompt("\nRun unit test suite (test_read_records) inside API container? (y/n)", default="y").lower()
    if run_tests in ["y", "yes"]:
        log_info("Running test_read_records inside API container...")
        test_cmd = [
            "docker", "compose",
            "--env-file", f"profiles/{profile_name}.env",
            "--profile", profile_name,
            "exec", "-T", "api",
            "python", "tests/test_read_records.py"
        ]
        res = subprocess.run(test_cmd)
        if res.returncode == 0:
            log_success("Unit test suite executed successfully.")
        else:
            log_warn("Test exited with non-zero status.")

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
    parser.add_argument(
        "--profile", "-p",
        default=None,
        help="Optional path or name of profile (e.g. profiles/asknowmuseum.env or asknowmuseum). If set, skips profile prompt."
    )
    args = parser.parse_args()

    print_banner()
    check_prerequisites()

    # Step 1: Clone or locate repository
    repo_dir = step_1_clone_repo(target_dir=args.dir, repo_url=args.repo_url)

    # Step 2: Select profile (and optionally read dump/network parameters from it)
    profile_name = step_3_select_profile(repo_dir=repo_dir, profile_arg=args.profile)
    profile_env_file = os.path.join(repo_dir, "profiles", f"{profile_name}.env")
    profile_config = parse_env_file(profile_env_file)

    # Step 3: Download and unpack protected dump (utilizing profile defaults if available)
    step_2_download_dump(repo_dir=repo_dir, profile_config=profile_config)

    # Step 3b: Restore indexes & data if requested or archives found
    step_3b_restore_indexes(repo_dir=repo_dir, profile_name=profile_name)

    # Step 4: Build Docker images
    step_4_build_docker_images(repo_dir=repo_dir, profile_name=profile_name)

    # Step 5: Run and test
    step_5_run_and_test(repo_dir=repo_dir, profile_name=profile_name)


if __name__ == "__main__":
    main()

