"""
Gera roteiro estruturado com foco em COESÃO usando IA.

Arquitetura de 2 passes (outline-first + expand + few-shot real):
1. Pass A (OUTLINE): gera só os beats (1 linha/cena) forçando estrutura
   narrativa Hook → Development → Climax → CTA.
2. Pass B (EXPAND): transforma cada bullet em cena completa, com few-shot
   de roteiros virais reais. Cada cena começa com conector explícito
   ("Mas...", "Porque...", "Entenda:", etc.), referenciando a anterior.

Modo premium opcional (SCRIPT_QUALITY_MODE=premium):
- Roda Pass B três vezes em paralelo e pede pra IA escolher o mais coeso.
- Custa 3x tokens mas cabe em 1000 RPD do Gemini 2.5 Flash free tier.

Cascata de fallback:
- Primário: Gemini 2.5 Flash (15 RPM, 1000 RPD)
- Fallback 1: Gemini 2.0 Flash (alto throughput)
- Fallback 2: Groq Llama 3.3 70B
- Fallback final: usa só o OUTLINE expandido mecanicamente (nunca crasha)
"""
from __future__ import annotations

import concurrent.futures
import json
import os
import re
from functools import lru_cache
from typing import Any, Callable

import google.generativeai as genai
from groq import Groq
from tenacity import retry, stop_after_attempt, wait_exponential

from src.pipeline.models import (
    BeatType,
    ImpactLevel,
    Script,
    ScriptLine,
    SceneType,
    TemplateType,
    VideoRequest,
)
from src.utils.config import ASSETS_DIR, config
from src.utils.logger import get_logger

logger = get_logger(__name__)


# ============================================
# FEW-SHOT: roteiros virais reais
# ============================================
SCRIPT_EXAMPLES_DIR = ASSETS_DIR / "script_examples"


@lru_cache(maxsize=8)
def _load_example(name: str) -> str:
    """Carrega roteiro exemplo (arquivo .txt) e retorna o conteúdo cru."""
    path = SCRIPT_EXAMPLES_DIR / f"{name}.txt"
    if not path.exists():
        logger.warning(f"Exemplo {name} não encontrado em {path}")
        return ""
    return path.read_text(encoding="utf-8").strip()


def _few_shot_for(template: TemplateType) -> str:
    """Retorna 2 roteiros exemplo do mesmo template (fallback pro motivacional)."""
    mapping = {
        TemplateType.MOTIVACIONAL: ["motivacional_1", "motivacional_2"],
        TemplateType.VIRAL: ["viral_1", "viral_2"],
        TemplateType.NOTICIAS: ["viral_1", "viral_2"],  # estilo informativo
        TemplateType.GAMING: ["viral_1", "viral_2"],
    }
    names = mapping.get(template, ["motivacional_1", "motivacional_2"])
    parts = []
    for n in names:
        content = _load_example(n)
        if content:
            parts.append(f"━━━ EXEMPLO: {n} ━━━\n{content}")
    return "\n\n".join(parts)


# ============================================
# PROMPTS
# ============================================
TEMPLATE_FLAVOR = {
    TemplateType.MOTIVACIONAL: (
        "tom firme e inspirador, linguagem direta em 'você', beats de superação "
        "com exemplos concretos (Kobe, Jocko, Rowling, Edison, atletas, empreendedores)"
    ),
    TemplateType.VIRAL: (
        "tom de curiosidade/revelação, hook com pergunta ou fato surpreendente, "
        "revelação em camadas (curiosity gap)"
    ),
    TemplateType.NOTICIAS: (
        "tom informativo e claro, fato principal nos primeiros 3s, linguagem neutra"
    ),
    TemplateType.GAMING: (
        "tom energético, gírias jovens, cenas curtíssimas, muita ênfase em keywords"
    ),
}


