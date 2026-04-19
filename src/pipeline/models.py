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


class BeatType(str, Enum):
    """
    Papel narrativo da cena. Afeta a velocidade de narração:
    - hook: abertura que prende (ritmo firme, levemente mais rápido)
    - development: argumento principal (ritmo neutro)
    - climax: virada / frase-chave (ritmo MAIS lento, dramático)
    - cta: call-to-action / fechamento (ritmo firme, autoritário)
    """
    HOOK = "hook"
    DEVELOPMENT = "development"
    CLIMAX = "climax"
    CTA = "cta"


class ImpactLevel(str, Enum):
    """
    Nível de impacto visual da legenda, marcado pela IA no roteiro.
    - low: cena normal (tamanho base)
    - medium: argumento forte (legenda 20% maior)
    - high: FRASE BOMBA — billboard gigante no centro (usar 1-2x por vídeo)
    """
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ScriptLine(BaseModel):
    """Uma frase do roteiro com instruções de cena."""
    text: str
    scene_type: SceneType
    visual_query: str = ""  # termo de busca no Pexels/Pixabay (primeira query)
    visual_queries: list[str] = Field(default_factory=list)  # alternativas (até 3) pra fetchers tentarem até achar b-roll bom
    person_name: Optional[str] = None  # se scene_type == PERSON_PHOTO
    bg_color: Optional[str] = None  # hex se scene_type == COLOR_BACKGROUND
    emphasis_words: list[str] = Field(default_factory=list)  # palavras pra destacar
    beat_type: BeatType = BeatType.DEVELOPMENT  # papel narrativo (afeta ritmo da narração)
    impact_level: ImpactLevel = ImpactLevel.LOW  # intensidade visual da legenda (IA marca)


class Script(BaseModel):
    """Roteiro completo."""
    lines: list[ScriptLine]
    title: str = ""
    hashtags: list[str] = Field(default_factory=list)

    @property
    def full_text(self) -> str:
        """
        Junta todas as linhas garantindo pontuação no fim de cada cena.
        Isso cria pausas naturais na narração (TTS respeita pontuação).
        """
        parts = []
        for line in self.lines:
            text = line.text.strip()
            if not text:
                continue
            # Se a cena não termina em pontuação forte, adiciona ponto
            if text[-1] not in ".!?":
                text = text + "."
            parts.append(text)
        return " ".join(parts)


class WordTimestamp(BaseModel):
    """
    Timestamp de uma palavra.

    Fonte primária: stream WordBoundary do Edge TTS (tempo exato, sem pontuação).
    Fonte fallback: faster-whisper (quando TTS cai pro Piper/gTTS).

    ⚠️ `word` é SEMPRE a palavra LIMPA (sem pontuação). A pontuação que vinha
    anexada fica em `terminator` e é usada pra decidir quebra de legenda.

    `beat_type` é propagado pelo orchestrator depois do alinhamento, pra que
    o gerador de legendas possa variar posição por beat (hook=centro, climax=topo).
    Default DEVELOPMENT evita `None` em words que o realign criou sem scene.
    """
    word: str
    start: float  # segundos
    end: float
    is_emphasis: bool = False
    terminator: str = ""  # pontuação que terminava a palavra: "", ".", ",", "!", "?", "...", ";", ":"
    beat_type: BeatType = BeatType.DEVELOPMENT
    impact_level: ImpactLevel = ImpactLevel.LOW


class Scene(BaseModel):
    """Uma cena renderizada (com mídia + timing).

    Quando cenas longas são quebradas em sub-clips visuais (pra aumentar ritmo
    e retenção), os sub-clips COMPARTILHAM a ScriptLine mas têm `clip_index`
    diferente. Isso permite que o MediaDispatcher busque mídia alternativa
    (rotacionando `visual_queries`) pra cada sub-clip, evitando repetição
    visual na mesma frase.
    """
    script_line: ScriptLine
    start_time: float
    end_time: float
    media_path: Optional[Path] = None  # None se COLOR_BACKGROUND
    words: list[WordTimestamp] = Field(default_factory=list)
    clip_index: int = 0       # posição do sub-clip dentro da cena original (0,1,2...)
    clip_total: int = 1       # quantos sub-clips a cena foi dividida
    flash_intro: bool = False  # flash branco nos primeiros 100ms (pattern interrupt em mudança de beat)

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
