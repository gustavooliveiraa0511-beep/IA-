"""
Gera roteiro estruturado usando Groq (Llama 3.3 70B).

Estratégia robusta em 3 camadas pra garantir roteiro com tamanho certo:
1. Prompt super explícito com EXEMPLO concreto
2. Se vier curto, faz 2ª chamada pra EXPANDIR
3. Se mesmo assim tiver curto, usa o que tem (não trava o pipeline)
"""
from __future__ import annotations

import json
import re
from typing import Any

from groq import Groq
from tenacity import retry, stop_after_attempt, wait_exponential

from src.pipeline.models import Script, ScriptLine, SceneType, TemplateType, VideoRequest
from src.utils.config import config
from src.utils.logger import get_logger

logger = get_logger(__name__)


TEMPLATE_INSTRUCTIONS = {
    TemplateType.MOTIVACIONAL: """
Estilo MOTIVACIONAL:
- Hook de impacto nos primeiros 3 segundos (frase que pare o scroll)
- Linguagem firme, direta, inspiradora
- Use "você" (fala direto com o espectador)
- Pelo menos 2 cenas de COLOR_BACKGROUND com frases de impacto curtas
- Cenas de VIDEO_BROLL com pessoas em ação (correndo, vencendo, trabalhando)
- Finalize com call-to-action ("siga pra mais", "salva esse vídeo", etc.)
- Cores de fundo: preto (#000000), laranja-fogo (#ff4500), dourado (#daa520), vermelho (#b22222)
""",
    TemplateType.VIRAL: """
Estilo VIRAL / CURIOSIDADES:
- Hook com pergunta ou fato surpreendente
- Ritmo acelerado, cenas curtas
- Revele a informação por etapas (curiosity gap)
- Mistura VIDEO_BROLL e IMAGE_KENBURNS
- Final com gancho pra próximo vídeo
""",
    TemplateType.NOTICIAS: """
Estilo NOTÍCIAS / INFORMATIVO:
- Apresente o fato de forma clara nos primeiros segundos
- Use PERSON_PHOTO quando citar pessoas públicas (defina person_name)
- Ritmo médio, cenas de 2-3 segundos
- Tom neutro e informativo
""",
    TemplateType.GAMING: """
Estilo GAMING / ENTRETENIMENTO:
- Energia alta, gírias jovens
- Cenas curtíssimas (~1s)
- Muita ênfase em palavras-chave
- VIDEO_BROLL com gameplay ou cenas dinâmicas
""",
}


