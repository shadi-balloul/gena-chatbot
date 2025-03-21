# /home/shadi2/bmo/code/gena-chatbot/gena-chatbot/app/services/gemini_client.py
import asyncio
import json
import pathlib
import os
import time
from datetime import datetime, timezone
from google import genai
from google.genai import types
from app.config import settings
import PyPDF2

CACHE_METADATA_FILE = "cache_metadata.json"

def extract_text_from_pdf(pdf_path: pathlib.Path) -> str:
    with pdf_path.open("rb") as f:
        reader = PyPDF2.PdfReader(f)
        text = ""
        for page in reader.pages:
            page_text = page.extract_text()
            if page_text:
                text += page_text + "\n"
    return text

class GeminiClient:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(GeminiClient, cls).__new__(cls)
        return cls._instance

    def __init__(self):
        if hasattr(self, "initialized") and self.initialized:
            return
        self.client = genai.Client(api_key=settings.GEMINI_API_KEY)
        self.cache = None
        self.initialized = True
        # Use a single, multimodal model (e.g., gemini-1.5-pro)
        self.model = self.client.models.get(model=settings.GEMINI_MODEL_NAME)


    async def initialize_cache(self):
        cache_metadata = None
        if os.path.exists(CACHE_METADATA_FILE):
            try:
                with open(CACHE_METADATA_FILE, "r") as f:
                    cache_metadata = json.load(f)
            except Exception as e:
                print("Error reading cache metadata file:", e)

        now = datetime.now(timezone.utc)
        if cache_metadata:
            try:
                expire_time = datetime.fromisoformat(cache_metadata["expire_time"])
                if expire_time > now:
                    try:
                        cache_obj = self.client.caches.get(name=cache_metadata["name"])
                        print(f"Using existing cache: {cache_obj.name}")
                        self.cache = cache_obj
                        return self.cache
                    except Exception as e:
                        print("Error retrieving cache by name:", e)
                else:
                    print("Cached content expired.")
            except Exception as e:
                print("Error processing cache metadata:", e)

        def load_pdf_text():
            pdf_path = pathlib.Path(settings.PDF_PATH)
            if not pdf_path.exists():
                raise FileNotFoundError(f"PDF file not found: {pdf_path}")
            return extract_text_from_pdf(pdf_path)

        pdf_text = await asyncio.to_thread(load_pdf_text)

        def create_cache():
            return self.client.caches.create(
                model=settings.GEMINI_MODEL_NAME,  # Use the multimodal model here
                config=types.CreateCachedContentConfig(
                    display_name='BEMO Bank Information',
                    system_instruction=(
                        "You are a helpful chatbot for BEMO bank, answering questions "
                        "based on the provided document about the bank's products and services."
                    ),
                    contents=[pdf_text],
                    ttl=settings.CACHE_TTL,
                )
            )
        self.cache = await asyncio.to_thread(create_cache)
        print(f"Created new cache: {self.cache.name}")

        metadata = {
            "name": self.cache.name,
            "model": self.cache.model,
            "display_name": self.cache.display_name,
            "create_time": str(self.cache.create_time),
            "update_time": str(self.cache.update_time),
            "expire_time": str(self.cache.expire_time)
        }
        try:
            with open(CACHE_METADATA_FILE, "w") as f:
                json.dump(metadata, f, indent=4)
            print("Cache metadata saved to", CACHE_METADATA_FILE)
        except Exception as e:
            print("Error writing cache metadata file:", e)

        return self.cache

    def create_chat(self):
        """Creates a chat session using the single multimodal model."""
        if not self.cache:
              raise ValueError("No cached content found. Ensure cache is initialized.")

        try:
            #  Use cached content for the multimodal model
            return self.client.chats.create(
                model=settings.GEMINI_MODEL_NAME,
                config=types.GenerateContentConfig(
                    cached_content=self.cache.name
                )
            )

        except Exception as e:
            print(f"Failed to create chat: {e}")
            return None

    async def _send_any_message(self, chat_session, content_parts):
        """Internal helper to send messages (handles both text and audio)."""
        if not chat_session.chat:
            raise ValueError("Chat session is not initialized properly.")

        def _send():
            return chat_session.chat.send_message(content_parts)

        response = await asyncio.to_thread(_send)

        # Extract token counts from `usage_metadata`
        prompt_tokens = response.usage_metadata.prompt_token_count if hasattr(response.usage_metadata, "prompt_token_count") else 0
        response_tokens = response.usage_metadata.candidates_token_count if hasattr(response.usage_metadata, "candidates_token_count") else 0
        total_tokens = response.usage_metadata.total_token_count if hasattr(response.usage_metadata, "total_token_count") else prompt_tokens + response_tokens

        print(f"Token Usage - Prompt: {prompt_tokens}, Response: {response_tokens}, Total: {total_tokens}")
        return response, prompt_tokens, response_tokens, total_tokens


    async def send_message(self, chat_session, message_text):
        """Sends a text message."""
        return await self._send_any_message(chat_session, [message_text])



    async def send_audio_message(self, chat_session, audio_data: bytes, file_name: str):
        """Sends an audio message directly (no transcription)."""
        audio_part = {
            "mime_type": "audio/wav",  # VERY IMPORTANT: Set the correct MIME type
            "data": audio_data
        }
        # Include a text prompt along with the audio
        text_part = f"Respond to the following audio message. file name is: {file_name}"

        return await self._send_any_message(chat_session, [text_part, audio_part])