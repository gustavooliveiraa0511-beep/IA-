"""
Transcreve o áudio narrado e retorna timestamp de CADA palavra.

Usa faster-whisper (mais rápido que whisper oficial) com o modelo "small"
que é leve o suficiente pra rodar no GitHub Actions.

Os timestamps por palavra são o que permite legendas animadas
palavra-por-palavra estilo CapCut/Submagic.
"""
from __future__ import annotations

from pathlib import Path

from faster_whisper import WhisperModel

from src.pipeline.models import WordTimestamp
from src.utils.logger import get_logger

logger = get_logger(__name__)


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
                word_text = (w.word or "").strip()
                if not word_text:
                    continue
                words.append(
                    WordTimestamp(
                        word=word_text,
                        start=w.start,
                        end=w.end,
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
        emphasis_lower = {w.lower().strip(".,!?") for w in emphasis_words}
        for w in words:
            clean = w.word.lower().strip(".,!?")
            if clean in emphasis_lower:
                w.is_emphasis = True
        return words
