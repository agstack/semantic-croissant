import json
import os
import sys
import urllib.request

BASE_URL = os.environ.get("MCP_BASE_URL", "http://localhost:7070").rstrip("/")


def post_json(path: str, payload: dict) -> dict:
    req = urllib.request.Request(
        f"{BASE_URL}{path}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        assert resp.status == 200, f"Expected 200, got {resp.status}"
        return json.loads(resp.read().decode("utf-8"))


def get_text(path: str) -> str:
    with urllib.request.urlopen(f"{BASE_URL}{path}", timeout=30) as resp:
        assert resp.status == 200, f"Expected 200 for {path}, got {resp.status}"
        return resp.read().decode("utf-8")


def main():
    # 1. Create document with summary
    res_create = post_json("/api/chat/document", {
        "action": "create",
        "prompt": "Summary of Semantic Croissant",
        "text": "# Summary\n\nSemantic Croissant indexes datasets into QLever and MinIO.",
        "doc_id": "",
    })
    assert res_create.get("status") == "success", res_create
    doc_id = res_create["doc_id"]
    print(f"Created doc_id={doc_id}")

    # 2. Append transcript Q&A to Agents.md
    res_add = post_json("/api/chat/document", {
        "action": "add",
        "doc_id": doc_id,
        "prompt": "How is provenance tracked?",
        "text": "Provenance is tracked using DIDs and UNF-6 fingerprints.",
    })
    assert res_add.get("status") == "success", res_add
    assert res_add.get("doc_url") == f"/vault/doc/{doc_id}_Agents.md", res_add

    # 3. Verify .md, _Agents.md, and .jsonld distribution
    md_text = get_text(f"/vault/{doc_id}.md")
    assert "Semantic Croissant indexes datasets" in md_text

    agents_text = get_text(f"/vault/{doc_id}_Agents.md")
    assert "How is provenance tracked?" in agents_text
    assert "DIDs and UNF-6 fingerprints" in agents_text

    jld = json.loads(get_text(f"/vault/{doc_id}.jsonld"))
    dist_names = {d.get("name") for d in jld.get("distribution", [])}
    assert f"{doc_id}.md" in dist_names, dist_names
    assert "Agents.md" in dist_names, dist_names
    print("PASS: All checks succeeded for", doc_id)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        sys.exit(1)
