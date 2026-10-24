#!/usr/bin/env python3
"""
SSHOC Marketplace API to Elasticsearch Importer
Fetches tools and services from https://marketplace-api.sshopencloud.eu/api/tools-services
and indexes them into the Elasticsearch 'sshoc' collection/index.
"""

import os
import sys
import json
import time
import argparse
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed

SSHOC_API_BASE = "https://marketplace-api.sshopencloud.eu/api/tools-services"
DEFAULT_ES_URL = os.environ.get("ELASTICSEARCH_URL", "http://localhost:9200")
DEFAULT_INDEX = "sshoc"


def get_default_es_urls():
    """
    Returns ordered candidate Elasticsearch URLs.
    Checks environment variable ELASTICSEARCH_URL, then localhost:9200 and localhost:9201.
    """
    candidates = []
    env_url = os.environ.get("ELASTICSEARCH_URL", "").rstrip("/")
    if env_url:
        candidates.append(env_url)
    for u in ["http://localhost:9200", "http://localhost:9201"]:
        if u not in candidates:
            candidates.append(u)
    return candidates


def resolve_active_es(requested_url=None):
    """
    Finds a responsive Elasticsearch endpoint from the requested URL or default candidates.
    """
    if requested_url:
        targets = [requested_url.rstrip("/")]
    else:
        targets = get_default_es_urls()

    for url in targets:
        try:
            r = requests.get(url, timeout=3)
            if r.status_code == 200:
                return url
        except Exception:
            continue
    return requested_url.rstrip("/") if requested_url else "http://localhost:9200"


def ensure_index(es_url, index_name="sshoc"):
    """
    Checks if index exists, and creates it with appropriate mapping if not.
    """
    check_url = f"{es_url}/{index_name}"
    try:
        r = requests.head(check_url, timeout=5)
        if r.status_code == 200:
            return True
        if r.status_code == 404:
            mapping = {
                "settings": {
                    "index.mapping.total_fields.limit": 20000
                },
                "mappings": {
                    "date_detection": False,
                    "properties": {
                        "id": {"type": "keyword"},
                        "persistentId": {"type": "keyword"},
                        "name": {"type": "text", "fields": {"keyword": {"type": "keyword", "ignore_above": 512}}},
                        "label": {"type": "text", "fields": {"keyword": {"type": "keyword", "ignore_above": 512}}},
                        "description": {"type": "text", "analyzer": "english"},
                        "url": {"type": "keyword"},
                        "accessibleAt": {"type": "keyword"},
                        "category": {"type": "keyword"},
                        "status": {"type": "keyword"},
                        "lastInfoUpdate": {"type": "date", "ignore_malformed": True},
                        "keywords": {"type": "keyword"},
                        "activities": {"type": "keyword"},
                        "intendedAudience": {"type": "keyword"},
                        "languages": {"type": "keyword"},
                        "termsOfUse": {"type": "text"},
                        "modeOfUse": {"type": "keyword"},
                        "source": {"type": "object", "enabled": True},
                        "contributors": {"type": "nested"},
                        "properties": {"type": "nested"},
                        "relatedItems": {"type": "nested"},
                        "media": {"type": "nested"},
                        "_source_url": {"type": "keyword"},
                        "_full_text": {"type": "text", "analyzer": "english"},
                        "@type": {"type": "keyword"},
                        "@context": {"type": "object", "enabled": False}
                    }
                }
            }
            create_r = requests.put(check_url, json=mapping, headers={"Content-Type": "application/json"}, timeout=10)
            if create_r.status_code in (200, 201):
                print(f"Created Elasticsearch index '{index_name}' on {es_url}")
                return True
            else:
                print(f"Warning: Index creation returned {create_r.status_code}: {create_r.text}")
    except Exception as e:
        print(f"Warning: Could not verify/create index '{index_name}' on {es_url}: {e}")
    return False


def collect_text_values(obj):
    """
    Recursively collects string values for full-text search indexing.
    """
    text_values = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if not k.startswith("@"):
                text_values.extend(collect_text_values(v))
    elif isinstance(obj, list):
        for item in obj:
            text_values.extend(collect_text_values(item))
    elif isinstance(obj, str) and obj.strip():
        text_values.append(obj.strip())
    return text_values


