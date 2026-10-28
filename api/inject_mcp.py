import re

with open("api/mcp_server.py", "r") as f:
    content = f.read()

process_text_code = """
        async def process_text(request):
            import os, tempfile, asyncio, re
            from starlette.responses import JSONResponse
            
            try:
                body = await request.json()
                text = body.get("text")
                if not text or not text.strip():
                    return JSONResponse({"detail": "Text cannot be empty"}, status_code=400)
                    
                with tempfile.NamedTemporaryFile(mode='w', suffix='.md', delete=False) as f:
                    f.write(text)
                    temp_path = f.name
                    
                script_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "../convertors/url_to_croissant.py"))
                if not os.path.exists(script_path):
                    script_path = "convertors/url_to_croissant.py"
                    
                cmd = ["python3", script_path, temp_path, "--is-file", "--elastic"]
                
                process = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                stdout, stderr = await process.communicate()
                
                try:
                    os.unlink(temp_path)
                except Exception:
                    pass
                    
                if process.returncode != 0:
                    return JSONResponse({"detail": f"Conversion failed: {stderr.decode('utf-8')}"}, status_code=500)
                    
                output = stdout.decode('utf-8')
                
                match = re.search(r"Extracted markdown successfully uploaded to vault: (https?://.*?/vault/[^\s]+)", output)
                if match:
                    redirect_url = match.group(1)
                else:
                    file_match = re.search(r"Extracted markdown saved to [^/]+/([^/]+)/([a-zA-Z0-9_-]+\\.md)", output)
                    if file_match:
                        redirect_url = f"/vault/doc/{file_match.group(2)}"
                    else:
                        return JSONResponse({"detail": "Could not determine generated filename from output"}, status_code=500)
                        
                if redirect_url.startswith("http"):
                    redirect_url = "/vault/doc/" + redirect_url.split("/vault/")[-1]
                    
                return JSONResponse({"status": "success", "redirect_url": redirect_url})
                
            except Exception as e:
                return JSONResponse({"detail": str(e)}, status_code=500)
"""

content = content.replace("async def process_dataverse(request):", process_text_code + "\n        async def process_dataverse(request):")
content = content.replace('Route("/api/dataverse/process", endpoint=process_dataverse, methods=["POST"]),', 'Route("/api/dataverse/process", endpoint=process_dataverse, methods=["POST"]),\n                Route("/api/text/process", endpoint=process_text, methods=["POST"]),')

with open("api/mcp_server.py", "w") as f:
    f.write(content)
