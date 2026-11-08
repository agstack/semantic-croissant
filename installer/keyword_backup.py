#!/usr/bin/env python3
"""
Semantic Croissant - Keyword-Filtered Backup Tool
Exports keyword-specific subsets from Elasticsearch, MinIO, and compiles QLever SPARQL indexes.

Processes a list of keywords sequentially (e.g. 'honduras', then 'coffee'):
1. Queries Elasticsearch (croissant, cdif, collections, groups, dedicated index)
2. Scans MinIO collections and vault objects
3. Converts CDIF / Croissant datasets to RDF N-Triples and builds a binary QLever index via Docker
4. Produces lightweight, portable archives ready for restore:
   - dumps/<keyword>/elasticsearch-<keyword>.tar.gz
   - dumps/<keyword>/minio-<keyword>.tar.gz
   - dumps/<keyword>/qlever-<keyword>-indexes.tar.gz

Usage:
    python3 installer/keyword_backup.py [keywords_comma_separated] [dumps_dir] [profile_env_file]

Example:
    python3 installer/keyword_backup.py "honduras,coffee" ./dumps profiles/asknowmuseum.env
"""

import sys
import os
import re
import json
import time
import shutil
import tarfile
import subprocess
import urllib.request
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

# ANSI formatting
GREEN = "\033[92m"
BLUE = "\033[94m"
YELLOW = "\033[93m"
RED = "\033[91m"
BOLD = "\033[1m"
RESET = "\033[0m"

def log_info(msg): print(f"{BLUE}[INFO]{RESET} {msg}")
def log_success(msg): print(f"{GREEN}[✓]{RESET} {msg}")
def log_warn(msg): print(f"{YELLOW}[WARN]{RESET} {msg}")
def log_error(msg): print(f"{RED}[ERROR]{RESET} {msg}")


def parse_env_file(filepath: str) -> dict:
    config = {}
    if not filepath or not os.path.isfile(filepath):
        return config
    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            line_s = line.strip()
            if not line_s or line_s.startswith("#") or "=" not in line_s:
                continue
            k, v = line_s.split("=", 1)
            config[k.strip()] = v.strip().strip('"').strip("'")
    return config


