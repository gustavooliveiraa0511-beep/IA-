"""
Orquestrador do pipeline: transforma um VideoRequest em vídeo final.

Fluxo (versão V2 — pós-reforma de qualidade):
1. Gera roteiro em 2 passes (outline + expand) com few-shot real.
2. Narra o roteiro INTEIRO via Edge TTS, variando rate por beat_type, e
   coleta WordBoundary do stream (timestamps exatos).
3. Se Edge TTS falhar e cair pra Piper/gTTS, rode Whisper como fallback
   pra obter os timestamps.
4. Alinha palavras → cenas por matching textual fuzzy (não mais proporcional).
5. Pra cada cena: busca mídia (vídeo/imagem/pessoa/fundo-cor).
6. FFmpeg prepara cada clipe em 9:16 com movimento (Ken Burns etc.).
7. Concatena clipes.
8. Gera legenda ASS animada (legendas já vêm sem pontuação porque word.word
   é limpo).
9. Mixa narração + SFX + música e queima legendas.
10. Upload no R2 e retorna URL pública.
"""
from __future__ import annotations

import difflib
import re
import shutil
import uuid
from pathlib import Path

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
from src.pipeline.narrator import Narrator, VOICE_BY_TEMPLATE
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
            # 1. ROTEIRO (2 passes: outline + expand)
            # ==========================================
            job.status = "scripting"
            logger.info(f"[{job.job_id}] === ROTEIRO ===")
            job.script = self.script_writer.generate(request)

            # ==========================================
            # 2. NARRAÇÃO + WordBoundary timestamps
            # ==========================================
            job.status = "narrating"
            logger.info(f"[{job.job_id}] === NARRAÇÃO ===")
            voice = request.voice or VOICE_BY_TEMPLATE.get(
                request.template.value, "pt-BR-AntonioNeural"
            )
            narrator = Narrator(voice=voice)
            narration_path = work_dir / "narration.mp3"

            narration_path, words = narrator.narrate_script(
                script=job.script,
                output_path=narration_path,
                template=request.template.value,
            )
            job.narration_path = narration_path

            # ==========================================
            # 3. FALLBACK: se não veio WordBoundary, usa Whisper
            # ==========================================
            if not words:
                logger.info(
                    f"[{job.job_id}] WordBoundary indisponível — chamando Whisper (medium)"
                )
                # Whisper medium (~770 MB) só é usado no caso raro de Piper/gTTS.
                # Qualidade de transcrição notavelmente melhor que "small";
                # modelo é cacheado no Actions via actions/cache.
                transcriber = Transcriber(model_size="medium")
                words = transcriber.transcribe(narration_path)

            if not words:
                raise RuntimeError(
                    "Sem timestamps de palavras (nem Edge WordBoundary nem Whisper). "
                    "Tente outro tema ou verifique o áudio da narração."
                )

            # Marca palavras de ênfase (union de todas as cenas)
            all_emphasis: list[str] = []
            for line in job.script.lines:
                all_emphasis.extend(line.emphasis_words)
            words = Transcriber.mark_emphasis(words, all_emphasis)

            # ==========================================
            # 4. Alinha palavras → cenas (fuzzy matching)
            # ==========================================
            job.scenes = self._align_words_to_scenes(job.script, words)
            if not job.scenes:
                raise RuntimeError("Nenhuma cena gerada após alinhamento")

            # ==========================================
            # 5+6. Busca mídia e prepara clipes 9:16
            # ==========================================
            job.status = "fetching_media"
            logger.info(f"[{job.job_id}] === MÍDIA + PREPARO ===")
            clip_paths: list[Path] = []
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
            # 8. Legendas ASS (já vêm sem pontuação — word.word é limpo)
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
                music_path=None,  # PR 3: música de fundo
                sfx_events=None,
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
    # Alinhamento FUZZY: cada ScriptLine → janela de palavras reais
    # ============================================
    @staticmethod
    def _align_words_to_scenes(
        script: Script, words: list[WordTimestamp]
    ) -> list[Scene]:
        """
        Encontra, pra cada frase do roteiro, onde ela COMEÇA na lista de
        palavras transcritas/boundary-detectadas. Usa SequenceMatcher do
        difflib pra permitir divergências (TTS pode pronunciar diferente).

        Fallback: se matching falha pra uma cena, usa fatia proporcional
        (algoritmo antigo) só pra aquela cena — não derruba o pipeline.

        Retorna cenas com start/end baseados nos timestamps REAIS das palavras.
        """
        if not words or not script.lines:
            return []

        n_words = len(words)
        script_word_counts = [len(line.text.split()) for line in script.lines]
        total_expected = sum(script_word_counts)
        if total_expected == 0:
            return []

        # Lista de tokens normalizados das words (sem pontuação, lowercase)
        token_stream = [_normalize(w.word) for w in words]

        cursor = 0
        scenes: list[Scene] = []
        for i, line in enumerate(script.lines):
            script_tokens = [_normalize(t) for t in line.text.split() if _normalize(t)]
            if not script_tokens:
                continue

            # Janela esperada: tamanho ~= nº de palavras no script da cena,
            # com +50% de folga e começando no cursor.
            expected = len(script_tokens)
            window_end_default = min(cursor + int(expected * 1.5) + 4, n_words)

            # Busca a melhor substring começando em [cursor, window_end_default]
            best_start = _find_best_start(
                haystack=token_stream,
                needle=script_tokens,
                search_from=cursor,
                search_to=min(cursor + int(expected * 2) + 6, n_words),
            )

            if best_start < 0:
                # Fallback proporcional pra esta cena
                scaled = max(1, round(n_words * (expected / total_expected)))
                scene_start = cursor
                scene_end = min(cursor + scaled, n_words)
            else:
                scene_start = best_start
                # Próxima cena começa onde essa termina (aprox.)
                if i + 1 < len(script.lines):
                    next_tokens = [
                        _normalize(t)
                        for t in script.lines[i + 1].text.split()
                        if _normalize(t)
                    ]
                    next_start = (
                        _find_best_start(
                            haystack=token_stream,
                            needle=next_tokens,
                            search_from=best_start + 1,
                            search_to=min(best_start + expected * 2 + 8, n_words),
                        )
                        if next_tokens
                        else -1
                    )
                    scene_end = (
                        next_start
                        if next_start > best_start
                        else min(best_start + expected + 2, n_words)
                    )
                else:
                    scene_end = n_words  # última cena pega até o fim

            window = words[scene_start:scene_end]
            if not window:
                continue

            scenes.append(
                Scene(
                    script_line=line,
                    start_time=window[0].start,
                    end_time=window[-1].end,
                    words=list(window),
                )
            )
            cursor = scene_end

        # Ajusta sobras: se última cena não cobriu até o fim, anexa tail
        if scenes and cursor < n_words:
            tail = words[cursor:]
            scenes[-1].words.extend(tail)
            scenes[-1].end_time = tail[-1].end

        # Garante duração mínima sem sobrepor com a próxima cena
        MIN_DURATION = 1.0
        total_end = scenes[-1].end_time if scenes else 0
        for i, sc in enumerate(scenes):
            if sc.duration < MIN_DURATION:
                upper_limit = (
                    scenes[i + 1].start_time if i + 1 < len(scenes) else total_end
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

        # Queries: usa visual_queries se tiver, senão visual_query
        queries = [q for q in (line.visual_queries or [line.visual_query]) if q]
        primary_query = queries[0] if queries else "cinematic"

        if st == SceneType.VIDEO_BROLL:
            path = self.media.fetch_video(primary_query)
            if not path:
                # Fallback: imagem
                path = self.media.fetch_image(primary_query or "abstract")
                if path:
                    line.scene_type = SceneType.IMAGE_KENBURNS
            scene.media_path = path

        elif st == SceneType.IMAGE_KENBURNS:
            scene.media_path = self.media.fetch_image(primary_query or "inspiration")

        elif st == SceneType.PERSON_PHOTO:
            if line.person_name:
                scene.media_path = self.media.fetch_person(line.person_name)
            if not scene.media_path:
                scene.media_path = self.media.fetch_image(primary_query or "portrait")
                line.scene_type = SceneType.IMAGE_KENBURNS

        elif st == SceneType.COLOR_BACKGROUND:
            scene.media_path = None  # gerado via lavfi


# ============================================
# Helpers de matching
# ============================================
_NORMALIZE_RE = re.compile(r"[^\w]", re.UNICODE)


def _normalize(token: str) -> str:
    """Lowercase + remove pontuação pra comparação fuzzy."""
    return _NORMALIZE_RE.sub("", token.lower().strip())


def _find_best_start(
    haystack: list[str],
    needle: list[str],
    search_from: int,
    search_to: int,
    min_ratio: float = 0.5,
) -> int:
    """
    Encontra índice em `haystack` onde começa uma janela que melhor
    corresponde a `needle` (lista de tokens da cena).

    Usa SequenceMatcher.ratio() numa janela deslizante. Retorna -1 se nenhum
    candidato passar do threshold.

    Estratégia: compara a primeira palavra da cena (âncora) primeiro, depois
    a janela de len(needle) palavras em volta. Mais rápido que brute-force
    em cada posição.
    """
    if not needle or search_from >= search_to:
        return -1

    # Âncora: primeira palavra relevante da needle
    anchor = needle[0]
    if not anchor:
        return search_from  # fallback neutro

    # Candidatos: posições onde a âncora bate ou se parece muito
    candidates: list[int] = []
    for i in range(search_from, search_to):
        if i >= len(haystack):
            break
        tok = haystack[i]
        if tok == anchor:
            candidates.append(i)
        elif len(anchor) > 3 and (anchor.startswith(tok[:3]) or tok.startswith(anchor[:3])):
            # Match fraco — só se âncora for palavra "grande" pra evitar
            # falsos positivos com preposições curtas
            if difflib.SequenceMatcher(None, tok, anchor).ratio() >= 0.7:
                candidates.append(i)

    if not candidates:
        # Sem nenhuma âncora: usa search_from como fallback soft
        return search_from if search_from < len(haystack) else -1

    # Pra cada candidato, mede match da janela inteira
    best_idx = -1
    best_ratio = min_ratio
    needle_joined = " ".join(needle)
    for c in candidates:
        window = haystack[c : c + len(needle)]
        window_joined = " ".join(window)
        ratio = difflib.SequenceMatcher(None, window_joined, needle_joined).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best_idx = c

    # Se nenhum passou do threshold, usa primeiro candidato (ainda melhor que random)
    if best_idx < 0 and candidates:
        best_idx = candidates[0]
    return best_idx


def cleanup_job(job_id: str) -> None:
    """Apaga diretório temporário do job (pra não encher disco)."""
    d = TEMP_DIR / f"job_{job_id}"
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)
