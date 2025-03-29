# F:\bemo\code\backend\gena-chatbot\app\routes\conversation_routes.py
# (Keep existing imports)
from fastapi import APIRouter, HTTPException, Query, UploadFile, File, Form
from fastapi.responses import FileResponse
from datetime import datetime
from bson import ObjectId
from typing import List, Optional
from app.models import Conversation, Message
from app.services.mongodb import MongoDBClient
from app.services.chat_session_manager import ChatSessionManager, ChatSession # Ensure ChatSession is imported if needed elsewhere, maybe not directly here
from app.services.gemini_client import GeminiClient
import os
import time
from app.config import settings
# No longer need 'types' from google.genai here for VTT
# from google.genai import types

router = APIRouter()
db = MongoDBClient.get_database()

# Define the base directory for audio files
AUDIO_FILES_BASE_DIR = "audio_files"
os.makedirs(AUDIO_FILES_BASE_DIR, exist_ok=True)

# --- Keep create_conversation as previously defined ---
@router.post("/conversations", response_model=Conversation)
async def create_conversation(conversation: Conversation):
    # (Logic remains the same as in the previous good version)
    # ... (ensure user session check, DB retrieval, new conversation creation, session creation in RAM) ...
    existing_session = ChatSessionManager.get_session(conversation.user_id)

    if existing_session:
        try:
            existing_conversation_id = ObjectId(existing_session.conversation_id)
        except Exception:
             print(f"Warning: Invalid conversation ID '{existing_session.conversation_id}' in session for user '{conversation.user_id}'. Creating new.")
             existing_conversation = None
        else:
             existing_conversation = await db.conversations.find_one({"_id": existing_conversation_id})

        if existing_conversation:
            if existing_conversation.get("user_id") == conversation.user_id: # Add user check
                existing_conversation["id"] = str(existing_conversation["_id"])
                try:
                     # Add check for messages field existence if necessary
                     if "messages" not in existing_conversation:
                         existing_conversation["messages"] = []
                     return Conversation(**existing_conversation)
                except Exception as e:
                     print(f"Error validating existing conversation data: {e}. Creating new.")
            else:
                 # This case indicates a potential issue, maybe remove the bad session?
                 print(f"Warning: Session user ID mismatch. Session user: {conversation.user_id}, Conversation owner: {existing_conversation.get('user_id')}. Creating new conversation.")
                 ChatSessionManager.remove_session(conversation.user_id) # Remove potentially incorrect session


    # Create a new conversation
    conversation.start_time = datetime.utcnow()
    conversation.last_message_time = conversation.start_time
    conversation.messages = []

    insert_data = conversation.model_dump(by_alias=True, exclude=["id"]) # Exclude 'id' Pydantic field

    result = await db.conversations.insert_one(insert_data)
    conversation.id = str(result.inserted_id) # Assign the generated _id as string to id

    # Create RAM session AFTER successful DB insertion
    ChatSessionManager.create_session(conversation.user_id, conversation.id)

    return conversation


