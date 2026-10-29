import asyncio
import httpx
from minio import Minio
from datetime import timedelta
async def stream_file():
    minio_base = "http://minio:9000"
    filename = "ueaGDTjRoC1Y0S55f9qxCA.md"
    endpoint = minio_base.replace("http://", "").replace("https://", "")
    m_client = Minio(endpoint, access_key="minioadmin", secret_key="minioadmin", secure=False)
    base_minio_url = m_client.presigned_get_object("vault", filename, expires=timedelta(hours=1))
    print("Base URL:", base_minio_url)
    async with httpx.AsyncClient() as client:
        async with client.stream("GET", base_minio_url) as response:
            print("Status code:", response.status_code)
            if response.status_code == 200:
                print("yielding chunks...")
                async for chunk in response.aiter_bytes():
                    print("Chunk size:", len(chunk))
                    
asyncio.run(stream_file())
