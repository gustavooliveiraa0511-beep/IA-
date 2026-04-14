# 🎬 Dark Viral Video Generator

> Gerador automático de vídeos virais estilo **"dark TikTok"** com máximo foco em retenção e engajamento — **powered by Claude AI**

---

## 🌑 O que é isso?

Um sistema completo que gera automaticamente vídeos verticais (9:16) prontos para TikTok, Reels e Shorts, com:

- 🧠 **Roteiro viral** gerado por Claude AI (Anthropic) com técnicas avançadas de retenção
- 🎙️ **Narração** com voz dark/misteriosa via TTS
- 🎬 **Imagens e vídeos** buscados automaticamente em Pexels/Pixabay
- ✏️ **Legendas animadas** estilo TikTok (word-by-word, destaque em palavras-chave)
- 🎞️ **Efeito Ken Burns** com pan e zoom cinematic
- 🎵 **Música de fundo** atmosférica e sombria
- 🌑 **Filtros dark** cinematográficos (vignette, color grading)

---

## 🚀 Instalação

### Pré-requisitos

- **Node.js** 18+ — [nodejs.org](https://nodejs.org)
- **FFmpeg** — [ffmpeg.org/download.html](https://ffmpeg.org/download.html)
- **API Key Anthropic** — [console.anthropic.com](https://console.anthropic.com)

### Instalar

```bash
# Clone o repositório
git clone <url> dark-viral-video
cd dark-viral-video

# Instalar dependências
npm install

# Configurar API Keys
cp .env.example .env
# Edite .env e adicione sua ANTHROPIC_API_KEY
```

### Instalar FFmpeg

```bash
# Ubuntu/Debian
sudo apt install ffmpeg

# macOS
brew install ffmpeg

# Windows - baixe de ffmpeg.org
```

---

## ⚙️ Configuração

Edite o arquivo `.env`:

```env
# OBRIGATÓRIO
ANTHROPIC_API_KEY=sk-ant-api03-...

# OPCIONAL (melhora muito a qualidade visual)
PEXELS_API_KEY=...        # pexels.com/api (gratuito)
PIXABAY_API_KEY=...       # pixabay.com/api (gratuito)
```

---

## 🎯 Como Usar

### Modo Interativo (recomendado para iniciantes)

```bash
node index.js
```

Você será guiado por perguntas sobre:
- Tema do vídeo
- Estilo (dark, emotional, informative)
- Duração (15-60s)
- Tipo de voz

### Linha de Comando

```bash
# Geração automática (tema gerado pelo Claude)
node index.js

# Com tema específico
node index.js --topic "segredos da mente humana" --duration 30

# Estilo emocional com voz feminina
node index.js --style emotional --voice dark-female --duration 45

# Apenas gerar roteiro (sem criar o vídeo)
node index.js --dry-run --topic "experimentos proibidos"

# Ver todas as opções
node index.js --help
```

### Opções Disponíveis

| Opção | Valores | Padrão | Descrição |
|-------|---------|--------|-----------|
| `--topic` | qualquer texto | automático | Tema do vídeo |
| `--style` | dark, emotional, informative | dark | Estilo narrativo |
| `--duration` | 15, 30, 45, 60 | 30 | Duração em segundos |
| `--voice` | dark-male, dark-female, narrator | dark-male | Perfil de voz |
| `--visual` | cinematic, moody, horror, subtle | cinematic | Estilo visual/filtros |
| `--dry-run` | — | false | Só gera o roteiro |

---

## 🎭 Estilos

### Estilos de Roteiro

| Estilo | Descrição | Melhor Para |
|--------|-----------|-------------|
| `dark` | Misterioso, sombrio, com arrepios | Conspirações, mistérios, psicologia sombria |
| `emotional` | Emocionante, reflexivo, tocante | Histórias de superação, revelações impactantes |
| `informative` | Revelador, factual mas surpreendente | Curiosidades científicas, fatos chocantes |

### Perfis de Voz

| Perfil | Voz | Efeito |
|--------|-----|--------|
| `dark-male` | pt-BR-AntonioNeural | Grave, pausado, misterioso |
| `dark-female` | pt-BR-FranciscaNeural | Sussurrante, intenso |
| `narrator` | pt-BR-AntonioNeural | Dramático, jornalístico |
| `en-dark` | en-US-GuyNeural | Narração em inglês |

### Estilos Visuais

| Estilo | Efeito de Cor | Melhor Para |
|--------|---------------|-------------|
| `cinematic` | Dark + vinheta suave | Uso geral |
| `moody` | Azulado + escuro | Temas psicológicos |
| `horror` | Vermelho sutil + contraste | Terror, suspense extremo |
| `subtle` | Levemente escurecido | Conteúdo informativo |

---

## 🏗️ Arquitetura

```
dark-viral-video/
├── index.js                    # Orquestrador principal + CLI
├── modules/
│   ├── scriptGenerator.js      # 🧠 Geração de roteiro com Claude AI
│   ├── voiceGenerator.js       # 🎙️ TTS + efeitos de voz dark
│   ├── mediaFetcher.js         # 🔍 Busca Pexels/Pixabay + download
│   ├── subtitleRenderer.js     # ✏️ Legendas TikTok-style + animações
│   ├── videoComposer.js        # 🎬 Composição, Ken Burns, filtros
│   └── musicLibrary.js         # 🎵 Música dark atmosférica
├── assets/
│   └── music/                  # Cache de músicas geradas
├── output/                     # Vídeos finais gerados
├── temp/                       # Arquivos temporários
│   └── media/                  # Imagens/vídeos baixados
├── .env.example                # Template de configuração
└── README.md
```

### Pipeline de Geração

```
┌─────────────────────────────────────────────────────────┐
│                    PIPELINE COMPLETO                     │
│                                                          │
│  1. TEMA ──► Claude AI ──► ROTEIRO VIRAL                │
│     (gancho + segmentos + técnicas de retenção)          │
│                                                          │
│  2. ROTEIRO ──► Edge-TTS ──► NARRAÇÃO DARK              │
│     (voz grave, pausas dramáticas, efeitos)              │
│                                                          │
│  3. KEYWORDS ──► Pexels/Pixabay ──► MÍDIA              │
│     (imagens/vídeos HD para cada segmento)               │
│                                                          │
│  4. MÍDIA ──► FFmpeg + Canvas ──► SLIDES                │
│     (Ken Burns, pan, zoom, transições)                   │
│                                                          │
│  5. SLIDES + NARRAÇÃO + MÚSICA ──► VÍDEO FINAL          │
│     (1080x1920 | 9:16 | 30fps | filtros dark)           │
│                                                          │
│  6. CANVAS ──► ASS/SRT ──► LEGENDAS ANIMADAS           │
│     (word-by-word, highlights, animações pop/zoom)       │
│                                                          │
│  OUTPUT: Vídeo pronto para TikTok/Reels/Shorts 🚀       │
└─────────────────────────────────────────────────────────┘
```

---

## 🧠 Inteligência do Roteiro (Claude AI)

O sistema usa **Claude Opus 4.6** com **adaptive thinking** para gerar roteiros otimizados:

### Técnicas de Retenção Implementadas

1. **Gancho dos 3 Segundos**: Primeira frase para no scroll imediatamente
2. **Micro-suspenses**: A cada 5-7 segundos uma nova revelação
3. **Curiosity Gap**: Sempre deixa uma pergunta aberta
4. **Padrão de Revelação**: "O que não te contaram é..."
5. **Frases Curtas**: Máximo 8 palavras para alto impacto
6. **Números Específicos**: "97% das pessoas..." gera credibilidade
7. **Pausas Estratégicas**: `[PAUSA]` para tensão dramática
8. **Final Perturbador**: Reflexão que fica na memória

### Estrutura do Roteiro Gerado

```json
{
  "hook": "Isso vai mudar como você vê tudo...",
  "title": "O Segredo da Mente Humana Revelado",
  "segments": [
    {
      "text": "Existe um lugar no seu cérebro...",
      "duration": 4,
      "emotion": "suspense",
      "visualKeyword": "brain neurons dark",
      "emphasis": ["cérebro", "segredo"]
    }
  ],
  "retentionTechniques": ["curiosity gap", "micro-revelações"],
  "callToAction": "Comenta o que você achou mais perturbador"
}
```

---

## 📊 Legendas Estilo TikTok

- **Word-by-word**: Palavras aparecem uma a uma
- **Highlight automático**: Palavras-chave em dourado (`#FFD700`)
- **Animações**: pop, zoom, fade, shake
- **Fundo semi-transparente**: Para máxima legibilidade
- **Posição central-inferior**: Padrão TikTok
- **Sincronizado**: Com o áudio da narração

---

## 🎵 Sistema de Música

A música é gerada algoritmicamente ou baixada de fontes gratuitas:

- **Dark Ambient**: Drones de baixa frequência (A1, A2, E3)
- **Suspense**: Frequências dissonantes com tremolo
- **Emotional**: Progressões suaves em G3-D4
- **Dramatic**: Bass profundo em C2-G3
- **Volume**: 15% do volume da narração por padrão

---

## 🔧 APIs Integradas

| API | Uso | Grátis? | Link |
|-----|-----|---------|------|
| Anthropic Claude | Geração de roteiro | Não | [console.anthropic.com](https://console.anthropic.com) |
| Edge-TTS (Microsoft) | Síntese de voz | Sim | Embutido |
| Pexels | Imagens e vídeos HD | Sim | [pexels.com/api](https://www.pexels.com/api/) |
| Pixabay | Imagens alternativas | Sim | [pixabay.com/api](https://pixabay.com/api/docs/) |
| FFmpeg | Processamento de vídeo | Sim | [ffmpeg.org](https://ffmpeg.org) |

---

## ⚡ Exemplos de Uso Avançado

```javascript
// Uso programático
const { generateVideo } = require('./index');

await generateVideo({
  topic: 'o que acontece quando você morre',
  style: 'dark',
  duration: 45,
  voiceProfile: 'dark-female',
  visualStyle: 'horror',
  preferVideo: true,  // prefere vídeos a imagens
  language: 'pt-BR',
});
```

---

## 🐛 Troubleshooting

### "FFmpeg not found"
```bash
# Ubuntu
sudo apt-get install ffmpeg

# macOS
brew install ffmpeg
```

### "canvas module error"
```bash
# Ubuntu - instalar dependências do canvas
sudo apt-get install -y build-essential libcairo2-dev libpango1.0-dev libjpeg-dev libgif-dev librsvg2-dev
npm install canvas
```

### "ANTHROPIC_API_KEY não encontrada"
Crie o arquivo `.env` na raiz do projeto:
```env
ANTHROPIC_API_KEY=sk-ant-api03-SUA_CHAVE
```

### Vídeo sem áudio
- Verifique se `edge-tts` está instalado (Python necessário)
- O app funciona sem áudio, gerando apenas o vídeo visual

---

## 📈 Métricas de Retenção

Os vídeos gerados são otimizados para:

- **Hook rate**: Taxa de parada no scroll
- **Watch time**: % de visualização completa  
- **Share rate**: Taxa de compartilhamento
- **Comment trigger**: Perguntas que geram comentários

---

## 📄 Licença

MIT License — use, modifique e distribua livremente.

---

*Criado com ❤️ e Claude AI | Anthropic*