# --- Modified send_message endpoint ---
@router.post("/conversations/{conversation_id}/messages", response_model=Message)
async def send_message(
    conversation_id: str,
    user_id: str = Form(...),
    type: str = Form(...),             # "text" or "audio"
    message: str = Form(...),           # ALWAYS required (text content or transcription)
    audio: Optional[UploadFile] = File(None) # Required ONLY if type is "audio"
):
    """
    Sends a user message (text content required) to the chatbot.
    If type is "audio", also saves the uploaded audio file.

    Handles multipart/form-data requests.

    - Requires 'user_id', 'type', and 'message' form fields always.
    - If type="audio", requires the 'audio' file upload field as well.
      The backend only saves this file, it does NOT perform STT.
    - The 'message' field content is sent to the Gemini chat model.

    Returns the model's response message.
    """
    message_type = type.lower()
    audio_file_path: Optional[str] = None # Store filename only

    # --- Input Validation ---
    if not message: # Message text is always required now
         raise HTTPException(status_code=400, detail="Form field 'message' is required.")

    if message_type == "audio":
        if not audio:
            raise HTTPException(status_code=400, detail="File upload 'audio' is required when type is 'audio'.")
    elif message_type == "text":
        if audio:
            # Optional: Log a warning or just ignore the unexpected file. Ignoring is simpler.
            print(f"Warning: Received audio file upload for type 'text' from user {user_id}. Ignoring file.")
            # To be strict, you could raise an error:
            # raise HTTPException(status_code=400, detail="File upload 'audio' should not be provided when type is 'text'.")
    else:
        raise HTTPException(status_code=400, detail="Invalid type specified. Must be 'text' or 'audio'.")

    # --- Database and Conversation Check ---
    try:
        conv_obj_id = ObjectId(conversation_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid conversation_id format.")

    conversation = await db.conversations.find_one({"_id": conv_obj_id})
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    if conversation.get("user_id") != user_id:
         raise HTTPException(status_code=403, detail="User not authorized for this conversation.")

    # Determine the user message index
    message_index = len(conversation.get("messages", [])) + 1

    # --- Save Audio File (if applicable) ---
    if message_type == "audio" and audio:
        try:
            audio_bytes = await audio.read()
            if not audio_bytes:
                # Handle empty file upload if necessary
                print(f"Warning: Received empty audio file for user {user_id}, conversation {conversation_id}.")
                # Decide if this is an error or just proceed without saving path
                # raise HTTPException(status_code=400, detail="Received empty audio file.")
                audio = None # Treat as if no file was sent if it's empty

        except Exception as e:
             print(f"Error reading audio file for user {user_id}: {e}")
             # Decide how to handle - proceed without saving? Return error?
             raise HTTPException(status_code=500, detail=f"Failed to read uploaded audio file: {e}")

        if audio: # Proceed only if audio object exists and wasn't empty
            dir_path = os.path.join(AUDIO_FILES_BASE_DIR, user_id, conversation_id)
            os.makedirs(dir_path, exist_ok=True)

            ts_str = datetime.utcnow().strftime("%Y%m%d%H%M%S")
            original_filename = audio.filename if audio.filename else "audio"
            ext = os.path.splitext(original_filename)[1].lower() if '.' in original_filename else ".wav"
            # Basic extension check/sanitization (optional)
            allowed_exts = ['.wav', '.mp3', '.ogg', '.m4a', '.aac', '.flac'] # Example list
            if ext not in allowed_exts:
                print(f"Warning: Received audio file with potentially unsupported extension '{ext}'. Saving as '.wav'.")
                ext = ".wav"

            filename = f"{ts_str}_{message_index}{ext}"
            file_path_on_disk = os.path.join(dir_path, filename)
            audio_file_path = filename # Store only the filename in DB

            try:
                with open(file_path_on_disk, "wb") as f:
                    f.write(audio_bytes)
                print(f"Audio file saved: {file_path_on_disk}")
            except Exception as e:
                 print(f"Error saving audio file to disk: {e}")
                 # Failed to save, ensure path is None so DB doesn't point to non-existent file
                 audio_file_path = None
                 # Optionally raise an error, but maybe the chat can continue without the saved audio?
                 # raise HTTPException(status_code=500, detail=f"Failed to save audio file: {e}")


    # --- Chat Session Management ---
    chat_session = ChatSessionManager.get_session(user_id)
    if not chat_session or chat_session.conversation_id != conversation_id:
        # If session doesn't exist, or points to a different conversation,
        # (re)create it for the *current* conversation.
        # This implicitly handles the case where create_conversation found an old session
        # but the user is now interacting with a *different* (newly created or other) conversation.
        if chat_session:
             print(f"Warning: Active session for user {user_id} points to different conversation ({chat_session.conversation_id}). Re-creating for {conversation_id}.")
             ChatSessionManager.remove_session(user_id) # Remove old session

        print(f"Creating/Re-creating session for user {user_id}, conversation {conversation_id}")
        # We need the GeminiClient instance to create the chat object within the session
        gemini_client_instance = GeminiClient()
        if not gemini_client_instance.cache:
             # This should ideally not happen if startup logic is correct, but handle it.
             print("Error: Gemini cache not initialized. Cannot create chat session.")
             raise HTTPException(status_code=500, detail="Chat service not ready (cache missing).")
        try:
             chat_session = ChatSessionManager.create_session(user_id, conversation_id)
        except Exception as e:
             print(f"Error creating chat session: {e}")
             raise HTTPException(status_code=500, detail="Failed to create chat session.")

    # Check request limits *before* sending to Gemini
    if chat_session.request_count >= settings.MAX_REQUESTS_PER_DAY:
         raise HTTPException(status_code=429, detail="Maximum daily requests reached for this session.")

    chat_session.increment_request_count()
    chat_session.update_last_message_time()

    # --- Prepare and Save User Message ---
    user_message = Message(
        role="user",
        content=message, # Use the text from the 'message' form field
        timestamp=datetime.utcnow(),
        type=message_type,
        audio_file_path=audio_file_path, # filename or None
        message_index=message_index
    )
    user_message_dict = user_message.model_dump(exclude_none=True)

    await db.conversations.update_one(
        {"_id": conv_obj_id},
        {
            "$push": {"messages": user_message_dict},
            "$set": {"last_message_time": user_message.timestamp}
        }
    )

    # --- Send to Gemini Chat Model ---
    gemini_client = GeminiClient() # Get singleton instance
    try:
        # Send the 'message' text received from the frontend
        response_chat, prompt_tokens, response_tokens, total_tokens = await gemini_client.send_message(
            chat_session, message
        )

        print(f"Gemini Response (snippet): {response_chat.text[:100]}...")
        print(f"Token Usage - Prompt: {prompt_tokens}, Response: {response_tokens}, Total: {total_tokens}")

    except Exception as e:
         print(f"Error sending message to Gemini: {e}")
         # Consider specific error handling for Gemini API errors vs internal errors
         import traceback
         traceback.print_exc()
         raise HTTPException(status_code=500, detail=f"Failed to get response from AI model: {e}")

    # --- Prepare and Save Model Response ---
    model_message = Message(
        role="model",
        content=response_chat.text,
        timestamp=datetime.utcnow(),
        token_count=response_tokens, # Storing response tokens specifically
        type=message_type,
        audio_file_path=None,
        message_index=message_index + 1,
        sent_message_index=message_index # Link to the user message index
    )
    model_message_dict = model_message.model_dump(exclude_none=True)

    # Save the model's response and update token counts
    # Ensure the token count fields exist in the document, initialize if necessary during creation
    # Or use $setOnInsert in the initial conversation creation if needed.
    await db.conversations.update_one(
        {"_id": conv_obj_id},
        {
            "$push": {"messages": model_message_dict},
            "$set": {"last_message_time": model_message.timestamp},
            "$inc": {
                # Initialize fields if they don't exist? Safer to ensure they exist.
                "total_prompt_tokens": prompt_tokens,
                "total_response_tokens": response_tokens,
                "total_token_count": total_tokens
            }
        },
        # upsert=False # Should not upsert here
    )

    # Return the model's response
    return model_message


# --- Keep get_conversations as previously defined ---
@router.get("/conversations/user/{user_id}", response_model=list[Conversation])
async def get_conversations(user_id: str):
    cursor = db.conversations.find({"user_id": user_id}).sort("start_time", -1) # Optional: sort by newest first
    conversations = []
    async for conv in cursor:
        # Ensure _id exists before processing
        if "_id" not in conv:
            print(f"Warning: Document found for user {user_id} without an '_id'. Skipping.")
            continue

        # 1. Convert ObjectId to string and assign to 'id'
        conv["id"] = str(conv["_id"])

        # 2. Remove the original '_id' field to avoid alias conflict
        del conv["_id"]

        # Ensure messages list exists for validation
        if "messages" not in conv:
            conv["messages"] = []

        try:
             # Now 'conv' has 'id' as a string and no '_id' key.
             # Pydantic will correctly populate the 'id' field using the 'id' key.
             conversations.append(Conversation(**conv))
        except Exception as e:
             # Log the specific id that failed if possible
             failed_id = conv.get("id", "UNKNOWN")
             print(f"Warning: Skipping conversation with id {failed_id} for user {user_id} due to validation error: {e}")
             # Optional: Log the problematic dictionary for deeper debugging
             # print(f"Problematic data for id {failed_id}: {conv}")

    return conversations


# --- Keep get_conversation_history as previously defined ---
@router.get("/conversations/{conversation_id}/history", response_model=list[Message])
async def get_conversation_history(conversation_id: str, user_id: str = Query(...)):
    # (Logic remains the same - find by ID and user, return messages, handle not found)
    try:
        conv_obj_id = ObjectId(conversation_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid conversation_id format.")

    conversation = await db.conversations.find_one({"_id": conv_obj_id, "user_id": user_id})
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found or user mismatch.")

    messages_data = conversation.get("messages", [])
    try:
         # Validate each message dict if needed, or trust data if saved correctly
         return [Message(**msg) for msg in messages_data]
    except Exception as e:
         print(f"Error validating message history for conversation {conversation_id}: {e}")
         raise HTTPException(status_code=500, detail="Error processing conversation history.")


# --- Keep get_conversation_token_stats as previously defined ---
@router.get("/conversations/{conversation_id}/token-stats")
async def get_conversation_token_stats(conversation_id: str, user_id: str = Query(...)):
    # (Logic remains the same)
    try:
        conv_obj_id = ObjectId(conversation_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid conversation_id format.")

    conversation = await db.conversations.find_one({"_id": conv_obj_id, "user_id": user_id})
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found or user mismatch.")

    total_prompt_tokens = conversation.get("total_prompt_tokens", 0)
    total_response_tokens = conversation.get("total_response_tokens", 0)
    total_token_count = conversation.get("total_token_count", 0) # Ensure this correctly reflects sum or is stored separately
    message_count = len(conversation.get("messages", []))

    # Recalculate total_token_count if it's not explicitly stored/updated correctly
    # calculated_total = total_prompt_tokens + total_response_tokens
    # if total_token_count != calculated_total:
    #      print(f"Warning: Stored total_token_count ({total_token_count}) differs from sum ({calculated_total}) for conv {conversation_id}")
         # Decide whether to return stored or calculated

    return {
        "conversation_id": conversation_id,
        "user_id": user_id,
        "total_user_tokens": total_prompt_tokens, # Renaming for clarity based on previous response example
        "total_model_tokens": total_response_tokens, # Renaming for clarity
        "total_tokens": total_token_count,
        "message_count": message_count
    }


# --- Keep get_audio_file as previously defined ---
@router.get("/conversations/{conversation_id}/audio/{filename}")
async def get_audio_file(conversation_id: str, filename: str, user_id: str = Query(...)):
    # (Logic remains the same - construct path, check existence, return FileResponse)
    # Add validation for filename
    if ".." in filename or "/" in filename or "\\" in filename:
         raise HTTPException(status_code=400, detail="Invalid filename.")

    file_path = os.path.join(AUDIO_FILES_BASE_DIR, user_id, conversation_id, filename)

    if not os.path.exists(file_path) or not os.path.isfile(file_path):
        raise HTTPException(status_code=404, detail="Audio file not found.")

    # Determine mime type (simple version)
    ext = os.path.splitext(filename)[1].lower()
    media_type = "application/octet-stream" # Default
    if ext == ".wav": media_type = "audio/wav"
    elif ext == ".mp3": media_type = "audio/mpeg"
    elif ext == ".ogg": media_type = "audio/ogg"
    elif ext == ".m4a": media_type = "audio/mp4"
    # Add others as needed

    return FileResponse(file_path, media_type=media_type, filename=filename)