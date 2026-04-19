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

from src.pipeline.models import BeatType, ImpactLevel, WordTimestamp
from src.utils.logger import get_logger

logger = get_logger(__name__)


# Posições seguras por beat_type (vídeo 1080x1920, evitando safe zones da UI 2026):
# - Top 150px = status/câmera do Instagram/TikTok
# - Bottom 280px = caption + audio track
# - Right 90px = ícones de engagement
# Zona segura = y entre 180 e 1640, x centralizado em 540.
#
# Cada entry é (y_center, descrição). x=540 sempre (centro horizontal).
POSITION_BY_BEAT: dict[BeatType, tuple[int, str]] = {
    BeatType.HOOK: (960, "centro"),          # CENTRO — prende atenção nos 1.5s iniciais
    BeatType.DEVELOPMENT: (1450, "inferior"), # inferior padrão (zona segura)
    BeatType.CLIMAX: (420, "superior"),       # TOPO — pattern interrupt dramático
    BeatType.CTA: (1400, "inferior"),         # inferior firme (acima da UI)
}


# Multiplicador de tamanho da fonte por beat (aplicado em \fs inline).
# Permite variação sutil: hook um pouco maior pra grip, climax maior pra drama.
SIZE_MULTIPLIER_BY_BEAT: dict[BeatType, float] = {
    BeatType.HOOK: 1.15,
    BeatType.DEVELOPMENT: 1.00,
    BeatType.CLIMAX: 1.25,
    BeatType.CTA: 1.10,
}


