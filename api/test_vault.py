import asyncio
from minio import Minio
import os

async def test_read():
    filename = "Lv0GirL3LKdxQqV6Zn3Ryg.md"
    minio_base = os.environ.get("MINIO_URL", "http://codata-minio:9000")
    endpoint = minio_base.replace("http://", "").replace("https://", "")
    
    try:
        m_client = Minio(
            endpoint,
            access_key=os.environ.get("MINIO_ROOT_USER", "minioadmin"),
            secret_key=os.environ.get("MINIO_ROOT_PASSWORD", "minioadmin"),
            secure=False
        )
        
        response = m_client.get_object("vault", filename)
        content = response.read()
        response.close()
        response.release_conn()
        
        text = content.decode("utf-8")
        print(f"Success! Read {len(text)} characters.")
        print(text[:200])
        
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(test_read())
