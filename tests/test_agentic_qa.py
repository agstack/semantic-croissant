import os
import sys
import json
import time
import httpx
import pytest

# Load agent.config
CONFIG_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "agents", "agent.config"))

def load_agent_config():
    if not os.path.exists(CONFIG_PATH):
        pytest.fail(f"agent.config not found at {CONFIG_PATH}")
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def test_agent_config_structure():
    """Verify agent.config exists, is valid JSON, and has required Ollama/Agent keys."""
    t0 = time.time()
    cfg = load_agent_config()
    assert "MODEL" in cfg, "agent.config must specify MODEL"
    assert "OLLAMA_HOST" in cfg, "agent.config must specify OLLAMA_HOST"
    assert cfg["MODEL"], "MODEL cannot be empty"
    elapsed = time.time() - t0
    print(f"\n[Config] Active model in agent.config: {cfg['MODEL']} at {cfg['OLLAMA_HOST']} (took {elapsed:.2f}s)")

def test_ollama_connectivity():
    """Verify the configured Ollama host is reachable and the model exists."""
    t0 = time.time()
    cfg = load_agent_config()
    ollama_host = os.environ.get("OLLAMA_HOST", cfg.get("OLLAMA_HOST"))
    model = os.environ.get("MODEL", cfg.get("MODEL"))
    token = os.environ.get("OLLAMA_TOKEN") or os.environ.get("OLLAMA_API_KEY") or "test-api-key-12345"
    headers = {"Authorization": f"Bearer {token}"} if token else {}

    # Test /api/tags
    with httpx.Client(timeout=15.0) as client:
        resp = client.get(f"{ollama_host}/api/tags", headers=headers)
        assert resp.status_code == 200, f"Ollama /api/tags returned status {resp.status_code}: {resp.text}"
        data = resp.json()
        models = [m.get("name") for m in data.get("models", [])]
        print(f"[Ollama] Available models: {models}")
        # Base model name check (e.g. gemma4:31b-cloud)
        base_model = model.split(":")[0]
        has_model = any(base_model in m for m in models)
        assert has_model, f"Model {model} (or base {base_model}) not found in Ollama tags: {models}"
    elapsed = time.time() - t0
    print(f"[Timing] test_ollama_connectivity took {elapsed:.2f}s")

def get_service_base_url():
    """Auto-detect the accessible base URL for the API/MCP service."""
    # Priority: Env var -> agent.config MCP_URL -> standard local ports -> public domain
    cfg = load_agent_config()
    candidates = []
    
    if os.environ.get("MCP_HTTP_URL"):
        candidates.append(os.environ.get("MCP_HTTP_URL"))
    if os.environ.get("BASE_URL"):
        candidates.append(os.environ.get("BASE_URL"))
        
    mcp_url = cfg.get("MCP_URL", "")
    if mcp_url:
        # e.g. http://localhost:7070/sse -> http://localhost:7070
        base_from_mcp = mcp_url.split("/sse")[0].rstrip("/")
        candidates.append(base_from_mcp)
        
    candidates.extend([
        "http://localhost:7115",
        "http://localhost:7070",
        "http://localhost:7013",
        "http://127.0.0.1:7115",
        "http://127.0.0.1:7070",
        "http://127.0.0.1:7013",
        "https://ai.mediaquantum.eu"
    ])

    print("\n[Service Discovery] Probing candidate base URLs for API availability:")
    probed_errors = []
    for url in candidates:
        if not url:
            continue
        try:
            with httpx.Client(timeout=4.0) as client:
                r = client.get(f"{url}/api/collections")
                if r.status_code in [200, 401, 403]:
                    print(f"  ✓ Reached {url} (HTTP {r.status_code})")
                    return url
                else:
                    probed_errors.append(f"  ✗ {url}/api/collections -> HTTP {r.status_code}")
        except Exception as e:
            probed_errors.append(f"  ✗ {url} -> {type(e).__name__}: {e}")

    for err in probed_errors:
        print(err)

    print("  ! None of the candidates responded successfully. Falling back to default: https://ai.mediaquantum.eu")
    return "https://ai.mediaquantum.eu"

