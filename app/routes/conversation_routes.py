# /home/shadi2/bmo/code/gena-chatbot/gena-chatbot/app/routes/conversation_routes.py
from fastapi import APIRouter, HTTPException, Query, UploadFile, File, Form
from typing import Union
from datetime import datetime
from bson import ObjectId, Binary
from app.models import Conversation, Message
from app.services.mongodb import MongoDBClient
from app.services.chat_session_manager import ChatSessionManager, ChatSession
from app.services.gemini_client import GeminiClient


router = APIRouter()
db = MongoDBClient.get_database()

@router.post("/conversations", response_model=Conversation)
async def create_conversation(conversation: Conversation):
    existing_session = ChatSessionManager.get_session(conversation.user_id)

    if existing_session:
        existing_conversation = await db.conversations.find_one({"_id": ObjectId(existing_session.conversation_id)})
        if existing_conversation:
            existing_conversation["id"] = str(existing_conversation["_id"])
            del existing_conversation["_id"]
            return Conversation(**existing_conversation)

    conversation.start_time = datetime.utcnow()
    conversation.last_message_time = conversation.start_time
    conversation.messages = []

    result = await db.conversations.insert_one(conversation.dict(by_alias=True, exclude_none=True))
    conversation.id = str(result.inserted_id)
    ChatSessionManager.create_session(conversation.user_id, conversation.id)  # No model type needed
    return conversation

@router.post("/conversations/{conversation_id}/messages", response_model=Message)
async def send_message(conversation_id: str, user_id: str = Form(...), message: Union[str, UploadFile] = Form(...)):
    """Handles text and audio messages using a single, multimodal model."""

    conversation = await db.conversations.find_one({"_id": ObjectId(conversation_id)})
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found.")

    chat_session = ChatSessionManager.get_session(user_id)
    if not chat_session:
        chat_session = ChatSessionManager.create_session(user_id, conversation_id)  # No model type

    chat_session.increment_request_count()
    chat_session.update_last_message_time()

    gemini_client = GeminiClient()

    if isinstance(message, str):
        # Text Message
        user_message = {
            "role": "user",
            "content": message,
            "timestamp": datetime.utcnow()
        }
        await db.conversations.update_one(
            {"_id": ObjectId(conversation_id)},
            {"$push": {"messages": user_message}, "$set": {"last_message_time": datetime.utcnow()}}
        )

        response, prompt_tokens, response_tokens, total_tokens = await gemini_client.send_message(chat_session, message)

        model_message = {
            "role": "model",
            "content": response.text,
            "timestamp": datetime.utcnow(),
            "token_count": response_tokens
        }
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

    elif isinstance(message, UploadFile):
        # Audio Message
        if message.content_type not in ["audio/wav", "audio/mpeg", "audio/ogg", "audio/webm"]:
            raise HTTPException(status_code=400, detail="Invalid audio file type.")

        audio_data = await message.read()
        file_name = message.filename

        response, prompt_tokens, response_tokens, total_tokens = await gemini_client.send_audio_message(chat_session, audio_data, file_name)

        user_message = {
            "role": "user",
            "content": {
                "audio": Binary(audio_data),
                "file_name": file_name
            },
            "timestamp": datetime.utcnow(),
        }
        await db.conversations.update_one(
            {"_id": ObjectId(conversation_id)},
            {"$push": {"messages": user_message}, "$set": {"last_message_time": datetime.utcnow()}}
        )

        model_message = {
            "role": "model",
            "content": response.text,
            "timestamp": datetime.utcnow(),
            "token_count": response_tokens
        }
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
    else:
        raise HTTPException(status_code=400, detail="Invalid message type.")

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
    conversation = await db.conversations.find_one({"_id": ObjectId(conversation_id), "user_id": user_id})
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found.")

    total_user_tokens = conversation.get("total_prompt_tokens", 0)
    total_model_tokens = conversation.get("total_response_tokens", 0)
    total_tokens = conversation.get("total_token_count", 0)
    message_count = len(conversation.get("messages", []))

    return {
        "conversation_id": conversation_id,
        "user_id": user_id,
        "total_user_tokens": total_user_tokens,
        "total_model_tokens": total_model_tokens,
        "total_tokens": total_tokens,
        "message_count": message_count
    }