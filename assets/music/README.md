# Música de fundo

Este diretório guarda música usada pelo `FinalAssembler` pra dar vibe
cinematográfica (com ducking automático quando a narração fala).

## Como configurar (grátis, CC0)

1. Abra `assets/music/tracks.json`
2. Cole URLs **diretas** de faixas instrumentais CC0 por template.

### Onde achar URLs CC0

- **Pixabay Music** (recomendado — licença "free for commercial use, no attribution required"):
  1. Entre em https://pixabay.com/music/
  2. Escolha uma faixa
  3. Clique em **Download**
  4. Clique com o botão direito no link de download → **Copiar URL**
  5. A URL final começa com `https://cdn.pixabay.com/download/audio/...`
- **FreePD** (domínio público): https://freepd.com — qualquer `.mp3` direto
- **Archive.org** — gravações em domínio público

### Exemplo preenchido

```json
{
  "motivacional": [
    "https://cdn.pixabay.com/download/audio/2023/01/02/audio_xxx.mp3",
    "https://cdn.pixabay.com/download/audio/2022/11/15/audio_yyy.mp3"
  ],
  "viral": [
    "https://cdn.pixabay.com/download/audio/..."
  ],
  "noticias": [],
  "gaming": []
}
```

## O que acontece na prática

- **Primeira vez** que o pipeline gera um vídeo pro template: baixa uma URL
  aleatória da lista, cacheia em `assets/music/{template}/{hash}.mp3`.
- **Próximos runs**: usa direto do cache (CI do GitHub Actions cacheia esse
  diretório via `actions/cache`).
- **Nenhuma URL configurada pro template**: vídeo sai SEM música. Sem crash.
- **Todas as URLs falham**: log warning e vídeo sai sem música.

## Ducking (música abaixa enquanto narra)

Automático no `FinalAssembler`. Usa `sidechaincompress` com narração como
sidechain: quando há voz, a música cai ~12dB; quando fica em silêncio, volta ao
volume base.

## Licenças

Só use faixas **CC0 / domínio público / "free for commercial, no attribution"**.
Nunca use faixas com copyright — TikTok/Instagram podem derrubar seu vídeo.
