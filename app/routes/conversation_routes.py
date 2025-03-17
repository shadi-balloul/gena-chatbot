from fastapi import APIRouter, HTTPException, Query
from app.models import Conversation, Message
from app.services.mongodb import MongoDBClient
from app.services.chat_session_manager import ChatSessionManager, ChatSession
from app.services.gemini_client import GeminiClient
from datetime import datetime
from bson import ObjectId

router = APIRouter()

db = MongoDBClient.get_database()

@router.post("/conversations", response_model=Conversation)
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


@router.post("/conversations/{conversation_id}/messages", response_model=Message)
async def send_message(conversation_id: str, payload: dict):
    # Expected payload: {"user_id": "<user>", "message": "<message text>"}
    user_id = payload.get("user_id")
    message_text = payload.get("message")
    if not user_id or not message_text:
        raise HTTPException(status_code=400, detail="user_id and message are required.")

    # Retrieve conversation from the database.
    conversation = await db.conversations.find_one({"_id": ObjectId(conversation_id)})
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found.")

    # Get or create a chat session.
    chat_session = ChatSessionManager.get_session(user_id)
    if not chat_session:
        chat_session = ChatSessionManager.create_session(user_id, conversation_id)
    
    chat_session.increment_request_count()
    chat_session.update_last_message_time()

    # Save the user's message.
    user_message = {
        "role": "user",
        "content": message_text,
        "timestamp": datetime.utcnow()
    }
    await db.conversations.update_one(
        {"_id": ObjectId(conversation_id)},
        {"$push": {"messages": user_message}, "$set": {"last_message_time": datetime.utcnow()}}
    )
    
    # Asynchronously send the message to Gemini.
    gemini_client = GeminiClient()
    response, prompt_tokens, response_tokens, total_tokens = await gemini_client.send_message(chat_session, message_text)

    # ✅ Log token counts for debugging
    print(f"Token Usage - Prompt: {prompt_tokens}, Response: {response_tokens}, Total: {total_tokens}")

    # Save model's response with token counts
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