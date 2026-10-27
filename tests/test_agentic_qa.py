import os
import sys
import json
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
    cfg = load_agent_config()
    assert "MODEL" in cfg, "agent.config must specify MODEL"
    assert "OLLAMA_HOST" in cfg, "agent.config must specify OLLAMA_HOST"
    assert cfg["MODEL"], "MODEL cannot be empty"
    print(f"\n[Config] Active model in agent.config: {cfg['MODEL']} at {cfg['OLLAMA_HOST']}")

def test_ollama_connectivity():
    """Verify the configured Ollama host is reachable and the model exists."""
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

def test_dataset_page_qa_vault_ask():
    """Test QA on a specific dataset document using /vault/ask recognizing agent.config."""
    cfg = load_agent_config()
    model = os.environ.get("MODEL", cfg.get("MODEL", "gemma4:31b-cloud"))
    base_url = os.environ.get("MCP_HTTP_URL", "http://localhost:7115")

    doc_id = "11cafec80d18b054"
    question = "what is about?"

    with httpx.Client(timeout=120.0) as client:
        # Fetch raw document context
        doc_resp = client.get(f"{base_url}/vault/doc/raw/{doc_id}")
        assert doc_resp.status_code == 200, f"Failed to fetch raw document: {doc_resp.status_code}"
        context_text = doc_resp.text
        assert len(context_text) > 50, "Document context is too short or empty"

        # Ask question using the configured model
        ask_payload = {
            "context": context_text[:8000],
            "question": question,
            "model": model
        }
        ask_resp = client.post(f"{base_url}/vault/ask", json=ask_payload)
        assert ask_resp.status_code == 200, f"/vault/ask failed with HTTP {ask_resp.status_code}: {ask_resp.text}"
        
        result = ask_resp.json()
        assert result.get("success") is True, f"QA call reported success=False: {result.get('error')}"
        answer = result.get("answer", "")
        assert answer and len(answer) > 20, f"QA answer is empty or too short: {answer}"
        
        print(f"\n[Dataset QA] Model: {model}")
        print(f"[Dataset QA] Question: {question}")
        print(f"[Dataset QA] Answer snippet: {answer[:250]}...")
        assert any(k in answer.lower() for k in ["warming", "climate", "political", "disaster", "cleavage", "dataset", "exposure"]), \
            f"Answer did not contain expected thematic keywords: {answer}"

def test_collections_qa_ask():
    """Test QA on collections using /api/collections/{id}/ask recognizing agent.config."""
    cfg = load_agent_config()
    model = os.environ.get("MODEL", cfg.get("MODEL", "gemma4:31b-cloud"))
    base_url = os.environ.get("MCP_HTTP_URL", "http://localhost:7115")

    # Using the Precipitation collection ID
    collection_id = "04b5336a-7c4c-479c-8dbd-143fc6f71efc"
    question = "explain what is about"

    with httpx.Client(timeout=120.0) as client:
        ask_payload = {
            "query": question
        }
        ask_resp = client.post(f"{base_url}/api/collections/{collection_id}/ask", json=ask_payload)
        assert ask_resp.status_code == 200, f"/api/collections/{collection_id}/ask failed with HTTP {ask_resp.status_code}: {ask_resp.text}"
        
        result = ask_resp.json()
        answer = result.get("answer", "")
        assert answer and len(answer) > 30, f"Collection QA answer is empty or too short: {answer}"
        
        print(f"\n[Collection QA] Collection: {collection_id}")
        print(f"[Collection QA] Question: {question}")
        print(f"[Collection QA] Answer snippet: {answer[:250]}...")
        assert any(k in answer.lower() for k in ["precipitation", "climate", "weather", "dataset", "collection", "hydrology"]), \
            f"Answer did not contain expected keywords about precipitation collection: {answer}"

def test_collections_index_qa_ask():
    """Test global collections Q&A across all collections using /api/collectionsindex/ask."""
    base_url = os.environ.get("MCP_HTTP_URL", "http://localhost:7115")
    question = "What datasets exist related to climate?"

    with httpx.Client(timeout=120.0) as client:
        ask_payload = {
            "query": question
        }
        ask_resp = client.post(f"{base_url}/api/collectionsindex/ask", json=ask_payload)
        assert ask_resp.status_code == 200, f"/api/collectionsindex/ask failed with HTTP {ask_resp.status_code}: {ask_resp.text}"
        
        result = ask_resp.json()
        answer = result.get("answer", "")
        assert answer and len(answer) > 20, f"Collections index QA answer is empty: {answer}"
        print(f"\n[Collections Index QA] Answer snippet: {answer[:200]}...")

if __name__ == "__main__":
    print("=== RUNNING QA AGENTIC TESTS ===")
    test_agent_config_structure()
    test_ollama_connectivity()
    test_dataset_page_qa_vault_ask()
    test_collections_qa_ask()
    test_collections_index_qa_ask()
    print("\n✅ All agentic QA tests passed successfully!")
