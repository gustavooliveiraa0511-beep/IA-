/**
 * Script Generator Module
 * Gera roteiros virais dark usando Groq (gratuito) ou templates locais (zero config)
 *
 * APIs:
 *  - Groq (console.groq.com) — grátis, sem cartão, 14.400 req/dia
 *  - Fallback: templates locais — funciona sem nenhuma conta
 */

class ScriptGenerator {
  constructor(options = {}) {
    this.apiKey = options.apiKey || process.env.GROQ_API_KEY || '';
    this.model = options.model || 'llama-3.3-70b-versatile';
    this.client = null;

    if (this.apiKey) {
      try {
        const Groq = require('groq-sdk');
        this.client = new Groq({ apiKey: this.apiKey });
        console.log('🤖 Groq AI ativado (llama-3.3-70b-versatile)');
      } catch {
        console.warn('⚠️  groq-sdk não encontrado. Usando templates locais.');
      }
    } else {
      console.log('📋 Modo templates locais (sem API key — 100% offline)');
    }
  }

  // ─────────────────────────────────────────────
  //  SISTEMA DE PROMPT PARA GERAÇÃO VIRAL
  // ─────────────────────────────────────────────
  buildSystemPrompt() {
    return `Você é um especialista em criação de conteúdo viral para TikTok, Instagram Reels e YouTube Shorts.
Seu estilo é DARK, MISTERIOSO e IMPACTANTE — como os vídeos mais virais de true crime, psicologia sombria e conspirações.

REGRAS OBRIGATÓRIAS:
1. Hook nos primeiros 3 segundos — deve ser chocante, provocativo ou perturbador
2. Frases CURTAS e IMPACTANTES — máximo 10 palavras por frase
3. Pausas dramáticas marcadas com [PAUSA]
4. Palavras de ênfase em MAIÚSCULAS
5. Ritmo crescente: começa lento, acelera, termina com revelação
6. Provoque curiosidade — nunca entregue tudo de uma vez
7. Sempre em português do Brasil, linguagem informal e direta

ESTRUTURA DO VÍDEO:
- gancho (3s): frase inicial chocante que prende
- suspense (5-8s): contexto misterioso, levantar dúvidas
- revelação (5-8s): a informação central, surpreendente
- choque (3-5s): detalhe perturbador/impactante
- reflexão (3-5s): insight final, deixa o espectador pensando

FORMATO DE SAÍDA — retorne APENAS um JSON válido:
{
  "title": "título do vídeo",
  "hook": "frase de gancho (máx 10 palavras)",
  "totalDuration": número em segundos (15-60),
  "backgroundMusicMood": "dark|suspense|emotional|dramatic",
  "segments": [
    {
      "id": número,
      "text": "texto narrado neste segmento",
      "emotion": "gancho|suspense|revelação|choque|reflexão",
      "duration": número em segundos,
      "visualKeyword": "palavra-chave em inglês para busca de imagem",
      "emphasis": ["palavra1", "palavra2"],
      "pause": true|false
    }
  ],
  "hashtags": ["#hashtag1", "#hashtag2"],
  "retentionTechniques": ["técnica1", "técnica2"]
}`;
  }

  // ─────────────────────────────────────────────
  //  GERAÇÃO VIA GROQ API (GRÁTIS)
  // ─────────────────────────────────────────────
  async generateWithGroq(topic, style, duration, language) {
    const userPrompt = `Crie um roteiro viral dark para TikTok sobre: "${topic}"

Configurações:
- Estilo: ${style} (aggressive=mais intenso/provocativo, emotional=mais íntimo/pessoal, informative=mais factual/perturbador)
- Duração desejada: ${duration} segundos
- Linguagem dos textos: ${language === 'en' ? 'inglês' : 'português do Brasil'}

Lembre-se: o objetivo é MÁXIMA retenção. Cada segundo deve fazer o espectador querer ver o próximo.
Retorne apenas o JSON, sem texto adicional.`;

    const response = await this.client.chat.completions.create({
      model: this.model,
      messages: [
        { role: 'system', content: this.buildSystemPrompt() },
        { role: 'user', content: userPrompt },
      ],
      max_tokens: 4096,
      temperature: 0.85,
      response_format: { type: 'json_object' },
    });

    const content = response.choices[0]?.message?.content || '';
    return JSON.parse(content);
  }

