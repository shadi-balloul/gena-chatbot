from fastapi import APIRouter, HTTPException, Query, UploadFile, File, Form
from datetime import datetime
from bson import ObjectId
from typing import List
from app.models import Conversation, Message
from app.services.mongodb import MongoDBClient
from app.services.chat_session_manager import ChatSessionManager, ChatSession
from app.services.gemini_client import GeminiClient
import os
import time
from fastapi.responses import FileResponse
from app.config import settings
from google.genai import types


router = APIRouter()
db = MongoDBClient.get_database()

AUDIO_FILES_BASE_DIR = "audio_files"

@router.post("/conversations", response_model=Conversation)
async def create_conversation(conversation: Conversation):
    # ✅ Check if the user already has an active session in RAM
    existing_session = ChatSessionManager.get_session(conversation.user_id)
    
    if existing_session:
        # ✅ Retrieve the existing conversation from MongoDB
        existing_conversation = await db.conversations.find_one({"_id": ObjectId(existing_session.conversation_id)})
        if existing_conversation:
            # Convert `_id` from ObjectId to string and remove `_id` to avoid validation issues
            existing_conversation["id"] = str(existing_conversation["_id"])
            del existing_conversation["_id"]

            return Conversation(**existing_conversation)  

    # ✅ If no active session, create a new conversation
    conversation.start_time = datetime.utcnow()
    conversation.last_message_time = conversation.start_time
    conversation.messages = []  # Ensure messages are initialized

    # ✅ Insert the conversation into MongoDB
    result = await db.conversations.insert_one(conversation.dict(by_alias=True, exclude_none=True))
    
    # ✅ Convert ObjectId to string for JSON response
    conversation.id = str(result.inserted_id)
    
    # ✅ Create a chat session in RAM
    ChatSessionManager.create_session(conversation.user_id, conversation.id)
    
    return conversation


    """
    Sends a user message to the chatbot.

    If type is "text":
      - The API expects a non-empty 'message' field.
      - The message is handled as a normal text message.

    If type is "audio":
      - The API expects an audio file in the 'audio' field.
      - The API sends the audio bytes to a dedicated voice-to-text Gemini model 
        (specified by VOICE_TO_TEXT_MODEL in .env) to extract text.
      - The extracted text is then used as the message content.
      - The audio file is stored on disk under audio_files/{user_id}/{conversation_id}/,
        with a filename composed of the UTC timestamp and the message index.
      - The audio_file_path field of the user message is updated with the stored path.

    In both cases, the message is sent to the chat model, and the model response is returned,
    including an extra field 'sent_message_index' with the user message index.
    """
    type = type.lower()
    # Determine message content based on type.
    if type == "text":
        if not message:
            raise HTTPException(status_code=400, detail="For text messages, 'message' field must not be empty.")
        message_text = message
    elif type == "audio":
        if audio is None:
            raise HTTPException(status_code=400, detail="For audio messages, an audio file must be provided.")
        # Read audio bytes.
        audio_bytes = await audio.read()
        # Use the dedicated voice-to-text model to extract text.
        voice_client = GeminiClient()  # You can create a separate client instance if desired.
        try:
            response_vtt = voice_client.client.models.generate_content(
                model=settings.VOICE_TO_TEXT_MODEL,
                contents=[
                    "Extract the text from this audio:",
                    types.Part.from_bytes(
                        data=audio_bytes,
                        mime_type=audio.content_type  # e.g. "audio/mp3"
                    )
                ]
            )
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Voice-to-text conversion failed: {e}")
        extracted_text = response_vtt.text.strip()
        if not extracted_text:
            raise HTTPException(status_code=500, detail="No text extracted from the audio.")
        message_text = extracted_text
    else:
        raise HTTPException(status_code=400, detail="Invalid type. Must be 'text' or 'audio'.")

    # Retrieve conversation from MongoDB.
    conversation = await db.conversations.find_one({"_id": ObjectId(conversation_id)})
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found.")

    # Determine the user message index (based on count of messages) and store it.
    message_index = len(conversation.get("messages", [])) + 1

    # Get or create a chat session.
    chat_session = ChatSessionManager.get_session(user_id)
    if not chat_session:
        chat_session = ChatSessionManager.create_session(user_id, conversation_id)
    chat_session.increment_request_count()
    chat_session.update_last_message_time()

    # Prepare the user message record.
    user_message = {
        "role": "user",
        "content": message_text,
        "timestamp": datetime.utcnow(),
        "type": type,
        "audio_file_path": None,  # To be updated later if type is audio.
        "message_index": message_index
    }

    # Save the user message into MongoDB.
    await db.conversations.update_one(
        {"_id": ObjectId(conversation_id)},
        {"$push": {"messages": user_message}, "$set": {"last_message_time": datetime.utcnow()}}
    )

    # If the message is audio, store the audio file and update the record.
    if type == "audio":
        base_dir = "audio_files"
        dir_path = os.path.join(base_dir, user_id, conversation_id)
        os.makedirs(dir_path, exist_ok=True)
        ts_str = datetime.utcnow().strftime("%Y%m%d%H%M%S")
        ext = os.path.splitext(audio.filename)[1] if audio.filename and os.path.splitext(audio.filename)[1] else ".wav"
        filename = f"{ts_str}_{message_index}{ext}"
        file_path = os.path.join(dir_path, filename)
        # Save the audio file using the already-read audio_bytes.
        with open(file_path, "wb") as f:
            f.write(audio_bytes)
        print(f"file_path: {file_path}")
        # Update the specific message record with the audio_file_path.
        await db.conversations.update_one(
            {"_id": ObjectId(conversation_id)},
            {"$set": {"messages.$[elem].audio_file_path": file_path}},
            array_filters=[{"elem.message_index": message_index, "elem.type": "audio"}]
        )

    # Asynchronously send the (extracted) text to the chat model.
    gemini_client = GeminiClient()
    response_chat, prompt_tokens, response_tokens, total_tokens = await gemini_client.send_message(chat_session, message_text)

    print(f"Token Usage - Prompt: {prompt_tokens}, Response: {response_tokens}, Total: {total_tokens}")

    # Prepare the model's response message. 
    # Assume the model's response message index is user_message_index + 1.
    model_message = {
        "role": "model",
        "content": response_chat.text,
        "timestamp": datetime.utcnow(),
        "token_count": response_tokens,
        "type": "text",
        "audio_file_path": None,
        "message_index": message_index + 1,
        "sent_message_index": message_index
    }

    # Save the model's response into MongoDB and update token statistics.
    await db.conversations.update_one(
        {"_id": ObjectId(conversation_id)},
        {
            "$push": {"messages": model_message},
            "$set": {"last_message_time": datetime.utcnow()},
            "$inc": {
                "total_prompt_tokens": prompt_tokens,
                "total_response_tokens": response_tokens,
                "total_token_count": total_tokens
            }
        }
    )

    return model_message

