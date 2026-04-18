"""
Entrypoint executado pelo GitHub Actions.

Recebe os inputs via variáveis de ambiente:
  THEME       — tema do vídeo
  TEMPLATE    — motivacional/viral/noticias/gaming
  VOICE       — voz Edge TTS
  CHAT_ID     — chat_id pro notifier mandar o vídeo

Fluxo:
1. Roda o PipelineOrchestrator
2. Upload pro Cloudflare R2
3. Manda mensagem no Telegram com o link do vídeo
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from src.bot.notifier import send_text, send_video_file, send_video_url
from src.pipeline.models import TemplateType, VideoRequest
from src.pipeline.orchestrator import PipelineOrchestrator
from src.utils.config import config
from src.utils.logger import get_logger
from src.utils.storage import R2Uploader

logger = get_logger(__name__)


async def main() -> int:
    theme = os.getenv("THEME", "").strip()
    template = os.getenv("TEMPLATE", "motivacional").strip().lower()
    voice = os.getenv("VOICE", "pt-BR-AntonioNeural").strip()
    chat_id = os.getenv("CHAT_ID", "").strip() or config.telegram_allowed_chat_id

    if not theme:
        logger.error("THEME não informado")
        return 2

    try:
        template_enum = TemplateType(template)
    except ValueError:
        logger.error(f"Template inválido: {template}")
        template_enum = TemplateType.MOTIVACIONAL

    # Aviso inicial
    if chat_id:
        await send_text(
            chat_id,
            f"🎬 Iniciando geração: *{theme}*\n"
            f"Template: `{template}` | Voz: `{voice}`",
        )

    # Checa configs obrigatórias
    missing = config.validate_for_pipeline()
    if missing:
        msg = f"❌ Configs faltando: {', '.join(missing)}"
        logger.error(msg)
        if chat_id:
            await send_text(chat_id, msg)
        return 3

    request = VideoRequest(
        theme=theme,
        template=template_enum,
        voice=voice,
    )

    try:
        orchestrator = PipelineOrchestrator()
        job = orchestrator.run(request)
    except Exception as e:
        logger.exception("pipeline falhou")
        if chat_id:
            await send_text(chat_id, f"❌ Erro na geração:\n`{type(e).__name__}: {e}`")
        return 1

    if not job.final_video_path or not job.final_video_path.exists():
        if chat_id:
            await send_text(chat_id, "❌ Vídeo final não foi gerado.")
        return 1

    # Upload R2 (se configurado) ou envia direto
    video_url: str | None = None
    if all([config.r2_account_id, config.r2_access_key_id, config.r2_secret_access_key]):
        try:
            uploader = R2Uploader()
            remote_key = f"videos/{job.job_id}.mp4"
            video_url = uploader.upload_file(job.final_video_path, remote_key)
            job.public_url = video_url
            logger.info(f"R2 URL: {video_url}")
        except Exception as e:
            logger.exception(f"upload R2 falhou: {e}")

    caption = _build_caption(job)

    if chat_id:
        if video_url:
            ok = await send_video_url(chat_id, video_url, caption)
            if not ok:
                await send_video_file(chat_id, job.final_video_path, caption)
        else:
            # Sem R2: sobe o arquivo direto (até 50MB)
            await send_video_file(chat_id, job.final_video_path, caption)

    return 0


def _build_caption(job) -> str:
    parts = ["✅ Pronto!"]
    if job.script:
        if job.script.title:
            parts.append(f"📌 *{job.script.title}*")
        if job.script.hashtags:
            parts.append(" ".join(job.script.hashtags))
    return "\n\n".join(parts)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
