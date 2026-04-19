"""
Gera roteiro estruturado usando Groq (Llama 3.3 70B).

O roteiro já vem com instruções de cena: qual imagem, quando
usar fundo colorido, quando mostrar foto de pessoa, quais palavras
destacar na legenda.
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


SYSTEM_PROMPT = """Você é um roteirista especialista em vídeos curtos verticais (TikTok/Reels/Shorts) com alta retenção.

⚠️ REGRAS OBRIGATÓRIAS DE TAMANHO ⚠️

1. DURAÇÃO ALVO: {duration} segundos narrados (NÃO menos!)
2. TAMANHO TOTAL: o roteiro COMPLETO (somando todas as cenas) deve ter entre {min_words} e {max_words} palavras em português. NUNCA menos que {min_words}.
3. NÚMERO DE CENAS: pelo menos {min_scenes} cenas, ideal {ideal_scenes}.
4. POR CENA: cada cena tem 1-2 frases com 8 a 18 palavras cada (NUNCA menos de 8 palavras por cena).
5. HOOK: a primeira cena é impacto forte nos primeiros 3 segundos — pergunta, fato surpreendente, ou afirmação forte.
6. Use português BRASILEIRO natural, direto, emocional.
7. Termine com call-to-action curto ("siga pra mais", "salva esse vídeo", etc.)

⛔ NÃO escreva roteiro curto. Se escrever menos de {min_words} palavras, está ERRADO.
⛔ NÃO use frases soltas de 3-4 palavras. Cada cena precisa de substância.

{template_instructions}

Para cada cena escolha UM scene_type:
- "video_broll": Cenas de ação, movimento, pessoas fazendo coisas. Use quando falar de ações.
- "image_kenburns": Imagens estáticas que ganham movimento por zoom/pan. Use pra conceitos.
- "color_background": Fundo liso colorido com a frase CENTRAL (frase de impacto isolada). Máximo 2x no vídeo.
- "person_photo": Quando citar pessoa específica famosa. Defina person_name com o nome exato.

Pra cada cena defina:
- text: a frase (em português)
- scene_type: um dos 4 acima
- visual_query: termos em INGLÊS pra buscar no banco de vídeos (ex: "person running sunrise", "businessman success")
- person_name: só se scene_type=person_photo
- bg_color: só se scene_type=color_background. Hex. (#000000, #ff4500, #daa520, #b22222)
- emphasis_words: 1-3 palavras MAIS IMPORTANTES da frase pra destacar na legenda

RESPONDA APENAS em JSON válido com este formato exato:
{{
  "title": "título curto",
  "hashtags": ["#motivacao", "#mindset", "..."],
  "lines": [
    {{
      "text": "...",
      "scene_type": "video_broll",
      "visual_query": "...",
      "person_name": null,
      "bg_color": null,
      "emphasis_words": ["palavra1", "palavra2"]
    }}
  ]
}}
"""


class ScriptWriter:
    def __init__(self) -> None:
        if not config.groq_api_key:
            raise ValueError("GROQ_API_KEY não configurado")
        self.client = Groq(api_key=config.groq_api_key)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, min=2, max=10), reraise=True)
    def generate(self, request: VideoRequest) -> Script:
        logger.info(f"Gerando roteiro: tema={request.theme!r} template={request.template}")

        # Calcula metas de tamanho baseadas na duração
        # ~150 palavras por minuto em PT-BR narrado
        duration = request.duration_seconds
        target_words = int(duration * 150 / 60)   # ex: 30s → 75 palavras
        min_words = int(target_words * 0.85)       # -15% tolerância
        max_words = int(target_words * 1.20)       # +20% tolerância
        min_scenes = max(5, duration // 6)         # pelo menos 1 cena a cada 6s
        ideal_scenes = max(6, duration // 4)       # ideal: 1 cena a cada 4s

        system = SYSTEM_PROMPT.format(
            duration=duration,
            min_words=min_words,
            max_words=max_words,
            min_scenes=min_scenes,
            ideal_scenes=ideal_scenes,
            template_instructions=TEMPLATE_INSTRUCTIONS[request.template],
        )

        user = (
            f"Tema do vídeo: {request.theme}\n\n"
            f"REGRA REFORÇADA: escreva entre {min_words} e {max_words} palavras "
            f"no total do roteiro. Isso equivale a aproximadamente {duration} segundos "
            f"quando narrado. Verifique o tamanho antes de responder."
        )
        if request.custom_script:
            user += f"\n\nTexto base do usuário (adapte em cenas):\n{request.custom_script}"

        completion = self.client.chat.completions.create(
            model=config.groq_model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0.85,
            max_tokens=3000,
            response_format={"type": "json_object"},
        )

        raw = completion.choices[0].message.content or "{}"
        logger.info(f"Resposta do Groq: {len(raw)} chars")

        data = self._parse_json(raw)
        script = self._to_script(data)
        word_count = len(script.full_text.split())
        logger.info(
            f"Roteiro: {len(script.lines)} cenas | {word_count} palavras | "
            f"alvo {min_words}-{max_words}"
        )

        # Se ficou MUITO curto, força retry via tenacity
        if word_count < min_words * 0.7:  # 30% abaixo do mínimo
            logger.warning(
                f"Roteiro muito curto ({word_count} < {min_words*0.7:.0f}). "
                f"Forçando retry pra gerar mais conteúdo."
            )
            raise RuntimeError(f"Roteiro muito curto: {word_count} palavras (mínimo {min_words})")

        return script

    @staticmethod
    def _parse_json(raw: str) -> dict[str, Any]:
        # Groq já responde JSON puro por causa do response_format
        # Mas reforço a extração pra garantir
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
            # Hardening: tolera respostas mal formadas da IA
            text = (item.get("text") or "").strip()
            if not text:
                continue  # pula linhas sem texto

            # scene_type: se inválido/ausente, usa VIDEO_BROLL como fallback
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