def query_es_scroll(es_host: str, index: str, keyword: str, max_hits: int = 10000, fields: list = None) -> list:
    """Scrolls and fetches all documents matching keyword in the specified Elasticsearch index."""
    url = f"{es_host.rstrip('/')}/{index}/_search?scroll=2m"
    fields_to_search = fields if fields else ["*"]
    query = {
        "query": {
            "multi_match": {
                "query": keyword,
                "fields": fields_to_search
            }
        },
        "size": 1000
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(query).encode("utf-8"),
        headers={"Content-Type": "application/json"}
    )
    hits = []
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        scroll_id = data.get("_scroll_id")
        current_hits = data.get("hits", {}).get("hits", [])
        hits.extend(current_hits)

        while current_hits and len(hits) < max_hits:
            scroll_req = urllib.request.Request(
                f"{es_host.rstrip('/')}/_search/scroll",
                data=json.dumps({"scroll": "2m", "scroll_id": scroll_id}).encode("utf-8"),
                headers={"Content-Type": "application/json"}
            )
            with urllib.request.urlopen(scroll_req, timeout=45) as scroll_resp:
                scroll_data = json.loads(scroll_resp.read().decode("utf-8"))
            scroll_id = scroll_data.get("_scroll_id")
            current_hits = scroll_data.get("hits", {}).get("hits", [])
            hits.extend(current_hits)

        # Clean scroll context
        if scroll_id:
            try:
                del_req = urllib.request.Request(
                    f"{es_host.rstrip('/')}/_search/scroll",
                    data=json.dumps({"scroll_id": [scroll_id]}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="DELETE"
                )
                urllib.request.urlopen(del_req, timeout=5)
            except Exception:
                pass
    except Exception as e:
        log_warn(f"ES search failed on '{index}' for keyword '{keyword}': {e}")
    return hits


def convert_cdif_hit_to_nt(hit: dict) -> str:
    """Converts a CDIF document into RDF N-Triples using rdflib."""
    from rdflib import Graph
    src = hit.get("_source", {})
    jsonld_obj = src.get("cdif_jsonld") or src
    if not jsonld_obj:
        return ""
    g = Graph()
    try:
        g.parse(data=json.dumps(jsonld_obj), format="json-ld")
        return g.serialize(format="nt")
    except Exception:
        return ""


def check_minio_file_for_keyword(file_path: str, kw_bytes: bytes) -> bool:
    """Fast binary check whether an xl.meta or json file contains the keyword."""
    meta_path = os.path.join(file_path, "xl.meta") if os.path.isdir(file_path) else file_path
    if not os.path.isfile(meta_path):
        return False
    try:
        with open(meta_path, "rb") as f:
            content = f.read()
            return kw_bytes in content.lower()
    except Exception:
        return False


def build_keyword_qlever_index(qlever_dir: str, nt_file: str, index_name: str = "default") -> bool:
    """Builds a portable binary QLever index inside qlever_dir using Docker."""
    os.makedirs(qlever_dir, exist_ok=True)
    try:
        os.chmod(qlever_dir, 0o777)
    except Exception:
        pass

    qleverfile_path = os.path.join(qlever_dir, "Qleverfile")
    qlever_config = f"""[data]
NAME = {index_name}
GET_DATA_CMD = cat {os.path.basename(nt_file)}
INDEX_INPUT_FILES = {os.path.basename(nt_file)}

[index]
INPUT_FILES = {os.path.basename(nt_file)}
CAT_INPUT_FILES = cat {os.path.basename(nt_file)}
SETTINGS_JSON = {{ "ascii-prefixes-only": false }}

[server]
PORT = 7001
ACCESS_TOKEN = croissant_7643543846_Zs6nw7yi3Z9m
HOST_NAME = 0.0.0.0
TIMEOUT = 30s
"""
    with open(qleverfile_path, "w", encoding="utf-8") as qf:
        qf.write(qlever_config)

    abs_qlever_dir = os.path.abspath(qlever_dir)
    cmd = [
        "docker", "run", "--rm",
        "-u", "65534:0",
        "-v", f"{abs_qlever_dir}:/data",
        "-w", "/data",
        "ghcr.io/zazukoians/qlever-server:latest",
        "qlever-index", "-i", index_name, "-f", os.path.basename(nt_file), "-F", "nt"
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    return res.returncode == 0


def create_tar_gz(source_dir: str, archive_path: str, exclude_patterns: list = None):
    """Creates a compressed .tar.gz archive of source_dir."""
    exclude_patterns = exclude_patterns or []
    if shutil.which("pigz"):
        exclude_args = []
        for pat in exclude_patterns:
            exclude_args.extend(["--exclude", pat])
        cmd = ["tar", "-C", source_dir] + exclude_args + [
            "--use-compress-program=pigz",
            "-cf", archive_path, "."
        ]
        res = subprocess.run(cmd)
        if res.returncode in (0, 1):
            return
    # Fallback standard tarfile
    with tarfile.open(archive_path, "w:gz") as tar:
        for item in os.listdir(source_dir):
            if any(item.endswith(pat.lstrip("*")) for pat in exclude_patterns):
                continue
            item_path = os.path.join(source_dir, item)
            tar.add(item_path, arcname=item)


def backup_for_keyword(kw: str, dumps_dir: str, root_dir: str, es_host: str):
    """Executes backup for a single keyword across Elasticsearch, MinIO, and QLever."""
    kw_slug = re.sub(r"[^a-zA-Z0-9_\-]", "", kw.lower().replace(" ", "_"))
    print(f"\n{BOLD}{'='*68}")
    print(f"   Executing Subset Backup for Keyword: '{kw}' ({kw_slug})")
    print(f"{'='*68}{RESET}")

    kw_dumps_dir = os.path.join(dumps_dir, kw_slug)
    os.makedirs(kw_dumps_dir, exist_ok=True)
    temp_dir = os.path.join(kw_dumps_dir, "temp_build")
    shutil.rmtree(temp_dir, ignore_errors=True)
    os.makedirs(temp_dir, exist_ok=True)

    # --------------------------------------------------------------------------
    # 1. Elasticsearch Keyword Subset Backup
    # --------------------------------------------------------------------------
    log_info(f"Step 1: Extracting Elasticsearch records matching '{kw}'...")
    es_out_file = os.path.join(temp_dir, "es_hits.jsonl")
    total_es_docs = 0
    cdif_hits = []

    candidate_indices = ["croissant", "cdif", "collections", "groups"]
    # Check if there is an index specifically named after the keyword (e.g. 'honduras')
    try:
        chk_req = urllib.request.Request(f"{es_host.rstrip('/')}/{kw_slug}")
        with urllib.request.urlopen(chk_req, timeout=5) as r:
            if r.status == 200:
                candidate_indices.append(kw_slug)
    except Exception:
        pass

    with open(es_out_file, "w", encoding="utf-8") as out_fp:
        for idx in candidate_indices:
            # Query relevant fields
            fields = ["title^3", "name^3", "description^2", "keywords", "cdif_jsonld.DDICDIModels.name^2", "cdif_jsonld.schema:name^2", "_full_text"]
            hits = query_es_scroll(es_host, idx, kw, fields=fields)
            if hits:
                log_info(f"  • Index '{idx}': {len(hits)} matching documents")
                for h in hits:
                    out_fp.write(json.dumps(h) + "\n")
                total_es_docs += len(hits)
                if idx == "cdif":
                    cdif_hits = hits

    es_archive = os.path.join(kw_dumps_dir, f"elasticsearch-{kw_slug}.tar.gz")
    es_pack_dir = os.path.join(temp_dir, "es_pack")
    os.makedirs(es_pack_dir, exist_ok=True)
    shutil.copyfile(es_out_file, os.path.join(es_pack_dir, "es_hits.jsonl"))
    create_tar_gz(es_pack_dir, es_archive)
    log_success(f"Elasticsearch subset archived: {es_archive} ({total_es_docs} docs, {os.path.getsize(es_archive) // 1024} KB)")

    # --------------------------------------------------------------------------
    # 2. MinIO Keyword Subset Backup (Collections + Vault)
    # --------------------------------------------------------------------------
    log_info(f"Step 2: Scanning MinIO storage for objects matching '{kw}'...")
    minio_dirs = [
        os.path.join(root_dir, "volumes", "asknowmuseum", "minio"),
        os.path.join(root_dir, "qlever-tests", "volumes", "asknowmuseum", "minio"),
        os.path.join(root_dir, "volumes", "codata", "minio"),
        os.path.join(root_dir, "volumes", "ciat", "minio"),
    ]
    minio_src = next((d for d in minio_dirs if os.path.isdir(d)), None)

    minio_pack_dir = os.path.join(temp_dir, "minio_pack")
    os.makedirs(minio_pack_dir, exist_ok=True)
    matched_minio_count = 0

    if minio_src and os.path.isdir(minio_src):
        kw_bytes = kw.lower().encode("utf-8")

        # 2a. Collections
        collections_dir = os.path.join(minio_src, "collections")
        if os.path.isdir(collections_dir):
            for col in os.listdir(collections_dir):
                c_path = os.path.join(collections_dir, col)
                if check_minio_file_for_keyword(c_path, kw_bytes):
                    target = os.path.join(minio_pack_dir, "collections", col)
                    os.makedirs(os.path.dirname(target), exist_ok=True)
                    if os.path.isdir(c_path):
                        shutil.copytree(c_path, target, dirs_exist_ok=True)
                    else:
                        shutil.copyfile(c_path, target)
                    matched_minio_count += 1

        # 2b. Vault objects
        vault_dir = os.path.join(minio_src, "vault")
        if os.path.isdir(vault_dir):
            vault_items = [os.path.join(vault_dir, f) for f in os.listdir(vault_dir)]
            with ThreadPoolExecutor(max_workers=32) as pool:
                res = list(pool.map(lambda p: (p, check_minio_file_for_keyword(p, kw_bytes)), vault_items))
            for p, matched in res:
                if matched:
                    rel_name = os.path.basename(p)
                    target = os.path.join(minio_pack_dir, "vault", rel_name)
                    os.makedirs(os.path.dirname(target), exist_ok=True)
                    if os.path.isdir(p):
                        shutil.copytree(p, target, dirs_exist_ok=True)
                    else:
                        shutil.copyfile(p, target)
                    matched_minio_count += 1

    minio_archive = os.path.join(kw_dumps_dir, f"minio-{kw_slug}.tar.gz")
    create_tar_gz(minio_pack_dir, minio_archive)
    log_success(f"MinIO subset archived: {minio_archive} ({matched_minio_count} objects, {os.path.getsize(minio_archive) // 1024} KB)")

    # --------------------------------------------------------------------------
    # 3. QLever Keyword Subset Index Build & Backup
    # --------------------------------------------------------------------------
    log_info(f"Step 3: Building QLever SPARQL knowledge graph for '{kw}'...")
    qlever_build_dir = os.path.join(temp_dir, "qlever_build")
    os.makedirs(qlever_build_dir, exist_ok=True)
    nt_file = os.path.join(qlever_build_dir, "default.nt")

    if cdif_hits:
        log_info(f"  • Converting {len(cdif_hits)} CDIF documents to RDF N-Triples...")
        total_triples = 0
        with open(nt_file, "w", encoding="utf-8") as ntf:
            for h in cdif_hits:
                nt = convert_cdif_hit_to_nt(h)
                if nt:
                    ntf.write(nt)
                    total_triples += len(nt.splitlines())
        log_info(f"  • Generated {total_triples} RDF N-Triples.")
    else:
        with open(nt_file, "w", encoding="utf-8") as ntf:
            ntf.write(f'<https://schema.org/Topic/{kw_slug}> <https://schema.org/name> "{kw}" .\n')

    # Build QLever Index
    log_info(f"  • Compiling binary QLever index...")
    build_ok = build_keyword_qlever_index(qlever_build_dir, nt_file, index_name="default")
    qlever_archive = os.path.join(kw_dumps_dir, f"qlever-{kw_slug}-indexes.tar.gz")

    if build_ok:
        create_tar_gz(qlever_build_dir, qlever_archive, exclude_patterns=["default.nt"])
        log_success(f"QLever index compiled and archived: {qlever_archive} ({os.path.getsize(qlever_archive) // 1024} KB)")
    else:
        log_warn("Failed to compile binary QLever index via Docker. Creating archive with raw RDF triples...")
        create_tar_gz(qlever_build_dir, qlever_archive)

    # Clean up temp build folder
    shutil.rmtree(temp_dir, ignore_errors=True)
    log_success(f"Completed backup for keyword '{kw}' -> {kw_dumps_dir}")


def main():
    root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    
    # 1. Determine keywords
    keywords_raw = None
    if len(sys.argv) > 1 and sys.argv[1].strip():
        keywords_raw = sys.argv[1].strip()
    
    dumps_dir = sys.argv[2] if len(sys.argv) > 2 else os.path.join(root_dir, "dumps")
    profile_env = sys.argv[3] if len(sys.argv) > 3 else os.path.join(root_dir, "profiles", "asknowmuseum.env")

    # Read profile config if present
    cfg = parse_env_file(profile_env)
    if not keywords_raw:
        keywords_raw = cfg.get("DUMPS_KEYWORDS", os.environ.get("DUMPS_KEYWORDS", "honduras,coffee"))

    es_host = cfg.get("ES_GLOBAL_HOST", cfg.get("ES_HOST", os.environ.get("ES_HOST", "http://10.147.18.24:9205")))

    # Split keywords: "honduras,coffee" -> ["honduras", "coffee"]
    keywords = [k.strip() for k in keywords_raw.split(",") if k.strip()]
    if not keywords:
        log_warn("No keywords specified. Defaulting to 'honduras'.")
        keywords = ["honduras"]

    print(f"\n{BOLD}{'='*68}")
    print(f"   Semantic Croissant - Keyword Backup Pipeline")
    print(f"   Keywords: {keywords}")
    print(f"   Destination: {dumps_dir}")
    print(f"   ES Host: {es_host}")
    print(f"{'='*68}{RESET}\n")

    for kw in keywords:
        t_kw_start = time.time()
        backup_for_keyword(kw, dumps_dir=dumps_dir, root_dir=root_dir, es_host=es_host)
        log_info(f"Keyword '{kw}' backup completed in {time.time() - t_kw_start:.1f}s.")

    print(f"\n{GREEN}{BOLD}{'='*68}")
    print("   All keyword subset backups generated successfully!")
    print(f"{'='*68}{RESET}")
    for kw in keywords:
        kw_slug = re.sub(r"[^a-zA-Z0-9_\-]", "", kw.lower().replace(" ", "_"))
        target_sub = os.path.join(dumps_dir, kw_slug)
        if os.path.isdir(target_sub):
            print(f"\nSubset archives in {target_sub}:")
            for f in sorted(os.listdir(target_sub)):
                f_path = os.path.join(target_sub, f)
                print(f"  • {f} ({os.path.getsize(f_path) // 1024} KB)")


if __name__ == "__main__":
    main()
