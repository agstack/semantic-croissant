#!/usr/bin/env python3
"""
Recreates or initializes the Elasticsearch 'cdif' index with the required schema mapping,
specifically setting "cdif_jsonld": {"type": "object", "enabled": false} so that arbitrary
nested JSON-LD structures (@context, DDICDIModels, etc.) do not trigger mapping merge errors.

Usage:
    # Run against default or environment ELASTICSEARCH_URL
    python3 api/recreate_cdif_index.py

    # Run against codata-elasticsearch on port 9222
    python3 api/recreate_cdif_index.py --es-url http://localhost:9222

    # Run without recreating if index already exists (ensure-only mode)
    python3 api/recreate_cdif_index.py --es-url http://localhost:9222 --ensure
"""

import sys
import json
import argparse
import urllib.request
import urllib.error

CDIF_INDEX_SCHEMA = {
    "settings": {
        "index": {
            "number_of_shards": 1,
            "number_of_replicas": 1
        }
    },
    "mappings": {
        "properties": {
            "collection_id": {"type": "keyword"},
            "dataset_id": {"type": "keyword"},
            "dataset_url": {"type": "keyword"},
            "file_name": {
                "type": "text",
                "fields": {
                    "keyword": {"type": "keyword"}
                }
            },
            "file_url": {"type": "keyword"},
            "file_format": {"type": "keyword"},
            "cdif_export_url": {"type": "keyword"},
            "title": {"type": "text"},
            "description": {"type": "text"},
            "models_count": {"type": "integer"},
            "created_at": {"type": "date"},
            "status": {"type": "keyword"},
            "cdif_jsonld": {
                "type": "object",
                "enabled": False
            }
        }
    }
}


def recreate_cdif_index(es_url: str, force_recreate: bool = True):
    es_url = es_url.rstrip("/")
    cdif_url = f"{es_url}/cdif"

    # Check if index exists
    index_exists = False
    try:
        req = urllib.request.Request(cdif_url, method="HEAD")
        with urllib.request.urlopen(req) as resp:
            if resp.status == 200:
                index_exists = True
    except urllib.error.HTTPError as e:
        if e.code == 404:
            index_exists = False
        else:
            print(f"Warning checking index existence: {e}", file=sys.stderr)
    except Exception as e:
        print(f"Error connecting to Elasticsearch at {es_url}: {e}", file=sys.stderr)
        return False

    if index_exists:
        if not force_recreate:
            print(f"Index 'cdif' already exists at {es_url}. Use without --ensure to recreate.")
            return True

        print(f"Index 'cdif' exists. Deleting from {es_url}...")
        try:
            req_del = urllib.request.Request(cdif_url, method="DELETE")
            with urllib.request.urlopen(req_del) as resp:
                res_body = resp.read().decode("utf-8")
                print(f"Delete response: {res_body}")
        except Exception as e:
            print(f"Error deleting index 'cdif': {e}", file=sys.stderr)
            return False

    print(f"Creating 'cdif' index with schema ('cdif_jsonld' object enabled=false) at {cdif_url}...")
    try:
        body = json.dumps(CDIF_INDEX_SCHEMA).encode("utf-8")
        req_put = urllib.request.Request(
            cdif_url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="PUT"
        )
        with urllib.request.urlopen(req_put) as resp:
            res_body = resp.read().decode("utf-8")
            print(f"Create response: {res_body}")
            print(f"Successfully configured 'cdif' index at {es_url}!")
            return True
    except Exception as e:
        print(f"Error creating 'cdif' index: {e}", file=sys.stderr)
        return False


def main():
    parser = argparse.ArgumentParser(description="Recreate or initialize Elasticsearch 'cdif' index schema.")
    parser.add_argument(
        "--es-url",
        default="http://localhost:9222",
        help="Elasticsearch base URL (default: http://localhost:9222 for codata)"
    )
    parser.add_argument(
        "--ensure",
        action="store_true",
        help="Only create the index if it does not already exist (do not delete)"
    )

    args = parser.parse_args()
    success = recreate_cdif_index(args.es_url, force_recreate=not args.ensure)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
