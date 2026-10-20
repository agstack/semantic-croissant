#!/usr/bin/env python3
"""
CDIF Agent CLI

Extracts and converts all tabular files across datasets in collections
into standard CDIF (DDI-CDI JSON-LD) and updates the Elasticsearch CDIF variables registry.

Usage:
    python cli/cdif_agent.py --list
    python cli/cdif_agent.py --collectionID <uuid>
    python cli/cdif_agent.py <uuid>
    python cli/cdif_agent.py --all
"""

import argparse
import json
import os
import sys
import time
from typing import List, Dict, Any, Optional

try:
    import httpx
except ImportError:
    print("Error: 'httpx' is required. Install with: pip install httpx", file=sys.stderr)
    sys.exit(1)

try:
    from rich.console import Console
    from rich.table import Table
    from rich.progress import (
        Progress,
        SpinnerColumn,
        BarColumn,
        TextColumn,
        TimeElapsedColumn,
        TaskProgressColumn
    )
    from rich.panel import Panel
    from rich.text import Text
    HAS_RICH = True
except ImportError:
    HAS_RICH = False

DEFAULT_SERVER_URL = os.environ.get("CDIF_SERVER_URL") or os.environ.get("MCP_SERVER_URL") or "http://127.0.0.1:7110"


class CDIFClient:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")
        self.client = httpx.Client(timeout=180.0)

    def list_collections(self) -> List[Dict[str, Any]]:
        url = f"{self.base_url}/api/collections"
        try:
            resp = self.client.get(url, timeout=30.0)
            if resp.status_code != 200:
                raise RuntimeError(f"HTTP {resp.status_code}: {resp.text}")
            data = resp.json()
            return data.get("collections", [])
        except httpx.RequestError as e:
            raise RuntimeError(f"Connection to {url} failed: {e}")

    def get_collection(self, cid: str) -> Dict[str, Any]:
        url = f"{self.base_url}/api/collections/{cid}"
        resp = self.client.get(url, timeout=30.0)
        if resp.status_code == 404:
            raise ValueError(f"Collection '{cid}' not found")
        if resp.status_code != 200:
            raise RuntimeError(f"HTTP {resp.status_code}: {resp.text}")
        return resp.json()

    def get_collection_variables_count(self, cid: str) -> Optional[int]:
        url = f"{self.base_url}/api/collections/{cid}/variables"
        try:
            resp = self.client.get(url, timeout=15.0)
            if resp.status_code == 200:
                data = resp.json()
                return data.get("total_variables", len(data.get("variables", [])))
        except Exception:
            pass
        return None

    def stream_analyze(self, cid: str):
        """
        Connects to SSE stream endpoint /api/collections/{id}/analyze?stream=true
        Yields parsed event JSON dicts.
        """
        url = f"{self.base_url}/api/collections/{cid}/analyze?stream=true"
        with self.client.stream("GET", url, headers={"Accept": "text/event-stream"}, timeout=300.0) as resp:
            if resp.status_code == 404:
                raise ValueError(f"Collection '{cid}' not found")
            if resp.status_code != 200:
                raise RuntimeError(f"HTTP {resp.status_code}: {resp.text}")

            buffer = ""
            for line in resp.iter_lines():
                line = line.strip()
                if line.startswith("data: "):
                    payload = line[6:].strip()
                    if payload:
                        try:
                            yield json.loads(payload)
                        except json.JSONDecodeError:
                            continue


