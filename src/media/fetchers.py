"""
Busca de mídia: vídeos (Pexels), imagens (Pixabay), fotos de pessoas (Wikipedia).

Cada fetcher retorna um Path local do arquivo baixado.
"""
from __future__ import annotations

import hashlib
import random
from pathlib import Path
from typing import Optional

import httpx
import wikipediaapi
from tenacity import retry, stop_after_attempt, wait_exponential

from src.utils.config import config, TEMP_DIR
from src.utils.logger import get_logger

logger = get_logger(__name__)


def _cache_path(key: str, ext: str) -> Path:
    cache_dir = TEMP_DIR / "media_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    digest = hashlib.md5(key.encode()).hexdigest()[:16]
    return cache_dir / f"{digest}.{ext}"


def _download(url: str, target: Path) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    with httpx.stream("GET", url, timeout=60.0, follow_redirects=True) as r:
        r.raise_for_status()
        with open(target, "wb") as f:
            for chunk in r.iter_bytes():
                f.write(chunk)
    return target


# ============================================
# PEXELS — Vídeos b-roll
# ============================================
class PexelsFetcher:
    BASE_URL = "https://api.pexels.com/videos/search"

    def __init__(self) -> None:
        self.api_key = config.pexels_api_key
        if not self.api_key:
            raise ValueError("PEXELS_API_KEY não configurado")

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, min=2, max=8))
    def search_video(
        self,
        query: str,
        orientation: str = "portrait",
        min_duration: int = 3,
        max_duration: int = 30,
    ) -> Optional[Path]:
        """
        Busca vídeo curto no Pexels e baixa.
        Retorna Path local ou None se não encontrar.
        """
        logger.info(f"Pexels: buscando {query!r}")
        headers = {"Authorization": self.api_key}
        params = {
            "query": query,
            "orientation": orientation,
            "per_page": 15,
            "size": "medium",
        }

        r = httpx.get(self.BASE_URL, headers=headers, params=params, timeout=30.0)
        r.raise_for_status()
        data = r.json()

        videos = data.get("videos", [])
        candidates = [
            v for v in videos
            if min_duration <= v.get("duration", 0) <= max_duration
        ]
        if not candidates:
            candidates = videos  # pega qualquer coisa se filtro for muito estrito
        if not candidates:
            logger.warning(f"Pexels: nada encontrado para {query!r}")
            return None

        # Pega um aleatório dentro dos primeiros 5 resultados (diversidade)
        chosen = random.choice(candidates[:5])

        # Escolhe o arquivo de melhor qualidade que seja vertical/quadrado
        files = chosen.get("video_files", [])
        # Ordena por altura (prioriza HD vertical)
        files.sort(key=lambda f: (f.get("height", 0), f.get("width", 0)), reverse=True)
        # Prefere vertical
        vertical = [f for f in files if f.get("height", 0) > f.get("width", 0)]
        pool = vertical if vertical else files
        if not pool:
            return None

        video_file = pool[0]
        url = video_file["link"]

        cache = _cache_path(f"pexels:{chosen['id']}", "mp4")
        if cache.exists():
            logger.info(f"Pexels: cache hit {cache.name}")
            return cache

        logger.info(f"Pexels: baixando {url[:60]}...")
        return _download(url, cache)


