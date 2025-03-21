# /home/shadi2/bmo/code/gena-chatbot/gena-chatbot/app/routes/context_cache_routes.py
import asyncio
from fastapi import APIRouter, HTTPException, status
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
    def list_caches():
        return gemini_client.client.caches.list()

    caches_list = await asyncio.to_thread(list_caches)

    if not caches_list:
        raise HTTPException(status_code=404, detail="No cached contents found.")

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


@router.delete("/context-cache", status_code=status.HTTP_204_NO_CONTENT)
async def delete_all_caches():
    """Deletes all Gemini caches."""
    gemini_client = GeminiClient()

    def _delete_all():
        for cache in gemini_client.client.caches.list():
            gemini_client.client.caches.delete(name=cache.name)

    try:
        await asyncio.to_thread(_delete_all)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error deleting caches: {e}",
        )