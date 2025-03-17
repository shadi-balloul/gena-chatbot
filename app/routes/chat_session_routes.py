import time
from fastapi import APIRouter
from app.config import settings
from app.services.chat_session_manager import ChatSessionManager

router = APIRouter()

@router.get("/chat-sessions")
def get_active_chat_sessions():
    current_time = time.time()
    sessions_info = []
    for user_id, session in ChatSessionManager._sessions.items():
        # Calculate remaining available duration.
        elapsed = current_time - session.last_message_time
        remaining_duration = settings.MAX_DURATION_AFTER_LAST_MESSAGE - elapsed
        if remaining_duration < 0:
            remaining_duration = 0

        sessions_info.append({
            "conversation_id": session.conversation_id,
            "user_id": session.user_id,
            "consumed_requests": session.request_count,
            "remaining_duration": remaining_duration  # in seconds
        })
    return sessions_info