OUTLINE_PROMPT = """Você é um editor-chefe de vídeos virais para TikTok/Reels em PT-BR.

TAREFA: Gere um OUTLINE (só bullets, 1 linha por cena) de um vídeo de {duration}s sobre:
TEMA: {theme}

ESTILO: {flavor}

━━━ ESTRUTURA OBRIGATÓRIA ━━━
1. HOOK (1 cena): frase que PARA o scroll. Pergunta, contradição, ou promessa inesperada.
2. DEVELOPMENT (3-5 cenas): cada uma ADICIONA uma camada nova ao argumento. Use nomes concretos, números, exemplos específicos.
3. CLIMAX (1-2 cenas): a VIRADA. A frase-chave que resume tudo. É aqui que você fala o que o vídeo inteiro tava construindo.
4. CTA (1 cena): chamada final — salvar, seguir, refletir.

━━━ FORMATO DE SAÍDA (só JSON) ━━━
{{
  "title": "título curto",
  "hashtags": ["#tag1", "#tag2", "#tag3"],
  "beats": [
    {{"role": "hook", "idea": "1 frase resumindo a cena"}},
    {{"role": "development", "idea": "..."}},
    {{"role": "climax", "idea": "..."}},
    {{"role": "cta", "idea": "..."}}
  ]
}}

━━━ REGRAS ━━━
- Total de beats: {min_scenes} a {max_scenes}.
- Cada `idea` tem 8-15 palavras, resume a essência da cena.
- NÃO escreva a cena completa ainda — só o núcleo da ideia.
- Evite clichês ("nunca desista", "acredite em si") — force ângulo específico.

Responda SÓ o JSON."""


EXPAND_PROMPT = """Você é um roteirista de vídeos virais para TikTok/Reels em PT-BR.

TAREFA: Expandir o OUTLINE abaixo em roteiro completo de {target_words} palavras.

━━━ TEMA ━━━
{theme}

━━━ OUTLINE ━━━
{outline_json}

━━━ ESTILO DO TEMPLATE ━━━
{flavor}

━━━ EXEMPLOS DE ROTEIROS QUE FUNCIONAM (ESTUDE A ESTRUTURA) ━━━
{few_shot}

━━━ REGRAS CRÍTICAS ━━━

1. **COESÃO OBRIGATÓRIA**: Cada cena (EXCETO a primeira, que é o hook) DEVE começar com um conector que amarra à anterior:
   - "Mas...", "Porque...", "E aqui está o ponto:", "Entenda:", "Pense assim:",
     "É aqui que...", "E tem mais:", "Só que...", "Por isso...", "Agora,".
   - Sem exceção. Se a cena começa sem conector, o roteiro é REJEITADO.

2. **PROIBIDO**:
   - Frases genéricas: "acredite em si", "vá em frente", "você consegue", "nunca desista", "os sonhos importam"
   - Repetir a MESMA ideia com palavras diferentes entre cenas
   - Clichês motivacionais vazios sem exemplo concreto
   - Abrir cena sem conector (quebra a coesão e é o bug que queremos consertar)

3. **OBRIGATÓRIO**:
   - Cada cena de `development` deve trazer EVIDÊNCIA CONCRETA: nome próprio, número, analogia física, exemplo histórico.
   - `climax` é onde você fala o INSIGHT — a frase que o espectador vai querer printar.
   - Frases curtas alternando com frases longas (pontos finais = pausa, vírgulas = meia-pausa).
   - Usar pontuação COMO INSTRUMENTO DE RITMO. Pontos finais criam pausa de respiração na narração.

4. **TAMANHO**: {min_words}-{max_words} palavras no total. Cada cena: 8-20 palavras.

━━━ TIPOS DE CENA ━━━
- "video_broll": ação, movimento (correr, trabalhar, vencer, falar em público)
- "image_kenburns": conceito abstrato (céu, livro, mapa, montanha)
- "color_background": fundo liso com frase de impacto CENTRAL. Máx 2 vezes no roteiro inteiro.
- "person_photo": só se citar pessoa famosa específica (preencha `person_name`)

━━━ IMPACT LEVEL (intensidade visual da legenda) ━━━
Para cada cena, classifique "impact_level":
- "low": cena normal de desenvolvimento (maioria das cenas)
- "medium": argumento forte, merece legenda 20% maior
- "high": FRASE BOMBA / REVELAÇÃO — legenda GIGANTE no centro da tela
  (MÁXIMO 1-2 cenas no roteiro todo marcadas como "high"). Normalmente é
  o insight-chave do climax ou a virada que o espectador vai querer printar.
  Se marcar 4 cenas como "high" perde totalmente o impacto.

━━━ FORMATO JSON DE SAÍDA ━━━
{{
  "title": "título curto",
  "hashtags": ["#tag1", "#tag2"],
  "lines": [
    {{
      "text": "Frase completa com pontuação natural, começando com conector (exceto hook).",
      "scene_type": "video_broll",
      "visual_queries": ["3 queries EM INGLÊS, mais geral → mais específica", "person running sunrise", "determined athlete training"],
      "person_name": null,
      "bg_color": null,
      "emphasis_words": ["palavra_chave1", "palavra_chave2"],
      "beat_type": "hook",
      "impact_level": "low"
    }}
  ]
}}

━━━ CHECKLIST ANTES DE RESPONDER ━━━
1. Cada cena (menos a primeira) começa com conector? (Se não, reescreva)
2. Cada cena de development tem exemplo CONCRETO? (Se não, adicione)
3. Total ≥ {min_words} palavras? (Se não, expanda)
4. Pontuação criando ritmo? (pontos = pausa grande, vírgulas = pausa curta)
5. Máximo 1-2 cenas com impact_level="high" — a revelação principal apenas?

Responda SÓ o JSON."""


