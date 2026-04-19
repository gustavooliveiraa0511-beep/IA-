"""
Gerador de legendas ASS (Advanced SubStation Alpha) animadas
estilo CapCut/Submagic.

O formato ASS permite:
- Cor, sombra, borda grossa
- Animações (\\t, \\fad, \\move, \\fscx, \\fscy)
- Karaokê por palavra (\\k)
- Palavra destacada aparecendo uma a uma
- Efeito de brilho e glow

Este módulo monta o arquivo .ass a partir dos timestamps do Whisper
e das palavras de ênfase definidas no roteiro.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from src.pipeline.models import WordTimestamp
from src.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class CaptionStyle:
    """Define a aparência da legenda. Cada template tem o seu."""
    font_name: str = "Anton"
    font_size: int = 92
    primary_color: str = "&H00FFFFFF"      # branco (BGR + alpha invertido)
    emphasis_color: str = "&H0000D7FF"     # laranja/dourado (destaque)
    outline_color: str = "&H00000000"      # preto (contorno)
    shadow_color: str = "&H80000000"       # preto semi-transparente
    outline_width: int = 6                 # grossura do contorno
    shadow_depth: int = 3                  # profundidade da sombra
    margin_v: int = 450                    # distância do rodapé (pra aparecer no centro-baixo)
    bold: bool = True
    italic: bool = False
    scale_pop: int = 115                   # zoom ao aparecer (%)
    pop_duration_ms: int = 120             # duração do zoom
    add_glow: bool = True                  # brilho extra na ênfase


# Estilos pré-definidos por template
STYLE_MOTIVACIONAL = CaptionStyle(
    font_name="Anton",
    font_size=96,
    primary_color="&H00FFFFFF",       # branco
    emphasis_color="&H0000D7FF",      # dourado (#FFD700 em BGR)
    outline_color="&H00000000",       # preto
    outline_width=7,
    shadow_depth=4,
    scale_pop=120,
    add_glow=True,
    margin_v=500,
)

STYLE_VIRAL = CaptionStyle(
    font_name="Anton",
    font_size=100,
    primary_color="&H0000FFFF",       # amarelo
    emphasis_color="&H000000FF",      # vermelho vibrante
    outline_color="&H00000000",
    outline_width=8,
    shadow_depth=3,
    scale_pop=125,
    add_glow=True,
    margin_v=600,
)

STYLE_NOTICIAS = CaptionStyle(
    font_name="Arial",
    font_size=78,
    primary_color="&H00FFFFFF",
    emphasis_color="&H000000FF",      # vermelho sóbrio
    outline_color="&H00000000",
    outline_width=5,
    shadow_depth=2,
    scale_pop=108,
    add_glow=False,
    margin_v=400,
    bold=True,
)

STYLE_GAMING = CaptionStyle(
    font_name="Anton",
    font_size=98,
    primary_color="&H0000FFFF",       # amarelo
    emphasis_color="&H00FF00FF",      # magenta
    outline_color="&H00000000",
    outline_width=8,
    scale_pop=130,
    add_glow=True,
    margin_v=550,
)


STYLES_BY_TEMPLATE = {
    "motivacional": STYLE_MOTIVACIONAL,
    "viral": STYLE_VIRAL,
    "noticias": STYLE_NOTICIAS,
    "gaming": STYLE_GAMING,
}


def _seconds_to_ass_time(seconds: float) -> str:
    """Converte segundos pra formato ASS: H:MM:SS.CC (centésimos)."""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h}:{m:02d}:{s:05.2f}"


class CaptionGenerator:
    """Gera arquivo .ass com legendas animadas palavra-por-palavra."""

    def __init__(
        self,
        style: CaptionStyle,
        video_width: int = 1080,
        video_height: int = 1920,
        words_per_line: int = 3,
    ) -> None:
        self.style = style
        self.video_width = video_width
        self.video_height = video_height
        # Quantas palavras aparecem simultaneamente na tela.
        # Estilo CapCut moderno usa 2-4 palavras por vez.
        self.words_per_line = words_per_line

    def generate(self, words: list[WordTimestamp], output_path: Path) -> Path:
        """Gera arquivo .ass e salva em output_path."""
        logger.info(f"Gerando legenda ASS para {len(words)} palavras")

        header = self._build_header()
        events = self._build_events(words)

        content = header + "\n" + events
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(content, encoding="utf-8")
        logger.info(f"Legenda salva em {output_path}")
        return output_path

    def _build_header(self) -> str:
        s = self.style
        return f"""[Script Info]
