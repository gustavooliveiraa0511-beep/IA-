"""
Gera roteiro estruturado usando IA — cascata Gemini → Groq.

Estratégia:
1. Gemini 2.0 Flash (Google) — primário, melhor PT-BR e mais disciplinado
2. Groq Llama 3.3 70B — fallback se Gemini falhar/rate limit

Ambos têm:
- Free tier generoso
- Suporte a structured output (JSON)
- Boa qualidade em português brasileiro

Se o roteiro vier curto demais, a gente faz 2ª chamada pra expandir.
Nunca falha o pipeline por tamanho — só loga warning.
"""
from __future__ import annotations

import json
import re
from typing import Any, Optional

import google.generativeai as genai
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


SYSTEM_PROMPT = """Você é um roteirista ESPECIALISTA em vídeos virais de {duration} segundos para TikTok/Reels em português brasileiro. Seu estilo é PROFUNDO, não superficial.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🎯 META: ROTEIRO COM {target_words} PALAVRAS, PROFUNDIDADE REAL.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

⛔ PROIBIDO:
- Frases genéricas ("acredite em si", "vá em frente", "você consegue")
- Repetir a MESMA ideia com palavras diferentes
- Clichês vazios sem substância
- Listas soltas de afirmações desconectadas

✅ OBRIGATÓRIO:
- Cada cena deve ADICIONAR algo novo ao argumento
- Use EXEMPLOS CONCRETOS: "como atleta correndo maratona", "como o Neymar aos 14 anos"
- Use ANALOGIAS específicas: "a dor de crescer é a mesma dor do músculo que se rompe pra ficar mais forte"
- Alterne frases CURTAS de impacto com frases LONGAS de desenvolvimento
- Use VÍRGULAS, pontos finais e reticências pra criar RITMO (importante: narração vai seguir a pontuação)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📝 EXEMPLO DE ROTEIRO EXCELENTE (tema: "nunca desista"):
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Cena 1: "Se você tá pensando em desistir agora, precisa ouvir isso antes de fazer qualquer coisa."
Cena 2: "Nenhum grande nome que você admira chegou onde está por talento. Chegou por teimosia."
Cena 3: "O Edison errou dois mil filamentos antes de achar o certo. Cada erro foi um passo."
Cena 4: "A Joanne Rowling foi recusada por doze editoras. Hoje Harry Potter salvou a vida dela."
Cena 5: "O que separa quem venceu de quem desistiu não é capacidade. É um dia a mais de esforço."
Cena 6: "Quando você quer parar, entenda: é exatamente ali que o resultado começa a aparecer."
Cena 7: "O universo cobra antes de entregar. Paga o preço. Aguenta o pior. E vai até o fim."
Cena 8: "Salva esse vídeo. Assiste de novo quando a vontade de desistir voltar."

TOTAL: 112 palavras. Observações:
✅ Cada cena traz uma ideia NOVA (talento vs teimosia, exemplos reais, explicação do mecanismo)
✅ Usa casos concretos (Edison, Rowling) pra não virar clichê
✅ Alterna ritmo: frases curtas e longas, muito uso de vírgulas e pontos
✅ Constrói argumento em camadas: contexto → exemplos → insight → call to action

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⛔ EXEMPLO DE ROTEIRO RUIM (NUNCA FAÇA):
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Cena 1: "Acredite em si mesmo"
Cena 2: "Você é capaz de tudo"
Cena 3: "Nunca desista dos sonhos"
Cena 4: "Continue tentando sempre"
Cena 5: "Você vai conseguir"

❌ Por quê é ruim:
- Todas as frases dizem a MESMA COISA
- Nenhum exemplo concreto
- Nenhuma analogia
- Sem pontuação natural → narração sai corrida
- Sem progressão de argumento

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📐 REGRAS TÉCNICAS:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

✅ TOTAL: {target_words} palavras (±15%)
✅ CENAS: {min_scenes} a {max_scenes} cenas
✅ POR CENA: 10 a 20 palavras com pontuação natural (pontos, vírgulas, reticências)
✅ Use frases completas que terminam com ponto final. Isso cria PAUSA NATURAL na narração.
✅ SEMPRE use dados/exemplos concretos quando possível (nomes, números, situações específicas)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

{template_instructions}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🎬 TIPOS DE CENA:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

- "video_broll": Ações e movimento (corrida, trabalho, esporte)
- "image_kenburns": Conceitos abstratos com zoom (céu, montanha, livro)
- "color_background": Fundo liso com FRASE CENTRAL. Máx 2 vezes.
- "person_photo": Só se citar pessoa famosa específica (Edison, Rowling, Jobs)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📦 FORMATO JSON DA RESPOSTA:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

{{
  "title": "título curto do vídeo",
  "hashtags": ["#motivacao", "#mindset", "#disciplina"],
  "lines": [
    {{
      "text": "Frase completa, com pontuação natural, trazendo ideia nova.",
      "scene_type": "video_broll",
      "visual_query": "termos EM INGLÊS (ex: person running sunrise)",
      "person_name": null,
      "bg_color": null,
      "emphasis_words": ["palavra_chave1", "palavra_chave2"]
    }}
  ]
}}

⚠️ ANTES DE RESPONDER, faça 3 checks:
1. Contar palavras: total é >= {min_words}?
2. Cada cena traz ideia NOVA ou só repete?
3. Tem pontuação pra criar pausas naturais na narração?

Se algum check falhar, REESCREVA.
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
        # Inicializa clientes disponíveis
        self.gemini_available = bool(config.gemini_api_key)
        self.groq_available = bool(config.groq_api_key)

        if self.gemini_available:
            genai.configure(api_key=config.gemini_api_key)
            self.gemini_client = genai.GenerativeModel(config.gemini_model)

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

    def generate(self, request: VideoRequest) -> Script:
        """
        Gera roteiro com cascata Gemini → Groq, expansão se curto,
        e nunca trava o pipeline.
        """
        logger.info(f"Gerando roteiro: tema={request.theme!r} template={request.template}")

        duration = request.duration_seconds
        target_words = int(duration * 150 / 60)
        min_words = int(target_words * 0.80)
        max_words = int(target_words * 1.30)
        min_scenes = max(5, duration // 6)
        max_scenes = max(8, duration // 3)

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
            f"Escreva um roteiro de {target_words} palavras "
            f"(distribuídas em {min_scenes}-{max_scenes} cenas de 8-18 palavras cada). "
            f"Siga o exemplo do sistema.\n\n"
            f"Conte as palavras antes de responder."
        )
        if request.custom_script:
            user += f"\n\nTexto base do usuário (adapte em cenas):\n{request.custom_script}"

        # === TENTATIVA 1: geração (Gemini primário, Groq fallback) ===
        script = self._generate_with_fallback(system, user)
        word_count = len(script.full_text.split())
        logger.info(
            f"Tentativa 1: {len(script.lines)} cenas, {word_count} palavras "
            f"(meta {target_words})"
        )

        # === TENTATIVA 2: expansão se curto ===
        if word_count < min_words:
            logger.warning(
                f"Roteiro curto ({word_count} < {min_words}). "
                f"Pedindo pra IA EXPANDIR..."
            )
            try:
                current_script = "\n".join(f"- {line.text}" for line in script.lines)
                expand_user = EXPAND_PROMPT.format(
                    current_words=word_count,
                    target_words=target_words,
                    current_script=current_script,
                )
                expand_system = (
                    "Você é um roteirista expandindo um roteiro curto. "
                    "Retorne JSON válido."
                )
                expanded = self._generate_with_fallback(expand_system, expand_user)
                expanded_words = len(expanded.full_text.split())
                logger.info(f"Após expansão: {expanded_words} palavras")
                if expanded_words > word_count:
                    script = expanded
                    word_count = expanded_words
            except Exception as e:
                logger.warning(f"Expansão falhou: {e}. Usando o roteiro original.")

        # === FALLBACK FINAL: não crasha, só alerta ===
        if word_count < min_words * 0.5:
            logger.warning(
                f"⚠️ Roteiro final com {word_count} palavras (alvo {target_words}). "
                f"Vídeo vai ficar mais curto que o ideal mas será gerado."
            )

        return script

    # ============================================
    # Cascata Gemini → Groq
    # ============================================
    def _generate_with_fallback(self, system: str, user: str) -> Script:
        """
        Tenta Gemini primeiro. Se falhar, cai pro Groq.
        """
        # Nível 1: Gemini
        if self.gemini_available:
            try:
                return self._call_gemini(system, user)
            except Exception as e:
                logger.warning(f"⚠️ Gemini falhou: {type(e).__name__}: {e}")
                if self.groq_available:
                    logger.warning("Tentando Groq como fallback...")
                else:
                    raise

        # Nível 2: Groq
        if self.groq_available:
            return self._call_groq(system, user)

        raise RuntimeError("Nenhuma IA disponível pra gerar roteiro")

    # ============================================
    # Gemini (primário)
    # ============================================
    @retry(stop=stop_after_attempt(2), wait=wait_exponential(multiplier=2, min=2, max=6), reraise=True)
    def _call_gemini(self, system: str, user: str) -> Script:
        logger.info("Chamando Gemini...")
        # Gemini aceita system_instruction + single user message
        model = genai.GenerativeModel(
            model_name=config.gemini_model,
            system_instruction=system,
            generation_config=genai.types.GenerationConfig(
                temperature=0.85,
                max_output_tokens=4000,
                response_mime_type="application/json",
            ),
        )
        response = model.generate_content(user)
        raw = response.text or "{}"
        logger.info(f"Gemini respondeu: {len(raw)} chars")

        data = self._parse_json(raw)
        return self._to_script(data)

    # ============================================
    # Groq (fallback)
    # ============================================
    @retry(stop=stop_after_attempt(2), wait=wait_exponential(multiplier=2, min=2, max=6), reraise=True)
    def _call_groq(self, system: str, user: str) -> Script:
        logger.info("Chamando Groq...")
        completion = self.groq_client.chat.completions.create(
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
        logger.info(f"Groq respondeu: {len(raw)} chars")

        data = self._parse_json(raw)
        return self._to_script(data)

    # ============================================
    # Parsing compartilhado
    # ============================================
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
                "IA retornou roteiro vazio/inválido. "
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
