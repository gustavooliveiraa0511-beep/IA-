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


DEFAULT_PREFS = {
    "template": None,           # preenchido no _load_prefs via config
    "voice": None,
    "last_theme": None,
    "quality_mode": "standard",  # ou "premium"
    "duration": 45,              # segundos
}


def _load_prefs() -> dict:
    base = {
        **DEFAULT_PREFS,
        "template": config.default_template,
        "voice": config.default_voice,
        "duration": config.video_duration_seconds,
    }
    if USER_PREFS_FILE.exists():
        saved = json.loads(USER_PREFS_FILE.read_text())
        base.update(saved)
    return base


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
# Inputs "legados" — sempre aceitos pelo workflow antigo
_CORE_INPUTS = {"theme", "template", "voice", "chat_id"}


async def _dispatch_github_action(inputs: dict) -> tuple[bool, str]:
    """
    Dispara o workflow 'generate-video.yml' no GitHub com os inputs.

    Retorna (sucesso, mensagem). A mensagem é descritiva do motivo da falha
    pra exibir ao usuário no Telegram.

    Resiliência: se o GitHub retornar 422 "Unexpected inputs provided"
    (workflow ainda tá na versão antiga sem quality_mode/duration), faz
    retry automático apenas com os inputs básicos.
    """
    if not config.github_token:
        return False, "GITHUB_TOKEN não configurado nos secrets do repositório."
    if not config.github_repo:
        return False, "GITHUB_REPO não configurado."

    url = f"https://api.github.com/repos/{config.github_repo}/actions/workflows/generate-video.yml/dispatches"
    headers = {
        "Authorization": f"Bearer {config.github_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    # Primeira tentativa: com todos os inputs
    ok, err = await _post_dispatch(url, headers, inputs)
    if ok:
        return True, ""

    # Se a falha foi por inputs extras (422 com mensagem sobre "Unexpected inputs"),
    # retenta só com os básicos. Acontece quando o workflow no main ainda não
    # foi atualizado pra aceitar quality_mode e duration.
    if "unexpected inputs" in err.lower() or "inputs provided" in err.lower():
        core_only = {k: v for k, v in inputs.items() if k in _CORE_INPUTS}
        logger.warning(
            "Workflow não aceita inputs extras — retentando com "
            f"apenas {list(core_only.keys())}"
        )
        ok2, err2 = await _post_dispatch(url, headers, core_only)
        if ok2:
            return True, (
                "⚠️ Gerado SEM /premium e /duracao porque o workflow ainda "
                "está desatualizado. Atualize o arquivo do workflow pra "
                "ativar esses comandos."
            )
        return False, err2

    return False, err


async def _post_dispatch(url: str, headers: dict, inputs: dict) -> tuple[bool, str]:
    """Faz um POST de workflow_dispatch. Retorna (ok, msg_erro)."""
    payload = {"ref": "main", "inputs": inputs}
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.post(url, headers=headers, json=payload)
    except httpx.RequestError as e:
        return False, f"Erro de rede chamando GitHub: {e}"

    if r.status_code in (200, 204):
        logger.info(f"GitHub Action disparada ✅ (inputs: {list(inputs.keys())})")
        return True, ""

    # Erro — loga completo e retorna mensagem curta ao usuário
    body = r.text[:500]
    logger.error(f"Falha ao disparar Action: {r.status_code} {body}")

    # Mensagens amigáveis por código
    if r.status_code == 401:
        return False, (
            "GITHUB_TOKEN inválido ou expirado. Regenera em "
            "github.com/settings/tokens e atualiza o secret."
        )
    if r.status_code == 403:
        return False, (
            "GITHUB_TOKEN sem permissão de 'actions:write'. "
            "Regenera o token marcando a permissão correta."
        )
    if r.status_code == 404:
        return False, (
            f"Workflow 'generate-video.yml' não encontrado no repo "
            f"'{config.github_repo}'. Verifica GITHUB_REPO."
        )
    if r.status_code == 422:
        return False, f"GitHub rejeitou inputs: {body}"

    return False, f"HTTP {r.status_code}: {body[:200]}"


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

*/premium* — liga/desliga modo premium do roteiro
 _3 rascunhos + seleção automática do mais coeso (leva +30s)_

*/duracao* `<segundos>` — muda duração do vídeo
 _10 a 90 segundos (default 45)_

*/roteiro* `<tema>` — só roteiro (aprova antes)

*/refazer* — refaz último tema com variação

*/status* — mostra config atual + último tema

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


async def cmd_premium(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    """Liga/desliga modo premium do roteiro (3 rascunhos paralelos + seleção)."""
    if await _reject_if_unauthorized(update):
        return
    if not update.message:
        return
    prefs = _load_prefs()
    new_mode = "standard" if prefs.get("quality_mode") == "premium" else "premium"
    prefs["quality_mode"] = new_mode
    _save_prefs(prefs)
    if new_mode == "premium":
        await update.message.reply_text(
            "✨ *Modo PREMIUM ligado.*\n"
            "Próximos roteiros vão usar 3 rascunhos paralelos + seleção.\n"
            "Gera roteiro mais coeso, mas leva +30s e gasta mais do free tier.",
            parse_mode="Markdown",
        )
    else:
        await update.message.reply_text(
            "✅ Modo *standard* reativado.", parse_mode="Markdown"
        )


async def cmd_duracao(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Muda duração alvo do vídeo (segundos, 10-90)."""
    if await _reject_if_unauthorized(update):
        return
    if not update.message:
        return
    if not context.args:
        prefs = _load_prefs()
        await update.message.reply_text(
            f"⏱️ Duração atual: *{prefs.get('duration', 45)}s*\n"
            f"Uso: /duracao <segundos>  (10 a 90)",
            parse_mode="Markdown",
        )
        return
    try:
        seconds = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ Duração inválida (use número inteiro).")
        return
    if not 10 <= seconds <= 90:
        await update.message.reply_text("❌ Duração fora do intervalo 10-90s.")
        return
    prefs = _load_prefs()
    prefs["duration"] = seconds
    _save_prefs(prefs)
    await update.message.reply_text(
        f"✅ Duração agora é *{seconds}s*.", parse_mode="Markdown"
    )


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
        "quality_mode": prefs.get("quality_mode", "standard"),
        "duration": str(prefs.get("duration", 45)),
    }

    ok, msg = await _dispatch_github_action(inputs)
    if ok:
        quality_tag = " ✨ PREMIUM" if inputs["quality_mode"] == "premium" else ""
        reply = (
            f"🎬 Vídeo em produção!\n"
            f"Tema: *{theme}*\n"
            f"Template: `{prefs['template']}` | Voz: `{prefs['voice']}`\n"
            f"Duração: {inputs['duration']}s{quality_tag}\n\n"
            f"Leva cerca de 3-7 min. Te mando quando ficar pronto 🚀"
        )
        if msg:
            reply += f"\n\n{msg}"
        await update.message.reply_text(reply, parse_mode="Markdown")
    else:
        await update.message.reply_text(
            f"❌ Falha ao iniciar geração.\n\n*Motivo:* {msg}",
            parse_mode="Markdown",
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
    quality = prefs.get("quality_mode", "standard")
    quality_label = "✨ premium" if quality == "premium" else "standard"
    await update.message.reply_text(
        f"📋 *Config atual:*\n"
        f"Template: `{prefs['template']}`\n"
        f"Voz: `{prefs['voice']}`\n"
        f"Duração: `{prefs.get('duration', 45)}s`\n"
        f"Qualidade: `{quality_label}`\n"
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
    app.add_handler(CommandHandler("premium", cmd_premium))
    app.add_handler(CommandHandler("duracao", cmd_duracao))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    return app


def main() -> None:
    app = build_app()
    logger.info("🤖 Bot iniciado. Mande /start no Telegram.")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
