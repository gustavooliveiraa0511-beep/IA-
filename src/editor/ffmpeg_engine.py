"""
Motor de edição FFmpeg.

Responsável por:
- Redimensionar/cortar mídias pra 9:16 (1080x1920)
- Aplicar Ken Burns effect nas imagens (zoom/pan) — NUNCA estático
- Cortar vídeos b-roll nos tempos corretos
- Concatenar cenas com transições (hard cut na maioria, cross-fade em viradas)
- Queimar legenda ASS no vídeo
- Mixar narração + música de fundo + efeitos sonoros
- Exportar em MP4 H.264 otimizado pra redes sociais
"""
from __future__ import annotations

import random
import subprocess
from pathlib import Path
from typing import Optional

from src.pipeline.models import Scene, SceneType
from src.utils.config import config
from src.utils.logger import get_logger

logger = get_logger(__name__)


def _run(cmd: list[str], description: str = "") -> None:
    """Roda comando FFmpeg com log bonito e captura erro completo."""
    if description:
        logger.info(f"FFmpeg: {description}")
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        # Log completo pra debug
        logger.error("=" * 60)
        logger.error(f"FFmpeg FALHOU em: {description}")
        logger.error(f"Comando: {' '.join(cmd)}")
        logger.error("--- stderr (últimas 3000 chars) ---")
        logger.error(result.stderr[-3000:] if result.stderr else "(vazio)")
        logger.error("=" * 60)
        # Mensagem de erro mais informativa
        err_preview = (result.stderr or "").strip().split("\n")[-3:]
        raise RuntimeError(
            f"FFmpeg falhou em: {description}\n"
            f"Últimas linhas: {' | '.join(err_preview)}"
        )


