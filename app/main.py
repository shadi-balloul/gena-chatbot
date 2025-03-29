# F:\bemo\code\backend\gena-chatbot\app\main.py

import asyncio
import pathlib  # <-- Added Import
from fastapi import FastAPI
from fastapi.responses import HTMLResponse # <-- Added Import
from app.routes import conversation_routes, context_cache_routes, chat_session_routes
from app.services.gemini_client import GeminiClient
from fastapi.middleware.cors import CORSMiddleware
from app.config import settings

# --- Global variable to store HTML documentation content ---
# Initialize with a fallback error message
api_documentation_html: str = "<html><body><h1>Error: API Documentation could not be loaded.</h1></body></html>"

app = FastAPI(title="BEMO Bank Chatbot API")

print("Configuring CORS Middleware...")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "https://localhost:3000",
        "http://192.168.201.130:3000",
        "https://192.168.201.130:3000"
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
print("CORS Middleware configured.")

print("Including API Routers...")
app.include_router(conversation_routes.router, prefix="/api", tags=["Conversations"])
app.include_router(context_cache_routes.router, prefix="/api", tags=["Context Cache"])
app.include_router(chat_session_routes.router, prefix="/api", tags=["Chat Sessions"])
print("API Routers included.")

# --- Health Check Endpoint ---
# Changed path from "/" to "/health" to avoid conflict
@app.get("/health", tags=["Health"])
async def health_check():
    """Basic health check endpoint."""
    # Consider adding checks for DB connection, Gemini client status etc. here
    print("Health check endpoint called.")
    return {"status": "ok", "message": "Health check successful!"}

# --- NEW: API Documentation Endpoint ---
@app.get("/documentation", response_class=HTMLResponse, include_in_schema=False, tags=["Documentation"])
async def get_api_documentation():
    """Serves the HTML API documentation page."""
    print("Documentation endpoint called.")
    # Returns the content read during startup
    if "Error: API Documentation could not be loaded" in api_documentation_html or \
       "Error: API Documentation file not found" in api_documentation_html:
         print("Serving documentation error page.")
         # Return the error HTML with a 500 status code
         return HTMLResponse(content=api_documentation_html, status_code=500)
    print("Serving documentation page.")
    return HTMLResponse(content=api_documentation_html)

# --- Startup Event Handler ---
@app.on_event("startup")
async def startup_event():
    """
    Handles application startup tasks:
    1. Load HTML documentation.
    2. Initialize Gemini client and cache.
    3. Start background session cleanup task.
    """
    print("Application startup event initiated...")
    global api_documentation_html # Declare modification of global variable

    # --- 1. Load HTML Documentation ---
    print("Attempting to load API documentation HTML...")
    try:
        # Assumes main.py is in 'app' folder and api-docs.html is in 'docs' folder alongside 'app'
        app_dir = pathlib.Path(__file__).resolve().parent # F:\...\gena-chatbot\app
        project_root = app_dir.parent                  # F:\...\gena-chatbot
        docs_file_path = project_root / "docs" / "api-docs.html"

        print(f"Looking for documentation file at: {docs_file_path}")
        if docs_file_path.is_file():
            with open(docs_file_path, "r", encoding="utf-8") as f:
                api_documentation_html = f.read()
            print("API Documentation HTML loaded successfully.")
        else:
            error_msg = f"ERROR: API Documentation file not found at expected path ({docs_file_path})."
            print(error_msg)
            api_documentation_html = f"<html><body><h1>{error_msg}</h1></body></html>"

    except Exception as e:
        error_msg = f"ERROR: Failed to load API Documentation HTML: {e}"
        print(error_msg)
        api_documentation_html = f"<html><body><h1>{error_msg}</h1></body></html>"

    # --- 2. Initialize Gemini Cache ---
    print("Initializing Gemini Client and Cache...")
    try:
        # Ensure settings are loaded correctly
        if not settings.GEMINI_API_KEY:
             print("WARNING: GEMINI_API_KEY not found in settings.")
             # Decide how to handle this - maybe raise an exception or proceed with limited functionality?

        gemini_client = GeminiClient(file_ext=settings.CACHED_FILE_EXT)
        await gemini_client.initialize_cache()
        print("Gemini Client and Cache Initialized.")
    except Exception as e:
        print(f"FATAL ERROR: Failed to initialize Gemini Client/Cache: {e}")
        # Depending on severity, you might want to prevent the app from fully starting
        # raise RuntimeError(f"Gemini Client initialization failed: {e}") from e


    # --- 3. Start Background Task for Session Cleanup ---
    print("Starting background task for chat session cleanup...")
    asyncio.create_task(cleanup_chat_sessions())
    print("Chat session cleanup task scheduled.")

    print("Application startup sequence finished.")

# --- Background Cleanup Task ---
async def cleanup_chat_sessions():
    """Periodically cleans up expired chat sessions from memory."""
    # Need to import here to potentially avoid circular import issues
    from app.services.chat_session_manager import ChatSessionManager
    print("Background session cleanup task running...")
    # Add a small initial delay to allow server to fully stabilize
    await asyncio.sleep(15)
    while True:
        try:
            # print("Running session cleanup cycle...") # Verbose logging
            active_sessions_before = len(ChatSessionManager._sessions) # Accessing protected member for logging - okay for internal use
            await ChatSessionManager.cleanup_sessions()
            active_sessions_after = len(ChatSessionManager._sessions)
            if active_sessions_before != active_sessions_after:
                 print(f"Session cleanup removed {active_sessions_before - active_sessions_after} sessions.")
            # print("Session cleanup cycle finished.") # Verbose logging

        except Exception as e:
            # Log error but continue the loop
            print(f"ERROR during chat session cleanup: {e}")
            import traceback
            traceback.print_exc() # Print full traceback for debugging

        # Determine sleep interval - shorter is more responsive but uses more resources
        # Example: Check every 5 minutes (300 seconds)
        cleanup_interval = 300
        # Or base it on expiry time, e.g., check every quarter of expiry time
        # cleanup_interval = max(60, settings.MAX_DURATION_AFTER_LAST_MESSAGE // 4)
        await asyncio.sleep(cleanup_interval)


# --- REMOVED Redundant Root Endpoint ---
# @app.get("/")
# def root():
#     return {"message": "Welcome to BEMO Bank Chatbot API"}

# --- Main execution block ---
if __name__ == "__main__":
    import uvicorn
    print("Starting Uvicorn server...")
    # Check if SSL files exist before trying to use them
    key_path = pathlib.Path("key.pem")
    cert_path = pathlib.Path("cert.pem")
    ssl_params = {}
    if key_path.is_file() and cert_path.is_file():
        print("SSL key and cert files found. Starting with HTTPS.")
        ssl_params = {
            "ssl_keyfile": str(key_path),
            "ssl_certfile": str(cert_path)
        }
    else:
        print("SSL key/cert files not found (key.pem, cert.pem). Starting with HTTP.")
        # Fallback to HTTP if SSL files are missing

    uvicorn.run(
        "app.main:app",  # Reference the app instance correctly as string
        host="0.0.0.0",
        port=8100,
        reload=True, # Enable reload for development - REMOVE THIS IN PRODUCTION
        **ssl_params # Unpack SSL parameters if they exist
    )