SYSTEM_PROMPT = """Você é um roteirista especialista em vídeos motivacionais virais de {duration} segundos para TikTok/Reels.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🎯 META PRINCIPAL: O ROTEIRO DEVE TER {target_words} PALAVRAS.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Essa é a regra MAIS IMPORTANTE. Conte as palavras. Menos de {min_words} é FALHA GRAVE.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📝 EXEMPLO DE ROTEIRO CORRETO (tema: "nunca desista"):
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Cena 1 (10 palavras): "Você já pensou em desistir hoje? Respira fundo e escuta."
Cena 2 (14 palavras): "Todo grande nome que você admira já esteve exatamente onde você está agora."
Cena 3 (13 palavras): "A diferença não é talento, é a teimosia de continuar quando dói."
Cena 4 (15 palavras): "Cada tentativa falhada é um tijolo da ponte que vai te levar lá."
Cena 5 (12 palavras): "O universo testa quem realmente quer antes de entregar o prêmio."
Cena 6 (16 palavras): "Então respira, enxuga o rosto e volta pro jogo. Você veio longe demais pra parar."
Cena 7 (9 palavras): "Salva esse vídeo e manda pra quem precisa ouvir."

TOTAL: 89 palavras → ~35 segundos narrado. IDEAL.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⛔ EXEMPLO DE ROTEIRO RUIM (NUNCA FAÇA):
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Cena 1: "Acredite"
Cena 2: "Você consegue"
Cena 3: "Vai em frente"
→ Isso tem 5 palavras. É INACEITÁVEL. Sempre escreva FRASES COMPLETAS.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📐 REGRAS DE TAMANHO:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

✅ TOTAL: {target_words} palavras (±15%)
✅ CENAS: {min_scenes} a {max_scenes} cenas
✅ POR CENA: 8 a 18 palavras (frases completas, não pedaços)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

{template_instructions}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🎬 TIPOS DE CENA (escolha um por cena):
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

- "video_broll": Ações e movimento. Use quando descrever ações.
- "image_kenburns": Imagens estáticas com zoom. Use pra conceitos abstratos.
- "color_background": Fundo liso colorido com FRASE CENTRAL de impacto. Máx 2x.
- "person_photo": Só se citar pessoa famosa específica.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📦 FORMATO JSON DA RESPOSTA:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

{{
  "title": "título curto do vídeo",
  "hashtags": ["#motivacao", "#mindset", "#disciplina"],
  "lines": [
    {{
      "text": "frase COMPLETA de 8-18 palavras",
      "scene_type": "video_broll",
      "visual_query": "termos EM INGLÊS pra busca de vídeo (ex: person running sunrise)",
      "person_name": null,
      "bg_color": null,
      "emphasis_words": ["palavra1", "palavra2"]
    }}
  ]
}}

⚠️ ANTES DE RESPONDER: conte o TOTAL de palavras somando todas as cenas.
Se for menos de {min_words}, REESCREVA tudo mais longo. É OBRIGATÓRIO.
"""


EXPAND_PROMPT = """O roteiro abaixo tem APENAS {current_words} palavras, mas precisa ter {target_words}.

ROTEIRO ATUAL:
{current_script}

⚠️ TAREFA: EXPANDA esse roteiro mantendo o mesmo tema e tom, mas adicionando MAIS CENAS e EXPANDINDO as existentes pra atingir {target_words} palavras no total.

Retorne APENAS o JSON expandido no mesmo formato (title, hashtags, lines).
Cada cena deve ter 8-18 palavras COMPLETAS. Não corte palavras, ESCREVA MAIS.
"""