def print_collections_table(collections: List[Dict[str, Any]], client: Optional[CDIFClient] = None):
    if not collections:
        print("No collections available.")
        return

    if HAS_RICH:
        console = Console()
        table = Table(
            title="Available Collections in Vault",
            title_style="bold magenta",
            border_style="blue",
            header_style="bold cyan",
            expand=True
        )
        table.add_column("#", justify="right", style="dim", width=4)
        table.add_column("Collection ID", style="bold green", no_wrap=True)
        table.add_column("Name", style="bright_white")
        table.add_column("Items", justify="center", style="yellow", width=8)
        table.add_column("Type", justify="center", style="magenta", width=12)

        for idx, col in enumerate(collections, 1):
            cid = col.get("id", "N/A")
            name = col.get("name") or "Untitled"
            items_cnt = len(col.get("items", []))
            is_super = "Supercollection" if col.get("is_supercollection") else "Collection"
            table.add_row(str(idx), cid, name, str(items_cnt), is_super)

        console.print(table)
        console.print(f"\n[dim]Total collections: {len(collections)} | Endpoint: {client.base_url if client else DEFAULT_SERVER_URL}[/dim]\n")
    else:
        print(f"\n{'#':>3}  {'Collection ID':<38}  {'Items':<6}  {'Name'}")
        print("-" * 80)
        for idx, col in enumerate(collections, 1):
            cid = col.get("id", "N/A")
            name = col.get("name") or "Untitled"
            items_cnt = len(col.get("items", []))
            print(f"{idx:>3}. {cid:<38}  {items_cnt:<6}  {name}")
        print(f"\nTotal: {len(collections)} collections.\n")


def analyze_collection(client: CDIFClient, cid: str) -> Dict[str, Any]:
    # 1. Fetch metadata first to display collection title
    try:
        col_meta = client.get_collection(cid)
        col_name = col_meta.get("name") or "Untitled Collection"
    except Exception as e:
        col_name = "Collection"

    summary_result = {
        "collection_id": cid,
        "name": col_name,
        "total_files": 0,
        "successful": 0,
        "failed": 0,
        "elapsed_seconds": 0.0,
        "files": []
    }

    if HAS_RICH:
        console = Console()
        console.print(Panel(
            Text.from_markup(f"[bold cyan]CDIF Analysis Agent[/bold cyan]\n"
                             f"[bold white]Collection:[/bold white] {col_name}\n"
                             f"[dim green]ID: {cid}[/dim green]"),
            border_style="cyan"
        ))

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(complete_style="green", finished_style="bold green"),
            TaskProgressColumn(),
            TextColumn("•"),
            TimeElapsedColumn(),
            console=console,
            expand=True
        ) as progress:
            task_id = progress.add_task("[yellow]Discovering tabular distributions...", total=100)

            total_tabular_files = 0
            for evt in client.stream_analyze(cid):
                evt_type = evt.get("type")

                if evt_type == "init":
                    total_tabular_files = evt.get("total_tabular_files", 0)
                    total_datasets = evt.get("total_datasets", 0)
                    summary_result["total_files"] = total_tabular_files
                    
                    if total_tabular_files == 0:
                        progress.update(task_id, completed=100, description="[dim]No tabular files found to analyze.[/dim]")
                    else:
                        progress.update(
                            task_id,
                            total=total_tabular_files,
                            completed=0,
                            description=f"[cyan]Analyzing {total_tabular_files} files across {total_datasets} datasets..."
                        )

                elif evt_type == "progress":
                    processed = evt.get("processed", 0)
                    current_file = evt.get("current_file", "")
                    cur_status = evt.get("current_status", "")
                    
                    status_style = "[green]SUCCESS[/green]" if cur_status in ["success", "cached"] else "[red]FAILED[/red]"
                    status_flag = "[CACHED]" if cur_status == "cached" else status_style

                    progress.update(
                        task_id,
                        completed=processed,
                        description=f"[cyan]{current_file[:30]:<30}[/cyan] {status_flag}"
                    )
                    summary_result["files"].append({
                        "file_name": current_file,
                        "status": cur_status
                    })

                elif evt_type == "complete":
                    summary_result["successful"] = evt.get("successful", 0)
                    summary_result["failed"] = evt.get("failed", 0)
                    summary_result["elapsed_seconds"] = evt.get("total_elapsed_seconds", 0.0)
                    progress.update(task_id, completed=total_tabular_files or 100, description="[bold green]Analysis Complete!")

        # Post-analysis Variable Registry stats
        var_count = client.get_collection_variables_count(cid)
        console.print(f"\n[bold green]✔[/bold green] Finished in [bold]{summary_result['elapsed_seconds']}s[/bold] | "
                      f"Successful exports: [bold green]{summary_result['successful']}[/bold green] | "
                      f"Failed: [bold red]{summary_result['failed']}[/bold red]")
        if var_count is not None:
            console.print(f"[bold cyan]ℹ CDIF Variables Registry:[/bold cyan] [bold yellow]{var_count}[/bold yellow] variables registered in Elasticsearch 'cdif' index.")
        console.print(f"[dim]Registry URL:[/dim] {client.base_url}/collections/{cid}\n")

    else:
        # Fallback standard terminal output with custom progress bar
        print(f"\n=== Analyzing Collection: {col_name} ({cid}) ===")
        total_files = 0
        for evt in client.stream_analyze(cid):
            evt_type = evt.get("type")
            if evt_type == "init":
                total_files = evt.get("total_tabular_files", 0)
                summary_result["total_files"] = total_files
                print(f"Found {total_files} tabular files across {evt.get('total_datasets', 0)} datasets.")
                if total_files == 0:
                    print("No tabular files found.")
            elif evt_type == "progress":
                processed = evt.get("processed", 0)
                pct = evt.get("percent", 0.0)
                cur_file = evt.get("current_file", "")
                st = evt.get("current_status", "")
                bar_len = 30
                filled = int(bar_len * (processed / total_files)) if total_files else 0
                bar = "=" * filled + "-" * (bar_len - filled)
                print(f"\r[{bar}] {processed}/{total_files} ({pct}%) - {cur_file[:25]:<25} [{st.upper()}]", end="", flush=True)
                summary_result["files"].append({"file_name": cur_file, "status": st})
            elif evt_type == "complete":
                summary_result["successful"] = evt.get("successful", 0)
                summary_result["failed"] = evt.get("failed", 0)
                summary_result["elapsed_seconds"] = evt.get("total_elapsed_seconds", 0.0)
                print(f"\nComplete! Processed in {summary_result['elapsed_seconds']}s. (Success: {summary_result['successful']}, Failed: {summary_result['failed']})")

        var_count = client.get_collection_variables_count(cid)
        if var_count is not None:
            print(f"Registered variables in Elasticsearch: {var_count}")

    return summary_result