@router.post("/conversations/{conversation_id}/messages", response_model=Message)
async def send_message(
    conversation_id: str,
    user_id: str = Form(...),
    type: str = Form(...),            # "text" or "audio"
    message: str = Form(""),            # For text messages; may be empty if audio.
    audio: UploadFile = File(None)      # Provided only when type == "audio"
):
    """
    Sends a user message to the chatbot.
    
    For type "text":
      - Uses the provided message.
      
    For type "audio":
      - Uses the provided audio file, sends it to the voice-to-text model (VOICE_TO_TEXT_MODEL),
        and uses the extracted text as the message content.
      - The audio file is stored on disk under audio_files/{user_id}/{conversation_id}/,
        with a filename based on the UTC timestamp and message index.
      - The message record is updated with the generated audio_file_path.
    
    Returns the model's response (always text) along with:
      - 'sent_message_index': The index of the user-sent message.
      - 'sent_message': The text that was sent by the user (either entered directly or extracted from audio).
    """
    type = type.lower()
    if type == "text":
        if not message:
            raise HTTPException(status_code=400, detail="For text messages, 'message' field must not be empty.")
        message_text = message
    elif type == "audio":
        if audio is None:
            raise HTTPException(status_code=400, detail="For audio messages, an audio file must be provided.")
        # Read audio bytes.
        audio_bytes = await audio.read()
        # Use the dedicated voice-to-text model to extract text.
        voice_client = GeminiClient()  # Reusing GeminiClient instance; can be separate if needed.
        try:
            response_vtt = voice_client.client.models.generate_content(
                model=settings.VOICE_TO_TEXT_MODEL,
                contents=[
                    "Extract the text from this audio:",
                    types.Part.from_bytes(
                        data=audio_bytes,
                        mime_type=audio.content_type  # e.g., "audio/mp3"
                    )
                ]
            )
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Voice-to-text conversion failed: {e}")
        extracted_text = response_vtt.text.strip()
        if not extracted_text:
            raise HTTPException(status_code=500, detail="No text extracted from the audio.")
        message_text = extracted_text
    else:
        raise HTTPException(status_code=400, detail="Invalid type. Must be 'text' or 'audio'.")

    # Retrieve conversation from the database.
    conversation = await db.conversations.find_one({"_id": ObjectId(conversation_id)})
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found.")

    # Determine the message index for the new user message.
    message_index = len(conversation.get("messages", [])) + 1

    # Get or create a chat session.
    chat_session = ChatSessionManager.get_session(user_id)
    if not chat_session:
        chat_session = ChatSessionManager.create_session(user_id, conversation_id)
    chat_session.increment_request_count()
    chat_session.update_last_message_time()

    # Create the user's message record.
    user_message = {
        "role": "user",
        "content": message_text,
        "timestamp": datetime.utcnow(),
        "type": type,
        "audio_file_path": None,  # For audio messages, this will be updated later.
        "message_index": message_index
    }

    # Save the user's message into MongoDB.
    await db.conversations.update_one(
        {"_id": ObjectId(conversation_id)},
        {"$push": {"messages": user_message}, "$set": {"last_message_time": datetime.utcnow()}}
    )

    # If the message is audio, store the audio file and update the record.
    if type == "audio":
        base_dir = "audio_files"
        dir_path = os.path.join(base_dir, user_id, conversation_id)
        os.makedirs(dir_path, exist_ok=True)
        ts_str = datetime.utcnow().strftime("%Y%m%d%H%M%S")
        ext = os.path.splitext(audio.filename)[1] if audio.filename and os.path.splitext(audio.filename)[1] else ".wav"
        filename = f"{ts_str}_{message_index}{ext}"
        file_path = os.path.join(dir_path, filename)
        with open(file_path, "wb") as f:
            f.write(audio_bytes)
        await db.conversations.update_one(
            {"_id": ObjectId(conversation_id)},
            {"$set": {"messages.$[elem].audio_file_path": file_path}},
            array_filters=[{"elem.message_index": message_index, "elem.type": "audio"}]
        )

    # Send the (extracted or provided) text to the chat model.
    gemini_client = GeminiClient()
    response_chat, prompt_tokens, response_tokens, total_tokens = await gemini_client.send_message(chat_session, message_text)

    print(f"Token Usage - Prompt: {prompt_tokens}, Response: {response_tokens}, Total: {total_tokens}")

    # Prepare the model's response message.
    model_message = {
        "role": "model",
        "content": response_chat.text,
        "timestamp": datetime.utcnow(),
        "token_count": response_tokens,
        "type": "text",
        "audio_file_path": None,
        "message_index": message_index + 1,
        "sent_message_index": message_index,
        "sent_message": message_text  # Extra field containing the extracted (or original) user text.
    }

    # Save the model's response into MongoDB and update token statistics.
    await db.conversations.update_one(
        {"_id": ObjectId(conversation_id)},
        {
            "$push": {"messages": model_message},
            "$set": {"last_message_time": datetime.utcnow()},
            "$inc": {
                "total_prompt_tokens": prompt_tokens,
                "total_response_tokens": response_tokens,
                "total_token_count": total_tokens
            }
        }
    )

    return model_message

