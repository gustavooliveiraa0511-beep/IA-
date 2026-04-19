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
import math
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
from src.pipeline.transcriber import Transcriber, _clean_word
from src.utils.config import config, OUTPUT_DIR, TEMP_DIR, ensure_dirs
from src.utils.logger import get_logger
from src.utils.music import fetch_music

logger = get_logger(__name__)


# Retenção em TikTok/Reels 2026 + feedback do usuário: cortes a cada ~1s geram
# sensação de vídeo viral (estilo Submagic/CapCut extremo). Dividimos cenas
# longas em sub-clips visuais de no máximo MAX_CLIP_DURATION segundos — a
# narração continua contínua, só o visual muda.
MAX_CLIP_DURATION = 1.0
# Split mínimo: evita gerar sub-clips tão curtos que a busca de mídia vira
# inútil (clipe de 0.3s não dá tempo nem de perceber a imagem).
MIN_SPLIT_DURATION = 0.5


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

            # CRÍTICO: sobrescreve o texto transcrito pelo texto ORIGINAL do roteiro.
            # Edge WordBoundary geralmente traz o texto correto (pois o TTS recebeu
            # o texto bruto), mas Whisper comete erros em nomes próprios (ex: "Kobe
            # Bryant" → "Cobi Brián"). Ao reescrever com o roteiro, a legenda vira
            # o source-of-truth e os timestamps continuam vindo do áudio real.
            words = self._realign_with_script_text(words, job.script)

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

            # Propaga beat_type + impact_level para cada word da cena
            # (captions usa pra variar posição, tamanho e estilo "billboard").
            for sc in job.scenes:
                for w in sc.words:
                    w.beat_type = sc.script_line.beat_type
                    w.impact_level = sc.script_line.impact_level

            # ==========================================
            # 4b. Split cenas longas em sub-clips (ritmo + retenção)
            #
            # Cenas com duração > MAX_CLIP_DURATION são divididas visualmente:
            # a narração continua inteira, mas aparecem mídias DIFERENTES em
            # cada sub-clip. Isso mantém os cortes a cada ~2s, que é o ritmo
            # comprovado de maior retenção em TikTok/Reels (2026).
            # ==========================================
            job.scenes = self._split_long_scenes(job.scenes)
            logger.info(
                f"[{job.job_id}] Após split: {len(job.scenes)} clipes visuais "
                f"(max {MAX_CLIP_DURATION}s cada)"
            )

            # ==========================================
            # 4c. Pattern interrupt: flash branco no primeiro clip após
            # mudança de beat_type. Pesquisa 2026 mostra que pattern
            # interrupts a cada 30-60s em momentos narrativos-chave
            # aumentam retenção significativamente. Limitado a 3 flashes
            # por vídeo pra não virar desconfortável.
            # ==========================================
            self._mark_beat_change_flashes(job.scenes, max_flashes=3)

            # ==========================================
            # 5+6. Busca mídia e prepara clipes 9:16
            # ==========================================
            job.status = "fetching_media"
            logger.info(f"[{job.job_id}] === MÍDIA + PREPARO ===")
            self.media.reset_used()  # diversidade por job (não reusar mídia entre cenas adjacentes)
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
            # 9. Assembly final (mixa áudio + queima legendas + ducking)
            # ==========================================
            logger.info(f"[{job.job_id}] === ASSEMBLY FINAL ===")
            final_path = OUTPUT_DIR / f"video_{job.job_id}.mp4"
            # Música de fundo opcional por template — nil se usuário não
            # configurou URLs em assets/music/tracks.json.
            music_path = fetch_music(request.template.value)
            self.assembler.assemble(
                video_path=concat_video,
                narration_path=narration_path,
                subtitle_ass_path=ass_path,
                output_path=final_path,
                music_path=music_path,
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
    # Marca flashes em mudanças de beat (pattern interrupt)
    # ============================================
    @staticmethod
    def _mark_beat_change_flashes(scenes: list[Scene], max_flashes: int = 3) -> None:
        """
        Marca com flash_intro=True a PRIMEIRA cena após uma mudança de
        beat_type (hook→development, development→climax, climax→cta).

        Limita a `max_flashes` flashes no vídeo todo (ordem cronológica)
        pra não ficar irritante. Nunca marca a cena 0 (ninguém precisa de
        flash no segundo zero).
        """
        if len(scenes) < 2:
            return
        flashed = 0
        prev_beat = scenes[0].script_line.beat_type
        for i in range(1, len(scenes)):
            curr_beat = scenes[i].script_line.beat_type
            if curr_beat != prev_beat:
                # Mudança de beat: marca primeira cena do novo beat
                scenes[i].flash_intro = True
                flashed += 1
                prev_beat = curr_beat
                if flashed >= max_flashes:
                    return

    # ============================================
    # Split de cenas longas: aumenta ritmo de cortes visuais
    # ============================================
    @staticmethod
    def _split_long_scenes(scenes: list[Scene]) -> list[Scene]:
        """
        Divide cenas > MAX_CLIP_DURATION em sub-clips visuais.

        Cada sub-clip compartilha a ScriptLine original (mesma narração, mesmo
        beat, mesma ênfase), mas recebe `clip_index` diferente para que o
        MediaDispatcher busque mídia alternativa em `visual_queries`.

        Os timestamps de palavras são redistribuídos por sub-clip: cada word
        cai no sub-clip cujo intervalo [start, end) a contém. Se uma word
        cai exatamente na fronteira, fica no primeiro sub-clip.

        ⚠️ A narração/áudio NÃO é recortada — o ffmpeg prepara cada sub-clip
        com a sua duração, e o final assembly sobrepõe a narração original
        inteira (que é contínua). Apenas o vídeo tem cortes extras.
        """
        new_scenes: list[Scene] = []
        for sc in scenes:
            # Sem tolerância: qualquer cena >= MAX_CLIP_DURATION é dividida.
            # Estudos de retenção mostram que mesmo clipes de 1.2s parecem
            # "parados" num feed de TikTok. Melhor dividir logo.
            if sc.duration < MAX_CLIP_DURATION:
                new_scenes.append(sc)
                continue

            # Alvo: ceil(duration / MAX) pra garantir que nenhum sub-clip
            # fique > MAX. Ex: 2.5s com MAX=1.0 → 3 splits de 0.83s cada
            # (se usasse round daria 2 splits de 1.25s, acima do limite).
            # Mas se ficaria muito picotado (< MIN_SPLIT_DURATION),
            # reduz o número de splits.
            n_splits = max(2, math.ceil(sc.duration / MAX_CLIP_DURATION))
            while n_splits > 2 and (sc.duration / n_splits) < MIN_SPLIT_DURATION:
                n_splits -= 1
            split_dur = sc.duration / n_splits

            for k in range(n_splits):
                start = sc.start_time + k * split_dur
                end = (
                    sc.start_time + (k + 1) * split_dur
                    if k < n_splits - 1
                    else sc.end_time
                )
                # Palavras que começam dentro do intervalo [start, end)
                split_words = [w for w in sc.words if start <= w.start < end]
                new_scenes.append(Scene(
                    script_line=sc.script_line,
                    start_time=start,
                    end_time=end,
                    words=split_words,
                    clip_index=k,
                    clip_total=n_splits,
                ))
        return new_scenes

    # ============================================
    # Realinhamento: substitui texto transcrito pelo texto original do roteiro
    # ============================================
    @staticmethod
    def _realign_with_script_text(
        words: list[WordTimestamp],
        script: Script,
    ) -> list[WordTimestamp]:
        """
        Reescreve o campo `.word` de cada WordTimestamp usando a sequência
        ORIGINAL do roteiro, mantendo os timestamps vindos do áudio.

        Garante que nomes difíceis (ex: "Kobe Bryant", "Schwarzenegger",
        "Einstein") apareçam na legenda EXATAMENTE como escritos no roteiro,
        mesmo que Whisper tenha ouvido algo diferente ou Edge TTS tenha
        pronunciado de forma estranha.

        Estratégia: difflib.SequenceMatcher sobre versões normalizadas
        (lowercase, sem pontuação, sem acentos) encontra blocos de match.
        Para cada bloco:
          - "equal" → usa timestamp do áudio + texto canônico do roteiro.
          - "replace" → mapeia proporcionalmente dentro do intervalo.
          - "insert" (roteiro tem palavras que o áudio pulou) → insere com
            timestamps curtos interpolados.
          - "delete" (áudio tem palavras extras) → descarta (lixo TTS).

        Fallback: se o ratio global for < 0.4, mantém a lista original
        (divergência grande demais, provavelmente erro no roteiro).
        """
        if not words or not script.lines:
            return words

        # 1. Extrai tokens canônicos do roteiro
        script_tokens: list[tuple[str, str]] = []  # [(clean_word, terminator), ...]
        for line in script.lines:
            for raw in line.text.split():
                clean, term = _clean_word(raw)
                if clean:
                    script_tokens.append((clean, term))

        if not script_tokens:
            return words

        # 2. Versões normalizadas pra matching
        script_norm = [_normalize(t[0]) for t in script_tokens]
        audio_norm = [_normalize(w.word) for w in words]

        matcher = difflib.SequenceMatcher(a=audio_norm, b=script_norm, autojunk=False)
        ratio = matcher.ratio()
        if ratio < 0.4:
            logger.warning(
                f"[Realign] ratio={ratio:.2f} muito baixo — mantendo texto transcrito. "
                f"(audio={len(words)} palavras, roteiro={len(script_tokens)} palavras)"
            )
            return words

        new_words: list[WordTimestamp] = []
        last_end = words[0].start if words else 0.0

        for op, i1, i2, j1, j2 in matcher.get_opcodes():
            if op == "equal":
                # Match perfeito: timestamp do áudio + texto do roteiro
                for audio_i, script_j in zip(range(i1, i2), range(j1, j2)):
                    clean, term = script_tokens[script_j]
                    new_words.append(WordTimestamp(
                        word=clean,
                        start=words[audio_i].start,
                        end=words[audio_i].end,
                        terminator=term,
                    ))
                    last_end = words[audio_i].end
            elif op == "replace":
                # Divergiu: usa intervalo do áudio, distribui as palavras do roteiro
                audio_span = words[i1:i2]
                script_span = script_tokens[j1:j2]
                if audio_span and script_span:
                    start = audio_span[0].start
                    end = audio_span[-1].end
                    step = (end - start) / max(1, len(script_span))
                    for k, (clean, term) in enumerate(script_span):
                        new_words.append(WordTimestamp(
                            word=clean,
                            start=start + k * step,
                            end=start + (k + 1) * step,
                            terminator=term,
                        ))
                    last_end = end
                elif script_span and not audio_span:
                    # Só roteiro: interpola perto do last_end
                    for clean, term in script_span:
                        new_words.append(WordTimestamp(
                            word=clean,
                            start=last_end,
                            end=last_end + 0.15,
                            terminator=term,
                        ))
                        last_end += 0.15
            elif op == "insert":
                # Roteiro tem palavras que o áudio não tem (TTS "comeu" palavra)
                script_span = script_tokens[j1:j2]
                for clean, term in script_span:
                    new_words.append(WordTimestamp(
                        word=clean,
                        start=last_end,
                        end=last_end + 0.15,
                        terminator=term,
                    ))
                    last_end += 0.15
            # op == "delete": palavras do áudio fora do roteiro → descarta

        if not new_words:
            return words

        # Sort defensivo (inserts com last_end podem empurrar timestamps
        # ligeiramente fora de ordem em casos patológicos). A lista final
        # precisa ser estritamente crescente em .start pra _align_words_to_scenes
        # funcionar corretamente.
        new_words.sort(key=lambda w: w.start)

        logger.info(
            f"[Realign] ratio={ratio:.2f} | "
            f"audio={len(words)} → saída={len(new_words)} palavras (texto do roteiro)"
        )
        return new_words

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

        # Queries: prefere a lista (alternativas), cai pro singular legado.
        queries = [q for q in (line.visual_queries or [line.visual_query]) if q]
        if not queries:
            queries = ["cinematic"]

        # Quando é sub-clip (clip_index > 0), rotaciona a ordem das queries
        # pra buscar mídia DIFERENTE. Ex: se queries=["motivação", "superação",
        # "vitória"] e clip_index=1, começa em "superação".
        if scene.clip_index > 0 and len(queries) > 1:
            rot = scene.clip_index % len(queries)
            queries = queries[rot:] + queries[:rot]

        # Variation index só pra sub-clips — garante resultado determinístico
        # diferente da principal (clip 0 = random, clips 1+ = índices fixos).
        var_idx = scene.clip_index if scene.clip_index > 0 else None

        if st == SceneType.VIDEO_BROLL:
            path = self.media.fetch_video(queries, variation_index=var_idx)
            if not path:
                # Fallback: imagem
                path = self.media.fetch_image(queries, variation_index=var_idx)
                if path:
                    line.scene_type = SceneType.IMAGE_KENBURNS
            scene.media_path = path

        elif st == SceneType.IMAGE_KENBURNS:
            scene.media_path = self.media.fetch_image(queries, variation_index=var_idx)

        elif st == SceneType.PERSON_PHOTO:
            # Para sub-clips de pessoa, alterna: clip 0 usa foto, clips
            # seguintes buscam b-roll contextual (ex: estádio, medalha)
            # pra quebrar monotonia visual.
            if scene.clip_index == 0 and line.person_name:
                scene.media_path = self.media.fetch_person(line.person_name)
            if not scene.media_path:
                # Busca vídeo contextual usando queries do roteiro
                scene.media_path = self.media.fetch_video(queries, variation_index=var_idx)
                if scene.media_path:
                    line.scene_type = SceneType.VIDEO_BROLL
                else:
                    scene.media_path = self.media.fetch_image(queries, variation_index=var_idx)
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