def test_dataset_page_qa_vault_ask():
    """Test QA on a specific dataset document using /vault/ask recognizing agent.config."""
    t0 = time.time()
    cfg = load_agent_config()
    model = os.environ.get("MODEL", cfg.get("MODEL", "gemma4:31b-cloud"))
    base_url = get_service_base_url()
    print(f"\n[Test] Running test_dataset_page_qa_vault_ask targeting: {base_url}")

    # Allow custom dataset ID via DATASET env var
    doc_id = os.environ.get("DATASET", "11cafec80d18b054")
    question = os.environ.get("DATASET_QUESTION", "what is about?")

    with httpx.Client(timeout=120.0) as client:
        # 1. Fetch raw document context
        doc_url = f"{base_url}/vault/doc/raw/{doc_id}"
        print(f"[Dataset QA] Step 1: Fetching document context from {doc_url} ...")
        t_doc = time.time()
        try:
            doc_resp = client.get(doc_url)
        except Exception as e:
            pytest.fail(f"Connection failed while requesting {doc_url}: {type(e).__name__}: {e}")

        if doc_resp.status_code != 200:
            pytest.fail(
                f"Failed to fetch raw document from {doc_url}.\n"
                f"HTTP Status: {doc_resp.status_code}\n"
                f"Response text: {doc_resp.text[:500]}"
            )
        context_text = doc_resp.text
        assert len(context_text) > 30, f"Document context from {doc_url} is too short or empty (len={len(context_text)})"
        print(f"[Dataset QA] Retrieved {len(context_text)} chars of context in {time.time() - t_doc:.2f}s.")

        # 2. Ask question using configured model
        ask_url = f"{base_url}/vault/ask"
        ask_payload = {
            "context": context_text[:8000],
            "question": question,
            "model": model
        }
        print(f"[Dataset QA] Step 2: Posting question to {ask_url} (model: {model}) ...")
        t_ask = time.time()
        try:
            ask_resp = client.post(ask_url, json=ask_payload)
        except Exception as e:
            pytest.fail(f"Connection failed while sending QA request to {ask_url}: {type(e).__name__}: {e}")

        if ask_resp.status_code != 200:
            pytest.fail(
                f"/vault/ask failed with HTTP {ask_resp.status_code} at {ask_url}.\n"
                f"Response body:\n{ask_resp.text}"
            )
        
        try:
            result = ask_resp.json()
        except Exception as e:
            pytest.fail(
                f"Failed to parse JSON response from {ask_url}: {e}\n"
                f"Raw response text:\n{ask_resp.text}"
            )

        if not result.get("success"):
            pytest.fail(f"QA call returned success=False. Error message: {result.get('error')}\nFull response: {result}")

        answer = result.get("answer", "")
        if not answer or len(answer) < 20:
            pytest.fail(f"QA answer is empty or suspiciously short: '{answer}'\nFull response: {result}")
        
        elapsed_ask = time.time() - t_ask
        elapsed_total = time.time() - t0
        print(f"\n[Dataset QA] Document ID: {doc_id}")
        print(f"[Dataset QA] Question: {question}")
        print(f"[Dataset QA] Answer snippet: {answer[:300]}...")
        print(f"[Timing] test_dataset_page_qa_vault_ask took {elapsed_total:.2f}s (inference: {elapsed_ask:.2f}s)")
        
        # Validate that the answer reflects either the default or custom dataset content
        if doc_id == "11cafec80d18b054":
            assert any(k in answer.lower() for k in ["warming", "climate", "political", "disaster", "cleavage", "dataset", "exposure"]), \
                f"Answer did not contain expected thematic keywords: {answer}"
        else:
            # For custom dataset, ensure a coherent informative response was returned
            assert len(answer.split()) >= 5, f"Answer is too short or non-informative: {answer}"
    return elapsed_total

