"""
Configuração central do projeto.
Lê variáveis de ambiente e valida.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

# Carrega .env se existir (em dev local)
# Em GitHub Actions, as variáveis vêm dos secrets
load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ASSETS_DIR = PROJECT_ROOT / "assets"
TEMP_DIR = PROJECT_ROOT / "temp"
OUTPUT_DIR = PROJECT_ROOT / "output"


@dataclass
class Config:
    # Telegram
    telegram_bot_token: str = field(default_factory=lambda: os.getenv("TELEGRAM_BOT_TOKEN", ""))
    telegram_allowed_chat_id: str = field(default_factory=lambda: os.getenv("TELEGRAM_ALLOWED_CHAT_ID", ""))

    # Gemini (primário — melhor PT-BR)
    gemini_api_key: str = field(default_factory=lambda: os.getenv("GEMINI_API_KEY", ""))
    gemini_model: str = "gemini-2.0-flash"

    # Groq (fallback)
    groq_api_key: str = field(default_factory=lambda: os.getenv("GROQ_API_KEY", ""))
    groq_model: str = "llama-3.3-70b-versatile"

    # Mídia
    pexels_api_key: str = field(default_factory=lambda: os.getenv("PEXELS_API_KEY", ""))
    pixabay_api_key: str = field(default_factory=lambda: os.getenv("PIXABAY_API_KEY", ""))

    # Cloudflare R2
    r2_account_id: str = field(default_factory=lambda: os.getenv("R2_ACCOUNT_ID", ""))
    r2_access_key_id: str = field(default_factory=lambda: os.getenv("R2_ACCESS_KEY_ID", ""))
    r2_secret_access_key: str = field(default_factory=lambda: os.getenv("R2_SECRET_ACCESS_KEY", ""))
    r2_bucket_name: str = field(default_factory=lambda: os.getenv("R2_BUCKET_NAME", "video-maker-bot"))
    r2_public_url: str = field(default_factory=lambda: os.getenv("R2_PUBLIC_URL", ""))

    # GitHub (pro bot disparar workflows)
    github_token: str = field(default_factory=lambda: os.getenv("GITHUB_TOKEN", ""))
    github_repo: str = field(default_factory=lambda: os.getenv("GITHUB_REPO", ""))

    # Padrões
    default_template: str = field(default_factory=lambda: os.getenv("DEFAULT_TEMPLATE", "motivacional"))
    default_voice: str = field(default_factory=lambda: os.getenv("DEFAULT_VOICE", "pt-BR-AntonioNeural"))
    video_duration_seconds: int = field(default_factory=lambda: int(os.getenv("VIDEO_DURATION_SECONDS", "45")))

    # Dimensões do vídeo (vertical 9:16 Full HD)
    video_width: int = 1080
    video_height: int = 1920
    video_fps: int = 30

    def validate_for_pipeline(self) -> list[str]:
        """
        Retorna lista de configs faltando pra rodar o pipeline.
        Gemini OU Groq são aceitos (um fallback do outro).
        """
        missing = []
        # Pelo menos uma IA (Gemini ou Groq) tem que estar configurada
        if not self.gemini_api_key and not self.groq_api_key:
            missing.append("GEMINI_API_KEY ou GROQ_API_KEY")

        required = {
            "PEXELS_API_KEY": self.pexels_api_key,
            "PIXABAY_API_KEY": self.pixabay_api_key,
        }
        for name, value in required.items():
            if not value:
                missing.append(name)
        return missing

    def validate_for_bot(self) -> list[str]:
        """Retorna lista de configs faltando pra rodar o bot."""
        missing = []
        required = {
            "TELEGRAM_BOT_TOKEN": self.telegram_bot_token,
            "TELEGRAM_ALLOWED_CHAT_ID": self.telegram_allowed_chat_id,
            "GITHUB_TOKEN": self.github_token,
            "GITHUB_REPO": self.github_repo,
        }
        for name, value in required.items():
            if not value:
                missing.append(name)
        return missing


# Instância global — importe de qualquer lugar
config = Config()


def ensure_dirs() -> None:
    """Cria diretórios temporários se não existirem."""
    TEMP_DIR.mkdir(exist_ok=True)
    OUTPUT_DIR.mkdir(exist_ok=True)