def transform_tool_to_doc(tool):
    """
    Transforms a SSHOC Marketplace tool-or-service object into an Elasticsearch document
    compatible with Croissant / Schema.org SoftwareApplication / CreativeWork search.
    """
    pid = tool.get("persistentId") or str(tool.get("id"))
    label = tool.get("label", "")
    description = tool.get("description", "")
    accessible_at = tool.get("accessibleAt", [])
    primary_url = accessible_at[0] if accessible_at else f"https://marketplace.sshopencloud.eu/tool-or-service/{pid}"

    # Extract classified properties
    keywords = []
    activities = []
    audiences = []
    languages = []
    modes_of_use = []
    terms_of_use = []

    for prop in tool.get("properties", []):
        ptype = prop.get("type", {}).get("code", "")
        concept = prop.get("concept", {})
        label_val = concept.get("label") or prop.get("value")
        if not label_val:
            continue

        if ptype == "keyword":
            keywords.append(label_val)
        elif ptype == "activity":
            activities.append(label_val)
        elif ptype == "intended-audience":
            audiences.append(label_val)
        elif ptype == "language":
            languages.append(label_val)
        elif ptype == "mode-of-use":
            modes_of_use.append(label_val)
        elif ptype == "terms-of-use":
            terms_of_use.append(label_val)

    # Format contributors for schema.org / Croissant compatibility
    creators = []
    for c in tool.get("contributors", []):
        actor = c.get("actor", {})
        role = c.get("role", {}).get("label", "Contributor")
        name = actor.get("name")
        if name:
            creators.append({
                "@type": "Person",
                "name": name,
                "roleName": role,
                "id": actor.get("id")
            })

    full_texts = [label, description]
    full_texts.extend(keywords)
    full_texts.extend(activities)
    full_texts.extend(audiences)
    full_texts.extend(languages)
    full_texts.extend(collect_text_values(tool.get("contributors", [])))
    full_text_str = "\n".join([t for t in full_texts if t])

    doc = {
        "@context": "https://schema.org/",
        "@type": "SoftwareApplication",
        "id": str(tool.get("id")),
        "persistentId": pid,
        "name": label,
        "label": label,
        "description": description,
        "url": primary_url,
        "accessibleAt": accessible_at,
        "category": tool.get("category", "tool-or-service"),
        "status": tool.get("status"),
        "lastInfoUpdate": tool.get("lastInfoUpdate"),
        "keywords": list(dict.fromkeys(keywords)),
        "activities": list(dict.fromkeys(activities)),
        "intendedAudience": list(dict.fromkeys(audiences)),
        "languages": list(dict.fromkeys(languages)),
        "modeOfUse": list(dict.fromkeys(modes_of_use)),
        "termsOfUse": " ".join(terms_of_use) if terms_of_use else None,
        "creator": creators,
        "source": tool.get("source"),
        "sourceItemId": tool.get("sourceItemId"),
        "properties": tool.get("properties", []),
        "externalIds": tool.get("externalIds", []),
        "relatedItems": tool.get("relatedItems", []),
        "media": tool.get("media", []),
        "_source_url": primary_url,
        "_full_text": full_text_str,
        "raw": tool
    }
    return pid, doc


def fetch_page(page=1, perpage=50, max_retries=5):
    """
    Fetches a page of tools/services from the SSHOC API.
    """
    url = f"{SSHOC_API_BASE}?perpage={perpage}&page={page}"
    for attempt in range(max_retries):
        try:
            r = requests.get(url, timeout=30, headers={"Accept": "application/json"})
            r.raise_for_status()
            return r.json()
        except Exception as e:
            if attempt == max_retries - 1:
                print(f"Error fetching page {page}: {e}", file=sys.stderr)
                return None
            time.sleep(2 ** attempt)
    return None


def bulk_index_docs(es_url, index_name, docs_batch):
    """
    Indexes a batch of documents into Elasticsearch using the _bulk API.
    """
    if not docs_batch:
        return 0, 0

    lines = []
    for doc_id, doc in docs_batch:
        lines.append(json.dumps({"index": {"_index": index_name, "_id": str(doc_id)}}))
        lines.append(json.dumps(doc))
    payload = "\n".join(lines) + "\n"

    bulk_url = f"{es_url}/_bulk"
    headers = {"Content-Type": "application/x-ndjson"}

    for attempt in range(3):
        try:
            r = requests.post(bulk_url, data=payload, headers=headers, timeout=60)
            if r.status_code == 200:
                resp = r.json()
                has_errors = resp.get("errors", False)
                items = resp.get("items", [])
                indexed_count = sum(1 for item in items if item.get("index", {}).get("status") in (200, 201))
                error_count = len(items) - indexed_count
                return indexed_count, error_count
            else:
                print(f"Bulk indexing attempt {attempt+1} failed ({r.status_code}): {r.text[:200]}", file=sys.stderr)
        except Exception as e:
            if attempt == 2:
                print(f"Bulk indexing failed: {e}", file=sys.stderr)
            time.sleep(2 ** attempt)
    return 0, len(docs_batch)