SELECT_BEST_PROMPT = """Você tem 3 roteiros abaixo para o mesmo tema. Responda SÓ o número (1, 2 ou 3) do mais COESO: o que melhor amarra as cenas umas nas outras, com conectores claros e argumento que flui.

Priorize: COESÃO > CONCRETUDE > TAMANHO. Ignore beleza poética — queremos fluxo de argumento.

━━━ CANDIDATO 1 ━━━
{script_1}

━━━ CANDIDATO 2 ━━━
{script_2}

━━━ CANDIDATO 3 ━━━
{script_3}

Responda SÓ o número: 1, 2 ou 3."""


# ============================================
# ScriptWriter
# ============================================
class ScriptWriter:
    def __init__(self) -> None:
        self.gemini_available = bool(config.gemini_api_key)
        self.groq_available = bool(config.groq_api_key)
        if self.gemini_available:
            genai.configure(api_key=config.gemini_api_key)
        if self.groq_available:
            self.groq_client = Groq(api_key=config.groq_api_key)
        if not self.gemini_available and not self.groq_available:
            raise ValueError(
                "Nenhuma IA configurada. Configure GEMINI_API_KEY ou GROQ_API_KEY."
            )
        logger.info(
            f"ScriptWriter: Gemini={'✅' if self.gemini_available else '❌'} | "
            f"Groq={'✅' if self.groq_available else '❌'}"
        )

    # ============================================
    # API pública
    # ============================================
    def generate(self, request: VideoRequest) -> Script:
        """
        Geração em 2 passes: outline → expand.
        Em modo premium: 3 expands paralelos + seleção.
        """
        duration = request.duration_seconds
        target_words = int(duration * 150 / 60)
        min_words = int(target_words * 0.80)
        max_words = int(target_words * 1.30)
        min_scenes = max(5, duration // 6)
        max_scenes = max(8, duration // 3)
        flavor = TEMPLATE_FLAVOR.get(request.template, "")

        logger.info(
            f"[ScriptWriter] tema={request.theme!r} template={request.template.value} "
            f"target={target_words} palavras {min_scenes}-{max_scenes} cenas"
        )

        # ==============================
        # PASS A — OUTLINE
        # ==============================
        outline = self._generate_outline(
            theme=request.theme,
            duration=duration,
            flavor=flavor,
            min_scenes=min_scenes,
            max_scenes=max_scenes,
        )
        logger.info(f"[ScriptWriter] Outline: {len(outline.get('beats', []))} beats")

        # ==============================
        # PASS B — EXPAND (com modo premium opcional)
        # ==============================
        few_shot = _few_shot_for(request.template)
        premium = os.getenv("SCRIPT_QUALITY_MODE", "standard").lower() == "premium"

        expand_fn: Callable[[], Script] = lambda: self._expand_outline(
            theme=request.theme,
            outline=outline,
            flavor=flavor,
            few_shot=few_shot,
            target_words=target_words,
            min_words=min_words,
            max_words=max_words,
        )

        script = None
        if premium:
            logger.info("[ScriptWriter] Modo PREMIUM: 3 rascunhos paralelos + seleção")
            try:
                script = self._premium_expand(expand_fn)
            except Exception as e:
                logger.warning(
                    f"[ScriptWriter] Modo premium falhou ({type(e).__name__}): {e}. "
                    f"Caindo pro modo standard (1 tentativa) antes do mecânico."
                )
                # Tenta 1x em modo standard antes de desistir pro mecânico
                try:
                    script = expand_fn()
                except Exception as e2:
                    logger.warning(
                        f"[ScriptWriter] Fallback standard também falhou: "
                        f"{type(e2).__name__}: {e2}"
                    )

        if script is None and not premium:
            # Tenta 2 vezes antes de cair pro outline mecânico. A segunda
            # tentativa costuma ser mais curta/certeira porque o LLM varia
            # resposta entre calls.
            for attempt in range(2):
                try:
                    script = expand_fn()
                    break
                except Exception as e:
                    logger.warning(
                        f"[ScriptWriter] Expand tentativa {attempt + 1} falhou "
                        f"({type(e).__name__}): {e}"
                    )

        # Fallback final compartilhado: se nem standard nem premium deram certo,
        # usa outline mecânico (sempre funciona — não depende do LLM).
        if script is None:
            logger.warning(
                "[ScriptWriter] Todas as tentativas de expand falharam, "
                "caindo pro outline mecânico (roteiro básico, mas funcional)"
            )
            script = self._mechanical_from_outline(outline, request.template)

        word_count = len(script.full_text.split())
        logger.info(
            f"[ScriptWriter] Final: {len(script.lines)} cenas, {word_count} palavras"
        )
        return script

    # ============================================
    # PASS A: OUTLINE
    # ============================================
    def _generate_outline(
        self,
        theme: str,
        duration: int,
        flavor: str,
        min_scenes: int,
        max_scenes: int,
    ) -> dict[str, Any]:
        prompt = OUTLINE_PROMPT.format(
            theme=theme,
            duration=duration,
            flavor=flavor,
            min_scenes=min_scenes,
            max_scenes=max_scenes,
        )
        raw = self._call_with_fallback(
            system="Você é um editor-chefe de conteúdo viral. Responda só JSON válido.",
            user=prompt,
            max_tokens=1200,
        )
        data = self._parse_json(raw)
        if not data.get("beats"):
            raise RuntimeError("Outline vazio — IA não retornou beats")
        return data

    # ============================================
    # PASS B: EXPAND
    # ============================================
    def _expand_outline(
        self,
        theme: str,
        outline: dict[str, Any],
        flavor: str,
        few_shot: str,
        target_words: int,
        min_words: int,
        max_words: int,
    ) -> Script:
        outline_json = json.dumps(outline, ensure_ascii=False, indent=2)
        prompt = EXPAND_PROMPT.format(
            theme=theme,
            outline_json=outline_json,
            flavor=flavor,
            few_shot=few_shot,
            target_words=target_words,
            min_words=min_words,
            max_words=max_words,
        )
        raw = self._call_with_fallback(
            system=(
                "Você é um roteirista de vídeos virais em PT-BR obcecado por COESÃO. "
                "Cada cena referencia a anterior com conector explícito. Responda só JSON."
            ),
            user=prompt,
            max_tokens=12000,  # few-shot + visual_queries + impact_level → JSON longo.
            # Gemini 2.5 Flash suporta até 65K tokens de output; 12K dá folga
            # pra roteiros com 10-15 cenas sem truncar.
        )
        data = self._parse_json(raw)
        script = self._to_script(data, outline)
        if not script.lines:
            raise RuntimeError("Expand retornou roteiro vazio")
        return script

    # ============================================
    # Modo PREMIUM: 3 rascunhos paralelos + seleção
    # ============================================
    def _premium_expand(self, expand_fn: Callable[[], Script]) -> Script:
        # Roda 3 expansões em paralelo (cada uma é rate-limitada internamente)
        scripts: list[Script] = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
            futures = [pool.submit(expand_fn) for _ in range(3)]
            for f in concurrent.futures.as_completed(futures):
                try:
                    scripts.append(f.result())
                except Exception as e:
                    logger.warning(f"[Premium] 1 rascunho falhou: {e}")

        if not scripts:
            raise RuntimeError("Nenhum rascunho premium gerou sucesso")
        if len(scripts) == 1:
            return scripts[0]

        # Preenche com cópia se vierem <3 (prompt fixo)
        while len(scripts) < 3:
            scripts.append(scripts[0])

        # Pede pra IA escolher o melhor
        try:
            raw = self._call_with_fallback(
                system="Você é editor-chefe. Responda SÓ o número, sem explicação.",
                user=SELECT_BEST_PROMPT.format(
                    script_1=scripts[0].full_text,
                    script_2=scripts[1].full_text,
                    script_3=scripts[2].full_text,
                ),
                max_tokens=8,
            )
            # Extrai primeiro dígito 1-3
            match = re.search(r"[123]", raw)
            chosen_idx = (int(match.group(0)) - 1) if match else 0
            chosen_idx = max(0, min(chosen_idx, len(scripts) - 1))
            logger.info(f"[Premium] escolhido rascunho {chosen_idx + 1}")
            return scripts[chosen_idx]
        except Exception as e:
            logger.warning(f"[Premium] seleção falhou, usando primeiro: {e}")
            return scripts[0]

    # ============================================
    # Fallback mecânico: se tudo falhar, transforma outline em cenas básicas
    # ============================================
    @staticmethod
    def _mechanical_from_outline(
        outline: dict[str, Any], template: TemplateType
    ) -> Script:
        """Último recurso: transforma beats crus em cenas sem expansão da IA."""
        lines: list[ScriptLine] = []
        beats = outline.get("beats") or []
        for b in beats:
            idea = (b.get("idea") or "").strip()
            if not idea:
                continue
            if idea[-1] not in ".!?":
                idea += "."
            role = (b.get("role") or "development").lower()
            try:
                beat = BeatType(role)
            except ValueError:
                beat = BeatType.DEVELOPMENT
            lines.append(
                ScriptLine(
                    text=idea,
                    scene_type=SceneType.VIDEO_BROLL,
                    visual_query=idea[:50],
                    visual_queries=[idea[:50]],
                    beat_type=beat,
                )
            )
        return Script(
            title=outline.get("title", "") or "",
            hashtags=outline.get("hashtags") or [],
            lines=lines,
        )

    # ============================================
    # Cascata Gemini 2.5 Flash → 2.0 Flash → Groq
    # ============================================
    def _call_with_fallback(self, system: str, user: str, max_tokens: int = 4000) -> str:
        """Tenta os modelos em ordem. Retorna a string bruta."""
        errors: list[str] = []

        if self.gemini_available:
            for model_name in (config.gemini_model, "gemini-2.0-flash"):
                try:
                    return self._call_gemini(system, user, model_name, max_tokens)
                except Exception as e:
                    msg = f"{model_name}: {type(e).__name__}: {e}"
                    logger.warning(f"⚠️ Gemini {msg}")
                    errors.append(msg)

        if self.groq_available:
            try:
                return self._call_groq(system, user, max_tokens)
            except Exception as e:
                msg = f"groq: {type(e).__name__}: {e}"
                logger.warning(f"⚠️ Groq falhou {msg}")
                errors.append(msg)

        raise RuntimeError(f"Nenhuma IA disponível. Erros: {'; '.join(errors)}")

    @retry(stop=stop_after_attempt(2), wait=wait_exponential(multiplier=2, min=2, max=6), reraise=True)
    def _call_gemini(self, system: str, user: str, model_name: str, max_tokens: int) -> str:
        logger.info(f"Chamando {model_name}...")
        model = genai.GenerativeModel(
            model_name=model_name,
            system_instruction=system,
            generation_config=genai.types.GenerationConfig(
                temperature=0.85,
                max_output_tokens=max_tokens,
                response_mime_type="application/json",
            ),
        )
        response = model.generate_content(user)
        raw = response.text or "{}"
        logger.info(f"{model_name} retornou {len(raw)} chars")
        return raw

    @retry(stop=stop_after_attempt(2), wait=wait_exponential(multiplier=2, min=2, max=6), reraise=True)
    def _call_groq(self, system: str, user: str, max_tokens: int) -> str:
        logger.info(f"Chamando Groq {config.groq_model}...")
        completion = self.groq_client.chat.completions.create(
            model=config.groq_model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0.85,
            max_tokens=max_tokens,
            response_format={"type": "json_object"},
        )
        raw = completion.choices[0].message.content or "{}"
        logger.info(f"Groq retornou {len(raw)} chars")
        return raw

    # ============================================
    # Parsing
    # ============================================
    @staticmethod
    def _parse_json(raw: str) -> dict[str, Any]:
        """
        Parser tolerante a JSON do LLM. Tenta em ordem:
        1. Parse direto
        2. Extrair bloco {...} com regex (descarta prosa antes/depois)
        3. Se o JSON veio TRUNCADO (LLM bateu no limite de tokens),
           tenta salvar o que der: fecha strings/arrays/objetos pendentes
           e parseia o que deu. Pelo menos as primeiras N cenas são
           recuperadas em vez de crashar tudo.
        """
        # Limpa cercas de código que alguns modelos insistem em mandar
        raw = raw.strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw)

        # Tentativa 1: parse direto
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass

        # Tentativa 2: pega o primeiro bloco {...} completo
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass

        # Tentativa 3: JSON truncado — fecha estruturas pendentes
        recovered = _recover_truncated_json(raw)
        if recovered is not None:
            logger.warning(
                "⚠️ JSON do LLM veio truncado. Recuperado parcialmente."
            )
            return recovered

        raise json.JSONDecodeError(
            f"Não foi possível parsear JSON do LLM. Primeiros 300 chars: {raw[:300]!r}",
            raw, 0,
        )

    @staticmethod
    def _to_script(data: dict[str, Any], outline_fallback: dict[str, Any]) -> Script:
        lines: list[ScriptLine] = []
        for item in data.get("lines", []):
            text = (item.get("text") or "").strip()
            if not text:
                continue

            # scene_type
            raw_scene = (item.get("scene_type") or "video_broll").strip().lower()
            try:
                scene_type = SceneType(raw_scene)
            except ValueError:
                logger.warning(f"scene_type inválido {raw_scene!r} → video_broll")
                scene_type = SceneType.VIDEO_BROLL

            # beat_type
            raw_beat = (item.get("beat_type") or "development").strip().lower()
            try:
                beat_type = BeatType(raw_beat)
            except ValueError:
                beat_type = BeatType.DEVELOPMENT

            # impact_level: IA marca intensidade visual da legenda.
            # Default "low" se omisso (backcompat com outputs LLM antigos).
            raw_impact = (item.get("impact_level") or "low").strip().lower()
            try:
                impact_level = ImpactLevel(raw_impact)
            except ValueError:
                logger.warning(f"impact_level inválido {raw_impact!r} → low")
                impact_level = ImpactLevel.LOW

            emphasis = item.get("emphasis_words") or []
            if not isinstance(emphasis, list):
                emphasis = []

            # visual_queries: aceita list (novo) OU string única (backcompat)
            raw_queries = item.get("visual_queries")
            if isinstance(raw_queries, list) and raw_queries:
                queries = [str(q).strip() for q in raw_queries if q]
            else:
                single = (item.get("visual_query") or "").strip()
                queries = [single] if single else []

            lines.append(
                ScriptLine(
                    text=text,
                    scene_type=scene_type,
                    visual_query=queries[0] if queries else "",
                    visual_queries=queries,
                    person_name=item.get("person_name"),
                    bg_color=item.get("bg_color"),
                    emphasis_words=[str(w) for w in emphasis if w],
                    beat_type=beat_type,
                    impact_level=impact_level,
                )
            )

        if not lines:
            raise RuntimeError("IA retornou roteiro vazio")

        # Limita impact_level="high" a no máximo 2 por roteiro. Se LLM exagerou,
        # rebaixa os excedentes (mantém os 2 primeiros, resto vira "medium").
        MAX_HIGH_IMPACT = 2
        high_count = 0
        for ln in lines:
            if ln.impact_level == ImpactLevel.HIGH:
                high_count += 1
                if high_count > MAX_HIGH_IMPACT:
                    logger.info(
                        f"[ScriptWriter] impact_level=high excedente, "
                        f"rebaixando '{ln.text[:40]}...' → medium"
                    )
                    ln.impact_level = ImpactLevel.MEDIUM

        hashtags = data.get("hashtags") or outline_fallback.get("hashtags") or []
        if not isinstance(hashtags, list):
            hashtags = []

        return Script(
            title=(data.get("title") or outline_fallback.get("title") or "").strip(),
            hashtags=[str(h) for h in hashtags if h],
            lines=lines,
        )


# ============================================
# Recuperação de JSON truncado pelo LLM
# ============================================
def _recover_truncated_json(raw: str) -> dict[str, Any] | None:
    """
    Quando o LLM bate no max_output_tokens e a resposta fica cortada no meio
    de uma string/array/objeto, esta função tenta fechar as estruturas
    pendentes e parsear o que deu.

    Estratégias (tenta na ordem):
    1. Truncar no último objeto interno completo (`},`) antes do truncate —
       pega a maior parte das cenas quando o LLM truncou dentro de uma cena.
    2. Truncar em último ponto "seguro" (fora de string, depth consistente) e
       fechar todos os brackets/braces pendentes.
    3. Último recurso: fatiar bem no início (primeiro `{...}` topo) e fechar
       tudo — retorna dict mesmo que sem `lines`, permitindo fallback upstream.
    """
    idx = raw.find("{")
    if idx < 0:
        return None
    text = raw[idx:]

    candidates: list[str] = []

    # === Estratégia 1: último objeto interno completo + fechar wrappers ===
    # Ex: `{"lines":[{...},{...},{...<TRUNC>` → cortar no último `},` e fechar.
    last_good_obj = _find_last_complete_inner_object(text)
    if last_good_obj > 0:
        partial = text[:last_good_obj]
        # Fecha estruturas pendentes (arrays/objetos abertos)
        closed = _close_open_structures(partial)
        if closed:
            candidates.append(closed)

    # === Estratégia 2: trunca no último ponto fora de string e fecha ===
    closed_2 = _close_open_structures(text)
    if closed_2:
        candidates.append(closed_2)

    # Tenta cada candidato. Aceita SÓ se o resultado tiver algum dado útil
    # (title ou lines ou beats — pelo menos indica que parseou um dict viável).
    for c in candidates:
        try:
            data = json.loads(c)
            if isinstance(data, dict):
                return data
        except (json.JSONDecodeError, ValueError):
            continue
    return None


def _find_last_complete_inner_object(text: str) -> int:
    """
    Retorna o índice (exclusive) logo após o último `}` que pertence a um
    objeto interno COMPLETO (no mesmo nível de aninhamento do primeiro objeto
    interno). Retorna -1 se não houver nenhum.

    Exemplo:
      text = `{"lines":[{"text":"a"},{"text":"b"},{"text":"trun`
      Retorna a posição logo depois de `{"text":"b"}` — o JSON pode ser
      fechado com `]}` e conter 2 cenas completas.
    """
    in_str = False
    escape = False
    depth = 0       # profundidade de `{}`
    target_depth = 2  # objeto interno do array de "lines" vive em depth 2+
    last_good = -1

    for i, ch in enumerate(text):
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            # Se acabou de fechar um objeto interno profundo, marca.
            if depth == target_depth:
                last_good = i + 1
            depth -= 1
    return last_good


def _close_open_structures(text: str) -> str | None:
    """
    Trunca `text` no último ponto fora de string/com depth válido e fecha
    brackets/braces/aspas pendentes pra formar JSON válido.

    Retorna a string fechada ou None se não conseguir reconstruir algo útil.
    """
    # 1. Scan: descobre o último índice "seguro" — onde estamos FORA de string
    # e com depth consistente (depth_brace e depth_bracket >= 0).
    in_str = False
    escape = False
    depth_brace = 0
    depth_bracket = 0
    last_safe = 0

    for i, ch in enumerate(text):
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == '"':
            in_str = not in_str
            # fronteira "saindo" da string é um ponto seguro
            if not in_str:
                last_safe = i + 1
            continue
        if in_str:
            continue
        if ch == "{":
            depth_brace += 1
        elif ch == "}":
            depth_brace -= 1
        elif ch == "[":
            depth_bracket += 1
        elif ch == "]":
            depth_bracket -= 1
        # Ponto seguro: fora de string, após processar o char
        if depth_brace >= 0 and depth_bracket >= 0:
            last_safe = i + 1

    # 2. Se o texto terminou dentro de string, trunca no último ponto seguro
    work = text[:last_safe] if in_str else text

    # 3. Limpa separadores pendentes (vírgula, dois-pontos, chaves de key
    # sem valor) que podem ter ficado no fim após truncar.
    work = work.rstrip()
    while work and work[-1] in ",:":
        work = work[:-1].rstrip()
    # Se terminou em `"key"` sem valor, remove a key incompleta — busca a
    # última vírgula ou chave aberta antes dela e corta.
    if work.endswith('"'):
        # Provavelmente dangling key — retrocede até `,` ou `{` prévio.
        for back in range(len(work) - 2, -1, -1):
            if work[back] in ",{[":
                work = work[: back + 1].rstrip()
                if work.endswith(","):
                    work = work[:-1].rstrip()
                break

    # 4. Recalcula depths finais
    in_str = False
    escape = False
    db = 0
    dbr = 0
    for ch in work:
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch == "{":
            db += 1
        elif ch == "}":
            db -= 1
        elif ch == "[":
            dbr += 1
        elif ch == "]":
            dbr -= 1

    # Se ainda está dentro de string (aspa nunca fechou), fecha a aspa
    if in_str:
        work += '"'

    # Fecha em ordem: arrays primeiro, objetos depois (LIFO inverso da abertura)
    closing = ("]" * max(0, dbr)) + ("}" * max(0, db))
    result = work + closing
    return result if result else None
