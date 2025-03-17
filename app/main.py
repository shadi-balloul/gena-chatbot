import asyncio
from fastapi import FastAPI
from app.routes import conversation_routes, context_cache_routes, chat_session_routes
from app.services.gemini_client import GeminiClient
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="BEMO Bank Chatbot API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],  # ✅ Allow requests from Next.js frontend
    allow_credentials=True,
    allow_methods=["*"],  # ✅ Allow all HTTP methods (GET, POST, etc.)
    allow_headers=["*"],  # ✅ Allow all headers
)

app.include_router(conversation_routes.router, prefix="/api")
app.include_router(context_cache_routes.router, prefix="/api") 
app.include_router(chat_session_routes.router, prefix="/api")

@app.on_event("startup")
async def startup_event():
    # Initialize the Gemini cache from the PDF at server startup.
    gemini_client = GeminiClient()
    await gemini_client.initialize_cache()
    # Start a background task to periodically clean up expired chat sessions.
    asyncio.create_task(cleanup_chat_sessions())

async def cleanup_chat_sessions():
    from app.services.chat_session_manager import ChatSessionManager
    while True:
        await ChatSessionManager.cleanup_sessions()
        await asyncio.sleep(60)  # cleanup interval

@app.get("/")
def root():
    return {"message": "Welcome to BEMO Bank Chatbot API"}
