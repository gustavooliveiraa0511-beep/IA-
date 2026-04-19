"""
Busca música de fundo por template.

Estratégia (100% grátis):
- Lemos `assets/music/tracks.json` — um JSON com URLs de faixas CC0 por template.
- Baixamos on-demand e cacheamos em `assets/music/{template}/{sha}.mp3`.
- Workflow do GitHub Actions cacheia `assets/music/` via `actions/cache`.
- Se o arquivo JSON não existir ou estiver vazio pro template pedido, retornamos
  None — vídeo sai sem música (melhor que crashar).

Formato esperado de `tracks.json`:

    {
      "motivacional": [
        "https://cdn.pixabay.com/download/audio/.../uplifting-cinematic.mp3",
        "https://cdn.pixabay.com/..."
      ],
      "viral": ["https://...", "..."],
      "noticias": ["https://..."],
      "gaming": ["https://..."]
    }

Onde achar URLs CC0:
- https://pixabay.com/music/  → clique na faixa → "Download" → clique direito no
  botão → Copiar URL (cdn.pixabay.com/...)
- https://freepd.com  → qualquer .mp3 direto
- https://archive.org  → gravações em domínio público
"""
from __future__ import annotations

import hashlib
import json
import random
from pathlib import Path
from typing import Optional

import httpx

from src.utils.config import ASSETS_DIR
from src.utils.logger import get_logger

logger = get_logger(__name__)


MUSIC_DIR = ASSETS_DIR / "music"
TRACKS_MANIFEST = MUSIC_DIR / "tracks.json"


def fetch_music(template: str) -> Optional[Path]:
    """
    Retorna o Path local de uma faixa aleatória do template.
    Baixa se necessário, cacheia pra sempre. Retorna None se nenhuma URL
    configurada ou se todos os downloads falharem.
    """
    urls = _load_manifest().get(template, [])
    if not urls:
        logger.info(f"[music] nenhuma URL configurada pro template {template!r}")
        return None

    random.shuffle(urls)  # diversifica entre jobs
    for url in urls:
        try:
            path = _download_cached(template, url)
            if path and path.exists() and path.stat().st_size > 10_000:
                logger.info(f"[music] usando {path.name} pro template {template!r}")
                return path
        except Exception as e:
            logger.warning(f"[music] falha em {url}: {type(e).__name__}: {e}")

    logger.warning(f"[music] todas as URLs falharam pro template {template!r}")
    return None


def _load_manifest() -> dict[str, list[str]]:
    """Lê tracks.json. Sempre retorna dict — mesmo se arquivo não existe."""
    if not TRACKS_MANIFEST.exists():
        logger.debug(f"[music] manifest não encontrado em {TRACKS_MANIFEST}")
        return {}
    try:
        data = json.loads(TRACKS_MANIFEST.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            logger.warning(f"[music] manifest malformado (não é objeto)")
            return {}
        # Normaliza: só listas de string
        return {k: [str(u) for u in v if isinstance(u, str)]
                for k, v in data.items() if isinstance(v, list)}
    except Exception as e:
        logger.warning(f"[music] erro lendo manifest: {e}")
        return {}


def _download_cached(template: str, url: str) -> Path:
    """Baixa URL pra cache local. Re-usa se já existir."""
    template_dir = MUSIC_DIR / template
    template_dir.mkdir(parents=True, exist_ok=True)

    digest = hashlib.sha256(url.encode()).hexdigest()[:16]
    # Tenta inferir extensão pelo final da URL; default mp3
    ext = ".mp3"
    lower = url.lower()
    for candidate in (".mp3", ".wav", ".ogg", ".m4a"):
        if candidate in lower:
            ext = candidate
            break
    target = template_dir / f"{digest}{ext}"

    if target.exists() and target.stat().st_size > 10_000:
        return target

    logger.info(f"[music] baixando {url[:80]}...")
    with httpx.stream("GET", url, timeout=60.0, follow_redirects=True) as r:
        r.raise_for_status()
        with open(target, "wb") as f:
            for chunk in r.iter_bytes():
                f.write(chunk)
    return target
