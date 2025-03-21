import os
import asyncio
from fastapi import APIRouter, HTTPException
from typing import List
from app.models import ContextCacheInfo
from app.services.gemini_client import GeminiClient
from app.config import settings

router = APIRouter()

@router.get("/context-cache/info", response_model=ContextCacheInfo)
async def get_context_cache_info():
    gemini_client = GeminiClient()
    if not gemini_client.cache:
        raise HTTPException(status_code=404, detail="No cached content found.")
    cache = gemini_client.cache
    return ContextCacheInfo(
        name=cache.name,
        model=cache.model,
        display_name=cache.display_name,
        create_time=str(cache.create_time),
        update_time=str(cache.update_time),
        expire_time=str(cache.expire_time),
    )

@router.get("/context-cache/list", response_model=List[ContextCacheInfo])
async def list_context_caches():
    gemini_client = GeminiClient()
    # Call the caches.list() method using the underlying client.
    def list_caches():
        # List caches for the specified model. Adjust this if your API requires different parameters.
        return gemini_client.client.caches.list()
    
    caches_list = await asyncio.to_thread(list_caches)
    
    if not caches_list:
        raise HTTPException(status_code=404, detail="No cached contents found.")
    
    # Convert each cache object to our Pydantic model.
    result = []
    for cache in caches_list:
        result.append(
            ContextCacheInfo(
                name=cache.name,
                model=cache.model,
                display_name=cache.display_name,
                create_time=str(cache.create_time),
                update_time=str(cache.update_time),
                expire_time=str(cache.expire_time),
            )
        )
    return result

@router.delete("/context-cache")
async def delete_all_caches():
    gemini_client = GeminiClient()
    
    def list_and_delete():
        # List all cached content metadata
        caches_list = gemini_client.client.caches.list()
        deleted_count = 0
        for cache in caches_list:
            try:
                # Delete each cache using its name
                gemini_client.client.caches.delete(name=cache.name)
                deleted_count += 1
            except Exception as e:
                print(f"Error deleting cache {cache.name}: {e}")
        return deleted_count

    deleted_count = await asyncio.to_thread(list_and_delete)
    
    # Optionally remove local metadata file if it exists
    # if os.path.exists(CACHE_METADATA_FILE):
    #    os.remove(CACHE_METADATA_FILE)
    
    return {"message": f"Deleted {deleted_count} cache object(s)."}