"""
Narração com sistema de fallback em cascata.

1. Tenta Edge TTS (vozes brasileiras excelentes da Microsoft)
2. Se falhar (ex: 403 bloqueando IPs de cloud), cai pro gTTS
   (Google Translate TTS — qualidade menor mas 100% confiável)

Vozes Edge TTS PT-BR:
- pt-BR-AntonioNeural  (masculina, firme — motivacional)
- pt-BR-FranciscaNeural (feminina, clara — notícias)
- pt-BR-ThalitaNeural  (feminina, jovem — viral)
"""
from __future__ import annotations

import asyncio
import concurrent.futures
from pathlib import Path

import edge_tts
from tenacity import retry, stop_after_attempt, wait_exponential

from src.utils.logger import get_logger

logger = get_logger(__name__)


def _run_async_safely(coro):
    """
    Roda uma coroutine em qualquer contexto:
    - Se não há event loop: asyncio.run normal
    - Se já há event loop rodando: roda em thread separada
    """
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
        Gera narração MP3 com fallback automático:
        Edge TTS → gTTS (se edge falhar).
        """
        logger.info(f"Narrando {len(text)} chars (voz {self.voice})")
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # === TENTATIVA 1: Edge TTS (melhor qualidade) ===
        try:
            self._narrate_edge(text, output_path, rate, pitch)
            if output_path.exists() and output_path.stat().st_size > 0:
                logger.info(
                    f"✅ Edge TTS ok ({output_path.stat().st_size / 1024:.1f} KB)"
                )
                return output_path
        except Exception as e:
            logger.warning(f"⚠️ Edge TTS falhou: {type(e).__name__}: {e}")
            logger.warning("Caindo pro fallback gTTS (Google)...")

        # === TENTATIVA 2: gTTS (fallback confiável) ===
        try:
            self._narrate_gtts(text, output_path)
            if output_path.exists() and output_path.stat().st_size > 0:
                logger.info(
                    f"✅ gTTS ok ({output_path.stat().st_size / 1024:.1f} KB)"
                )
                return output_path
        except Exception as e:
            logger.error(f"❌ gTTS também falhou: {type(e).__name__}: {e}")
            raise RuntimeError(
                f"Nenhum TTS funcionou. Edge TTS bloqueado e gTTS falhou: {e}"
            ) from e

        raise RuntimeError("TTS não gerou áudio (arquivo vazio ou ausente)")

    # ============================================
    # Edge TTS (primário)
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
    # gTTS (fallback) — sempre funciona
    # ============================================
    def _narrate_gtts(self, text: str, output_path: Path) -> None:
        """
        gTTS usa a TTS do Google Translate.
        - Não tem controle de voz (usa a padrão pt-BR)
        - Não tem controle de velocidade (exceto slow=True/False)
        - Mas sempre funciona de qualquer IP
        """
        from gtts import gTTS

        # Velocidade: slow=False é o padrão "rápido" do gTTS
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
