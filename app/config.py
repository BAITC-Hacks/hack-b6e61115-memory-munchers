from dataclasses import dataclass
from pathlib import Path
import os

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / '.env', encoding='utf-8-sig')


@dataclass
class Settings:
    data_dir: Path = ROOT / 'data'
    db_path: Path = ROOT / 'data' / 'assistant.sqlite'
    model: str = os.getenv('OPENAI_MODEL') or 'gpt-5.4-mini'
    research_model: str = os.getenv('OPENAI_RESEARCH_MODEL') or 'gpt-5.4-mini'
    api_key: str = os.getenv('OPENAI_API_KEY', '')
    telegram_token: str = os.getenv('TELEGRAM_BOT_TOKEN', '')
    public_base_url: str = os.getenv('PUBLIC_BASE_URL', '').rstrip('/')
    bot_username: str = os.getenv('TELEGRAM_BOT_USERNAME', '')
    model_timeout: float = min(5.2,float(os.getenv('MODEL_TIMEOUT_SECONDS', '5.2')))
    response_timeout: float = min(7.2,float(os.getenv('RESPONSE_TIMEOUT_SECONDS', '7.2')))
    max_attachment_bytes: int = 15 * 1024 * 1024


settings = Settings()