@router.get("/conversations/user/{user_id}", response_model=list[Conversation])
async def get_conversations(user_id: str):
    cursor = db.conversations.find({"user_id": user_id})
    conversations = []
    async for conv in cursor:
        conv["_id"] = str(conv["_id"])
        conversations.append(Conversation(**conv))
    return conversations

@router.get("/conversations/{conversation_id}/history", response_model=list[Message])
async def get_conversation_history(conversation_id: str, user_id: str):
    conversation = await db.conversations.find_one({"_id": ObjectId(conversation_id), "user_id": user_id})
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    return conversation.get("messages", [])

@router.get("/conversations/{conversation_id}/token-stats")
async def get_conversation_token_stats(conversation_id: str, user_id: str = Query(...)):
    # Fetch the conversation from the database
    conversation = await db.conversations.find_one({"_id": ObjectId(conversation_id), "user_id": user_id})
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found.")

    # Extract token statistics (defaulting to 0 if missing)
    total_user_tokens = conversation.get("total_prompt_tokens", 0)  # Tokens from user messages
    total_model_tokens = conversation.get("total_response_tokens", 0)  # Tokens from model responses
    total_tokens = conversation.get("total_token_count", 0)  # Sum of both
    message_count = len(conversation.get("messages", []))  # Count the number of messages

    return {
        "conversation_id": conversation_id,
        "user_id": user_id,
        "total_user_tokens": total_user_tokens,
        "total_model_tokens": total_model_tokens,
        "total_tokens": total_tokens,
        "message_count": message_count
    }
    