# ============================================
# PASSO 1: Normaliza cada mídia pra 9:16
# ============================================
class ClipPreparer:
    """Prepara cada clipe (vídeo/imagem/fundo) com a duração certa em 9:16."""

    def __init__(self, width: int = 1080, height: int = 1920, fps: int = 30) -> None:
        self.w = width
        self.h = height
        self.fps = fps

    def prepare(self, scene: Scene, output_path: Path) -> Path:
        duration = scene.duration
        if scene.script_line.scene_type == SceneType.VIDEO_BROLL:
            path = self._prep_video(scene.media_path, duration, output_path)
        elif scene.script_line.scene_type == SceneType.IMAGE_KENBURNS:
            path = self._prep_image_kenburns(scene.media_path, duration, output_path)
        elif scene.script_line.scene_type == SceneType.PERSON_PHOTO:
            path = self._prep_person_photo(scene.media_path, duration, output_path)
        elif scene.script_line.scene_type == SceneType.COLOR_BACKGROUND:
            path = self._prep_color_bg(
                scene.script_line.bg_color or "#000000",
                duration,
                output_path,
            )
        else:
            raise ValueError(f"Scene type desconhecido: {scene.script_line.scene_type}")

        # Pattern interrupt: flash branco 100ms no início do clipe.
        # Usa fade "in" na cor branca — os primeiros 100ms da imagem/vídeo
        # emergem de um overlay branco. Efeito sutil mas perceptível.
        if scene.flash_intro:
            path = self._apply_flash_intro(path)
        return path

    @staticmethod
    def _apply_flash_intro(clip_path: Path) -> Path:
        """
        Aplica um flash branco de 100ms no início do clipe.
        Usa fade=in:color=white — o vídeo emerge de branco em 0.1s.
        """
        tmp = clip_path.with_suffix(".flash.mp4")
        cmd = [
            "ffmpeg", "-y", "-i", str(clip_path),
            "-vf", "fade=t=in:st=0:d=0.10:color=white",
            "-c:v", "libx264", "-preset", "medium", "-crf", "19",
            "-pix_fmt", "yuv420p",
            "-an",
            str(tmp),
        ]
        _run(cmd, f"flash intro on {clip_path.name}")
        # Substitui o arquivo original
        tmp.replace(clip_path)
        return clip_path

    def _prep_video(self, media_path: Optional[Path], duration: float, out: Path) -> Path:
        """
        Vídeo b-roll: encaixa em 9:16 (1080x1920) com crop central.
        Vídeos b-roll já têm movimento natural (cena gravada), então
        não precisa adicionar zoom sintético que complica o filtro.

        Estratégia simples e robusta:
        - scale pra preencher 1080x1920 mantendo proporção (sobra é cortada)
        - crop central nas dimensões exatas
        - força sar=1 pra evitar distorção
        """
        if not media_path or not media_path.exists():
            return self._prep_color_bg("#000000", duration, out)

        start_offset = self._pick_random_start(media_path, duration)

        # Filtro mínimo e confiável:
        # 1. Scale mantendo aspect, usando par (-2) pra libx264
        # 2. Crop central nas dimensões exatas
        vf = (
            f"scale=w={self.w}:h={self.h}:force_original_aspect_ratio=increase:flags=lanczos,"
            f"crop={self.w}:{self.h},"
            f"setsar=1"
        )

        cmd = [
            "ffmpeg", "-y",
            "-ss", str(start_offset), "-i", str(media_path),
            "-t", f"{duration:.3f}",
            "-vf", vf,
            "-r", str(self.fps),
            "-an",
            "-c:v", "libx264", "-preset", "medium", "-crf", "19",
            "-pix_fmt", "yuv420p",
            str(out),
        ]
        _run(cmd, f"prep video {media_path.name} {duration:.1f}s")
        return out

    def _prep_image_kenburns(self, media_path: Optional[Path], duration: float, out: Path) -> Path:
        """
        Imagem com Ken Burns effect: zoom/pan ao longo da cena.
        Nunca fica estática.

        Com sub-clips de 2s, o zoom precisa ser PROPORCIONAL à duração pra
        que o movimento seja perceptível (uma cena curta com zoom lento
        parece parada). Ken Burns adaptativo: clipes curtos têm zoom mais
        agressivo, clipes longos têm movimento suave.

        Usa scale 2x (não 4x — consome muita memória no GitHub Actions)
        e zoompan com expressões testadas.
        """
        if not media_path or not media_path.exists():
            return self._prep_color_bg("#000000", duration, out)

        frames = max(1, int(duration * self.fps))
        # 2x é suficiente pra zoom até 1.15x sem pixelizar
        scale_w = self.w * 2
        scale_h = self.h * 2

        movement = random.choice(["zoom_in", "zoom_out", "pan_right", "pan_left"])
        z_expr, x_expr, y_expr = self._kenburns_expressions(movement, frames, duration)

        vf = (
            f"scale={scale_w}:{scale_h}:force_original_aspect_ratio=increase:flags=lanczos,"
            f"crop={scale_w}:{scale_h},"
            f"zoompan=z='{z_expr}':x='{x_expr}':y='{y_expr}':"
            f"d={frames}:s={self.w}x{self.h}:fps={self.fps},"
            f"setsar=1"
        )

        cmd = [
            "ffmpeg", "-y", "-loop", "1", "-i", str(media_path),
            "-t", f"{duration:.3f}",
            "-vf", vf,
            "-r", str(self.fps),
            "-c:v", "libx264", "-preset", "medium", "-crf", "19",
            "-pix_fmt", "yuv420p",
            str(out),
        ]
        _run(cmd, f"ken burns {media_path.name} [{movement}] {duration:.1f}s")
        return out

    def _prep_person_photo(self, media_path: Optional[Path], duration: float, out: Path) -> Path:
        """
        Foto de pessoa: usa o mesmo Ken Burns das imagens normais,
        mas com movimento mais sutil (zoom in lento, sem pan).

        Simplifiquei pra usar só o zoompan (mesma pipeline da imagem)
        porque o filter_complex com split+blur+overlay é MUITO mais
        frágil e quebra por pequenas diferenças de dimensões.
        """
        if not media_path or not media_path.exists():
            return self._prep_color_bg("#000000", duration, out)

        frames = max(1, int(duration * self.fps))
        scale_w = self.w * 2
        scale_h = self.h * 2

        # Zoom-in leve e centralizado (sem pan lateral, fica mais elegante em rosto).
        # Adaptativo à duração: em clipes curtos o zoom precisa ser maior pra dar
        # sensação de movimento (antes era fixo 1.08x que sumia em 2s).
        duration_safe = max(1.0, duration)
        zoom_max = max(1.06, min(1.14, 1.06 + 0.14 / duration_safe))
        zoom_step = (zoom_max - 1.0) / max(1, frames)
        z_expr = f"min(zoom+{zoom_step:.5f},{zoom_max:.3f})"
        x_expr = "iw/2-(iw/zoom/2)"
        y_expr = "ih/2-(ih/zoom/2)"

        vf = (
            f"scale={scale_w}:{scale_h}:force_original_aspect_ratio=increase:flags=lanczos,"
            f"crop={scale_w}:{scale_h},"
            f"zoompan=z='{z_expr}':x='{x_expr}':y='{y_expr}':"
            f"d={frames}:s={self.w}x{self.h}:fps={self.fps},"
            f"setsar=1"
        )
        cmd = [
            "ffmpeg", "-y", "-loop", "1", "-i", str(media_path),
            "-t", f"{duration:.3f}",
            "-vf", vf,
            "-r", str(self.fps),
            "-c:v", "libx264", "-preset", "medium", "-crf", "19",
            "-pix_fmt", "yuv420p",
            str(out),
        ]
        _run(cmd, f"person photo {media_path.name} {duration:.1f}s")
        return out

    def _prep_color_bg(self, hex_color: str, duration: float, out: Path) -> Path:
        """
        Fundo de cor sólida com pulsação sutil (evita sensação estática).
        A frase de impacto vem via legenda por cima, então aqui só renderiza
        o fundo colorido.
        """
        hex_color = hex_color.lstrip("#")
        cmd = [
            "ffmpeg", "-y",
            "-f", "lavfi",
            "-i", f"color=c=0x{hex_color}:size={self.w}x{self.h}:rate={self.fps}",
            "-t", str(duration),  # garante duração correta
            "-vf", (
                # Vinheta sutil
                "vignette=PI/5,"
                # Pulsação de brilho muito leve
                "eq=brightness='0.02*sin(2*PI*t/2)'"
            ),
            "-c:v", "libx264", "-preset", "medium", "-crf", "19",
            "-pix_fmt", "yuv420p",
            str(out),
        ]
        _run(cmd, f"color bg #{hex_color} {duration:.1f}s")
        return out

    @staticmethod
    def _kenburns_expressions(
        movement: str, frames: int, duration: float = 3.0
    ) -> tuple[str, str, str]:
        """
        Retorna (zoom, x, y) expressions pro zoompan filter.

        Ken Burns adaptativo à duração: cenas curtas (~2s) recebem zoom mais
        agressivo (até 1.18x) pra que o movimento seja visível; cenas longas
        (>4s) ficam mais suaves (até 1.08x). Os "steps" (0.0015 etc) são
        recalibrados pra que o movimento cubra 100% do clipe sem parar antes.
        """
        # Cena curta → zoom mais intenso; longa → suave
        # 2s → ~1.18x, 3s → ~1.13x, 4s → ~1.10x, 6s+ → ~1.08x
        duration = max(1.0, duration)
        zoom_max = max(1.08, min(1.18, 1.08 + 0.20 / duration))
        # Step calibrado pra atingir zoom_max exatamente no último frame
        zoom_step = (zoom_max - 1.0) / max(1, frames)

        if movement == "zoom_in":
            z = f"min(zoom+{zoom_step:.5f},{zoom_max:.3f})"
            x = "iw/2-(iw/zoom/2)"
            y = "ih/2-(ih/zoom/2)"
        elif movement == "zoom_out":
            z = f"if(lte(zoom,1.0),{zoom_max:.3f},max(1.001,zoom-{zoom_step:.5f}))"
            x = "iw/2-(iw/zoom/2)"
            y = "ih/2-(ih/zoom/2)"
        elif movement == "pan_right":
            # Pan usa zoom fixo levemente menor pra dar espaço pro movimento
            z_fixed = f"{min(1.12, zoom_max):.3f}"
            z = z_fixed
            x = f"iw/zoom*(on/{frames})"
            y = "ih/2-(ih/zoom/2)"
        elif movement == "pan_left":
            z_fixed = f"{min(1.12, zoom_max):.3f}"
            z = z_fixed
            x = f"iw/zoom-(iw/zoom*(on/{frames}))"
            y = "ih/2-(ih/zoom/2)"
        else:  # zoom_pan combinado
            z = f"min(zoom+{zoom_step * 0.7:.5f},{zoom_max:.3f})"
            x = f"iw/2-(iw/zoom/2)+(iw*0.05*(on/{frames}))"
            y = f"ih/2-(ih/zoom/2)"
        return z, x, y

    @staticmethod
    def _pick_random_start(video_path: Path, needed_duration: float) -> float:
        """Pega duração total do vídeo e escolhe um start aleatório."""
        try:
            result = subprocess.run(
                [
                    "ffprobe", "-v", "error",
                    "-show_entries", "format=duration",
                    "-of", "default=noprint_wrappers=1:nokey=1",
                    str(video_path),
                ],
                capture_output=True, text=True, check=True,
            )
            total = float(result.stdout.strip())
        except Exception:
            return 0.0
        headroom = max(0.0, total - needed_duration - 0.5)
        return random.uniform(0.0, headroom) if headroom > 0 else 0.0


