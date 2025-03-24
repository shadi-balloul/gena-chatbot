import asyncio
from fastapi import FastAPI
from app.routes import conversation_routes, context_cache_routes, chat_session_routes
from app.services.gemini_client import GeminiClient
from fastapi.middleware.cors import CORSMiddleware
from app.config import settings
from app.middlewares.request_logging import RequestLoggingMiddleware

# Add this to your FastAPI app setup


app = FastAPI(title="BEMO Bank Chatbot API")

app.add_middleware(RequestLoggingMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "https://localhost:3000",
        "https://127.0.0.1:3000",
        "http://192.168.201.130:3000",
        "https://192.168.201.130:3000"
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(conversation_routes.router, prefix="/api")
app.include_router(context_cache_routes.router, prefix="/api") 
app.include_router(chat_session_routes.router, prefix="/api")

@app.get("/")
async def health_check():
    return "The health check is successful!"

@app.on_event("startup")
async def startup_event():
    # Set up HTTP request logging
    import logging
    import http.client
    
    # Enable HTTP connection debugging
    http.client.HTTPConnection.debuglevel = 1
    
    # Configure requests logging
    requests_log = logging.getLogger("urllib3")
    requests_log.setLevel(logging.DEBUG)
    requests_log.propagate = True
    
    # Initialize the Gemini cache from the PDF at server startup.
    gemini_client = GeminiClient(file_ext=settings.CACHED_FILE_EXT)
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

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
        ssl_keyfile="key.pem",
        ssl_certfile="cert.pem"
    )

