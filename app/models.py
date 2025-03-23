from datetime import datetime
from pydantic import BaseModel, Field
from typing import List, Optional

class Message(BaseModel):
    role: str
    content: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    token_count: Optional[int] = None
    type: str = "text"           # "text" or "audio"
    audio_file_path: Optional[str] = None
    message_index: Optional[int] = None 
    sent_message_index: Optional[int] = None 
    sent_message: Optional[str] = None

class Conversation(BaseModel):
    id: Optional[str] = Field(default=None, alias="_id")
    user_id: str
    messages: List[Message] = []
    start_time: datetime = Field(default_factory=datetime.utcnow)
    last_message_time: datetime = Field(default_factory=datetime.utcnow)

    class Config:
        populate_by_name = True

class ContextCacheInfo(BaseModel):
    name: str
    model: str
    display_name: str
    create_time: str
    update_time: str
    expire_time: str
