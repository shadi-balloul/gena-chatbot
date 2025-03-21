# /home/shadi2/bmo/code/gena-chatbot/gena-chatbot/app/config.py
import os
from dotenv import load_dotenv

load_dotenv()  # Load environment variables from .env

class Settings:
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY")
    GEMINI_MODEL_NAME: str = os.getenv("GEMINI_MODEL_NAME", "gemini-1.5-pro-002") # Use a single multimodal model
    PDF_PATH: str = os.getenv("PDF_PATH")
    MONGODB_URI: str = os.getenv("MONGODB_URI")
    MONGODB_DB: str = os.getenv("MONGODB_DB")
    MAX_REQUESTS_PER_DAY: int = int(os.getenv("MAX_REQUESTS_PER_DAY", "100"))
    MAX_DURATION_AFTER_LAST_MESSAGE: int = int(os.getenv("MAX_DURATION_AFTER_LAST_MESSAGE", "3600"))  # in seconds
    CACHE_TTL: str = os.getenv("CACHE_TTL", "300s")
    TEMP_AUDIO_DIR: str = os.getenv("TEMP_AUDIO_DIR", "temp_audio") # Still useful for potential future use

settings = Settings()