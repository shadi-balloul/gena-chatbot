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
