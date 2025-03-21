import asyncio
import json
import pathlib
import os
import time
from datetime import datetime, timezone
from google import genai
from google.genai import types
from app.config import settings
import PyPDF2  # Ensure PyPDF2 is in your requirements

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

def extract_text_from_md(md_path: pathlib.Path) -> str:
    with md_path.open("r", encoding="utf-8") as f:
        return f.read()

class GeminiClient:
    _instance = None

    def __new__(cls, file_ext: str = None, md_path: str = None):
        if cls._instance is None:
            cls._instance = super(GeminiClient, cls).__new__(cls)
        return cls._instance

    def __init__(self, file_ext: str = None, md_path: str = None):
        # Allow reinitialization parameters on first creation only.
        if hasattr(self, "initialized") and self.initialized:
            return

        # Read file extension and markdown path from either the arguments or settings.
        self.file_ext = file_ext if file_ext is not None else settings.CACHED_FILE_EXT
        self.md_path = md_path if md_path is not None else settings.MD_PATH

        self.client = genai.Client(api_key=settings.GEMINI_API_KEY)
        self.cache = None
        self.initialized = True

    async def initialize_cache(self):
        # Step 1. Try to load existing cache metadata from file.
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
                # Check if the cached content is still valid.
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

        # Step 2. No valid cache found; create a new cache.
        def load_file_text():
            if self.file_ext.lower() == "pdf":
                file_path = pathlib.Path(settings.PDF_PATH)
                if not file_path.exists():
                    raise FileNotFoundError(f"PDF file not found: {file_path}")
                return extract_text_from_pdf(file_path)
            elif self.file_ext.lower() == "md":
                file_path = pathlib.Path(self.md_path)
                if not file_path.exists():
                    raise FileNotFoundError(f"Markdown file not found: {file_path}")
                return extract_text_from_md(file_path)
            else:
                raise ValueError("Unsupported file extension for cached content")

        file_text = await asyncio.to_thread(load_file_text)

        def create_cache():
            return self.client.caches.create(
                model=settings.GEMINI_MODEL_NAME,
                config=types.CreateCachedContentConfig(
                    display_name='BEMO Bank Information',
                    system_instruction=(
                        "You are a helpful chatbot for BEMO bank, answering questions "
                        "based on the provided document in the context cache about the bank's products and services."
                        "The data in the cache context is written in Markdown format. Use the Markdown syntax to "
                        "understand the context and provide accurate answers to the user's questions."
                        "Use the headings such as # and ## to understand the sections and to relate the information in the cached data"
                        "You also should use the lists to understand the information in the cached data. It is very important to understand the information in the nested lists and create the most accurate answer"
                        "The content is written in Arabic, and there are many FAQs in the cached data. You should answer the questions in Arabic"
                        "The clients of the bank may ask questions that are not identical to the FAQs in the cached data."
                        "You should be able to analyze the questions and answers in the FAGs"
                        "In the cached data, The FAQs sections are named in Arabic As: "
                        "الأسئلة الشائعة أو الأسئلة الشائعة والمتكررة"
                        "ٍSee this example of a question and answer in the cached data:"
                        "السؤال: ماهي مدة صلاحية كلمة المرور الخاصة بالتطبيق أو الموقع الالكتروني؟  "
                        "الجواب:  "
                        "إن مدة صلاحية كلمة المرور هي 90 يوم وينصح بتغييرها بشكل دوري."
                        "In the cached data, There are directionss on how to answers on the clients questions in some situations"
                        "The direction section starts by this heading and title:"
                        "# توجيهات للإجابة في حالات ومواقف متنوعة عند استفسار الزبون"
                        "Find below an example of a situation and direction in the cached data:"
                        "الموقف: عند تقديم العميل شكوى؟ "
                        "الجواب:    "
                        "العميل العزيز ، سوف يتم مراسلتكم عبر بريد الصفحة الرسمية ليتم معرفة تفاصيل الشكوى ومتابعتها بالشكل الأمثل، وشكراً."
                        "I want  concise, clear and accurate answers to the questions asked by the clients"
                        "Try not to exceed 100 words in your answer"
                        "If the questions of the bank clients are not about the context and not about the bank products and services, Tell him that you cannot answer questions that are not related to BEMO bank"
                        "Let the client feel that he chats with a human and not a machine"
                        "In the end of each message write the following:"
                        "مساعد بنك بيمو الرقمي"
                        
                        
                        
                    ),
                    contents=[file_text],
                    ttl=settings.CACHE_TTL,
                )
            )
        self.cache = await asyncio.to_thread(create_cache)
        print(f"Created new cache: {self.cache.name}")

        # Step 3. Save cache metadata to file.
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
        if not self.cache:
            raise ValueError("No cached content found. Ensure cache is initialized.")
        try:
            return self.client.chats.create(
                model=settings.GEMINI_MODEL_NAME,
                config=types.GenerateContentConfig(
                    cached_content=self.cache.name
                )
            )
        except Exception as e:
            print(f"Failed to create chat: {e}")
            return None

    async def send_message(self, chat_session, message):
        if not chat_session.chat:
            raise ValueError("Chat session is not initialized properly.")

        def _send():
            return chat_session.chat.send_message(message)

        response = await asyncio.to_thread(_send)
        prompt_tokens = response.usage_metadata.prompt_token_count if hasattr(response.usage_metadata, "prompt_token_count") else 0
        response_tokens = response.usage_metadata.candidates_token_count if hasattr(response.usage_metadata, "candidates_token_count") else 0
        total_tokens = response.usage_metadata.total_token_count if hasattr(response.usage_metadata, "total_token_count") else prompt_tokens + response_tokens

        print(f"Token Usage - Prompt: {prompt_tokens}, Response: {response_tokens}, Total: {total_tokens}")
        return response, prompt_tokens, response_tokens, total_tokens