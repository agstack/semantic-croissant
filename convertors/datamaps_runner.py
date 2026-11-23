import os
import io
import json
import hashlib
import requests

RESULTS = {}


def url_matches(url: str) -> bool:
    if not url:
        return False
    patterns_raw = os.environ.get(
        "DATAMAPS_URL_PATTERNS",
        "cenaos.copeco.gob.hn/productos/pronostico,pronosticoestacional.pdf,pronostico_estacional",
    )
    patterns = [p.strip().lower() for p in patterns_raw.split(",") if p.strip()]
    u_lower = str(url).lower()
    return any(p in u_lower for p in patterns)


def run(url: str):
    """
    Execute the datamaps two-map-per-page (left/right) CV & GeoPandas pipeline via PaleFire API.
    Caches result in RESULTS[url] so convert_to_croissant can upload CSVs & JSON-LD distributions.
    """
    if url in RESULTS and RESULTS[url]:
        return RESULTS[url]

    endpoints = []
    env_pf = os.environ.get("PALEFIRE_API_URL", "")
    if env_pf:
        base_pf = env_pf.split("/parse")[0].rstrip("/")
        endpoints.append(f"{base_pf}/datamaps/run")
    endpoints.extend([
        "http://palefire-api:8000/datamaps/run",
        "http://localhost:8008/datamaps/run",
        "http://host.docker.internal:8008/datamaps/run",
    ])

    last_err = None
    for ep in dict.fromkeys(endpoints):
        try:
            resp = requests.post(ep, json={"url": url}, timeout=300)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("status") == "success" and data.get("maps"):
                    RESULTS[url] = data
                    print(f"Datamaps pipeline succeeded via {ep}: {len(data.get('maps', []))} maps extracted (left & right panels).")
                    return data
        except Exception as e:
            last_err = e

    if last_err:
        print(f"Warning: Datamaps pipeline call failed: {last_err}")
    return None


def to_markdown(dm_result: dict, doc_id: str = None) -> str:
    """
    Render a summary section of the two-map-per-page (left/right) extraction for the document markdown.
    Note: keep this deterministic or consistent across runs if needed, or format per-page left/right panels.
    """
    if not dm_result or not dm_result.get("maps"):
        return ""

    maps = dm_result.get("maps", [])
    lines = [
        "\n\n## Datamaps Two-Panel (Left & Right) Map Extraction\n",
        f"Automatically detected and extracted **{len(maps)} map panels** (left and right maps per page) across 18 Honduran departments.\n",
        "| Page | Panel | Map Variable / Title | Sample Department Values |",
        "|---|---|---|---|",
    ]
    for m in maps:
        p = m.get("page", "")
        side = m.get("side", "")
        title = (m.get("title") or "").replace("|", "/")
        graph = (m.get("jsonld") or {}).get("@graph", [])
        samples = []
        for obs in graph[:3]:
            loc = obs.get("name", "")
            val = obs.get("anomaly") if obs.get("anomaly") not in (None, "Unknown") else obs.get("value", "")
            if loc and val:
                samples.append(f"{loc}: {val}")
        sample_str = "; ".join(samples) if samples else "No Map"
        lines.append(f"| Page {p:02d} | `{side}` | {title} | {sample_str} |")

    return "\n".join(lines) + "\n"


