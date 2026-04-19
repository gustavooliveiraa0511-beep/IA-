"""
Orquestrador do pipeline: transforma um VideoRequest em vídeo final.

Fluxo:
1. Groq → roteiro estruturado
2. Edge TTS → narração completa (MP3)
3. Whisper → timestamp palavra-a-palavra
4. Divide timestamps em cenas (alinhando com as frases do roteiro)
5. Pra cada cena: busca mídia (vídeo/imagem/pessoa/fundo-cor)
6. FFmpeg prepara cada clipe em 9:16 com movimento (Ken Burns etc.)
7. Concatena clipes
8. Gera legenda ASS animada
9. Mixa narração + SFX + música e queima legendas
10. Upload no R2 e retorna URL pública
"""
from __future__ import annotations

import shutil
import uuid
from pathlib import Path
from typing import Optional

from src.editor.captions import build_caption_generator
from src.editor.ffmpeg_engine import ClipPreparer, FinalAssembler, SceneConcatenator
from src.media.fetchers import MediaDispatcher
from src.pipeline.models import (
    Scene,
    SceneType,
    Script,
    ScriptLine,
    VideoJob,
    VideoRequest,
    WordTimestamp,
)
from src.pipeline.narrator import Narrator, RATE_BY_TEMPLATE, VOICE_BY_TEMPLATE
from src.pipeline.script_writer import ScriptWriter
from src.pipeline.transcriber import Transcriber
from src.utils.config import config, OUTPUT_DIR, TEMP_DIR, ensure_dirs
from src.utils.logger import get_logger

logger = get_logger(__name__)