# ============================================
# PASSO 2: Concatena cenas com transições seletivas
# ============================================
XFADE_DURATION = 0.15  # segundos


class SceneConcatenator:
    """
    Junta as cenas preparadas aplicando cross-fade (xfade) seletivo.

    Regra:
    - Hard cut (corte seco) DEFAULT — mantém ritmo.
    - Cross-fade 0.15s apenas quando cena seguinte é "virada":
      * Muda de `scene_type` (ex: video_broll → color_background)
      * OU é uma cena COLOR_BACKGROUND (virada de tópico/frase-chave)
      * OU muda de `beat_type` (hook → development → climax → cta)

    Fallback: se xfade falhar, cai pro concat demuxer (hard cuts em tudo).
    """

    def __init__(self, width: int = 1080, height: int = 1920, fps: int = 30) -> None:
        self.w = width
        self.h = height
        self.fps = fps

    def concat(self, clip_paths: list[Path], scenes: list[Scene], output: Path) -> Path:
        if len(clip_paths) == 1:
            _run(
                ["ffmpeg", "-y", "-i", str(clip_paths[0]), "-c", "copy", str(output)],
                "single clip copy",
            )
            return output

        # Decide onde inserir xfade
        transitions = self._decide_transitions(scenes)
        xfade_count = sum(1 for t in transitions if t)
        logger.info(
            f"Concat: {len(clip_paths)} clipes, {xfade_count} xfades, "
            f"{len(clip_paths) - 1 - xfade_count} hard cuts"
        )

        if xfade_count == 0:
            # Sem transições suaves: demuxer simples (mais rápido)
            return self._concat_demuxer(clip_paths, output)

        try:
            return self._concat_with_xfade(clip_paths, scenes, transitions, output)
        except Exception as e:
            logger.warning(
                f"⚠️ Concat com xfade falhou ({type(e).__name__}): {e}. "
                f"Caindo pro demuxer (hard cuts)."
            )
            return self._concat_demuxer(clip_paths, output)

    @staticmethod
    def _decide_transitions(scenes: list[Scene]) -> list[bool]:
        """
        Retorna lista com len = len(scenes)-1.
        True em posição i = aplicar xfade entre scene[i] e scene[i+1].
        """
        if len(scenes) < 2:
            return []

        result: list[bool] = []
        for i in range(len(scenes) - 1):
            curr = scenes[i]
            nxt = scenes[i + 1]
            # Viradas de tipo
            type_change = curr.script_line.scene_type != nxt.script_line.scene_type
            # Virada narrativa (mudou o beat)
            beat_change = curr.script_line.beat_type != nxt.script_line.beat_type
            # Cena de frase-chave (fundo de cor)
            is_pivot = (
                nxt.script_line.scene_type.value == "color_background"
                or curr.script_line.scene_type.value == "color_background"
            )
            result.append(type_change or beat_change or is_pivot)
        return result

    def _concat_with_xfade(
        self,
        clip_paths: list[Path],
        scenes: list[Scene],
        transitions: list[bool],
        output: Path,
    ) -> Path:
        """
        Concatena com xfade pontual via filter_complex.

        Cada xfade encurta o vídeo final em `XFADE_DURATION` (overlap).
        Calculamos offsets acumulados levando isso em conta.
        """
        inputs_args: list[str] = []
        for p in clip_paths:
            inputs_args += ["-i", str(p)]

        # Probe duração de cada clipe
        durations = [_probe_duration(p) for p in clip_paths]

        # Monta cadeia: [0:v] [1:v] xfade=offset=d0-0.15 → [v01]
        #               [v01] [2:v] xfade=offset=d0+d1-2*0.15 → [v012]
        # Pra hard cut: concat simples.
        # Pra simplicidade e robustez, misturamos:
        #   - Segmento contínuo de hard-cuts vira 1 concat com reencode
        #   - Quando vier um xfade, usa xfade no concat anterior
        # Mas isso é complexo. Abordagem mais simples e funcional: sempre
        # usa filter_complex com concat filter (v=1:a=0) entre hard cuts e
        # xfade nas viradas, mantendo offset acumulado.

        filter_parts: list[str] = []
        current_label = "0:v"
        cumulative = durations[0]

        for i in range(1, len(clip_paths)):
            next_label = f"v{i}" if i < len(clip_paths) - 1 else "vout"
            if transitions[i - 1]:
                # xfade: offset = cumulative - XFADE_DURATION
                offset = max(0.0, cumulative - XFADE_DURATION)
                filter_parts.append(
                    f"[{current_label}][{i}:v]xfade=transition=fade:"
                    f"duration={XFADE_DURATION:.2f}:offset={offset:.3f}"
                    f"[{next_label}]"
                )
                cumulative += durations[i] - XFADE_DURATION
            else:
                # Hard cut via concat filter
                filter_parts.append(
                    f"[{current_label}][{i}:v]concat=n=2:v=1:a=0[{next_label}]"
                )
                cumulative += durations[i]
            current_label = next_label

        filter_complex = ";".join(filter_parts)
        cmd = [
            "ffmpeg", "-y",
            *inputs_args,
            "-filter_complex", filter_complex,
            "-map", "[vout]",
            "-c:v", "libx264", "-preset", "medium", "-crf", "19",
            "-pix_fmt", "yuv420p",
            "-r", str(self.fps),
            str(output),
        ]
        _run(cmd, f"concat com {sum(transitions)} xfades")
        return output

    def _concat_demuxer(self, clip_paths: list[Path], output: Path) -> Path:
        list_file = output.parent / "concat_list.txt"
        with open(list_file, "w") as f:
            for p in clip_paths:
                f.write(f"file '{p.resolve()}'\n")
        cmd = [
            "ffmpeg", "-y", "-f", "concat", "-safe", "0",
            "-i", str(list_file),
            "-c:v", "libx264", "-preset", "medium", "-crf", "19",
            "-pix_fmt", "yuv420p",
            "-r", str(self.fps),
            str(output),
        ]
        _run(cmd, "concat scenes (hard cuts)")
        return output


