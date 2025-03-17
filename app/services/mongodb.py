import motor.motor_asyncio
from app.config import settings

class MongoDBClient:
    _client = None

    @classmethod
    def get_client(cls):
        if cls._client is None:
            cls._client = motor.motor_asyncio.AsyncIOMotorClient(settings.MONGODB_URI)
        return cls._client

    @classmethod
    def get_database(cls):
        client = cls.get_client()
        return client[settings.MONGODB_DB]