class ScriptWriter:
    def __init__(self) -> None:
        if not config.groq_api_key:
            raise ValueError("GROQ_API_KEY não configurado")
        self.client = Groq(api_key=config.groq_api_key)

    def generate(self, request: VideoRequest) -> Script:
        """
        Gera roteiro com expansão automática se vier curto.
        Nunca falha o pipeline por causa de tamanho — apenas warn.
        """
        logger.info(f"Gerando roteiro: tema={request.theme!r} template={request.template}")

        duration = request.duration_seconds
        target_words = int(duration * 150 / 60)
        min_words = int(target_words * 0.80)
        max_words = int(target_words * 1.30)
        min_scenes = max(5, duration // 6)
        max_scenes = max(8, duration // 3)

        # === TENTATIVA 1: geração normal ===
        script = self._call_groq(
            request=request,
            duration=duration,
            target_words=target_words,
            min_words=min_words,
            max_words=max_words,
            min_scenes=min_scenes,
            max_scenes=max_scenes,
        )
        word_count = len(script.full_text.split())
        logger.info(f"Tentativa 1: {len(script.lines)} cenas, {word_count} palavras (meta {target_words})")

        # === TENTATIVA 2: expansão se curto ===
        if word_count < min_words:
            logger.warning(
                f"Roteiro curto ({word_count} < {min_words}). "
                f"Pedindo pra IA EXPANDIR..."
            )
            try:
                expanded = self._expand_script(script, target_words)
                expanded_words = len(expanded.full_text.split())
                logger.info(f"Após expansão: {expanded_words} palavras")
                if expanded_words > word_count:
                    script = expanded
                    word_count = expanded_words
            except Exception as e:
                logger.warning(f"Expansão falhou: {e}. Usando o roteiro original.")

        # === FALLBACK FINAL: se ainda curto, usa mesmo assim (não crasha) ===
        if word_count < min_words * 0.5:  # só alerta se < 50% do mínimo
            logger.warning(
                f"⚠️ Roteiro final tem {word_count} palavras (alvo {target_words}). "
                f"Vídeo vai ficar mais curto que o ideal mas será gerado."
            )

        return script

    def _call_groq(
        self,
        request: VideoRequest,
        duration: int,
        target_words: int,
        min_words: int,
        max_words: int,
        min_scenes: int,
        max_scenes: int,
    ) -> Script:
        system = SYSTEM_PROMPT.format(
            duration=duration,
            target_words=target_words,
            min_words=min_words,
            max_words=max_words,
            min_scenes=min_scenes,
            max_scenes=max_scenes,
            template_instructions=TEMPLATE_INSTRUCTIONS[request.template],
        )

        user = (
            f"TEMA: {request.theme}\n\n"
            f"Escreva um roteiro motivacional com {target_words} palavras "
            f"(distribuídas em {min_scenes}-{max_scenes} cenas de 8-18 palavras cada). "
            f"Siga o exemplo do sistema.\n\n"
            f"Conte as palavras antes de responder."
        )
        if request.custom_script:
            user += f"\n\nTexto base do usuário (adapte em cenas):\n{request.custom_script}"

        return self._run_groq(system, user)

    def _expand_script(self, script: Script, target_words: int) -> Script:
        """Segunda chamada: expande um roteiro curto."""
        current_script = "\n".join(f"- {line.text}" for line in script.lines)
        current_words = len(script.full_text.split())

        user = EXPAND_PROMPT.format(
            current_words=current_words,
            target_words=target_words,
            current_script=current_script,
        )
        system = "Você é um roteirista expandindo um roteiro curto. Retorne JSON válido."
        return self._run_groq(system, user)

    @retry(stop=stop_after_attempt(2), wait=wait_exponential(multiplier=2, min=2, max=6), reraise=True)
    def _run_groq(self, system: str, user: str) -> Script:
        completion = self.client.chat.completions.create(
            model=config.groq_model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0.85,
            max_tokens=3500,
            response_format={"type": "json_object"},
        )
        raw = completion.choices[0].message.content or "{}"
        data = self._parse_json(raw)
        return self._to_script(data)

    @staticmethod
    def _parse_json(raw: str) -> dict[str, Any]:
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", raw, re.DOTALL)
            if match:
                return json.loads(match.group(0))
            raise

    @staticmethod
    def _to_script(data: dict[str, Any]) -> Script:
        lines = []
        for item in data.get("lines", []):
            text = (item.get("text") or "").strip()
            if not text:
                continue

            raw_scene = (item.get("scene_type") or "video_broll").strip().lower()
            try:
                scene_type = SceneType(raw_scene)
            except ValueError:
                logger.warning(f"scene_type inválido {raw_scene!r} — usando video_broll")
                scene_type = SceneType.VIDEO_BROLL

            emphasis = item.get("emphasis_words") or []
            if not isinstance(emphasis, list):
                emphasis = []

            lines.append(
                ScriptLine(
                    text=text,
                    scene_type=scene_type,
                    visual_query=(item.get("visual_query") or "").strip(),
                    person_name=item.get("person_name"),
                    bg_color=item.get("bg_color"),
                    emphasis_words=[str(w) for w in emphasis if w],
                )
            )

        if not lines:
            raise RuntimeError(
                "Groq retornou roteiro vazio/inválido. "
                "Tente outro tema ou roda de novo."
            )

        hashtags = data.get("hashtags") or []
        if not isinstance(hashtags, list):
            hashtags = []

        return Script(
            title=(data.get("title") or "").strip(),
            hashtags=[str(h) for h in hashtags if h],
            lines=lines,
        )