def test_collections_qa_ask():
    """Test QA on collections using /api/collections/{id}/ask recognizing agent.config."""
    t0 = time.time()
    base_url = get_service_base_url()
    # Allow custom collection ID via COLLECTION env var
    collection_id = os.environ.get("COLLECTION", "04b5336a-7c4c-479c-8dbd-143fc6f71efc")
    question = os.environ.get("COLLECTION_QUESTION", "explain what is about")
    ask_url = f"{base_url}/api/collections/{collection_id}/ask"

    print(f"\n[Test] Running test_collections_qa_ask targeting: {ask_url}")

    with httpx.Client(timeout=120.0) as client:
        ask_payload = {"query": question}
        t_ask = time.time()
        try:
            ask_resp = client.post(ask_url, json=ask_payload)
        except Exception as e:
            pytest.fail(f"Connection failed while posting to {ask_url}: {type(e).__name__}: {e}")

        if ask_resp.status_code != 200:
            pytest.fail(
                f"/api/collections/{collection_id}/ask failed with HTTP {ask_resp.status_code} at {ask_url}.\n"
                f"Response body:\n{ask_resp.text}"
            )
        
        try:
            result = ask_resp.json()
        except Exception as e:
            pytest.fail(f"Failed to parse JSON response from {ask_url}: {e}\nRaw response:\n{ask_resp.text}")

        answer = result.get("answer", "")
        if not answer or len(answer) < 30:
            pytest.fail(f"Collection QA answer is empty or too short: '{answer}'\nFull response: {result}")
        
        elapsed_total = time.time() - t0
        elapsed_ask = time.time() - t_ask
        print(f"\n[Collection QA] Collection: {collection_id}")
        print(f"[Collection QA] Question: {question}")
        print(f"[Collection QA] Answer snippet: {answer[:300]}...")
        print(f"[Timing] test_collections_qa_ask took {elapsed_total:.2f}s (inference: {elapsed_ask:.2f}s)")

        if collection_id == "04b5336a-7c4c-479c-8dbd-143fc6f71efc":
            assert any(k in answer.lower() for k in ["precipitation", "climate", "weather", "dataset", "collection", "hydrology"]), \
                f"Answer did not contain expected keywords: {answer}"
        else:
            assert len(answer.split()) >= 5, f"Answer is too short or non-informative: {answer}"
    return elapsed_total

def test_collections_index_qa_ask():
    """Test global collections Q&A across all collections using /api/collectionsindex/ask."""
    t0 = time.time()
    base_url = get_service_base_url()
    question = "What datasets exist related to climate?"
    ask_url = f"{base_url}/api/collectionsindex/ask"

    print(f"\n[Test] Running test_collections_index_qa_ask targeting: {ask_url}")

    with httpx.Client(timeout=120.0) as client:
        ask_payload = {"query": question}
        t_ask = time.time()
        try:
            ask_resp = client.post(ask_url, json=ask_payload)
        except Exception as e:
            pytest.fail(f"Connection failed while posting to {ask_url}: {type(e).__name__}: {e}")

        if ask_resp.status_code != 200:
            pytest.fail(
                f"/api/collectionsindex/ask failed with HTTP {ask_resp.status_code} at {ask_url}.\n"
                f"Response body:\n{ask_resp.text}"
            )
        
        try:
            result = ask_resp.json()
        except Exception as e:
            pytest.fail(f"Failed to parse JSON response from {ask_url}: {e}\nRaw response:\n{ask_resp.text}")

        answer = result.get("answer", "")
        if not answer or len(answer) < 20:
            pytest.fail(f"Collections index QA answer is empty: '{answer}'\nFull response: {result}")
        
        elapsed_total = time.time() - t0
        elapsed_ask = time.time() - t_ask
        print(f"\n[Collections Index QA] Answer snippet: {answer[:200]}...")
        print(f"[Timing] test_collections_index_qa_ask took {elapsed_total:.2f}s (inference: {elapsed_ask:.2f}s)")
    return elapsed_total

if __name__ == "__main__":
    total_start = time.time()
    print("=== RUNNING QA AGENTIC TESTS ===")
    test_agent_config_structure()
    test_ollama_connectivity()
    t1 = test_dataset_page_qa_vault_ask()
    t2 = test_collections_qa_ask()
    t3 = test_collections_index_qa_ask()
    total_duration = time.time() - total_start
    print("\n" + "="*50)
    print(f"⏱️  SUMMARY OF EXECUTION TIMES:")
    print(f"   • Dataset Page QA (/vault/ask):            {t1:.2f}s")
    print(f"   • Specific Collection QA (/ask):           {t2:.2f}s")
    print(f"   • Global Collections QA (/index/ask):      {t3:.2f}s")
    print(f"   • Total Test Suite Duration:               {total_duration:.2f}s")
    print("="*50)
    print("✅ All agentic QA tests passed successfully!")