  // ─────────────────────────────────────────────
  //  TEMPLATES LOCAIS (ZERO CONFIG, 100% OFFLINE)
  // ─────────────────────────────────────────────
  generateFromTemplate(topic, style, duration) {
    const templates = {
      mente: {
        title: 'Segredos Sombrios da Mente Humana',
        hook: 'Seu cérebro te MENTE 200 vezes por dia',
        backgroundMusicMood: 'suspense',
        segments: [
          { emotion: 'gancho', text: 'Seu cérebro te MENTE [PAUSA] duzentas vezes por dia.', visualKeyword: 'dark brain mind shadow', duration: 3 },
          { emotion: 'suspense', text: 'E o pior... [PAUSA] você nunca percebe quando isso acontece.', visualKeyword: 'mysterious fog darkness', duration: 5 },
          { emotion: 'revelação', text: 'Cientistas descobriram que nossa mente cria MEMÓRIAS FALSAS [PAUSA] para nos proteger de certas verdades.', visualKeyword: 'brain neurons dark', duration: 8 },
          { emotion: 'choque', text: 'Aquela memória que você tem mais certeza [PAUSA] pode ser completamente INVENTADA.', visualKeyword: 'shattered mirror reflection', duration: 6 },
          { emotion: 'reflexão', text: 'Então... [PAUSA] quem realmente é você [PAUSA] se suas memórias não são confiáveis?', visualKeyword: 'silhouette darkness question', duration: 6 },
        ],
        hashtags: ['#psicologia', '#mentehumana', '#fatos', '#dark', '#tiktok'],
        retentionTechniques: ['open loop', 'pattern interrupt', 'shock value'],
      },
      sono: {
        title: 'O que Acontece com Você Dormindo',
        hook: 'À noite seu corpo faz coisas que você NÃO controla',
        backgroundMusicMood: 'dark',
        segments: [
          { emotion: 'gancho', text: 'À noite [PAUSA] seu corpo faz coisas que você NÃO controla.', visualKeyword: 'dark bedroom night shadow', duration: 3 },
          { emotion: 'suspense', text: 'Enquanto você dorme... [PAUSA] algo se move dentro de você.', visualKeyword: 'sleeping dark night', duration: 5 },
          { emotion: 'revelação', text: 'Durante o sono profundo [PAUSA] seu cérebro DELETA memórias [PAUSA] e literalmente se come a si mesmo.', visualKeyword: 'brain dark neural', duration: 8 },
          { emotion: 'choque', text: 'É chamado de AUTOFAGIA. [PAUSA] Seu próprio corpo consumindo partes de si mesmo.', visualKeyword: 'dark horror biology', duration: 6 },
          { emotion: 'reflexão', text: 'Então toda vez que você acorda [PAUSA] você nunca é exatamente a MESMA pessoa.', visualKeyword: 'morning mirror dark', duration: 6 },
        ],
        hashtags: ['#sono', '#ciencia', '#fatos', '#dark', '#perturbador'],
        retentionTechniques: ['curiosity gap', 'fear trigger', 'identity challenge'],
      },
      realidade: {
        title: 'Sua Realidade É Uma Ilusão',
        hook: 'Nada do que você vê é REAL',
        backgroundMusicMood: 'dramatic',
        segments: [
          { emotion: 'gancho', text: 'Nada do que você vê [PAUSA] é real.', visualKeyword: 'matrix reality illusion dark', duration: 3 },
          { emotion: 'suspense', text: 'Tudo que você enxerga [PAUSA] é uma RECONSTRUÇÃO do seu cérebro.', visualKeyword: 'perception brain dark', duration: 5 },
          { emotion: 'revelação', text: 'Seus olhos capturam apenas 10% da realidade. [PAUSA] O resto... [PAUSA] seu cérebro INVENTA.', visualKeyword: 'eye vision dark illusion', duration: 8 },
          { emotion: 'choque', text: 'Isso significa que duas pessoas nunca vivem na MESMA realidade.', visualKeyword: 'parallel worlds dark', duration: 5 },
          { emotion: 'reflexão', text: 'Se a realidade é individual [PAUSA] então o que é VERDADE?', visualKeyword: 'philosophy dark contemplation', duration: 5 },
        ],
        hashtags: ['#realidade', '#filosofia', '#ciencia', '#dark', '#mente'],
        retentionTechniques: ['reality questioning', 'existential dread', 'knowledge gap'],
      },
      morte: {
        title: 'O Que Acontece Quando Você Morre',
        hook: 'Nos primeiros 7 minutos após a morte...',
        backgroundMusicMood: 'dark',
        segments: [
          { emotion: 'gancho', text: 'Nos primeiros 7 minutos após a morte [PAUSA] algo PERTURBADOR acontece.', visualKeyword: 'dark death shadow mysterious', duration: 4 },
          { emotion: 'suspense', text: 'Seu cérebro [PAUSA] ainda está VIVO.', visualKeyword: 'brain dark neural activity', duration: 4 },
          { emotion: 'revelação', text: 'Estudos mostram que nesse período [PAUSA] o cérebro reproduz toda a sua vida [PAUSA] em segundos.', visualKeyword: 'memories flash dark light', duration: 8 },
          { emotion: 'choque', text: 'É por isso que pessoas que voltaram da morte [PAUSA] relatam ver SUAS PRÓPRIAS MEMÓRIAS.', visualKeyword: 'near death experience dark', duration: 6 },
          { emotion: 'reflexão', text: 'Talvez a morte [PAUSA] não seja o fim. [PAUSA] Talvez seja apenas... o começo da revisão.', visualKeyword: 'dark light tunnel mystery', duration: 6 },
        ],
        hashtags: ['#morte', '#ciencia', '#neardeath', '#dark', '#perturbador'],
        retentionTechniques: ['taboo topic', 'scientific mystery', 'hope vs fear'],
      },
      poder: {
        title: 'Como o Poder Corrompe a Mente',
        hook: 'O poder muda fisicamente o seu cérebro',
        backgroundMusicMood: 'dramatic',
        segments: [
          { emotion: 'gancho', text: 'O poder [PAUSA] muda FISICAMENTE o seu cérebro.', visualKeyword: 'power corruption dark shadow', duration: 3 },
          { emotion: 'suspense', text: 'Não é metáfora. [PAUSA] É neurociência COMPROVADA.', visualKeyword: 'brain power dark control', duration: 4 },
          { emotion: 'revelação', text: 'Quando uma pessoa ganha poder [PAUSA] ela literalmente perde a capacidade de sentir EMPATIA.', visualKeyword: 'manipulation dark psychology', duration: 8 },
          { emotion: 'choque', text: 'Os neurônios responsáveis por entender os outros [PAUSA] começam a DESLIGAR.', visualKeyword: 'brain neurons shutdown dark', duration: 6 },
          { emotion: 'reflexão', text: 'Então toda pessoa poderosa que parece não se importar [PAUSA] literalmente não consegue mais.', visualKeyword: 'power lonely dark', duration: 6 },
        ],
        hashtags: ['#poder', '#psicologia', '#neurociencia', '#dark', '#verdade'],
        retentionTechniques: ['authority paradox', 'empathy reversal', 'social commentary'],
      },
    };

    // Tentar fazer match com o tópico fornecido
    const topicLower = (topic || '').toLowerCase();
    let selectedTemplate = null;

    if (topicLower.includes('mente') || topicLower.includes('cérebro') || topicLower.includes('psicolog')) {
      selectedTemplate = templates.mente;
    } else if (topicLower.includes('sono') || topicLower.includes('dorm')) {
      selectedTemplate = templates.sono;
    } else if (topicLower.includes('realidade') || topicLower.includes('ilusão') || topicLower.includes('exist')) {
      selectedTemplate = templates.realidade;
    } else if (topicLower.includes('morte') || topicLower.includes('morrer') || topicLower.includes('death')) {
      selectedTemplate = templates.morte;
    } else if (topicLower.includes('poder') || topicLower.includes('corrupç') || topicLower.includes('polit')) {
      selectedTemplate = templates.poder;
    } else {
      // Usar template aleatório se não houver match
      const keys = Object.keys(templates);
      selectedTemplate = templates[keys[Math.floor(Math.random() * keys.length)]];
    }

    // Ajustar duração dos segmentos conforme solicitado
    const targetDuration = duration || 30;
    const totalTemplateDuration = selectedTemplate.segments.reduce((s, seg) => s + seg.duration, 0);
    const ratio = targetDuration / totalTemplateDuration;

    const segments = selectedTemplate.segments.map((seg, i) => ({
      id: i + 1,
      text: seg.text,
      emotion: seg.emotion,
      duration: Math.max(2, Math.round(seg.duration * ratio)),
      visualKeyword: seg.visualKeyword,
      emphasis: seg.text
        .split(' ')
        .filter(w => w === w.toUpperCase() && w.length > 2)
        .map(w => w.replace(/[^a-zA-ZÀ-ú]/g, '').toLowerCase()),
      pause: seg.text.includes('[PAUSA]'),
    }));

    return {
      title: selectedTemplate.title,
      hook: selectedTemplate.hook,
      totalDuration: segments.reduce((s, seg) => s + seg.duration, 0),
      backgroundMusicMood: selectedTemplate.backgroundMusicMood,
      segments,
      hashtags: selectedTemplate.hashtags,
      retentionTechniques: selectedTemplate.retentionTechniques,
      generatedBy: 'template',
    };
  }

