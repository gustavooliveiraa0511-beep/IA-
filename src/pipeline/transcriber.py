"""
Transcreve áudio narrado e retorna timestamp de CADA palavra.

⚠️ Depois da reforma de qualidade, o Transcriber é FALLBACK, não caminho primário:
- Caminho primário: WordBoundary do Edge TTS (timestamps exatos, sem pontuação).
- Fallback: quando Edge TTS cai pra Piper/gTTS, o áudio não tem WordBoundary
  e precisamos transcrever com Whisper.

Usa faster-whisper (mais rápido que whisper oficial) com compute_type int8.

BUG HISTÓRICO: antes a pontuação vinha anexada à palavra (ex: "esperança,")
e ia pra legenda queimada no vídeo. Agora `_clean_word` separa a pontuação
num campo `terminator` — a palavra fica limpa, a pontuação só serve pra lógica
de quebra de janela de legenda.
"""
from __future__ import annotations

from pathlib import Path

from faster_whisper import WhisperModel

from src.pipeline.models import WordTimestamp
from src.utils.logger import get_logger

logger = get_logger(__name__)


PUNCTUATION_CHARS = ".,!?;:…—\"')]}>»›"


def _clean_word(raw: str) -> tuple[str, str]:
    """
    Separa a palavra limpa da pontuação que vinha anexada.

    Exemplos:
        "esperança," → ("esperança", ",")
        "mundo."     → ("mundo", ".")
        "sério?!"    → ("sério", "?!")
        "olá"        → ("olá", "")

    Pontuação inicial (abertura de parênteses/aspas) também é removida mas
    descartada — não precisamos dela pra decidir pausa.
    """
    raw = raw.strip()
    # Remove pontuação de abertura no começo
    while raw and raw[0] in "\"'([{<«‹":
        raw = raw[1:]
    # Coleta pontuação de fechamento/terminação no fim
    trailing = ""
    while raw and raw[-1] in PUNCTUATION_CHARS:
        trailing = raw[-1] + trailing
        raw = raw[:-1]
    return raw, trailing


class Transcriber:
    _model: WhisperModel | None = None

    def __init__(self, model_size: str = "small", device: str = "cpu") -> None:
        self.model_size = model_size
        self.device = device

    @classmethod
    def _get_model(cls, size: str, device: str) -> WhisperModel:
        if cls._model is None:
            logger.info(f"Carregando modelo Whisper {size} ({device})...")
            # int8 é o mais leve pra CPU
            cls._model = WhisperModel(size, device=device, compute_type="int8")
        return cls._model

    def transcribe(self, audio_path: Path, language: str = "pt") -> list[WordTimestamp]:
        model = self._get_model(self.model_size, self.device)
        logger.info(f"Transcrevendo {audio_path.name}...")

        segments, info = model.transcribe(
            str(audio_path),
            language=language,
            word_timestamps=True,  # essencial pra animação palavra-a-palavra
            vad_filter=True,        # remove silêncios
            beam_size=5,
        )

        words: list[WordTimestamp] = []
        for segment in segments:
            if not segment.words:
                continue
            for w in segment.words:
                raw = (w.word or "").strip()
                if not raw:
                    continue
                clean, terminator = _clean_word(raw)
                if not clean:
                    # Se sobrou só pontuação, grampeia na última palavra
                    if terminator and words:
                        words[-1].terminator = (words[-1].terminator or "") + terminator
                    continue
                words.append(
                    WordTimestamp(
                        word=clean,
                        start=w.start,
                        end=w.end,
                        terminator=terminator,
                    )
                )

        logger.info(
            f"Transcrito: {len(words)} palavras | "
            f"duração={info.duration:.2f}s | idioma={info.language}"
        )
        return words

    @staticmethod
    def mark_emphasis(
        words: list[WordTimestamp], emphasis_words: list[str]
    ) -> list[WordTimestamp]:
        """Marca palavras que devem ser destacadas na legenda."""
        emphasis_lower = {w.lower().strip(PUNCTUATION_CHARS + " ") for w in emphasis_words}
        for w in words:
            # word já vem limpa, mas garantimos lowercase
            if w.word.lower() in emphasis_lower:
                w.is_emphasis = True
        return words
