"""
Gerador de legendas ASS animadas estilo CapCut/Submagic.

Mudanças da reforma de qualidade:
- `word.word` SEMPRE vem limpo (sem pontuação). A pontuação que veio anexada
  está em `word.terminator` e é usada SÓ pra decidir quebra de janela.
- Janelas não são mais fixas em N palavras: quebram automaticamente em fim de
  frase (terminator contém `.`, `!`, `?`) ou ao atingir limite duro de 4.
- Vírgula NÃO quebra janela (pausa fica a cargo da narração/pontuação).
- Fade-in por evento (80ms) pra suavizar transição entre janelas.
- Tracking (\\fsp) e ease-out no pop da palavra ativa.
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
    outline_width: int = 7                 # grossura do contorno (+1 pra contraste)
    shadow_depth: int = 3
    margin_v: int = 450                    # distância do rodapé (centro-baixo)
    bold: bool = True
    italic: bool = False
    scale_pop: int = 115                   # zoom ao aparecer (%)
    pop_duration_ms: int = 150             # duração do ease-out (zoom→100)
    add_glow: bool = True
    fade_in_ms: int = 80                   # fade-in leve entre janelas
    max_words_per_window: int = 4          # limite duro (quebra em pontuação se antes)


# Estilos pré-definidos por template
STYLE_MOTIVACIONAL = CaptionStyle(
    font_name="Anton",
    font_size=96,
    primary_color="&H00FFFFFF",
    emphasis_color="&H0000D7FF",
    outline_color="&H00000000",
    outline_width=8,
    shadow_depth=4,
    scale_pop=120,
    add_glow=True,
    margin_v=500,
)

STYLE_VIRAL = CaptionStyle(
    font_name="Anton",
    font_size=100,
    primary_color="&H0000FFFF",
    emphasis_color="&H000000FF",
    outline_color="&H00000000",
    outline_width=9,
    shadow_depth=3,
    scale_pop=125,
    add_glow=True,
    margin_v=600,
)

STYLE_NOTICIAS = CaptionStyle(
    font_name="Arial",
    font_size=78,
    primary_color="&H00FFFFFF",
    emphasis_color="&H000000FF",
    outline_color="&H00000000",
    outline_width=6,
    shadow_depth=2,
    scale_pop=108,
    add_glow=False,
    margin_v=400,
    bold=True,
)

STYLE_GAMING = CaptionStyle(
    font_name="Anton",
    font_size=98,
    primary_color="&H0000FFFF",
    emphasis_color="&H00FF00FF",
    outline_color="&H00000000",
    outline_width=9,
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


# Pontuação que QUEBRA janela (fim de frase)
HARD_BREAK_PUNCTUATION = {".", "!", "?", "…"}


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
    ) -> None:
        self.style = style
        self.video_width = video_width
        self.video_height = video_height

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

    # ============================================
    # Janelas: quebram em pontuação forte OU em limite de N palavras
    # ============================================
    def _build_windows(self, words: list[WordTimestamp]) -> list[tuple[int, int]]:
        """
        Retorna lista de (start_idx, end_idx_exclusive) definindo cada janela.

        Regras:
        - Quebra quando a palavra corrente termina em `.!?…` (fim de frase).
        - Quebra quando atingir `max_words_per_window` (limite duro).
        - Vírgulas/dois-pontos NÃO quebram (pausa vem da narração).
        """
        windows: list[tuple[int, int]] = []
        if not words:
            return windows

        start = 0
        for i, w in enumerate(words):
            count_in_window = i - start + 1
            hits_limit = count_in_window >= self.style.max_words_per_window
            ends_sentence = bool(w.terminator) and any(
                c in HARD_BREAK_PUNCTUATION for c in w.terminator
            )
            if hits_limit or ends_sentence:
                windows.append((start, i + 1))
                start = i + 1

        if start < len(words):
            windows.append((start, len(words)))
        return windows

    def _build_events(self, words: list[WordTimestamp]) -> str:
        """
        Um evento ASS por PALAVRA. A janela inteira fica na tela enquanto
        a palavra ativa (atual) é animada com pop + cor de ênfase.

        Fim de frase (.!?) quebra janela antes do limite de 4 palavras.
        """
        windows = self._build_windows(words)
        logger.info(f"Captions: {len(windows)} janelas pra {len(words)} palavras")
        lines: list[str] = []
        s = self.style

        for w_start, w_end in windows:
            window_words = words[w_start:w_end]

            for rel_idx, word in enumerate(window_words):
                abs_idx = w_start + rel_idx
                # Timing: evento dura enquanto a palavra é falada; fecha no
                # início da próxima palavra pra não sobrepor janelas.
                start_t = word.start
                if abs_idx + 1 < len(words):
                    end_t = max(words[abs_idx + 1].start, start_t + 0.05)
                else:
                    end_t = word.end + 0.3

                text = self._render_window(window_words, rel_idx)
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

        Animação da palavra ativa:
        - Fade-in leve da linha inteira (\\fad no início do token)
        - Scale-pop com ease-out (\\t(0,pop_ms,\\fscx100\\fscy100))
        - Cor de ênfase
        - Glow opcional (\\blur)
        - Tracking aumentado (\\fsp) pra presença

        Palavras não-ativas ficam na cor primária sem animação.
        """
        s = self.style
        fade_prefix = f"{{\\fad({s.fade_in_ms},0)}}" if s.fade_in_ms > 0 else ""
        parts: list[str] = []

        for idx, word in enumerate(window_words):
            text = word.word.strip()
            if not text:
                continue

            if idx == active_index:
                # Palavra ativa: pop + cor + glow
                tags = (
                    f"{{\\fscx{s.scale_pop}\\fscy{s.scale_pop}"
                    f"\\t(0,{s.pop_duration_ms},\\fscx100\\fscy100)"
                    f"\\c{s.emphasis_color}"
                    f"\\3c{s.outline_color}"
                    f"\\fsp2"
                )
                if s.add_glow:
                    tags += "\\blur0.8"
                if word.is_emphasis:
                    # Palavra marcada como ênfase no roteiro: bold + fonte maior
                    tags += "\\b1\\fs" + str(int(s.font_size * 1.15))
                tags += "}"
                parts.append(f"{tags}{text}{{\\r}}")
            else:
                # Palavras da janela que não são ativas: cor primária
                parts.append(text)

        return fade_prefix + " ".join(parts)


def build_caption_generator(template: str, **kwargs) -> CaptionGenerator:
    """Factory: retorna CaptionGenerator com o estilo do template."""
    style = STYLES_BY_TEMPLATE.get(template, STYLE_MOTIVACIONAL)
    # Aceita apenas video_width e video_height (words_per_line foi removido)
    kwargs = {k: v for k, v in kwargs.items() if k in {"video_width", "video_height"}}
    return CaptionGenerator(style=style, **kwargs)
