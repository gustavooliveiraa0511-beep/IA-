"""
Envia mensagem/vídeo pelo Telegram via API REST (sem polling).
Usado pelo GitHub Actions pra mandar o vídeo pronto de volta.

Mantido como SYNC pra não conflitar com o event loop do Edge TTS.
"""
from __future__ import annotations

from pathlib import Path

import httpx

from src.utils.config import config
from src.utils.logger import get_logger

logger = get_logger(__name__)


BASE = "https://api.telegram.org"


def send_text(chat_id: str, text: str, parse_mode: str = "Markdown") -> bool:
    url = f"{BASE}/bot{config.telegram_bot_token}/sendMessage"
    try:
        with httpx.Client(timeout=20.0) as client:
            r = client.post(url, json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": parse_mode,
            })
            if r.status_code != 200:
                logger.error(f"sendMessage falhou: {r.status_code} {r.text}")
                return False
            return True
    except Exception as e:
        logger.error(f"sendMessage exceção: {e}")
        return False


def send_video_url(chat_id: str, video_url: str, caption: str = "") -> bool:
    url = f"{BASE}/bot{config.telegram_bot_token}/sendVideo"
    try:
        with httpx.Client(timeout=60.0) as client:
            r = client.post(url, json={
                "chat_id": chat_id,
                "video": video_url,
                "caption": caption,
                "supports_streaming": True,
            })
            if r.status_code != 200:
                logger.error(f"sendVideo URL falhou: {r.status_code} {r.text}")
                # Fallback: manda só o link como texto
                return send_text(chat_id, f"{caption}\n\n🎥 {video_url}")
            return True
    except Exception as e:
        logger.error(f"sendVideo URL exceção: {e}")
        return send_text(chat_id, f"{caption}\n\n🎥 {video_url}")


def send_video_file(chat_id: str, video_path: Path, caption: str = "") -> bool:
    """
    Envia vídeo como UPLOAD (até 50MB via bot API).
    Se for maior, use send_video_url com link do R2.
    """
    size_mb = video_path.stat().st_size / (1024 * 1024)
    if size_mb > 49:
        logger.warning(f"Vídeo {size_mb:.1f}MB excede limite do Bot API")
        return False

    url = f"{BASE}/bot{config.telegram_bot_token}/sendVideo"
    try:
        with httpx.Client(timeout=120.0) as client:
            with open(video_path, "rb") as f:
                files = {"video": (video_path.name, f, "video/mp4")}
                data = {
                    "chat_id": chat_id,
                    "caption": caption,
                    "supports_streaming": "true",
                }
                r = client.post(url, files=files, data=data)
                if r.status_code != 200:
                    logger.error(f"sendVideo file falhou: {r.status_code} {r.text}")
                    return False
        return True
    except Exception as e:
        logger.error(f"sendVideo file exceção: {e}")
        return False
