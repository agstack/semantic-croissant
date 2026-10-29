import httpx
import json
import asyncio

API_KEY = "test-api-key-12345"
URL = "https://mcp.dev.codata.org/gateway/v1/messages"

async def test_tool_calling():
    headers = {
        "x-api-key": API_KEY,
        "content-type": "application/json",
        "anthropic-version": "2023-06-01"
    }
    
    # We use the claude-3-5-sonnet-20241022 proxy id which maps to gemma4:e2b
    payload = {
        "model": "claude-3-5-sonnet-20241022",
        "max_tokens": 1024,
        "messages": [
            {
                "role": "user",
                "content": "list all CODATA MCP tools"
            }
        ],
        "tools": [
            {
                "name": "onboarding",
                "description": "Get detailed information about how every tool works in the CODATA MCP environment.",
                "input_schema": {
                    "type": "object",
                    "properties": {}
                }
            }
        ]
    }
    
    print(f"Sending request to {URL}...")
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(URL, headers=headers, json=payload)
        
        print(f"Status Code: {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            print("Response Data:")
            print(json.dumps(data, indent=2))
            
            # Check if it called a tool
            stop_reason = data.get("stop_reason")
            print(f"\nStop Reason: {stop_reason}")
            
            if stop_reason == "tool_use":
                for item in data.get("content", []):
                    if item.get("type") == "tool_use":
                        print(f"SUCCESS: Model decided to call tool: {item.get('name')}")
                        return
            else:
                print("FAILED: Model did not invoke tool_use")
        else:
            print("FAILED: HTTP Error")
            print(response.text)

if __name__ == "__main__":
    asyncio.run(test_tool_calling())
