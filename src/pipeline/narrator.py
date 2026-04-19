"""
Narração usando Edge TTS (Microsoft) — 100% grátis, ilimitado.

Vozes PT-BR recomendadas:
- pt-BR-AntonioNeural  (masculina, firme — bom pra motivacional)
- pt-BR-FranciscaNeural (feminina, clara — bom pra notícias)
- pt-BR-ThalitaNeural  (feminina, jovem — bom pra viral)
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
    Roda uma coroutine funcionando em qualquer contexto:
    - Se não há event loop: usa asyncio.run normal
    - Se já há event loop rodando: roda em thread separada pra não conflitar
    """
    try:
        asyncio.get_running_loop()
        # Já estamos num event loop — roda em thread separada
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(asyncio.run, coro)
            return future.result()
    except RuntimeError:
        # Sem event loop ativo — asyncio.run funciona
        return asyncio.run(coro)


class Narrator:
    def __init__(self, voice: str = "pt-BR-AntonioNeural") -> None:
        self.voice = voice

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, min=2, max=10), reraise=True)
    def narrate(self, text: str, output_path: Path, rate: str = "+0%", pitch: str = "+0Hz") -> Path:
        """
        Gera narração MP3 do texto.

        rate: velocidade (ex: "+10%" mais rápido, "-5%" mais devagar)
        pitch: tom (ex: "+2Hz" mais agudo)
        """
        logger.info(f"Narrando {len(text)} chars com voz {self.voice}")
        output_path.parent.mkdir(parents=True, exist_ok=True)

        _run_async_safely(self._generate_async(text, output_path, rate, pitch))

        if not output_path.exists() or output_path.stat().st_size == 0:
            raise RuntimeError("Edge TTS não gerou áudio")

        logger.info(f"Áudio salvo em {output_path} ({output_path.stat().st_size / 1024:.1f} KB)")
        return output_path

    async def _generate_async(self, text: str, output_path: Path, rate: str, pitch: str) -> None:
        communicate = edge_tts.Communicate(
            text,
            voice=self.voice,
            rate=rate,
            pitch=pitch,
        )
        await communicate.save(str(output_path))

    @staticmethod
    def list_brazilian_voices() -> list[dict]:
        """Lista vozes brasileiras disponíveis."""
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


# Ajuste de velocidade por template (motivacional fala com pausa; viral acelerado)
RATE_BY_TEMPLATE = {
    "motivacional": "-5%",
    "viral": "+15%",
    "noticias": "+0%",
    "gaming": "+20%",
}
