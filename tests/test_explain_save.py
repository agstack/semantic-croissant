#!/usr/bin/env python3
"""
Test saving analysis results to Vault and retrieving via both raw markdown and rendered viewer.
Verifies fix for:
1. POST /api/variables/explain/save persists document to MinIO and returns doc_id & URLs.
2. GET /vault/doc/raw/{doc_id} successfully returns the document text/markdown.
3. GET /vault/{doc_id}.jsonld returns valid Croissant / CDIF metadata.
4. GET /vault/doc/{doc_id} renders document HTML without NetworkError.
"""
import sys
import json
import httpx

API_BASE = "http://localhost:7094"
MCP_BASE = "http://localhost:7196"

def test_explain_save_and_retrieve():
    print(f"[TEST] Testing explain save and document load...")
    
    sample_markdown = """# 🥐 Test Climate Variables Analysis

> Scientific overview of prioritized variables.

## 1. Executive Summary & Scientific Overview
This export includes atmospheric and soil temperature observations across stations.

## 2. Priority Measurement Matrix
| Priority Rank | Variable Name | Rationale | Unit of Measure | Standardization Rules |
|---|---|---|---|---|
| High | air_temp | Primary thermodynamic state driver | °C | QUDT unit:DEG_C |
| High | precip_rate | Hydrological inflow | mm/day | UCUM mm/d |

## 3. FAIR Recommendations
- **Findable**: Register longitudinal harmonized version with DOI.
- **Accessible**: Map `.sav` to machine-readable JSON-LD.
- **Interoperable**: Use QUDT vocabularies.
- **Reusable**: Provide machine-actionable codebook.

## 4. CDIF 1.1 Integration
- **schema:variableMeasured**: Map series to `climate_policy_perception` with `observation_date`.
- **Cross-Linkage**: Temporal Join linking economic `gas` price with socio-political perceptions.
"""

    payload = {
        "markdown": sample_markdown,
        "filter": "climate test",
        "model": "gemma4:31b-cloud",
        "variables": [
            {
                "variable_name": "air_temp",
                "display_label": "Air Temperature",
                "data_type": "float",
                "unit": "°C",
                "file_name": "weather_data.csv",
                "dataset_title": "Meteorological Survey 2026",
                "dataset_url": "https://example.org/dataset/met2026"
            }
        ]
    }

    # 1. Test POST /api/variables/explain/save via API proxy
    with httpx.Client(timeout=30.0) as client:
        save_resp = client.post(f"{API_BASE}/api/variables/explain/save", json=payload)
        print(f"[*] POST /api/variables/explain/save status: {save_resp.status_code}")
        assert save_resp.status_code == 200, f"Expected 200, got {save_resp.status_code}: {save_resp.text}"
        
        save_data = save_resp.json()
        assert save_data.get("status") == "success", f"Save status not success: {save_data}"
        doc_id = save_data.get("doc_id")
        assert doc_id, "Missing doc_id in response"
        print(f"[*] Document created with ID: {doc_id}")

        # 2. Test GET /vault/doc/raw/{doc_id}
        raw_resp = client.get(f"{API_BASE}/vault/doc/raw/{doc_id}")
        print(f"[*] GET /vault/doc/raw/{doc_id} status: {raw_resp.status_code}")
        assert raw_resp.status_code == 200, f"Expected 200 from raw, got {raw_resp.status_code}"
        assert "air_temp" in raw_resp.text, "Saved content not found in raw markdown"

        # 3. Test GET /vault/{doc_id}.jsonld
        jsonld_resp = client.get(f"{API_BASE}/vault/{doc_id}.jsonld")
        print(f"[*] GET /vault/{doc_id}.jsonld status: {jsonld_resp.status_code}")
        assert jsonld_resp.status_code == 200, f"Expected 200 from jsonld, got {jsonld_resp.status_code}"
        jsonld_data = jsonld_resp.json()
        assert jsonld_data.get("@type") == ["Dataset", "cdif:DiscoveryProfile"], f"Unexpected type: {jsonld_data.get('@type')}"

        # 4. Test GET /vault/doc/{doc_id} with browser User-Agent
        html_resp = client.get(
            f"{API_BASE}/vault/doc/{doc_id}",
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36"}
        )
        print(f"[*] GET /vault/doc/{doc_id} status: {html_resp.status_code}")
        assert html_resp.status_code == 200, f"Expected 200 for HTML viewer, got {html_resp.status_code}"
        assert "doc_viewer" in html_resp.text or "Markdown Viewer" in html_resp.text

    print("[SUCCESS] All analysis save and document load tests passed!")

if __name__ == "__main__":
    test_explain_save_and_retrieve()
