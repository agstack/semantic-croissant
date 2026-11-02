# CDIF Variable Search & Extraction Specification

This document details how variable search and extraction work across Elasticsearch and Python services (e.g. `api/mcp_server.py`, `scripts/search_elastic_variables.py`) when querying the `cdif` index and secondary indices.

---

## 1. Overview Architecture

The search works in a two-stage pipeline:
1. **Elasticsearch Document Retrieval**: A targeted multi-field text search (`multi_match`) selects relevant dataset and distribution documents.
2. **Python Extraction & Token Satisfaction**: Parses the JSON-LD payload, extracts individual variable structures, and verifies that each variable (or its enclosing dataset) satisfies all query tokens.

---

## 2. Elasticsearch Query Stage (`cdif` Index)

When querying the `cdif` index, Elasticsearch executes a `multi_match` query across weighted fields:

### A. Dataset & File Headers
| Field | Boost / Weight | Description |
|---|---|---|
| `title` | `^3` | Dataset or distribution title |
| `description` | `^2` | Dataset or distribution description |
| `file_name` | `^2` | Associated data file name |

### B. DDI-CDI 1.0 RDF Format (`cdif_jsonld.DDICDIModels`)
Used when metadata follows the DDI-CDI specification:
| Field | Boost / Weight | Description |
|---|---|---|
| `cdif_jsonld.DDICDIModels.name` | `^4` | Variable name / identifier |
| `cdif_jsonld.DDICDIModels.displayLabel.locationVariant.entryValue` | `^4` | Variable human-readable display label |
| `cdif_jsonld.DDICDIModels.dct:description` | `^2` | Variable description |
| `cdif_jsonld.DDICDIModels.definition.content` | `^2` | Variable semantic definition |
| `cdif_jsonld.DDICDIModels.physicalDataType.entryValue` | `^1` | Underlying physical data type |

### C. CDIF Discovery Profile Format (`cdif_jsonld.schema:variableMeasured`)
Used when metadata follows the Schema.org / CDIF discovery profile:
| Field | Boost / Weight | Description |
|---|---|---|
| `cdif_jsonld.schema:variableMeasured.schema:name` | `^4` | Variable name (`schema:name`) |
| `cdif_jsonld.schema:variableMeasured.name` | `^4` | Variable name (`name`) |
| `cdif_jsonld.schema:variableMeasured.schema:description` | `^2` | Variable description |
| `cdif_jsonld.variableMeasured.name` | `^4` | Fallback variable name |

---

## 3. Python Extraction & Normalization Stage

After Elasticsearch returns matching documents, Python parses the raw JSON-LD payload (`cdif_jsonld` or `recordSet`).

### A. DDI-CDI 1.0 Model Extraction
Iterates over elements in `cdif_jsonld.DDICDIModels` where `@type == "InstanceVariable"`:
* **Name**: `name` or `name.name`
* **Label**: `displayLabel.locationVariant.entryValue`
* **Data Type**: `physicalDataType.entryValue`
* **Description**: `dct:description` or `definition.content`
* **Physical Mapping**: `cdif:hasPhysicalMapping.cdif:formats_InstanceVariable.@id`

### B. Schema.org / CDIF Discovery Profile Extraction
Iterates over elements in `cdif_jsonld.schema:variableMeasured` or `cdif_jsonld.variableMeasured`:
* **Name**: `schema:name` or `name` or `@id`
* **Label / Description**: `schema:description` or `description`
* **Data Type**: `cdif:physicalDataType` or `physicalDataType`

### C. Croissant / Dataverse Fallback Extraction
When searching general Croissant indices (`*,-.*,-collections,-history,-groups,-cdif`):
* Iterates over `recordSet.field` or `recordSet[].field[]`
* **Name**: `field.name` or `field.@id`
* **Description**: `field.description`
* **Data Type**: `field.dataType`

---

## 4. Token Satisfaction & Matching Logic

To ensure high precision, every extracted variable is evaluated against the search query tokens:

1. **Dataset-level Match**:
   * If the dataset `title` or `file_name` contains **all** query tokens (e.g., both `"air"` and `"pollution"`), **all variables** within that dataset or file are matched.
2. **Variable-level Match**:
   * If any query tokens are missing from the dataset title/file name, the variable itself must satisfy the missing tokens across its:
     - `name`
     - `label`
     - `description`
3. **Deduplication**:
   * In multi-index and multi-document queries, variables are deduplicated by `(dataset_title, file_name, variable_name)` to prevent counting duplicate document versions or multiple harvest runs.

---

## 5. Related Files & Scripts

- [`api/mcp_server.py`](../api/mcp_server.py): Implements `/api/variableindex/data` and `/export?q=...`.
- [`scripts/search_elastic_variables.py`](../scripts/search_elastic_variables.py): CLI tool replicating the exact search & variable extraction logic with index switching (`--index cdif`, `--index dataverse,cdif`).
- [`scripts/count_unique_cdif_variables.py`](../scripts/count_unique_cdif_variables.py): Inspects total unique variables indexed in the `cdif` index.