@router.post("/conversations0", response_model=Conversation)
async def create_conversation(conversation: Conversation):
    # Ensure the user does not have an active session.
    existing_session = ChatSessionManager.get_session(conversation.user_id)
    if existing_session:
        raise HTTPException(status_code=400, detail="Active chat session already exists for this user.")

    conversation.start_time = datetime.utcnow()
    conversation.last_message_time = conversation.start_time
    # Insert the conversation into MongoDB and get its _id.
    result = await db.conversations.insert_one(conversation.dict(by_alias=True, exclude_none=True))
    print(result)
    print(result.inserted_id)
    conversation.id = str(result.inserted_id)
    
    # Now create a chat session and pass the conversation id.
    ChatSessionManager.create_session(conversation.user_id, conversation.id)
    
    return conversation

@router.post("/conversations/{conversation_id}/audio", response_model=Message)
async def upload_audio(
    conversation_id: str,
    user_id: str = Form(...),
    message_index: int = Form(...),
    audio: UploadFile = File(...)
):
    """
    Updates an existing audio message record with the audio file path.
    
    This endpoint assumes that the user message of type "audio" was already created via the send message API,
    with a defined message_index. It then:
      1. Locates the message by conversation_id, user_id, and message_index.
      2. Saves the uploaded audio file on disk in a structured directory:
         audio_files/{user_id}/{conversation_id}/ with a filename generated using the UTC timestamp and the message index.
      3. Updates the located message with the generated audio file path using an array filter.
      4. Returns the updated message.
    """
    # Retrieve the conversation from the database.
    conversation = await db.conversations.find_one({"_id": ObjectId(conversation_id), "user_id": user_id})
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    
    print(f"conversation_id: {conversation_id}")
    print(f"user_id: {user_id}")
    print(f"message_index: {message_index}")
    
    # Verify that the target message exists.
    target_message = None
    for msg in conversation.get("messages", []):
        if msg.get("message_index") == message_index and msg.get("type") == "audio":
            target_message = msg
            break
    if not target_message:
        raise HTTPException(status_code=404, detail="Audio message with the provided index not found.")
    
    # Define the directory structure: audio_files/{user_id}/{conversation_id}/
    base_dir = AUDIO_FILES_BASE_DIR
    dir_path = os.path.join(base_dir, user_id, conversation_id)
    os.makedirs(dir_path, exist_ok=True)
    
    print(f"dir_path: {dir_path}")
    
    # Generate a unique filename using the current UTC timestamp and the message index.
    ts_str = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    ext = os.path.splitext(audio.filename)[1] if audio.filename and os.path.splitext(audio.filename)[1] else ".wav"
    filename = f"{ts_str}_{message_index}{ext}"
    file_path = os.path.join(dir_path, filename)
    
    print(f"file_path: {file_path}")
    
    # Save the uploaded audio file to disk.
    with open(file_path, "wb") as f:
        content = await audio.read()
        f.write(content)
    
    # Update the specific message in MongoDB using an array filter.
    update_result = await db.conversations.update_one(
        {"_id": ObjectId(conversation_id)},
        {"$set": {"messages.$[elem].audio_file_path": file_path}},
        array_filters=[{"elem.message_index": message_index, "elem.type": "audio"}]
    )
    
    if update_result.modified_count == 0:
        raise HTTPException(status_code=500, detail="Failed to update audio file path.")
    
    # Retrieve the updated conversation and extract the updated audio message.
    updated_conversation = await db.conversations.find_one({"_id": ObjectId(conversation_id)})
    updated_message = next(
        (msg for msg in updated_conversation.get("messages", [])
         if msg.get("message_index") == message_index and msg.get("type") == "audio"),
        None
    )
    
    print(f"updated_message: {updated_message}")
    
    if not updated_message:
        raise HTTPException(status_code=500, detail="Audio message not found after update.")
    
    return Message(**updated_message)



@router.get("/conversations/{conversation_id}/audio", response_model=List[Message])
async def get_audio_messages(conversation_id: str, user_id: str = Query(...)):
    """
    This endpoint retrieves all audio messages for a given conversation.
    It filters the messages where type is 'audio' and returns them.
    """
    conversation = await db.conversations.find_one({"_id": ObjectId(conversation_id), "user_id": user_id})
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found.")

    # Filter messages of type "audio"
    audio_messages = [Message(**msg) for msg in conversation.get("messages", []) if msg.get("type") == "audio"]
    return audio_messages

@router.get("/conversations/{conversation_id}/audio/{filename}")
async def get_audio_file(conversation_id: str, filename: str, user_id: str = Query(...)):
    """
    Retrieve an audio file stored on the server.
    
    The file path is constructed using the format:
      audio_files/{user_id}/{conversation_id}/{filename}
    
    Example file path:
      audio_files/shadi/67dde86ea4f562f43ccb80d6/20250321223104_1.wav
    """
    file_path = os.path.join(AUDIO_FILES_BASE_DIR, user_id, conversation_id, filename)
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Audio file not found.")
    
    return FileResponse(file_path, media_type="audio/wav", filename=filename)