class PipelineOrchestrator:
    def __init__(self) -> None:
        ensure_dirs()
        self.script_writer = ScriptWriter()
        self.media = MediaDispatcher()
        self.clip_preparer = ClipPreparer(
            width=config.video_width,
            height=config.video_height,
            fps=config.video_fps,
        )
        self.concat = SceneConcatenator(
            width=config.video_width,
            height=config.video_height,
            fps=config.video_fps,
        )
        self.assembler = FinalAssembler(
            width=config.video_width,
            height=config.video_height,
            fps=config.video_fps,
        )

    def run(self, request: VideoRequest) -> VideoJob:
        job = VideoJob(job_id=uuid.uuid4().hex[:12], request=request)
        work_dir = TEMP_DIR / f"job_{job.job_id}"
        work_dir.mkdir(parents=True, exist_ok=True)

        try:
            # ==========================================
            # 1. ROTEIRO
            # ==========================================
            job.status = "scripting"
            logger.info(f"[{job.job_id}] === ROTEIRO ===")
            job.script = self.script_writer.generate(request)

            # ==========================================
            # 2. NARRAÇÃO (cena por cena com pausa entre elas)
            # ==========================================
            job.status = "narrating"
            logger.info(f"[{job.job_id}] === NARRAÇÃO ===")
            voice = request.voice or VOICE_BY_TEMPLATE.get(
                request.template.value, "pt-BR-AntonioNeural"
            )
            rate = RATE_BY_TEMPLATE.get(request.template.value, "+0%")
            narrator = Narrator(voice=voice)
            narration_path = work_dir / "narration.mp3"

            # Gap entre cenas varia por template
            gap = {
                "motivacional": 0.45,  # pausa maior pra peso emocional
                "viral": 0.20,          # pausa curta pra manter ritmo
                "noticias": 0.35,
                "gaming": 0.15,
            }.get(request.template.value, 0.35)

            scene_texts = [line.text for line in job.script.lines]
            narrator.narrate_scenes(
                scene_texts=scene_texts,
                output_path=narration_path,
                rate=rate,
                gap_seconds=gap,
            )
            job.narration_path = narration_path

            # ==========================================
            # 3. TRANSCRIÇÃO palavra-a-palavra
            # ==========================================
            logger.info(f"[{job.job_id}] === TRANSCRIÇÃO ===")
            transcriber = Transcriber(model_size="small")
            words = transcriber.transcribe(narration_path)

            # Marca palavras de ênfase (união de todas as cenas)
            all_emphasis = []
            for line in job.script.lines:
                all_emphasis.extend(line.emphasis_words)
            words = Transcriber.mark_emphasis(words, all_emphasis)

            # ==========================================
            # 4. Divide palavras em cenas (alinhamento
            #    por texto das frases do roteiro)
            # ==========================================
            if not words:
                raise RuntimeError(
                    "Whisper não detectou fala na narração. "
                    "Tente outro tema ou verifique se o Edge TTS funcionou."
                )
            job.scenes = self._align_words_to_scenes(job.script, words)
            if not job.scenes:
                raise RuntimeError("Nenhuma cena gerada após alinhamento de palavras.")

            # ==========================================
            # 5+6. Busca mídia e prepara clipes 9:16
            # ==========================================
            job.status = "fetching_media"
            logger.info(f"[{job.job_id}] === MÍDIA + PREPARO ===")
            clip_paths = []
            for idx, scene in enumerate(job.scenes):
                self._attach_media(scene)
                out_clip = work_dir / f"clip_{idx:02d}.mp4"
                self.clip_preparer.prepare(scene, out_clip)
                clip_paths.append(out_clip)

            # ==========================================
            # 7. Concatena
            # ==========================================
            job.status = "editing"
            logger.info(f"[{job.job_id}] === CONCAT ===")
            concat_video = work_dir / "concat.mp4"
            self.concat.concat(clip_paths, job.scenes, concat_video)

            # ==========================================
            # 8. Legendas ASS
            # ==========================================
            logger.info(f"[{job.job_id}] === LEGENDAS ASS ===")
            caption_gen = build_caption_generator(
                template=request.template.value,
                video_width=config.video_width,
                video_height=config.video_height,
            )
            ass_path = work_dir / "captions.ass"
            caption_gen.generate(words, ass_path)

            # ==========================================
            # 9. Assembly final (mixa áudio + queima legendas)
            # ==========================================
            logger.info(f"[{job.job_id}] === ASSEMBLY FINAL ===")
            final_path = OUTPUT_DIR / f"video_{job.job_id}.mp4"
            self.assembler.assemble(
                video_path=concat_video,
                narration_path=narration_path,
                subtitle_ass_path=ass_path,
                output_path=final_path,
                music_path=None,  # TODO: música de fundo opcional
                sfx_events=None,  # TODO: SFX automático
            )
            job.final_video_path = final_path
            job.status = "done"
            logger.info(f"[{job.job_id}] ✅ PRONTO: {final_path}")

        except Exception as e:
            logger.exception(f"[{job.job_id}] ❌ erro: {e}")
            job.status = "error"
            job.error_message = str(e)
            raise

        return job

    # ============================================
    # Alinhamento: cada ScriptLine → janela de palavras do Whisper
    # ============================================
    @staticmethod
    def _align_words_to_scenes(
        script: Script, words: list[WordTimestamp]
    ) -> list[Scene]:
        """
        Distribui as palavras transcritas entre as frases do roteiro
        proporcionalmente ao número de palavras de cada frase.

        Simples e robusto: usa contagem de palavras em vez de matching
        textual (que é frágil com pontuação e TTS).
        """
        script_word_counts = [len(line.text.split()) for line in script.lines]
        total_expected = sum(script_word_counts)
        if total_expected == 0 or not words:
            return []

        scenes: list[Scene] = []
        cursor = 0  # índice na lista de words
        for line, expected_count in zip(script.lines, script_word_counts):
            # Quantas palavras pegar pra essa cena
            # Escala pelo total real (caso Whisper detecte mais/menos)
            scaled = max(1, round(len(words) * (expected_count / total_expected)))
            end_idx = min(cursor + scaled, len(words))
            window = words[cursor:end_idx]
            if not window:
                break

            scene = Scene(
                script_line=line,
                start_time=window[0].start,
                end_time=window[-1].end,
                words=window,
            )
            scenes.append(scene)
            cursor = end_idx

        # Se sobraram palavras, anexa na última cena
        if cursor < len(words) and scenes:
            tail = words[cursor:]
            scenes[-1].words.extend(tail)
            scenes[-1].end_time = tail[-1].end

        # Garante duração mínima SEM:
        # - expandir além do total da narração (-shortest cortaria narração)
        # - sobrepor com a cena seguinte (causa desalinhamento de legendas)
        MIN_DURATION = 1.0
        total_end = scenes[-1].end_time if scenes else 0
        for i, sc in enumerate(scenes):
            if sc.duration < MIN_DURATION:
                # Limite superior: início da próxima cena (ou fim do áudio)
                upper_limit = (
                    scenes[i + 1].start_time
                    if i + 1 < len(scenes)
                    else total_end
                )
                new_end = min(sc.start_time + MIN_DURATION, upper_limit)
                if new_end > sc.end_time:
                    sc.end_time = new_end

        return scenes

    # ============================================
    # Busca da mídia pra cada cena
    # ============================================
    def _attach_media(self, scene: Scene) -> None:
        line = scene.script_line
        st = line.scene_type

        if st == SceneType.VIDEO_BROLL:
            path = self.media.fetch_video(line.visual_query or "cinematic")
            if not path:
                # Fallback: imagem
                path = self.media.fetch_image(line.visual_query or "abstract")
                if path:
                    line.scene_type = SceneType.IMAGE_KENBURNS
            scene.media_path = path

        elif st == SceneType.IMAGE_KENBURNS:
            scene.media_path = self.media.fetch_image(line.visual_query or "inspiration")

        elif st == SceneType.PERSON_PHOTO:
            if line.person_name:
                scene.media_path = self.media.fetch_person(line.person_name)
            if not scene.media_path:
                # Cai pra imagem genérica
                scene.media_path = self.media.fetch_image(line.visual_query or "portrait")
                line.scene_type = SceneType.IMAGE_KENBURNS

        elif st == SceneType.COLOR_BACKGROUND:
            scene.media_path = None  # gerado via lavfi


def cleanup_job(job_id: str) -> None:
    """Apaga diretório temporário do job (pra não encher disco)."""
    d = TEMP_DIR / f"job_{job_id}"
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)