# Multiplicador de tamanho por impact_level (marcado pela IA no roteiro).
# HIGH aciona o modo "billboard" — renderizado por outra função.
SIZE_MULTIPLIER_BY_IMPACT: dict[ImpactLevel, float] = {
    ImpactLevel.LOW: 1.00,
    ImpactLevel.MEDIUM: 1.20,
    ImpactLevel.HIGH: 1.80,
}


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
    # Variação de posição por beat (se None, usa margin_v fixo em alignment 2).
    # Quando ligado, cada janela aparece na posição correspondente ao beat_type
    # da sua palavra ativa.
    vary_position_by_beat: bool = True
    # Punch-in em palavras de ênfase: zoom MUITO maior pra destacar
    emphasis_pop_scale: int = 140          # era scale_pop (115%), ênfase vai a 140%
    emphasis_pop_duration_ms: int = 120    # um pouco mais rápido pra dar "tapa"


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

        Posição e tamanho variam por beat_type + impact_level da palavra ativa.
        Quando impact_level=HIGH, usa render "billboard" (palavra GIGANTE
        no centro em vez da janela normal).
        """
        windows = self._build_windows(words)
        logger.info(f"Captions: {len(windows)} janelas pra {len(words)} palavras")
        lines: list[str] = []

        for w_idx, (w_start, w_end) in enumerate(windows):
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

                # Cenas HIGH impact usam render "billboard" (palavra gigante
                # sozinha no centro) ao invés da janela normal.
                if word.impact_level == ImpactLevel.HIGH:
                    text = self._render_billboard(word)
                else:
                    text = self._render_window(window_words, rel_idx, w_idx)
                lines.append(
                    f"Dialogue: 0,{_seconds_to_ass_time(start_t)},"
                    f"{_seconds_to_ass_time(end_t)},Default,,0,0,0,,{text}"
                )

        return "\n".join(lines)

    def _render_billboard(self, word: WordTimestamp) -> str:
        """
        Render BILLBOARD: palavra sozinha GIGANTE no centro da tela, usada
        quando impact_level=HIGH. Reservado pra no máximo 2 momentos por vídeo
        (a IA marca no roteiro — se marcar mais, script_writer rebaixa).

        Estilo: fonte 1.8x, centralizada, contorno preto grosso (billboard
        outdoor feel), entra com rotação leve + pop, cor de ênfase cheia.
        """
        s = self.style
        text = word.word.strip()
        if not text:
            return ""

        big_size = int(s.font_size * SIZE_MULTIPLIER_BY_IMPACT[ImpactLevel.HIGH])
        x_center = self.video_width // 2
        y_center = self.video_height // 2

        tags = (
            f"{{\\an5\\pos({x_center},{y_center})"
            f"\\fs{big_size}"
            f"\\c{s.emphasis_color}"
            f"\\3c{s.outline_color}"
            f"\\bord{max(12, s.outline_width + 4)}"   # contorno GROSSO
            f"\\shad{max(5, s.shadow_depth + 2)}"
            f"\\b1"
            f"\\fad(120,60)"
            f"\\fscx150\\fscy150"
            f"\\t(0,200,\\fscx100\\fscy100)"          # pop de entrada
            f"\\frz-2\\t(0,180,\\frz0)"               # pequena inclinação
            f"}}"
        )
        return f"{tags}{text}{{\\r}}"

    def _position_override(self, beat: BeatType) -> str:
        """
        Retorna override ASS `\\pos(x,y)` com alignment neutro `\\an5` (centro
        do texto no ponto x,y) baseado no beat_type da palavra. Se
        `vary_position_by_beat=False`, retorna string vazia (usa estilo default).
        """
        if not self.style.vary_position_by_beat:
            return ""
        y_center, _desc = POSITION_BY_BEAT.get(
            beat, POSITION_BY_BEAT[BeatType.DEVELOPMENT]
        )
        x_center = self.video_width // 2
        # \an5 = centro do texto ancorado no ponto; \pos fixa o ponto.
        return f"\\an5\\pos({x_center},{y_center})"

    def _render_window(
        self,
        window_words: list[WordTimestamp],
        active_index: int,
        w_idx: int = 0,
    ) -> str:
        """
        Monta a linha de legenda com a palavra ativa destacada.

        Animação da palavra ativa:
        - Fade-in leve da linha inteira (\\fad no início do token)
        - Posição variável por beat (hook=centro, climax=topo) via \\pos
        - Tamanho de fonte (\\fs) varia por beat + impact_level
        - Scale-pop com ease-out (\\t(0,pop_ms,\\fscx100\\fscy100))
        - Ênfases recebem punch-in MAIOR + cor de destaque
        - Glow opcional (\\blur)
        - Rotação sutil alternada entre janelas (anti-monotonia)

        Palavras não-ativas ficam na cor primária sem animação.
        """
        s = self.style

        # Posição da janela é ditada pela palavra ativa (cada palavra vira
        # sua própria linha de diálogo ASS então isso funciona).
        active_word = window_words[active_index]
        pos_tags = self._position_override(active_word.beat_type)

        # Tamanho efetivo da janela: multiplicador por beat × impact_level.
        # Exemplo: climax + medium = 1.25 × 1.20 = 1.5x o font_size base.
        beat_mult = SIZE_MULTIPLIER_BY_BEAT.get(active_word.beat_type, 1.0)
        impact_mult = SIZE_MULTIPLIER_BY_IMPACT.get(active_word.impact_level, 1.0)
        window_size = int(s.font_size * beat_mult * impact_mult)

        # Variação leve de rotação entre janelas (determinística pelo índice)
        # — evita que 40-50 janelas de um vídeo fiquem idênticas. Padrão de 6
        # janelas: neutra, -1°, neutra, neutra, +1°, neutra.
        rot_variation = 0
        seed = w_idx % 6
        if seed == 1:
            rot_variation = -1
        elif seed == 4:
            rot_variation = 1

        # Fade + posição + tamanho da janela ficam no prefixo
        prefix_tags = []
        if s.fade_in_ms > 0:
            prefix_tags.append(f"\\fad({s.fade_in_ms},0)")
        if pos_tags:
            prefix_tags.append(pos_tags)
        prefix_tags.append(f"\\fs{window_size}")
        if rot_variation != 0:
            prefix_tags.append(f"\\frz{rot_variation}")
        prefix = f"{{{''.join(prefix_tags)}}}" if prefix_tags else ""

        parts: list[str] = []

        for idx, word in enumerate(window_words):
            text = word.word.strip()
            if not text:
                continue

            if idx == active_index:
                # Ênfase tem pop BEM maior pra dar "tapa" visual
                is_emph = word.is_emphasis
                pop = s.emphasis_pop_scale if is_emph else s.scale_pop
                pop_dur = s.emphasis_pop_duration_ms if is_emph else s.pop_duration_ms
                color = s.emphasis_color  # todas as ativas ficam coloridas

                tags = (
                    f"{{\\fscx{pop}\\fscy{pop}"
                    f"\\t(0,{pop_dur},\\fscx100\\fscy100)"
                    f"\\c{color}"
                    f"\\3c{s.outline_color}"
                    f"\\fsp2"
                )
                if s.add_glow:
                    tags += "\\blur0.8"
                if is_emph:
                    # Ênfase: bold + fonte AINDA maior (sobre a já ampliada
                    # da janela) + shake sutil via \frz
                    emph_size = int(window_size * 1.20)
                    tags += (
                        f"\\b1\\fs{emph_size}"
                        f"\\t(0,80,\\frz-1.5)\\t(80,160,\\frz{rot_variation})"
                    )
                tags += "}"
                parts.append(f"{tags}{text}{{\\r}}")
            else:
                # Palavras da janela que não são ativas: cor primária
                parts.append(text)

        return prefix + " ".join(parts)


def build_caption_generator(template: str, **kwargs) -> CaptionGenerator:
    """Factory: retorna CaptionGenerator com o estilo do template."""
    style = STYLES_BY_TEMPLATE.get(template, STYLE_MOTIVACIONAL)
    # Aceita apenas video_width e video_height (words_per_line foi removido)
    kwargs = {k: v for k, v in kwargs.items() if k in {"video_width", "video_height"}}
    return CaptionGenerator(style=style, **kwargs)
