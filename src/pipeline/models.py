"""
Modelos de dados que atravessam o pipeline inteiro.
Do comando do usuário até o vídeo final.
"""
from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, Field


class TemplateType(str, Enum):
    MOTIVACIONAL = "motivacional"
    VIRAL = "viral"
    NOTICIAS = "noticias"
    GAMING = "gaming"


class SceneType(str, Enum):
    """Tipo de cada cena do vídeo."""
    VIDEO_BROLL = "video_broll"       # vídeo de fundo (Pexels)
    IMAGE_KENBURNS = "image_kenburns"  # imagem com zoom/pan
    COLOR_BACKGROUND = "color_background"  # fundo liso com frase de impacto
    PERSON_PHOTO = "person_photo"     # foto de pessoa citada


class VideoRequest(BaseModel):
    """Pedido do usuário (vem do Telegram)."""
    theme: str
    template: TemplateType = TemplateType.MOTIVACIONAL
    duration_seconds: int = 45  # balanceamento: tempo suficiente sem saturar
    voice: str = "pt-BR-AntonioNeural"
    custom_script: Optional[str] = None  # se o usuário quiser mandar o próprio texto


class ScriptLine(BaseModel):
    """Uma frase do roteiro com instruções de cena."""
    text: str
    scene_type: SceneType
    visual_query: str = ""  # termo de busca no Pexels/Pixabay
    person_name: Optional[str] = None  # se scene_type == PERSON_PHOTO
    bg_color: Optional[str] = None  # hex se scene_type == COLOR_BACKGROUND
    emphasis_words: list[str] = Field(default_factory=list)  # palavras pra destacar


class Script(BaseModel):
    """Roteiro completo."""
    lines: list[ScriptLine]
    title: str = ""
    hashtags: list[str] = Field(default_factory=list)

    @property
    def full_text(self) -> str:
        return " ".join(line.text for line in self.lines)


class WordTimestamp(BaseModel):
    """Timestamp de uma palavra (saída do Whisper)."""
    word: str
    start: float  # segundos
    end: float
    is_emphasis: bool = False


class Scene(BaseModel):
    """Uma cena renderizada (com mídia + timing)."""
    script_line: ScriptLine
    start_time: float
    end_time: float
    media_path: Optional[Path] = None  # None se COLOR_BACKGROUND
    words: list[WordTimestamp] = Field(default_factory=list)

    @property
    def duration(self) -> float:
        return self.end_time - self.start_time

    class Config:
        arbitrary_types_allowed = True


class VideoJob(BaseModel):
    """Estado completo de um job de geração."""
    job_id: str
    request: VideoRequest
    script: Optional[Script] = None
    narration_path: Optional[Path] = None
    scenes: list[Scene] = Field(default_factory=list)
    final_video_path: Optional[Path] = None
    public_url: Optional[str] = None
    status: Literal["pending", "scripting", "narrating", "fetching_media",
                    "editing", "uploading", "done", "error"] = "pending"
    error_message: Optional[str] = None

    class Config:
        arbitrary_types_allowed = True
