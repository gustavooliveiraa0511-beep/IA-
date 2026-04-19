"""
Narração com sistema de fallback em cascata de 3 níveis.

1. Edge TTS (Microsoft) — vozes brasileiras nomeadas, qualidade TOP
2. Piper TTS — neural LOCAL, sem dependência de internet, qualidade boa
3. gTTS (Google) — robótica, mas SEMPRE funciona (último recurso)

Vozes Edge TTS PT-BR:
- pt-BR-AntonioNeural  (masculina, firme — motivacional)
- pt-BR-FranciscaNeural (feminina, clara — notícias)
- pt-BR-ThalitaNeural  (feminina, jovem — viral)

Piper voice: pt_BR-faber-medium (masculina neural, ~63MB)
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import subprocess
import os
from pathlib import Path

import edge_tts
import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from src.utils.config import ASSETS_DIR
from src.utils.logger import get_logger

logger = get_logger(__name__)


PIPER_MODELS_DIR = ASSETS_DIR / "piper_models"
PIPER_VOICE_NAME = "pt_BR-faber-medium"
PIPER_MODEL_URL = (
    "https://huggingface.co/rhasspy/piper-voices/resolve/main/"
    "pt/pt_BR/faber/medium/pt_BR-faber-medium.onnx"
)
PIPER_CONFIG_URL = (
    "https://huggingface.co/rhasspy/piper-voices/resolve/main/"
    "pt/pt_BR/faber/medium/pt_BR-faber-medium.onnx.json"
)


def _run_async_safely(coro):
    """Roda coroutine em qualquer contexto (sync ou async)."""
    try:
        asyncio.get_running_loop()
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(asyncio.run, coro)
            return future.result()
    except RuntimeError:
        return asyncio.run(coro)


class Narrator:
    def __init__(self, voice: str = "pt-BR-AntonioNeural") -> None:
        self.voice = voice

    def narrate(self, text: str, output_path: Path, rate: str = "+0%", pitch: str = "+0Hz") -> Path:
        """
        Gera narração com fallback em 3 níveis:
        Edge TTS → Piper TTS (local neural) → gTTS (robótica mas ok).
        """
        logger.info(f"Narrando {len(text)} chars (voz {self.voice})")
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # === NÍVEL 1: Edge TTS ===
        try:
            self._narrate_edge(text, output_path, rate, pitch)
            if output_path.exists() and output_path.stat().st_size > 1000:
                logger.info(
                    f"✅ Edge TTS ok ({output_path.stat().st_size / 1024:.1f} KB)"
                )
                return output_path
        except Exception as e:
            logger.warning(f"⚠️ Edge TTS falhou: {type(e).__name__}: {e}")
            logger.warning("Tentando Piper TTS (neural local)...")

        # === NÍVEL 2: Piper TTS (neural local) ===
        try:
            self._narrate_piper(text, output_path)
            if output_path.exists() and output_path.stat().st_size > 1000:
                logger.info(
                    f"✅ Piper TTS ok ({output_path.stat().st_size / 1024:.1f} KB)"
                )
                return output_path
        except Exception as e:
            logger.warning(f"⚠️ Piper TTS falhou: {type(e).__name__}: {e}")
            logger.warning("Caindo pro gTTS (último recurso)...")

        # === NÍVEL 3: gTTS (último recurso) ===
        try:
            self._narrate_gtts(text, output_path)
            if output_path.exists() and output_path.stat().st_size > 1000:
                logger.info(
                    f"✅ gTTS ok ({output_path.stat().st_size / 1024:.1f} KB)"
                )
                return output_path
        except Exception as e:
            logger.error(f"❌ gTTS também falhou: {type(e).__name__}: {e}")
            raise RuntimeError(
                f"Nenhum TTS funcionou. Último erro: {e}"
            ) from e

        raise RuntimeError("TTS não gerou áudio válido (arquivo vazio)")

    # ============================================
    # NÍVEL 1: Edge TTS
    # ============================================
    @retry(stop=stop_after_attempt(2), wait=wait_exponential(multiplier=2, min=2, max=6), reraise=True)
    def _narrate_edge(self, text: str, output_path: Path, rate: str, pitch: str) -> None:
        _run_async_safely(self._generate_async_edge(text, output_path, rate, pitch))
        if not output_path.exists() or output_path.stat().st_size == 0:
            raise RuntimeError("Edge TTS não produziu áudio")

    async def _generate_async_edge(
        self, text: str, output_path: Path, rate: str, pitch: str
    ) -> None:
        communicate = edge_tts.Communicate(
            text,
            voice=self.voice,
            rate=rate,
            pitch=pitch,
        )
        await communicate.save(str(output_path))

    # ============================================
    # NÍVEL 2: Piper TTS (neural local)
    # ============================================
    def _narrate_piper(self, text: str, output_path: Path) -> None:
        """
        Piper TTS: modelo neural local (não precisa internet após setup).
        Baixa o modelo se não existir (primeira vez ~63MB).
        Gera WAV via piper CLI, depois converte pra MP3.
        """
        model_path, config_path = self._ensure_piper_model()

        # Piper gera WAV; salvamos num tmp e convertemos
        wav_path = output_path.with_suffix(".piper.wav")

        # Roda piper via subprocess (piper-tts instalado via pip expõe CLI)
        cmd = [
            "piper",
            "--model", str(model_path),
            "--config", str(config_path),
            "--output_file", str(wav_path),
        ]
        result = subprocess.run(
            cmd,
            input=text,
            text=True,
            capture_output=True,
            timeout=120,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"Piper falhou: {result.stderr[-500:] if result.stderr else 'sem stderr'}"
            )
        if not wav_path.exists() or wav_path.stat().st_size == 0:
            raise RuntimeError("Piper não gerou WAV")

        # Converte WAV → MP3 via ffmpeg
        conv = subprocess.run(
            [
                "ffmpeg", "-y", "-i", str(wav_path),
                "-codec:a", "libmp3lame", "-b:a", "128k",
                str(output_path),
            ],
            capture_output=True,
            text=True,
        )
        wav_path.unlink(missing_ok=True)
        if conv.returncode != 0:
            raise RuntimeError(f"ffmpeg WAV→MP3 falhou: {conv.stderr[-300:]}")

    def _ensure_piper_model(self) -> tuple[Path, Path]:
        """Baixa modelo Piper na primeira vez (~63MB)."""
        PIPER_MODELS_DIR.mkdir(parents=True, exist_ok=True)
        model_path = PIPER_MODELS_DIR / f"{PIPER_VOICE_NAME}.onnx"
        config_path = PIPER_MODELS_DIR / f"{PIPER_VOICE_NAME}.onnx.json"

        if not model_path.exists():
            logger.info(f"Baixando modelo Piper {PIPER_VOICE_NAME} (~63MB)...")
            self._download(PIPER_MODEL_URL, model_path)
        if not config_path.exists():
            self._download(PIPER_CONFIG_URL, config_path)

        return model_path, config_path

    @staticmethod
    def _download(url: str, target: Path) -> None:
        with httpx.stream("GET", url, timeout=120.0, follow_redirects=True) as r:
            r.raise_for_status()
            with open(target, "wb") as f:
                for chunk in r.iter_bytes():
                    f.write(chunk)

    # ============================================
    # NÍVEL 3: gTTS (último recurso)
    # ============================================
    def _narrate_gtts(self, text: str, output_path: Path) -> None:
        from gtts import gTTS
        tts = gTTS(text=text, lang="pt", tld="com.br", slow=False)
        tts.save(str(output_path))

    @staticmethod
    def list_brazilian_voices() -> list[dict]:
        """Lista vozes brasileiras disponíveis no Edge TTS."""
        async def _list():
            voices = await edge_tts.list_voices()
            return [v for v in voices if v["Locale"].startswith("pt-BR")]
        return _run_async_safely(_list())


# Voz recomendada por template
VOICE_BY_TEMPLATE = {
    "motivacional": "pt-BR-AntonioNeural",
    "viral": "pt-BR-ThalitaNeural",
    "noticias": "pt-BR-FranciscaNeural",
    "gaming": "pt-BR-AntonioNeural",
}


# Ajuste de velocidade por template
RATE_BY_TEMPLATE = {
    "motivacional": "-5%",
    "viral": "+15%",
    "noticias": "+0%",
    "gaming": "+20%",
}
