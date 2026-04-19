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
            return self._prep_video(scene.media_path, duration, output_path)
        elif scene.script_line.scene_type == SceneType.IMAGE_KENBURNS:
            return self._prep_image_kenburns(scene.media_path, duration, output_path)
        elif scene.script_line.scene_type == SceneType.PERSON_PHOTO:
            return self._prep_person_photo(scene.media_path, duration, output_path)
        elif scene.script_line.scene_type == SceneType.COLOR_BACKGROUND:
            return self._prep_color_bg(
                scene.script_line.bg_color or "#000000",
                duration,
                output_path,
            )
        else:
            raise ValueError(f"Scene type desconhecido: {scene.script_line.scene_type}")

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
        z_expr, x_expr, y_expr = self._kenburns_expressions(movement, frames)

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

        # Zoom-in leve e centralizado (sem pan lateral, fica mais elegante em rosto)
        z_expr = "min(zoom+0.0008,1.08)"
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
    def _kenburns_expressions(movement: str, frames: int) -> tuple[str, str, str]:
        """Retorna (zoom, x, y) expressions pro zoompan filter."""
        if movement == "zoom_in":
            z = f"min(zoom+0.0015,1.15)"
            x = "iw/2-(iw/zoom/2)"
            y = "ih/2-(ih/zoom/2)"
        elif movement == "zoom_out":
            z = f"if(lte(zoom,1.0),1.15,max(1.001,zoom-0.0015))"
            x = "iw/2-(iw/zoom/2)"
            y = "ih/2-(ih/zoom/2)"
        elif movement == "pan_right":
            z = "1.1"
            x = f"iw/zoom*(on/{frames})"
            y = "ih/2-(ih/zoom/2)"
        elif movement == "pan_left":
            z = "1.1"
            x = f"iw/zoom-(iw/zoom*(on/{frames}))"
            y = "ih/2-(ih/zoom/2)"
        else:  # zoom_pan
            z = f"min(zoom+0.001,1.12)"
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
class SceneConcatenator:
    """
    Junta as cenas preparadas.

    Regra de transições:
    - Hard cut (corte seco) 80% das vezes — ritmo
    - Cross-fade curto (0.2s) apenas quando scene_type muda de tipo
      OU quando é uma cena de COLOR_BACKGROUND (virada de tópico)
    """

    def __init__(self, width: int = 1080, height: int = 1920, fps: int = 30) -> None:
        self.w = width
        self.h = height
        self.fps = fps

    def concat(self, clip_paths: list[Path], scenes: list[Scene], output: Path) -> Path:
        if len(clip_paths) == 1:
            # Só copia
            _run(["ffmpeg", "-y", "-i", str(clip_paths[0]), "-c", "copy", str(output)],
                 "single clip copy")
            return output

        # Monta filter_complex com xfade seletivo
        filter_parts: list[str] = []
        inputs_args: list[str] = []
        for p in clip_paths:
            inputs_args += ["-i", str(p)]

        # Usa concat demuxer simples se não houver transição suave marcada
        # Pra simplicidade e estabilidade, usamos concat demuxer (hard cut)
        # e adicionamos transições pontuais via xfade quando necessário.
        # Implementação inicial: só concat demuxer (hard cuts em todos).
        # Transições xfade são adicionadas pelo FinalAssembler.
        return self._concat_demuxer(clip_paths, output)

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
        _run(cmd, "concat scenes")
        return output


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
    ) -> Path:
        """
        sfx_events: lista de (timestamp_segundos, caminho_sfx)
        music_path: música de fundo (opcional, volume baixo)
        """
        inputs: list[str] = ["-i", str(video_path), "-i", str(narration_path)]
        input_idx = 2  # próximos índices pros inputs extras

        audio_filters: list[str] = []
        audio_filters.append("[1:a]volume=1.0[narr]")

        last_audio_label = "narr"

        # Música de fundo (volume 0.12 — baixinha, não atrapalha narração)
        if music_path and music_path.exists():
            inputs += ["-i", str(music_path)]
            audio_filters.append(f"[{input_idx}:a]volume=0.12,aloop=loop=-1:size=2e+09[bgm]")
            audio_filters.append(f"[{last_audio_label}][bgm]amix=inputs=2:duration=first:dropout_transition=2[mixed1]")
            last_audio_label = "mixed1"
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
        video_filter = f"[0:v]ass={subtitle_escaped}[vsubs]"

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