  // ─────────────────────────────────────────────
  //  MÉTODO PRINCIPAL
  // ─────────────────────────────────────────────
  async generateScript(options = {}) {
    const {
      topic = 'segredos da mente humana',
      style = 'aggressive',
      duration = 30,
      language = 'pt',
    } = options;

    console.log(`\n📝 Gerando roteiro: "${topic}"`);
    console.log(`   🎨 Estilo: ${style} | ⏱️  Duração: ${duration}s`);

    // Modo Groq (com API key)
    if (this.client) {
      try {
        console.log('   🤖 Usando Groq AI (llama-3.3-70b-versatile)...');
        const script = await this.generateWithGroq(topic, style, duration, language);
        script.generatedBy = 'groq';
        this.validateScript(script);
        console.log(`   ✅ Roteiro gerado: "${script.title}" (${script.totalDuration}s, ${script.segments.length} segmentos)`);
        return script;
      } catch (err) {
        console.warn(`   ⚠️  Groq falhou: ${err.message}`);
        console.log('   🔄 Usando template local...');
      }
    }

    // Fallback: templates locais
    const script = this.generateFromTemplate(topic, style, duration);
    console.log(`   ✅ Roteiro por template: "${script.title}" (${script.totalDuration}s, ${script.segments.length} segmentos)`);
    return script;
  }