def upload_and_distributions(dm_result: dict, minio_client, base_host: str, doc_id: str = None) -> list:
    """
    Upload master CSV, per-map (page_XX_left.csv / page_XX_right.csv) CSVs, and JSON-LD observations
    to the MinIO `vault` bucket, returning Croissant FileObject distributions.
    """
    if not dm_result:
        return []

    base_host = (base_host or "http://localhost:7070").rstrip("/")
    prefix = f"{doc_id}_" if doc_id else ""
    distributions = []

    # 1. Master compiled CSV report
    master_csv = dm_result.get("master_csv", "")
    if master_csv:
        csv_bytes = master_csv.encode("utf-8")
        sha = hashlib.sha256(csv_bytes).hexdigest()
        names_to_upload = []
        if doc_id:
            names_to_upload.append((f"{doc_id}.csv", "Master compiled CSV report of climate heatmap data across all pages (left & right maps)"))
            names_to_upload.append((f"{doc_id}_final_heatmap_report.csv", "Final Heatmap Report CSV (all pages, left & right maps)"))
        else:
            names_to_upload.append(("final_heatmap_report.csv", "Final Heatmap Report CSV (all pages, left & right maps)"))

        for obj_name, desc in names_to_upload:
            if minio_client:
                try:
                    minio_client.put_object("vault", obj_name, io.BytesIO(csv_bytes), len(csv_bytes), content_type="text/csv")
                except Exception as e:
                    print(f"Warning: failed to upload {obj_name}: {e}")
            distributions.append({
                "@type": "cr:FileObject",
                "@id": obj_name,
                "name": obj_name,
                "description": desc,
                "contentUrl": f"{base_host}/vault/{obj_name}",
                "encodingFormat": "text/csv",
                "contentSize": f"{len(csv_bytes)} B",
                "sha256": sha,
            })

    # 2. Per-map left & right CSVs and JSON-LD files
    for m in dm_result.get("maps", []):
        p = m.get("page") or 0
        side = m.get("side") or "single"
        title = m.get("title") or f"Page {p} {side}"
        csv_text = m.get("csv") or ""
        jsonld_obj = m.get("jsonld") or {}

        csv_name = f"{prefix}page_{p:02d}_{side}.csv"
        if csv_text:
            c_bytes = csv_text.encode("utf-8")
            c_sha = hashlib.sha256(c_bytes).hexdigest()
            if minio_client:
                try:
                    minio_client.put_object("vault", csv_name, io.BytesIO(c_bytes), len(c_bytes), content_type="text/csv")
                except Exception as e:
                    print(f"Warning: failed to upload {csv_name}: {e}")
            distributions.append({
                "@type": "cr:FileObject",
                "@id": csv_name,
                "name": csv_name,
                "description": f"Extracted tabular map data for Page {p:02d} ({side} map): {title}",
                "contentUrl": f"{base_host}/vault/{csv_name}",
                "encodingFormat": "text/csv",
                "contentSize": f"{len(c_bytes)} B",
                "sha256": c_sha,
            })

        json_name = f"{prefix}page_{p:02d}_{side}.jsonld"
        if jsonld_obj:
            j_bytes = json.dumps(jsonld_obj, indent=2, ensure_ascii=False).encode("utf-8")
            j_sha = hashlib.sha256(j_bytes).hexdigest()
            if minio_client:
                try:
                    minio_client.put_object("vault", json_name, io.BytesIO(j_bytes), len(j_bytes), content_type="application/ld+json")
                except Exception as e:
                    print(f"Warning: failed to upload {json_name}: {e}")
            distributions.append({
                "@type": "cr:FileObject",
                "@id": json_name,
                "name": json_name,
                "description": f"Semantic JSON-LD map observations for Page {p:02d} ({side} map): {title}",
                "contentUrl": f"{base_host}/vault/{json_name}",
                "encodingFormat": "application/ld+json",
                "contentSize": f"{len(j_bytes)} B",
                "sha256": j_sha,
            })

    return distributions


def inject_distributions(output_json_str: str, dm_dists: list) -> str:
    if not dm_dists:
        return output_json_str
    try:
        j_obj = json.loads(output_json_str)
        tgt = j_obj[0] if isinstance(j_obj, list) and j_obj else j_obj
        if isinstance(tgt, dict):
            existing = tgt.get("distribution") or []
            if isinstance(existing, dict):
                existing = [existing]
            existing_ids = {d.get("@id") or d.get("name") for d in existing if isinstance(d, dict)}
            new_list = []
            for d in dm_dists:
                key = d.get("@id") or d.get("name")
                if key not in existing_ids:
                    new_list.append(d)
                    existing_ids.add(key)
            # Put CSVs at the front so they are immediately visible
            tgt["distribution"] = new_list + existing
            return json.dumps(j_obj, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"Warning: inject_distributions failed: {e}")
    return output_json_str
