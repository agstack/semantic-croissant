# CDIF Agent CLI (`cdif_agent.py`)

A command-line tool to analyze collections of research datasets, extract variables conforming to **CDIF (Cross-Domain Interoperability Framework / DDI-CDI JSON-LD)** standards, and register them into the Elasticsearch `cdif` variables registry and MinIO vault cache.

---

## Overview

The **CDIF Agent** automates the end-to-end extraction and harmonization pipeline:
1. **Discovery**: Inspects member datasets of a collection (including descendant subcollections) to discover tabular distribution files (`.csv`, `.tsv`, `.tab`, `.xlsx`, `.xls`, `.dta`, `.sav`, `.por`, `.parquet`).
2. **Streaming Conversion**: Forwards candidate tabular distributions to the DDI-CDI converter with bounded concurrency and memory guards.
3. **Registry Update**: Stores every parsed CDIF representation and its extracted instance variables, data types, physical mappings, and units into the Elasticsearch `cdif` index and MinIO vault cache.
4. **Live Progress**: Displays an interactive terminal progress bar indicating the active file, completion percentage, elapsed time, and status (`[SUCCESS]`, `[CACHED]`, `[FAILED]`).

---

## Installation & Requirements

The CLI script requires Python 3.8+ and standard dependencies:

```bash
pip install httpx rich
```

*(If `rich` is not installed, the tool gracefully falls back to a clean ASCII terminal progress bar).*

---

## Command-Line Parameters

```text
usage: cdif_agent.py [-h] [-c COLLECTIONID] [-l] [--all] [--server SERVER] [pos_collectionID]
```

| Parameter | Shorthand | Type | Description |
| :--- | :---: | :---: | :--- |
| `--list` | `-l` | Flag | Displays an interactive formatted table of all available collections in the vault (Collection ID, Name, Dataset Item Count, and Type). |
| `--collectionID` | `-c` | String | Target collection identifier UUID to analyze (e.g. `a35ba3f0-7252-40c7-8f81-8d088d2c8970`). Can also be passed as a positional argument. |
| `pos_collectionID` | — | String | Positional collection identifier UUID shorthand (e.g. `python cli/cdif_agent.py <UUID>`). |
| `--all` | — | Flag | Iterates through and sequentially analyzes all collections stored in the vault. |
| `--server` | — | URL | Target MCP / API backend server URL. Defaults to `http://127.0.0.1:7110` or the value of environment variable `CDIF_SERVER_URL` / `MCP_SERVER_URL`. |
| `--help` | `-h` | Flag | Show the CLI help message and parameter overview. |

---

## Usage Examples

### 1. List Available Collections
View all available collections, their UUIDs, names, item counts, and supercollection hierarchy:
```bash
python3 cli/cdif_agent.py --list
```
**Output Example:**
```text
                                Available Collections in Vault                                           
┏━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━┳━━━━━━━┳━━━━━━━━━━━━━┓
┃  # ┃ Collection ID                        ┃ Name               ┃ Items ┃ Type        ┃
┡━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━╇━━━━━━━╇━━━━━━━━━━━━━┩
│  1 │ 3b64074d-c75b-4b27-a705-0e5986c48de5 │ Quantum Computers  │    25 │ Collection  │
│  2 │ 1d1d4d54-f8e3-4ca0-b375-145f8ef36d63 │ GPU Datasets       │    17 │ Collection  │
│  3 │ 1578855d-1b6e-4404-b4e5-d7ee85273b6c │ Climate Change     │    23 │ Collection  │
└────┴──────────────────────────────────────┴────────────────────┴───────┴─────────────┘
```

---

### 2. Analyze a Specific Collection
Run the CDIF extraction pipeline on a collection using the `--collectionID` (or `-c`) parameter:
```bash
python3 cli/cdif_agent.py -c 3b64074d-c75b-4b27-a705-0e5986c48de5
```

Alternatively, use the positional argument:
```bash
python3 cli/cdif_agent.py 3b64074d-c75b-4b27-a705-0e5986c48de5
```

**Output Example:**
```text
╭──────────────────────────────────────────────────────────────────────────────────╮
│ CDIF Analysis Agent                                                              │
│ Collection: Quantum Computers                                                    │
│ ID: 3b64074d-c75b-4b27-a705-0e5986c48de5                                         │
╰──────────────────────────────────────────────────────────────────────────────────╯
    Analysis Complete!           ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━   100% • 0:00:03

✔ Finished in 3.12s | Successful exports: 5 | Failed: 0
ℹ CDIF Variables Registry: 40 variables registered in Elasticsearch 'cdif' index.
Registry URL: http://127.0.0.1:7110/collections/3b64074d-c75b-4b27-a705-0e5986c48de5
```

---

### 3. Analyze All Collections (Batch Processing)
To process and index variables for every collection in the vault sequentially:
```bash
python3 cli/cdif_agent.py --all
```

---

### 4. Custom Backend Server
If your service runs on a different port or domain (e.g. production host or remote profile):
```bash
python3 cli/cdif_agent.py --server "https://ai.mediaquantum.eu" --list
python3 cli/cdif_agent.py --server "https://ai.mediaquantum.eu" -c 3b64074d-c75b-4b27-a705-0e5986c48de5
```
Or configure it globally using the environment variable:
```bash
export CDIF_SERVER_URL="https://ai.mediaquantum.eu"
python3 cli/cdif_agent.py -c 3b64074d-c75b-4b27-a705-0e5986c48de5
```

---

## Verifying Extracted Variables

Once the analysis completes, variables can be explored via:
1. **Interactive Web Registry**: Navigate to `http://<server-host>/variableindex` or open the collection dashboard at `http://<server-host>/collections/<collectionID>`.
2. **API Endpoint**:
   ```bash
   curl -s "http://127.0.0.1:7110/api/collections/<collectionID>/variables" | jq .
   ```
3. **Elasticsearch Direct Query**:
   ```bash
   curl -s "http://127.0.0.1:9222/cdif/_search?size=5" | jq .
   ```
