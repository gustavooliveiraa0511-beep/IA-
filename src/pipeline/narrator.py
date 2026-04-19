"""
Narração com fluxo prosódico natural.

Filosofia (depois da reforma de qualidade):
- A PONTUAÇÃO do roteiro é o instrumento primário de ritmo. Edge TTS interpreta
  pontos, vírgulas, reticências e pontos-de-exclamação muito bem — criando
  respiração natural sem precisar de silêncios artificiais.
- NÃO narramos cena-por-cena com gap fixo (isso quebrava o fluxo argumentativo).
- Narramos o ROTEIRO INTEIRO como um texto só, mas variamos o `rate` por trecho
  de mesmo `beat_type` (hook/development/climax/cta). Assim o climax fica mais
  lento e dramático sem perder continuidade.
- Extraímos WORD BOUNDARIES direto do stream do Edge TTS (timestamps exatos
  de cada palavra) — fonte primária pra legendas e alinhamento de cenas.

Cascata de fallback (na ordem):
1. Edge TTS (Microsoft) — qualidade alta + WordBoundary exato
2. Piper TTS — neural local, grátis, sem WordBoundary (cai pro Whisper)
3. gTTS (Google) — último recurso, sem WordBoundary

Vozes Edge TTS PT-BR:
- pt-BR-AntonioNeural    (masculina, firme — motivacional)
- pt-BR-FranciscaNeural  (feminina, clara — notícias)
- pt-BR-ThalitaNeural    (feminina, jovem — viral)
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import re
import subprocess
from pathlib import Path
from typing import Optional

import edge_tts
import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from src.pipeline.models import BeatType, Script, ScriptLine, WordTimestamp
from src.utils.config import ASSETS_DIR
from src.utils.logger import get_logger

logger = get_logger(__name__)


PIPER_MODELS_DIR = ASSETS_DIR / "piper_models"
PIPER_VOICE_NAME = "pt_BR-faber-medium"
PIPER_MODEL_URL = (
    "https://huggingface.co/rhasspy/piper-voices/resolve/main/"
    "pt/pt_BR/faber/medium/pt_BR-faber-medium.onnx"
)
PIPER_CONFIG_URL = (
    "https://huggingface.co/rhasspy/piper-voices/resolve/main/"
    "pt/pt_BR/faber/medium/pt_BR-faber-medium.onnx.json"
)


# ============================================
# Rate (velocidade) por papel narrativo (beat_type)
# O Edge TTS aceita "+X%"/"-X%". Valores negativos = mais devagar.
#
# Princípio (pós-ajuste de naturalidade):
# - Só desacelera onde PRECISA: climax (dramático) e CTA (firme).
# - Hook e development mantêm ritmo pra não derrubar retenção nos
#   primeiros segundos.
# - Climax é empurrado mais fundo (-12%) porque é onde a fala fica
#   mais "embolada" quando acumula palavras de ênfase.
# ============================================
RATE_BY_BEAT: dict[BeatType, str] = {
    BeatType.HOOK: "-3%",          # firme mas levemente mais rápido
    BeatType.DEVELOPMENT: "+0%",   # neutro
    BeatType.CLIMAX: "-12%",       # dramático, respirando mais (era -8%)
    BeatType.CTA: "-5%",           # autoritário
}


# Ajuste extra de velocidade por template (multiplica/soma com o rate de beat)
# Mantido pra permitir tom diferente entre motivacional e gaming sem mudar beat.
# Reduzimos os picos de +8%/+12% pra não emboletar a fala em momentos críticos.
RATE_BY_TEMPLATE: dict[str, int] = {
    "motivacional": -2,   # levemente mais lento (dá peso)
    "viral": +5,          # energia, mas sem apressar demais (era +8)
    "noticias": 0,        # neutro
    "gaming": +8,         # alta energia sem emboletar (era +12)
}


# Volume por beat (Edge TTS aceita "+X%"/"-X%"). Como PITCH foi removido no
# edge-tts 6.0.3, volume é o único outro eixo de prosódia disponível. Simula
# variação de "tom emocional" — climax sobe volume, pausa abaixa.
#
# Valores calibrados: >+25% começa a clipar áudio mesmo com loudnorm no final.
VOLUME_BY_BEAT: dict[BeatType, str] = {
    BeatType.HOOK: "+5%",          # presente sem gritar
    BeatType.DEVELOPMENT: "+0%",   # neutro
    BeatType.CLIMAX: "+15%",       # pico emocional — SOBE
    BeatType.CTA: "+8%",           # autoritário
}


# Pontuação que separa palavras/frases (usado pra achar ocorrência de ênfase
# em qualquer posição da frase, sem distinguir "palavra," de "palavra.").
_EMPH_STRIP = ".,!?;:…\"')]}>»›"


def _inject_emphasis_pause(text: str, emphasis_word: str) -> str:
    """
    Insere micro-pausa natural EM TORNO da palavra de ênfase.

    Estratégia: substitui a primeira ocorrência por `"..., <palavra>,"`
    (ellipsis antes + vírgula depois). Edge TTS interpreta ellipsis como
    pausa de ~350ms e vírgula como pausa de ~150ms, criando um efeito
    dramático sem artefato sonoro.

    Retorna o texto modificado (ou original se a palavra não for encontrada).

    ⚠️ Isso altera a narração mas NÃO a legenda — `_realign_with_script_text`
    no orchestrator sobrescreve o texto da legenda com o roteiro original.
    """
    if not emphasis_word or not emphasis_word.strip():
        return text
    word = emphasis_word.strip().strip(_EMPH_STRIP)
    if not word or len(word) < 3:
        return text
    # Match palavra inteira (case-insensitive), ignora variantes com acento
    # que já foram cuidadas no script
    pattern = re.compile(
        r"(?<![\wÀ-ÿ])(" + re.escape(word) + r")(?![\wÀ-ÿ])",
        re.IGNORECASE,
    )
    replaced = [False]

    def repl(m: re.Match) -> str:
        if replaced[0]:
            return m.group(0)
        replaced[0] = True
        return f"... {m.group(1)},"

    new_text = pattern.sub(repl, text, count=1)
    return new_text if replaced[0] else text


def _combine_rate(beat: BeatType, template: str) -> str:
    """Soma o rate do beat com ajuste do template. Retorna '+X%' ou '-X%'."""
    base = int(RATE_BY_BEAT[beat].rstrip("%"))
    bonus = RATE_BY_TEMPLATE.get(template, 0)
    total = base + bonus
    return _fmt_pct(total)


def _fmt_pct(n: int) -> str:
    """Formata inteiro como '+X%' ou '-X%' (necessário pro Edge TTS)."""
    sign = "+" if n >= 0 else ""
    return f"{sign}{n}%"


# Vozes recomendadas por template
VOICE_BY_TEMPLATE: dict[str, str] = {
    "motivacional": "pt-BR-AntonioNeural",
    "viral": "pt-BR-ThalitaNeural",
    "noticias": "pt-BR-FranciscaNeural",
    "gaming": "pt-BR-AntonioNeural",
}


def _run_async_safely(coro):
    """Roda coroutine em qualquer contexto (sync ou async)."""
    try:
        asyncio.get_running_loop()
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(asyncio.run, coro)
            return future.result()
    except RuntimeError:
        return asyncio.run(coro)


# ============================================
# Narrator
# ============================================
class Narrator:
    def __init__(self, voice: str = "pt-BR-AntonioNeural") -> None:
        self.voice = voice

    # ============================================
    # API principal: narra o roteiro todo e retorna timestamps (WordBoundary)
    # ============================================
    def narrate_script(
        self,
        script: Script,
        output_path: Path,
        template: str = "motivacional",
    ) -> tuple[Path, list[WordTimestamp]]:
        """
        Narra o roteiro completo variando a velocidade por `beat_type`.

        Retorna (caminho_mp3, lista_de_WordTimestamp).
        Se Edge TTS funcionar, a lista vem com timestamps EXATOS do TTS
        (sem precisar rodar Whisper). Caso contrário, retorna lista vazia
        e o orchestrator chama Whisper como fallback.
        """
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if not script.lines:
            raise RuntimeError("Script vazio — nada pra narrar")

        # Agrupa linhas consecutivas com mesmo beat_type (cada grupo = 1 chamada TTS)
        groups = self._group_by_beat(script.lines)
        logger.info(
            f"[Narrator] Narrando {len(script.lines)} cenas em "
            f"{len(groups)} grupos de beat: {[g[0].value for g in groups]}"
        )

        tmp_dir = output_path.parent / f".narr_{output_path.stem}"
        tmp_dir.mkdir(exist_ok=True)

        parts: list[Path] = []
        all_words: list[WordTimestamp] = []
        cumulative_offset = 0.0
        any_edge_success = False

        try:
            for g_idx, (beat, lines) in enumerate(groups):
                base_rate = _combine_rate(beat, template)
                base_volume = VOLUME_BY_BEAT.get(beat, "+0%")

                # Em CLIMAX/CTA com MÚLTIPLAS linhas, narra linha-a-linha
                # alternando rate/volume pra simular contorno emocional
                # (sobe-desce-sobe). Em hook/dev, mantém agrupado pra fluxo.
                if beat in (BeatType.CLIMAX, BeatType.CTA) and len(lines) > 1:
                    sub_narrations = self._climax_variations(
                        lines, base_rate=base_rate, base_volume=base_volume
                    )
                else:
                    # Chamada única tradicional
                    text = self._group_text(lines)
                    sub_narrations = [(text, base_rate, base_volume)]

                for v_idx, (sub_text, sub_rate, sub_vol) in enumerate(sub_narrations):
                    part_path = tmp_dir / f"grp_{g_idx:02d}_{v_idx:02d}.mp3"

                    # Tenta Edge TTS com WordBoundary
                    words_in_part: list[WordTimestamp] = []
                    edge_ok = False
                    try:
                        words_in_part = self._edge_narrate_with_boundaries(
                            sub_text, part_path, rate=sub_rate, volume=sub_vol
                        )
                        edge_ok = True
                        any_edge_success = True
                        logger.info(
                            f"[Narrator] grp {g_idx}.{v_idx} ({beat.value}, "
                            f"rate={sub_rate}, vol={sub_vol}): Edge ✅ "
                            f"{len(words_in_part)} palavras"
                        )
                    except Exception as e:
                        logger.warning(
                            f"[Narrator] Edge falhou no grp {g_idx}.{v_idx}: "
                            f"{type(e).__name__}: {e}"
                        )

                    # Fallback pra Piper / gTTS (sem WordBoundary — Whisper depois)
                    if not edge_ok:
                        try:
                            self._piper_narrate(sub_text, part_path)
                            logger.info(
                                f"[Narrator] grp {g_idx}.{v_idx}: Piper ✅ (sem boundaries)"
                            )
                        except Exception as e:
                            logger.warning(
                                f"[Narrator] Piper falhou no grp {g_idx}.{v_idx}: "
                                f"{type(e).__name__}"
                            )
                            self._gtts_narrate(sub_text, part_path)
                            logger.info(
                                f"[Narrator] grp {g_idx}.{v_idx}: gTTS ✅ (último recurso)"
                            )

                    parts.append(part_path)

                    # Ajusta offsets das words desse sub-trecho pro tempo acumulado
                    for w in words_in_part:
                        all_words.append(
                            WordTimestamp(
                                word=w.word,
                                start=w.start + cumulative_offset,
                                end=w.end + cumulative_offset,
                                terminator=w.terminator,
                            )
                        )

                    # Descobre duração real do mp3 pra atualizar o offset
                    part_duration = _probe_duration(part_path)
                    cumulative_offset += part_duration

            # Concatena os mp3s em um único arquivo (sem gap entre grupos — fluxo contínuo)
            self._concat(parts, output_path)

            logger.info(
                f"[Narrator] ✅ Narração total: {cumulative_offset:.2f}s "
                f"({output_path.stat().st_size / 1024:.1f} KB), "
                f"{len(all_words)} words com timestamps "
                f"(Edge={'✅' if any_edge_success else '❌'})"
            )

            # Se todos os grupos caíram pro Piper/gTTS, retorna words vazio
            # (orchestrator vai chamar Whisper).
            return output_path, (all_words if any_edge_success else [])
        finally:
            # Limpa temporários
            for p in parts:
                p.unlink(missing_ok=True)
            if tmp_dir.exists():
                try:
                    tmp_dir.rmdir()
                except OSError:
                    pass

    # ============================================
    # Agrupa cenas consecutivas com o mesmo beat_type
    # ============================================
    @staticmethod
    def _group_by_beat(
        lines: list[ScriptLine],
    ) -> list[tuple[BeatType, list[ScriptLine]]]:
        groups: list[tuple[BeatType, list[ScriptLine]]] = []
        current_beat: Optional[BeatType] = None
        current_lines: list[ScriptLine] = []
        for line in lines:
            if line.beat_type != current_beat:
                if current_lines:
                    groups.append((current_beat, current_lines))  # type: ignore
                current_beat = line.beat_type
                current_lines = [line]
            else:
                current_lines.append(line)
        if current_lines:
            groups.append((current_beat, current_lines))  # type: ignore
        return groups

    @staticmethod
    def _group_text(lines: list[ScriptLine]) -> str:
        """
        Junta o texto das linhas do grupo garantindo pontuação final.

        Em CLIMAX e CTA, injeta micro-pausas nas palavras de ênfase pra que
        o Edge TTS respire antes delas (ellipsis) e depois (vírgula). Isso
        desembola a fala nos momentos mais carregados de emoção sem precisar
        desacelerar o grupo inteiro.
        """
        parts: list[str] = []
        for line in lines:
            t = line.text.strip()
            if not t:
                continue
            # Só nos momentos críticos (climax/cta) e quando há ênfase marcada
            if line.emphasis_words and line.beat_type in (BeatType.CLIMAX, BeatType.CTA):
                for emph in line.emphasis_words:
                    t = _inject_emphasis_pause(t, emph)
            if t[-1] not in ".!?…":
                t += "."
            parts.append(t)
        return " ".join(parts)

    @staticmethod
    def _climax_variations(
        lines: list[ScriptLine],
        base_rate: str,
        base_volume: str,
    ) -> list[tuple[str, str, str]]:
        """
        Produz variações (texto, rate, volume) POR LINHA num grupo de
        CLIMAX/CTA, alternando intensidade pra criar contorno emocional:
        sobe-desce-sobe. Simula variação de "tom" já que Edge TTS não
        suporta mais pitch.

        Padrão cíclico:
          linha 0: rate mais lento + volume mais alto   (pico emocional)
          linha 1: rate intermediário + volume médio    (alívio)
          linha 2: rate lento + volume alto             (subida final)

        Os deltas são aplicados EM CIMA de base_rate/base_volume do beat,
        então o efeito respeita a configuração do template.
        """
        # Deltas (rate%, volume%) por posição no ciclo
        deltas = [
            (-3, +5),   # pico inicial: mais lento + mais alto
            (+5, -5),   # alívio: mais rápido + um pouco mais baixo
            (-1, +3),   # subida: levemente mais lento + um pouco mais alto
        ]
        out: list[tuple[str, str, str]] = []
        base_r = int(base_rate.rstrip("%"))
        base_v = int(base_volume.rstrip("%"))
        for i, line in enumerate(lines):
            dr, dv = deltas[i % len(deltas)]
            rate = _fmt_pct(base_r + dr)
            volume = _fmt_pct(base_v + dv)

            t = line.text.strip()
            if not t:
                continue
            if line.emphasis_words:
                for emph in line.emphasis_words:
                    t = _inject_emphasis_pause(t, emph)
            if t[-1] not in ".!?…":
                t += "."
            out.append((t, rate, volume))
        return out

    # ============================================
    # Edge TTS com stream WordBoundary
    # ============================================
    @retry(stop=stop_after_attempt(2), wait=wait_exponential(multiplier=2, min=2, max=6), reraise=True)
    def _edge_narrate_with_boundaries(
        self,
        text: str,
        output_path: Path,
        rate: str = "+0%",
        volume: str = "+0%",
    ) -> list[WordTimestamp]:
        """
        Gera áudio via Edge TTS e coleta eventos WordBoundary do stream.

        WordBoundary vem com:
        - offset: em "100ns ticks" (dividir por 10_000_000 pra segundos)
        - duration: idem
        - text: palavra sem pontuação

        Retorna lista de WordTimestamp com `terminator=""` (Edge não anexa
        pontuação no text do evento).

        volume: "+X%"/"-X%" passado direto pro prosody SSML interno do edge-tts.
        """
        audio_chunks, words = _run_async_safely(
            self._edge_stream_async(text, rate, volume)
        )
        if not audio_chunks:
            raise RuntimeError("Edge TTS não retornou áudio")

        # Salva MP3
        with open(output_path, "wb") as f:
            for chunk in audio_chunks:
                f.write(chunk)

        if output_path.stat().st_size < 1000:
            raise RuntimeError(f"Edge TTS gerou áudio muito pequeno ({output_path.stat().st_size}B)")
        return words

    async def _edge_stream_async(
        self, text: str, rate: str, volume: str = "+0%"
    ) -> tuple[list[bytes], list[WordTimestamp]]:
        communicate = edge_tts.Communicate(
            text=text, voice=self.voice, rate=rate, volume=volume
        )
        audio_chunks: list[bytes] = []
        words: list[WordTimestamp] = []
        async for chunk in communicate.stream():
            ctype = chunk.get("type")
            if ctype == "audio":
                audio_chunks.append(chunk["data"])
            elif ctype == "WordBoundary":
                offset_ticks = chunk.get("offset", 0)
                duration_ticks = chunk.get("duration", 0)
                start = offset_ticks / 10_000_000.0
                end = (offset_ticks + duration_ticks) / 10_000_000.0
                word_text = (chunk.get("text") or "").strip()
                if not word_text:
                    continue
                words.append(
                    WordTimestamp(
                        word=word_text,
                        start=start,
                        end=end,
                        terminator="",
                    )
                )
        return audio_chunks, words

    # ============================================
    # Piper TTS (fallback local)
    # ============================================
    def _piper_narrate(self, text: str, output_path: Path) -> None:
        model_path, config_path = self._ensure_piper_model()
        wav_path = output_path.with_suffix(".piper.wav")
        cmd = [
            "piper",
            "--model", str(model_path),
            "--config", str(config_path),
            "--output_file", str(wav_path),
        ]
        result = subprocess.run(
            cmd, input=text, text=True, capture_output=True, timeout=120,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"Piper falhou: {result.stderr[-500:] if result.stderr else 'sem stderr'}"
            )
        if not wav_path.exists() or wav_path.stat().st_size == 0:
            raise RuntimeError("Piper não gerou WAV")
        conv = subprocess.run(
            [
                "ffmpeg", "-y", "-i", str(wav_path),
                "-codec:a", "libmp3lame", "-b:a", "128k",
                str(output_path),
            ],
            capture_output=True, text=True,
        )
        wav_path.unlink(missing_ok=True)
        if conv.returncode != 0:
            raise RuntimeError(f"ffmpeg WAV→MP3 falhou: {conv.stderr[-300:]}")

    def _ensure_piper_model(self) -> tuple[Path, Path]:
        PIPER_MODELS_DIR.mkdir(parents=True, exist_ok=True)
        model_path = PIPER_MODELS_DIR / f"{PIPER_VOICE_NAME}.onnx"
        config_path = PIPER_MODELS_DIR / f"{PIPER_VOICE_NAME}.onnx.json"
        if not model_path.exists():
            logger.info(f"Baixando modelo Piper {PIPER_VOICE_NAME} (~63MB)...")
            self._download(PIPER_MODEL_URL, model_path)
        if not config_path.exists():
            self._download(PIPER_CONFIG_URL, config_path)
        return model_path, config_path

    @staticmethod
    def _download(url: str, target: Path) -> None:
        with httpx.stream("GET", url, timeout=120.0, follow_redirects=True) as r:
            r.raise_for_status()
            with open(target, "wb") as f:
                for chunk in r.iter_bytes():
                    f.write(chunk)

    # ============================================
    # gTTS (último recurso)
    # ============================================
    @staticmethod
    def _gtts_narrate(text: str, output_path: Path) -> None:
        from gtts import gTTS
        tts = gTTS(text=text, lang="pt", tld="com.br", slow=False)
        tts.save(str(output_path))

    # ============================================
    # Concat sem gap (fluxo contínuo)
    # ============================================
    @staticmethod
    def _concat(parts: list[Path], output: Path) -> None:
        if len(parts) == 1:
            # Copia direto
            import shutil
            shutil.copyfile(parts[0], output)
            return
        list_file = output.parent / f".concat_{output.stem}.txt"
        with open(list_file, "w") as f:
            for p in parts:
                f.write(f"file '{p.resolve()}'\n")
        try:
            result = subprocess.run(
                [
                    "ffmpeg", "-y",
                    "-f", "concat", "-safe", "0",
                    "-i", str(list_file),
                    "-c:a", "libmp3lame", "-b:a", "128k",
                    str(output),
                ],
                capture_output=True, text=True,
            )
            if result.returncode != 0:
                raise RuntimeError(f"Concat áudio falhou: {result.stderr[-500:]}")
        finally:
            list_file.unlink(missing_ok=True)

    # ============================================
    # Utilitário: listar vozes PT-BR disponíveis
    # ============================================
    @staticmethod
    def list_brazilian_voices() -> list[dict]:
        async def _list():
            voices = await edge_tts.list_voices()
            return [v for v in voices if v["Locale"].startswith("pt-BR")]
        return _run_async_safely(_list())


def _probe_duration(path: Path) -> float:
    """Duração em segundos via ffprobe."""
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
        return 0.0