def main():
    parser = argparse.ArgumentParser(
        description="CDIF Agent CLI: Extract CDIF variables from collections and update Elasticsearch index."
    )
    parser.add_argument(
        "pos_collectionID",
        nargs="?",
        help="Optional positional collection ID (e.g. a35ba3f0-7252-40c7-8f81-8d088d2c8970)"
    )
    parser.add_argument(
        "-c", "--collectionID",
        dest="collectionID",
        help="Collection identifier (e.g. a35ba3f0-7252-40c7-8f81-8d088d2c8970)"
    )
    parser.add_argument(
        "-l", "--list",
        action="store_true",
        help="Show all available collections in the vault"
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Sequentially analyze all available collections"
    )
    parser.add_argument(
        "--server",
        default=DEFAULT_SERVER_URL,
        help=f"Target backend URL (default: {DEFAULT_SERVER_URL})"
    )

    args = parser.parse_args()
    client = CDIFClient(args.server)

    target_cid = args.collectionID or args.pos_collectionID

    if args.list:
        try:
            cols = client.list_collections()
            print_collections_table(cols, client)
        except Exception as e:
            print(f"Error fetching collections: {e}", file=sys.stderr)
            sys.exit(1)
        return

    if args.all:
        try:
            cols = client.list_collections()
            print(f"Starting batch analysis for {len(cols)} collections...")
            for c in cols:
                cid = c.get("id")
                if cid:
                    analyze_collection(client, cid)
        except Exception as e:
            print(f"Error in batch analysis: {e}", file=sys.stderr)
            sys.exit(1)
        return

    if not target_cid:
        parser.print_help()
        print("\nTip: Run with --list to see available collection IDs.", file=sys.stderr)
        sys.exit(1)

    try:
        analyze_collection(client, target_cid)
    except Exception as e:
        print(f"\nError analyzing collection '{target_cid}': {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