def _probe_duration(path: Path) -> float:
    """Duração total em segundos via ffprobe."""
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            capture_output=True, text=True, check=True,
        )
        return float(result.stdout.strip())
    except Exception as e:
        logger.warning(f"ffprobe duração falhou em {path.name}: {e}")
        return 3.0  # fallback razoável


# ============================================
# PASSO 3: Mixa áudio (narração + música + SFX) e queima legendas
# ============================================
class FinalAssembler:
    """Junta vídeo concatenado + narração + SFX + legendas ASS."""

    def __init__(self, width: int = 1080, height: int = 1920, fps: int = 30) -> None:
        self.w = width
        self.h = height
        self.fps = fps

    def assemble(
        self,
        video_path: Path,
        narration_path: Path,
        subtitle_ass_path: Path,
        output_path: Path,
        music_path: Optional[Path] = None,
        sfx_events: Optional[list[tuple[float, Path]]] = None,
        with_progress_bar: bool = True,
    ) -> Path:
        """
        sfx_events: lista de (timestamp_segundos, caminho_sfx)
        music_path: música de fundo (opcional, volume baixo)
        with_progress_bar: desenha barra sutil de progresso no topo (4px, 55% opacidade).
            Recurso comprovado de retenção: vídeos com barra de progresso
            visível retêm mais porque o usuário vê "está acabando" e não
            abandona antes do payoff.
        """
        inputs: list[str] = ["-i", str(video_path), "-i", str(narration_path)]
        input_idx = 2  # próximos índices pros inputs extras

        audio_filters: list[str] = []
        has_music = bool(music_path and music_path.exists())

        if has_music:
            # Narração é split em 2: uma pro mix, outra como sidechain do ducking
            audio_filters.append("[1:a]volume=1.0,asplit=2[narr][narr_sc]")
        else:
            audio_filters.append("[1:a]volume=1.0[narr]")

        last_audio_label = "narr"

        # Música de fundo com DUCKING automático (sidechaincompress):
        # - bgm no volume base, looped infinito
        # - sidechaincompress usa a narração como controle: quando há voz,
        #   comprime a música; em silêncio, abre.
        if has_music:
            inputs += ["-i", str(music_path)]
            # aloop=loop=-1: loop infinito (caso música acabe antes do vídeo)
            # aformat: normaliza sample rate e layout pra compatibilidade com sidechain
            audio_filters.append(
                f"[{input_idx}:a]aloop=loop=-1:size=2e+09,"
                f"volume=0.28,"
                f"aformat=sample_rates=44100:channel_layouts=stereo[bgm_raw]"
            )
            # Ducking: threshold=0.05 (-26dB approx), ratio=8, attack=5ms, release=400ms
            audio_filters.append(
                "[bgm_raw][narr_sc]sidechaincompress="
                "threshold=0.05:ratio=8:attack=5:release=400:"
                "makeup=1[bgm_ducked]"
            )
            # Mix narração + música ducked
            audio_filters.append(
                f"[{last_audio_label}][bgm_ducked]amix=inputs=2:"
                f"duration=first:dropout_transition=0:normalize=0[mix1]"
            )
            last_audio_label = "mix1"
            input_idx += 1

        # SFX
        if sfx_events:
            for i, (t, sfx_path) in enumerate(sfx_events):
                inputs += ["-i", str(sfx_path)]
                delay_ms = int(t * 1000)
                audio_filters.append(
                    f"[{input_idx}:a]adelay={delay_ms}|{delay_ms},volume=0.5[sfx{i}]"
                )
                input_idx += 1

            sfx_labels = "".join(f"[sfx{i}]" for i in range(len(sfx_events)))
            audio_filters.append(
                f"[{last_audio_label}]{sfx_labels}amix=inputs={1 + len(sfx_events)}:"
                f"duration=first:dropout_transition=0[finalaudio]"
            )
            last_audio_label = "finalaudio"

        # Pós-processamento de áudio (mix final):
        # - highpass 80Hz: remove rumble/low-end vazio que muitos celulares reproduzem mal.
        # - loudnorm EBU R128 (I=-14, TP=-1.5, LRA=11): padrão de streaming (TikTok/YouTube).
        #   Single-pass é "bom o suficiente" e não custa tempo extra (two-pass analisa primeiro).
        audio_filters.append(
            f"[{last_audio_label}]highpass=f=80,"
            f"loudnorm=I=-14:TP=-1.5:LRA=11[aout]"
        )
        final_audio_label = "aout"

        # Legenda ASS queimada no vídeo
        # Pro filtro ass=, o truque mais confiável no Linux é usar o path
        # ABSOLUTO do arquivo copiado pra CWD (sem espaços/caracteres especiais).
        # Escape necessário em filter_complex: \ → \\, : → \:, , → \,, [ → \[, ] → \]
        abs_path = str(subtitle_ass_path.resolve())
        subtitle_escaped = (
            abs_path
            .replace("\\", "\\\\")
            .replace(":", r"\:")
            .replace(",", r"\,")
            .replace("[", r"\[")
            .replace("]", r"\]")
            .replace("'", r"\\\'")
        )

        # Cadeia de filtros no vídeo: [progress bar] → [ass subtitles]
        # Progress bar 4px no topo com opacidade 55%, cor branca.
        # Largura cresce com t (tempo atual do frame) relativa à duração total.
        # Sem aspas ao redor da expressão pra não conflitar com parser do
        # filter_complex (; separa filter chains, , separa filtros).
        video_filter_chain: list[str] = []
        if with_progress_bar:
            total_dur = _probe_duration(video_path)
            if total_dur > 0.5:
                # `w=iw*t/TOTAL` onde t = PTS do frame em segundos.
                # Barra vai crescendo do 0 até iw (largura do vídeo) ao longo
                # da duração total. Vírgulas internas do drawbox usam `:` como
                # separador de key-value, então não há conflito com filter_complex.
                video_filter_chain.append(
                    f"drawbox=x=0:y=0:w=iw*t/{total_dur:.3f}:h=4:"
                    f"color=white@0.55:t=fill"
                )
        video_filter_chain.append(f"ass={subtitle_escaped}")
        video_filter = f"[0:v]{','.join(video_filter_chain)}[vsubs]"

        filter_complex = ";".join([video_filter] + audio_filters)

        # Encoding final com mais qualidade:
        # - preset "slow" + crf 18: qualidade visual notavelmente melhor que "medium"/crf 20.
        # - AAC 256k: headroom maior pra mix com música (PR 3).
        # Custa ~1 min a mais por vídeo, mas cabe nos 25min do Actions.
        cmd = [
            "ffmpeg", "-y",
            *inputs,
            "-filter_complex", filter_complex,
            "-map", "[vsubs]",
            "-map", f"[{final_audio_label}]",
            "-c:v", "libx264", "-preset", "slow", "-crf", "18",
            "-c:a", "aac", "-b:a", "256k",
            "-pix_fmt", "yuv420p",
            "-r", str(self.fps),
            "-movflags", "+faststart",
            "-shortest",
            str(output_path),
        ]
        _run(cmd, "final assembly")
        return output_path
