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

router = APIRouter()
db = MongoDBClient.get_database()

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

@router.post("/conversations/{conversation_id}/messages", response_model=Message)
async def send_message(conversation_id: str, payload: dict):
    """
    Sends a user message to the chatbot.

    Payload example:
    {
        "user_id": "shadi",
        "message": "أريد أن أشتري رصيد...",
        "type": "audio"   // or "text"
    }

    For an audio message, the message record is created with type "audio" and no audio_file_path.
    The audio_file_path will be updated later by the audio upload API.
    
    The API returns the model response along with an extra field 'sent_message_index'
    which contains the message_index of the user-sent message.
    """
    user_id = payload.get("user_id")
    message_text = payload.get("message")
    message_type = payload.get("type", "text")  # Default to "text"

    if not user_id or not message_text:
        raise HTTPException(status_code=400, detail="user_id and message are required.")

    # Retrieve conversation from the database.
    conversation = await db.conversations.find_one({"_id": ObjectId(conversation_id)})
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found.")

    # Determine the message index as the count of existing messages + 1.
    message_index = len(conversation.get("messages", [])) + 1

    # Get or create a chat session.
    chat_session = ChatSessionManager.get_session(user_id)
    if not chat_session:
        chat_session = ChatSessionManager.create_session(user_id, conversation_id)

    chat_session.increment_request_count()
    chat_session.update_last_message_time()

    # Prepare the user's message record with message_index.
    user_message = {
        "role": "user",
        "content": message_text,
        "timestamp": datetime.utcnow(),
        "type": message_type,
        "audio_file_path": None,  # Not set here for audio messages.
        "message_index": message_index
    }

    # Save the user's message into MongoDB.
    await db.conversations.update_one(
        {"_id": ObjectId(conversation_id)},
        {"$push": {"messages": user_message}, "$set": {"last_message_time": datetime.utcnow()}}
    )

    # Asynchronously send the message text to Gemini.
    gemini_client = GeminiClient()
    response, prompt_tokens, response_tokens, total_tokens = await gemini_client.send_message(chat_session, message_text)

    print(f"Token Usage - Prompt: {prompt_tokens}, Response: {response_tokens}, Total: {total_tokens}")

    # Prepare the model's response message.
    # Here, we assign its message_index as user_message_index + 1.
    model_message = {
        "role": "model",
        "content": response.text,
        "timestamp": datetime.utcnow(),
        "token_count": response_tokens,
        "type": "text",
        "audio_file_path": None,
        "message_index": message_index + 1,
        "sent_message_index": message_index   # Extra field: the index of the user-sent message.
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
      3. Updates the located message with the generated audio file path.
      4. Returns the updated message.
    """
    # Retrieve the conversation from the database.
    conversation = await db.conversations.find_one({"_id": ObjectId(conversation_id), "user_id": user_id})
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    
    # Locate the message with the specified message_index and type "audio".
    messages = conversation.get("messages", [])
    target_message = None
    for msg in messages:
        if msg.get("message_index") == message_index and msg.get("type") == "audio":
            target_message = msg
            break
    
    if not target_message:
        raise HTTPException(status_code=404, detail="Audio message with the provided index not found.")
    
    # Define the directory structure: audio_files/{user_id}/{conversation_id}/
    base_dir = "audio_files"
    dir_path = os.path.join(base_dir, user_id, conversation_id)
    os.makedirs(dir_path, exist_ok=True)
    
    # Generate a unique filename using the current UTC timestamp and the message index.
    ts_str = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    ext = os.path.splitext(audio.filename)[1] if audio.filename and os.path.splitext(audio.filename)[1] else ".wav"
    filename = f"{ts_str}_{message_index}{ext}"
    file_path = os.path.join(dir_path, filename)
    
    # Save the uploaded audio file to disk.
    with open(file_path, "wb") as f:
        content = await audio.read()
        f.write(content)
    
    # Update the message in MongoDB to set the audio_file_path.
    update_result = await db.conversations.update_one(
        {
            "_id": ObjectId(conversation_id),
            "messages.message_index": message_index,
            "messages.type": "audio"
        },
        {"$set": {"messages.$.audio_file_path": file_path}}
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