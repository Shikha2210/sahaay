from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field

class Settings(BaseSettings):
    mongodb_uri: str = Field(alias='MONGODB_URI')
    mongodb_database: str = Field(default='sahaay', alias='MONGODB_DATABASE')
    jwt_secret: str = Field(alias='JWT_SECRET')
    jwt_expire_minutes: int = Field(default=720, alias='JWT_EXPIRE_MINUTES')
    cors_origins: str = Field(default='http://localhost:5173,http://127.0.0.1:5173', alias='CORS_ORIGINS')
    groq_api_key: str = Field(default='', alias='GROQ_API_KEY')
    groq_model: str = Field(default='llama-3.3-70b-versatile', alias='GROQ_MODEL')
    bootstrap_supervisor_email: str = Field(default='supervisor@sahaay.local', alias='BOOTSTRAP_SUPERVISOR_EMAIL')
    bootstrap_supervisor_password: str = Field(default='ChangeThisBeforeUse123!', alias='BOOTSTRAP_SUPERVISOR_PASSWORD')
    seed_demo_data: bool = Field(default=True, alias='SEED_DEMO_DATA')
    model_config = SettingsConfigDict(env_file='.env', extra='ignore', populate_by_name=True)

settings = Settings()
