# 🎬 Video Maker Bot — Gerador Automático de Vídeos para Redes Sociais

Bot pessoal que gera vídeos verticais (9:16) editados profissionalmente para TikTok, Reels e YouTube Shorts a partir de um comando no Telegram.

## 🎯 O que ele faz

Você manda uma mensagem no Telegram tipo:

```
/criar motivacional "Nunca desista dos seus sonhos"
```

E em 2–5 minutos recebe de volta um vídeo pronto com:

- ✅ Roteiro gerado por IA (Groq / Llama 3.3)
- ✅ Narração em português (Edge TTS)
- ✅ Legendas animadas palavra-por-palavra (estilo CapCut/Submagic)
- ✅ Mix de b-roll em vídeo + imagens com Ken Burns
- ✅ Efeitos sonoros nos cortes de impacto
- ✅ Transições suaves apenas em momentos de virada
- ✅ Formato 1080×1920 (9:16) otimizado para retenção

## 🏗️ Arquitetura

```
Telegram (você)
    ↓ comando
GitHub Actions (CPU gratuito)
    ↓ executa pipeline
Python → Groq → Edge TTS → Whisper → FFmpeg
    ↓ vídeo final
Cloudflare R2 (storage grátis)
    ↓ link
Telegram (você recebe)
```

## 💰 Custo

**R$ 0 / mês** — tudo roda em camadas gratuitas:

| Serviço | Limite grátis | Uso estimado |
|---------|---------------|--------------|
| GitHub Actions | 2.000 min/mês | ~5 min por vídeo = 400 vídeos/mês |
| Groq API | Generoso | 1 chamada por vídeo |
| Edge TTS | Ilimitado | Infinito |
| Pexels/Pixabay | Ilimitado prático | 5-10 mídias por vídeo |
| Cloudflare R2 | 10 GB storage + saída grátis | ~500 vídeos armazenados |
| Telegram Bot | Ilimitado | Infinito |

## 📁 Estrutura do projeto

```
├── .github/workflows/   # GitHub Actions (trigger de geração)
├── src/
│   ├── bot/            # Bot do Telegram (webhook handler)
│   ├── pipeline/       # Orquestração do fluxo (roteiro → vídeo)
│   ├── media/          # Busca de vídeos/imagens (Pexels, Pixabay, Wikipedia)
│   ├── editor/         # Motor FFmpeg (cortes, Ken Burns, legendas ASS)
│   ├── templates/      # Templates de edição (motivacional, viral, notícias)
│   └── utils/          # Helpers (config, logs, R2 upload)
├── assets/
│   ├── fonts/          # Fontes pra legenda
│   └── sfx/            # Efeitos sonoros (whoosh, impacto, etc.)
├── docs/               # Documentação de setup
└── tests/              # Testes
```

## 🚀 Setup

Veja [docs/SETUP.md](docs/SETUP.md) para o passo-a-passo completo de configuração (telegram bot, Groq API, Cloudflare R2, GitHub secrets).

## 📝 Comandos do bot

| Comando | O que faz |
|---------|-----------|
| `/criar <tema>` | Gera vídeo com o tema informado (template padrão: motivacional) |
| `/template <nome>` | Troca o template padrão (motivacional, viral, noticias) |
| `/roteiro <tema>` | Gera só o roteiro pra aprovação antes de renderizar |
| `/refazer` | Refaz o último vídeo com variação |
| `/status` | Mostra status do job em andamento |
| `/ajuda` | Lista todos os comandos |

## 📜 Licença

Uso pessoal. Código proprietário.