  // ─────────────────────────────────────────────
  //  VALIDAÇÃO
  // ─────────────────────────────────────────────
  validateScript(script) {
    if (!script.segments || !Array.isArray(script.segments) || script.segments.length === 0) {
      throw new Error('Script inválido: sem segmentos');
    }
    // Garantir campos obrigatórios em cada segmento
    script.segments = script.segments.map((seg, i) => ({
      id: seg.id || i + 1,
      text: seg.text || '',
      emotion: seg.emotion || 'suspense',
      duration: seg.duration || 5,
      visualKeyword: seg.visualKeyword || 'dark mysterious shadow',
      emphasis: seg.emphasis || [],
      pause: !!seg.pause,
    }));

    if (!script.totalDuration) {
      script.totalDuration = script.segments.reduce((s, seg) => s + seg.duration, 0);
    }
    if (!script.backgroundMusicMood) script.backgroundMusicMood = 'dark';
    if (!script.hashtags) script.hashtags = ['#dark', '#viral', '#tiktok'];
    if (!script.hook) script.hook = script.segments[0]?.text?.slice(0, 60) || '';
  }

  // ─────────────────────────────────────────────
  //  TÓPICOS VIRAIS PRÉ-DEFINIDOS
  // ─────────────────────────────────────────────
  getViralTopics() {
    return [
      'segredos da mente humana',
      'o que acontece quando você dorme',
      'sua realidade é uma ilusão',
      'o que acontece quando você morre',
      'como o poder corrompe a mente',
      'segredos que o governo esconde',
      'o lado sombrio das redes sociais',
      'como as empresas controlam você',
      'o que os sonhos realmente significam',
      'psicologia dos serial killers',
    ];
  }
}

module.exports = ScriptGenerator;
