"""
Bot do Telegram — interface do usuário.

Comandos:
- /criar <tema>            Gera vídeo com tema informado
- /template <nome>         Troca template padrão (motivacional/viral/noticias/gaming)
- /roteiro <tema>          Só gera o roteiro pra aprovação
- /refazer                 Refaz último vídeo
- /voz <nome>              Troca voz (Antonio/Francisca/Thalita)
- /status                  Mostra último job
- /ajuda                   Lista comandos

Arquitetura:
O bot NÃO gera vídeo ele mesmo (seria lento e limitado).
Ele dispara um GitHub Actions workflow que roda o pipeline pesado.
Quando o Action termina, ele posta o resultado via bot (webhook).
"""
from __future__ import annotations

import json
from pathlib import Path

import httpx
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from src.pipeline.models import TemplateType
from src.utils.config import config
from src.utils.logger import get_logger

logger = get_logger(__name__)


# Config "leve" do usuário persistida num JSON (uso pessoal, sem DB)
USER_PREFS_FILE = Path("user_prefs.json")


def _load_prefs() -> dict:
    if USER_PREFS_FILE.exists():
        return json.loads(USER_PREFS_FILE.read_text())
    return {
        "template": config.default_template,
        "voice": config.default_voice,
        "last_theme": None,
    }


def _save_prefs(prefs: dict) -> None:
    USER_PREFS_FILE.write_text(json.dumps(prefs, indent=2, ensure_ascii=False))


# ============================================
# Autenticação (só um usuário pode usar)
# ============================================
def _is_authorized(update: Update) -> bool:
    if not config.telegram_allowed_chat_id:
        return True  # sem filtro configurado
    chat_id = str(update.effective_chat.id) if update.effective_chat else ""
    return chat_id == config.telegram_allowed_chat_id


async def _reject_if_unauthorized(update: Update) -> bool:
    if not _is_authorized(update):
        logger.warning(
            f"Acesso negado: chat_id={update.effective_chat.id if update.effective_chat else '?'}"
        )
        if update.message:
            await update.message.reply_text("⛔ Bot privado.")
        return True
    return False


# ============================================
# Disparador de GitHub Actions
# ============================================
async def _dispatch_github_action(inputs: dict) -> bool:
    """
    Dispara o workflow 'generate-video.yml' no GitHub com os inputs.

    O workflow:
    - Instala deps
    - Roda o pipeline
    - Sobe no R2
    - Chama de volta o bot via webhook/API pra entregar o vídeo
    """
    if not (config.github_token and config.github_repo):
        logger.error("GITHUB_TOKEN/REPO não configurados — impossível disparar workflow")
        return False

    url = f"https://api.github.com/repos/{config.github_repo}/actions/workflows/generate-video.yml/dispatches"
    headers = {
        "Authorization": f"Bearer {config.github_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    payload = {
        "ref": "main",
        "inputs": inputs,
    }

    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.post(url, headers=headers, json=payload)
        if r.status_code not in (200, 204):
            logger.error(f"Falha ao disparar Action: {r.status_code} {r.text}")
            return False
    logger.info("GitHub Action disparada ✅")
    return True


# ============================================
# HANDLERS
# ============================================
HELP_TEXT = """🎬 *Video Maker Bot* — comandos disponíveis:

*/criar* `<tema>` — gera vídeo
 _ex: /criar nunca desista dos seus sonhos_

*/template* `<nome>` — muda estilo
 opções: motivacional, viral, noticias, gaming

*/voz* `<nome>` — muda a voz
 opções: antonio, francisca, thalita

*/roteiro* `<tema>` — só roteiro (aprova antes)

*/refazer* — refaz último tema com variação

*/status* — mostra job atual

*/ajuda* — esta mensagem
"""