def update_collection_entry(es_url, index_name, total_indexed):
    """
    Ensures that the 'collections' index in Elasticsearch has an entry for this collection,
    matching the format used across semantic-croissant.
    """
    try:
        col_url = f"{es_url}/collections"
        check_col = requests.head(col_url, timeout=3)
        if check_col.status_code == 200:
            doc_id = index_name
            # Check if sshoc entry exists
            get_r = requests.get(f"{col_url}/_doc/{doc_id}", timeout=3)
            existing = get_r.json().get("_source", {}) if get_r.status_code == 200 else {}

            payload = {
                "id": doc_id,
                "name": existing.get("name") or "SSHOC Marketplace",
                "description": existing.get("description") or "SSH Open Cloud Marketplace tools and services registry",
                "expert": "sshoc",
                "expert_index": index_name,
                "is_private": False,
                "created_at": existing.get("created_at") or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "total_items": total_indexed
            }
            requests.put(f"{col_url}/_doc/{doc_id}", json=payload, headers={"Content-Type": "application/json"}, timeout=5)
            print(f"Registered/updated collection entry '{doc_id}' in {es_url}/collections")
    except Exception as e:
        print(f"Note: Could not register collection entry in /collections: {e}")


def main():
    parser = argparse.ArgumentParser(description="Ingest SSHOC Marketplace tools and services into Elasticsearch collection 'sshoc'.")
    parser.add_argument("--es", type=str, default=None, help=f"Elasticsearch base URL (default: {DEFAULT_ES_URL} or localhost:9201/9200)")
    parser.add_argument("--index", type=str, default=DEFAULT_INDEX, help=f"Elasticsearch index/collection name (default: {DEFAULT_INDEX})")
    parser.add_argument("--perpage", type=int, default=50, help="Number of items per API page (default: 50)")
    parser.add_argument("--max-pages", type=int, default=None, help="Maximum number of pages to ingest (optional, default: all)")
    parser.add_argument("--workers", type=int, default=4, help="Number of worker threads for parallel fetching (default: 4)")
    args = parser.parse_args()

    es_url = resolve_active_es(args.es)
    print(f"Using Elasticsearch endpoint: {es_url}")
    print(f"Target index/collection: {args.index}")

    # Ensure index exists
    ensure_index(es_url, args.index)

    # Initial probe to determine total count and pages
    print("Connecting to SSHOC Marketplace API...")
    probe_data = fetch_page(page=1, perpage=args.perpage)
    if not probe_data:
        print("Failed to reach SSHOC API. Exiting.", file=sys.stderr)
        sys.exit(1)

    total_hits = probe_data.get("hits", 0)
    total_pages = probe_data.get("pages", 1)
    print(f"Found {total_hits} total tools/services across {total_pages} pages (perpage={args.perpage}).")

    if args.max_pages and args.max_pages < total_pages:
        pages_to_fetch = args.max_pages
        print(f"Limiting fetch to {pages_to_fetch} page(s).")
    else:
        pages_to_fetch = total_pages

    # Process first page
    total_indexed = 0
    total_errors = 0

    first_tools = probe_data.get("tools", [])
    if first_tools:
        docs = [transform_tool_to_doc(t) for t in first_tools]
        idx_count, err_count = bulk_index_docs(es_url, args.index, docs)
        total_indexed += idx_count
        total_errors += err_count
        print(f"Page 1/{pages_to_fetch}: Indexed {idx_count} items ({total_indexed}/{total_hits})")

    # Fetch remaining pages concurrently
    if pages_to_fetch > 1:
        pages = list(range(2, pages_to_fetch + 1))
        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            future_to_page = {
                executor.submit(fetch_page, p, args.perpage): p for p in pages
            }
            for future in as_completed(future_to_page):
                page_num = future_to_page[future]
                try:
                    pdata = future.result()
                    if pdata and "tools" in pdata:
                        tools = pdata["tools"]
                        docs = [transform_tool_to_doc(t) for t in tools]
                        idx_count, err_count = bulk_index_docs(es_url, args.index, docs)
                        total_indexed += idx_count
                        total_errors += err_count
                        print(f"Page {page_num}/{pages_to_fetch}: Indexed {idx_count} items (total indexed: {total_indexed})")
                    else:
                        print(f"Page {page_num}/{pages_to_fetch}: No tools found or fetch failed.")
                        total_errors += 1
                except Exception as exc:
                    print(f"Page {page_num} generated an exception: {exc}", file=sys.stderr)
                    total_errors += 1

    print(f"\nIngestion completed!")
    print(f"Total successfully indexed: {total_indexed}")
    print(f"Total errors: {total_errors}")

    # Update collections metadata
    update_collection_entry(es_url, args.index, total_indexed)


if __name__ == "__main__":
    main()
