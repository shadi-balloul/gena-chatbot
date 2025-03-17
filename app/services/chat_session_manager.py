# In app/services/chat_session_manager.py

import time
from app.config import settings
from app.services.gemini_client import GeminiClient

class ChatSession:
    def __init__(self, user_id: str, conversation_id: str = None):
        self.user_id = user_id
        self.conversation_id = conversation_id  # This will hold the conversation id if provided.
        self.chat = GeminiClient().create_chat()
        # self.chat = None  # Set up your chat session (e.g., via GeminiClient) as needed.
        self.start_time = time.time()
        self.last_message_time = time.time()
        self.request_count = 0

    def update_last_message_time(self):
        self.last_message_time = time.time()

    def increment_request_count(self):
        self.request_count += 1

class ChatSessionManager:
    _sessions: dict[str, ChatSession] = {}

    @classmethod
    def create_session(cls, user_id: str, conversation_id: str = None) -> ChatSession:
        if user_id in cls._sessions:
            raise Exception("User already has an active chat session.")
        session = ChatSession(user_id, conversation_id)
        cls._sessions[user_id] = session
        return session

    @classmethod
    def get_session(cls, user_id: str) -> ChatSession:
        return cls._sessions.get(user_id)

    @classmethod
    def remove_session(cls, user_id: str):
        if user_id in cls._sessions:
            del cls._sessions[user_id]

    @classmethod
    async def cleanup_sessions(cls):
        current_time = time.time()
        to_remove = []
        for user_id, session in cls._sessions.items():
            if (session.request_count >= settings.MAX_REQUESTS_PER_DAY) or \
               (current_time - session.last_message_time > settings.MAX_DURATION_AFTER_LAST_MESSAGE):
                to_remove.append(user_id)
        for user_id in to_remove:
            cls.remove_session(user_id)