# ============================================
# PIXABAY — Imagens e vídeos complementares
# ============================================
class PixabayFetcher:
    IMAGE_URL = "https://pixabay.com/api/"
    VIDEO_URL = "https://pixabay.com/api/videos/"

    def __init__(self) -> None:
        self.api_key = config.pixabay_api_key
        if not self.api_key:
            raise ValueError("PIXABAY_API_KEY não configurado")

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, min=2, max=8))
    def search_image(self, query: str, orientation: str = "vertical") -> Optional[Path]:
        logger.info(f"Pixabay: buscando imagem {query!r}")
        params = {
            "key": self.api_key,
            "q": query,
            "orientation": orientation,
            "per_page": 20,
            "image_type": "photo",
            "safesearch": "true",
        }
        r = httpx.get(self.IMAGE_URL, params=params, timeout=30.0)
        r.raise_for_status()
        data = r.json()
        hits = data.get("hits", [])
        if not hits:
            logger.warning(f"Pixabay: sem imagens pra {query!r}")
            return None

        chosen = random.choice(hits[:10])
        url = chosen.get("largeImageURL") or chosen.get("webformatURL")
        cache = _cache_path(f"pixabay_img:{chosen['id']}", "jpg")
        if cache.exists():
            return cache
        return _download(url, cache)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, min=2, max=8))
    def search_video(self, query: str) -> Optional[Path]:
        logger.info(f"Pixabay: buscando vídeo {query!r}")
        params = {
            "key": self.api_key,
            "q": query,
            "per_page": 10,
        }
        r = httpx.get(self.VIDEO_URL, params=params, timeout=30.0)
        r.raise_for_status()
        data = r.json()
        hits = data.get("hits", [])
        if not hits:
            return None
        chosen = random.choice(hits[:5])
        videos = chosen.get("videos", {})
        # Prefere qualidade média (arquivo menor, mais rápido)
        for quality in ("medium", "small", "large"):
            if quality in videos:
                url = videos[quality]["url"]
                cache = _cache_path(f"pixabay_vid:{chosen['id']}", "mp4")
                if cache.exists():
                    return cache
                return _download(url, cache)
        return None


# ============================================
# WIKIPEDIA — Fotos de pessoas famosas
# ============================================
class WikipediaPhotoFetcher:
    def __init__(self) -> None:
        self.wiki_pt = wikipediaapi.Wikipedia(
            user_agent="VideoMakerBot/1.0 (pessoal)",
            language="pt",
        )
        self.wiki_en = wikipediaapi.Wikipedia(
            user_agent="VideoMakerBot/1.0 (pessoal)",
            language="en",
        )

    def fetch_person_photo(self, person_name: str) -> Optional[Path]:
        """
        Busca foto de pessoa. Usa API REST do Wikipedia pra pegar
        a imagem principal da página.
        """
        logger.info(f"Wikipedia: buscando foto de {person_name!r}")

        for lang in ("pt", "en"):
            photo = self._try_fetch(person_name, lang)
            if photo:
                return photo
        logger.warning(f"Wikipedia: sem foto pra {person_name!r}")
        return None

    def _try_fetch(self, name: str, lang: str) -> Optional[Path]:
        # Usa REST API do Wikipedia (summary) que retorna thumbnail/original
        url = f"https://{lang}.wikipedia.org/api/rest_v1/page/summary/{name.replace(' ', '_')}"
        try:
            r = httpx.get(url, timeout=15.0, follow_redirects=True)
            if r.status_code != 200:
                return None
            data = r.json()
            # Prefere 'originalimage', cai pra 'thumbnail'
            img = data.get("originalimage") or data.get("thumbnail")
            if not img or not img.get("source"):
                return None
            img_url = img["source"]
            cache = _cache_path(f"wiki:{lang}:{name}", "jpg")
            if cache.exists():
                return cache
            return _download(img_url, cache)
        except Exception as e:
            logger.warning(f"Wikipedia erro ({lang}): {e}")
            return None


# ============================================
# Dispatcher inteligente
# ============================================
class MediaDispatcher:
    """Decide qual fetcher usar com fallback automático."""

    def __init__(self) -> None:
        self.pexels = PexelsFetcher() if config.pexels_api_key else None
        self.pixabay = PixabayFetcher() if config.pixabay_api_key else None
        self.wiki = WikipediaPhotoFetcher()

    def fetch_video(self, query: str) -> Optional[Path]:
        # Tenta Pexels primeiro (qualidade melhor), cai pra Pixabay
        if self.pexels:
            result = self.pexels.search_video(query)
            if result:
                return result
        if self.pixabay:
            return self.pixabay.search_video(query)
        return None

    def fetch_image(self, query: str) -> Optional[Path]:
        if self.pixabay:
            return self.pixabay.search_image(query)
        return None

    def fetch_person(self, name: str) -> Optional[Path]:
        return self.wiki.fetch_person_photo(name)
