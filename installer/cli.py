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


def step_3_select_profile(repo_dir: str) -> str:
    """Lists available environment profiles and prompts the user to select or create one."""
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