Title: Video Maker Bot
ScriptType: v4.00+
WrapStyle: 0
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709
PlayResX: {self.video_width}
PlayResY: {self.video_height}

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{s.font_name},{s.font_size},{s.primary_color},{s.primary_color},{s.outline_color},{s.shadow_color},{-1 if s.bold else 0},{-1 if s.italic else 0},0,0,100,100,0,0,1,{s.outline_width},{s.shadow_depth},2,60,60,{s.margin_v},1
Style: Emphasis,{s.font_name},{int(s.font_size * 1.05)},{s.emphasis_color},{s.emphasis_color},{s.outline_color},{s.shadow_color},{-1 if s.bold else 0},{-1 if s.italic else 0},0,0,100,100,0,0,1,{s.outline_width + 1},{s.shadow_depth + 1},2,60,60,{s.margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"""

    def _build_events(self, words: list[WordTimestamp]) -> str:
        """
        Agrupa palavras em janelas de N palavras (words_per_line) e
        dentro de cada janela anima cada palavra aparecendo com pop
        e destacando a palavra 'atual'.

        Cria um evento por palavra: a janela atualiza palavra por
        palavra (estilo CapCut moderno).
        """
        lines: list[str] = []
        n = len(words)
        s = self.style

        for i in range(n):
            # Janela: palavras [start..end)
            window_start = (i // self.words_per_line) * self.words_per_line
            window_end = min(window_start + self.words_per_line, n)
            window_words = words[window_start:window_end]

            # Palavra "ativa" (sendo falada) dentro da janela
            active_index_in_window = i - window_start

            # Timing: esta linha aparece enquanto a palavra ativa é falada.
            # Fecha exatamente no start da próxima palavra pra evitar overlap
            # e flicker de 2 eventos ASS renderizados ao mesmo tempo.
            start_t = words[i].start
            if i + 1 < n:
                end_t = max(words[i + 1].start, start_t + 0.05)
            else:
                end_t = words[i].end + 0.3

            text = self._render_window(
                window_words,
                active_index_in_window,
            )

            lines.append(
                f"Dialogue: 0,{_seconds_to_ass_time(start_t)},"
                f"{_seconds_to_ass_time(end_t)},Default,,0,0,0,,{text}"
            )

        return "\n".join(lines)

    def _render_window(
        self,
        window_words: list[WordTimestamp],
        active_index: int,
    ) -> str:
        """
        Monta a linha de legenda com a palavra ativa destacada.

        A palavra ativa recebe:
        - cor de destaque
        - zoom (pop) no momento de aparecer
        - glow opcional
        """
        s = self.style
        parts: list[str] = []

        for idx, word in enumerate(window_words):
            text = word.word.strip()
            if not text:
                continue

            if idx == active_index:
                # Palavra ativa: cor de destaque + pop in
                # \t(0,pop_ms,\fscx\fscy=100) faz o zoom decrescer
                pop_tag = (
                    f"{{\\fscx{s.scale_pop}\\fscy{s.scale_pop}"
                    f"\\t(0,{s.pop_duration_ms},\\fscx100\\fscy100)"
                    f"\\c{s.emphasis_color}"
                    f"\\3c{s.outline_color}"
                )
                if s.add_glow:
                    # Glow via blur (\blur)
                    pop_tag += "\\blur0.8"
                # Palavra de ênfase marcada pelo roteiro ganha bold extra
                if word.is_emphasis:
                    pop_tag += "\\b1\\fs" + str(int(s.font_size * 1.15))
                pop_tag += "}"
                parts.append(f"{pop_tag}{text}{{\\r}}")
            else:
                # Palavras passadas/futuras ficam em cor primária
                # Se for palavra já falada (antes da ativa), fica mais opaca
                parts.append(text)

        return " ".join(parts)


def build_caption_generator(template: str, **kwargs) -> CaptionGenerator:
    """Factory: retorna CaptionGenerator com o estilo do template."""
    style = STYLES_BY_TEMPLATE.get(template, STYLE_MOTIVACIONAL)
    return CaptionGenerator(style=style, **kwargs)
