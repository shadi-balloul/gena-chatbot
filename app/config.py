import os
from dotenv import load_dotenv

load_dotenv()  # Load environment variables from .env

class Settings:
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY")
    GEMINI_MODEL_NAME: str = os.getenv("GEMINI_MODEL_NAME", "gemini-1.5-flash-002")
    PDF_PATH: str = os.getenv("PDF_PATH")
    MD_PATH: str = os.getenv("MD_PATH")  # New: path to markdown file
    MONGODB_URI: str = os.getenv("MONGODB_URI")
    MONGODB_DB: str = os.getenv("MONGODB_DB")
    MAX_REQUESTS_PER_DAY: int = int(os.getenv("MAX_REQUESTS_PER_DAY", "100"))
    MAX_DURATION_AFTER_LAST_MESSAGE: int = int(os.getenv("MAX_DURATION_AFTER_LAST_MESSAGE", "3600"))
    CACHE_TTL: str = os.getenv("CACHE_TTL", "3600s")
    CACHED_FILE_EXT: str = os.getenv("CACHED_FILE_EXT", "pdf")  # New: file extension for cached content

settings = Settings()