async def cmd_start(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    chat_id = update.effective_chat.id if update.effective_chat else "?"
    await update.message.reply_text(
        f"Olá! Seu chat_id é `{chat_id}`.\n"
        f"Configure isso como TELEGRAM_ALLOWED_CHAT_ID pra travar o bot só pra você.\n\n"
        + HELP_TEXT,
        parse_mode="Markdown",
    )


async def cmd_help(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    if await _reject_if_unauthorized(update):
        return
    if update.message:
        await update.message.reply_text(HELP_TEXT, parse_mode="Markdown")


async def cmd_template(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await _reject_if_unauthorized(update):
        return
    if not update.message:
        return
    if not context.args:
        await update.message.reply_text(
            "Uso: /template motivacional | viral | noticias | gaming"
        )
        return
    choice = context.args[0].lower()
    try:
        TemplateType(choice)
    except ValueError:
        await update.message.reply_text("❌ Template inválido.")
        return
    prefs = _load_prefs()
    prefs["template"] = choice
    _save_prefs(prefs)
    await update.message.reply_text(f"✅ Template agora é *{choice}*.", parse_mode="Markdown")


VOICE_ALIASES = {
    "antonio": "pt-BR-AntonioNeural",
    "francisca": "pt-BR-FranciscaNeural",
    "thalita": "pt-BR-ThalitaNeural",
}


async def cmd_voz(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await _reject_if_unauthorized(update):
        return
    if not update.message:
        return
    if not context.args:
        await update.message.reply_text(
            "Uso: /voz antonio | francisca | thalita"
        )
        return
    alias = context.args[0].lower()
    voice = VOICE_ALIASES.get(alias)
    if not voice:
        await update.message.reply_text("❌ Voz inválida.")
        return
    prefs = _load_prefs()
    prefs["voice"] = voice
    _save_prefs(prefs)
    await update.message.reply_text(f"✅ Voz agora é *{alias}*.", parse_mode="Markdown")


async def cmd_criar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await _reject_if_unauthorized(update):
        return
    if not update.message:
        return
    theme = " ".join(context.args).strip()
    if not theme:
        await update.message.reply_text("Uso: /criar <tema do vídeo>")
        return

    prefs = _load_prefs()
    prefs["last_theme"] = theme
    _save_prefs(prefs)

    chat_id = str(update.effective_chat.id) if update.effective_chat else ""
    inputs = {
        "theme": theme,
        "template": prefs["template"],
        "voice": prefs["voice"],
        "chat_id": chat_id,
    }

    ok = await _dispatch_github_action(inputs)
    if ok:
        await update.message.reply_text(
            f"🎬 Vídeo em produção!\n"
            f"Tema: *{theme}*\n"
            f"Template: `{prefs['template']}`\n"
            f"Voz: `{prefs['voice']}`\n\n"
            f"Leva cerca de 2-5 min. Te mando quando ficar pronto 🚀",
            parse_mode="Markdown",
        )
    else:
        await update.message.reply_text(
            "❌ Falha ao iniciar geração. Cheque os secrets no GitHub."
        )


async def cmd_refazer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await _reject_if_unauthorized(update):
        return
    if not update.message:
        return
    prefs = _load_prefs()
    last = prefs.get("last_theme")
    if not last:
        await update.message.reply_text("Nenhum vídeo anterior. Use /criar <tema>.")
        return
    # Re-chama cmd_criar com o último tema
    context.args = last.split()
    await cmd_criar(update, context)


async def cmd_status(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    if await _reject_if_unauthorized(update):
        return
    if not update.message:
        return
    prefs = _load_prefs()
    await update.message.reply_text(
        f"📋 *Config atual:*\n"
        f"Template: `{prefs['template']}`\n"
        f"Voz: `{prefs['voice']}`\n"
        f"Último tema: `{prefs.get('last_theme') or 'nenhum'}`\n\n"
        f"_Para status do job em execução, veja a aba 'Actions' do GitHub._",
        parse_mode="Markdown",
    )


async def cmd_roteiro(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await _reject_if_unauthorized(update):
        return
    if not update.message:
        return
    theme = " ".join(context.args).strip()
    if not theme:
        await update.message.reply_text("Uso: /roteiro <tema>")
        return
    # Gera localmente (é leve, só chamada Groq)
    from src.pipeline.models import VideoRequest
    from src.pipeline.script_writer import ScriptWriter

    prefs = _load_prefs()
    req = VideoRequest(theme=theme, template=TemplateType(prefs["template"]))
    writer = ScriptWriter()
    script = writer.generate(req)

    lines = "\n".join(
        f"*{i+1}.* _{line.scene_type.value}_ — {line.text}"
        for i, line in enumerate(script.lines)
    )
    hashtags = " ".join(script.hashtags) if script.hashtags else ""
    await update.message.reply_text(
        f"📝 *Roteiro:* {script.title}\n\n{lines}\n\n{hashtags}\n\n"
        f"Se curtiu, manda /criar {theme}",
        parse_mode="Markdown",
    )


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Mensagens soltas: interpreta como /criar <texto>."""
    if await _reject_if_unauthorized(update):
        return
    if not update.message or not update.message.text:
        return
    context.args = update.message.text.split()
    await cmd_criar(update, context)


def build_app() -> Application:
    missing = config.validate_for_bot()
    if missing:
        raise RuntimeError(f"Configs faltando pro bot: {missing}")

    app = Application.builder().token(config.telegram_bot_token).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("ajuda", cmd_help))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("criar", cmd_criar))
    app.add_handler(CommandHandler("template", cmd_template))
    app.add_handler(CommandHandler("voz", cmd_voz))
    app.add_handler(CommandHandler("refazer", cmd_refazer))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("roteiro", cmd_roteiro))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    return app


def main() -> None:
    app = build_app()
    logger.info("🤖 Bot iniciado. Mande /start no Telegram.")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
