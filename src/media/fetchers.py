"""
Busca de mídia: vídeos (Pexels), imagens (Pixabay), fotos de pessoas (Wikipedia).

Cada fetcher retorna um Path local do arquivo baixado.
"""
from __future__ import annotations

import hashlib
import random
from pathlib import Path
from typing import Optional
from urllib.parse import quote

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

    def search_video(
        self,
        query: str,
        orientation: str = "portrait",
        min_duration: int = 3,
        max_duration: int = 30,
    ) -> Optional[Path]:
        """
        Busca vídeo curto no Pexels e baixa.
        Retorna Path local ou None se não encontrar/falhar.
        """
        try:
            return self._search_video_with_retry(
                query, orientation, min_duration, max_duration
            )
        except Exception as e:
            logger.warning(f"Pexels search falhou pra {query!r}: {e}")
            return None

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, min=2, max=8), reraise=True)
    def _search_video_with_retry(
        self,
        query: str,
        orientation: str,
        min_duration: int,
        max_duration: int,
    ) -> Optional[Path]:
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

        videos = data.get("videos") or []
        candidates = [
            v for v in videos
            if min_duration <= (v.get("duration") or 0) <= max_duration
        ]
        if not candidates:
            candidates = videos
        if not candidates:
            logger.warning(f"Pexels: nada encontrado para {query!r}")
            return None

        chosen = random.choice(candidates[:5])
        chosen_id = chosen.get("id")
        if not chosen_id:
            return None

        files = chosen.get("video_files") or []
        files = [f for f in files if f.get("link")]  # filtra só com link
        if not files:
            return None

        files.sort(key=lambda f: (f.get("height", 0), f.get("width", 0)), reverse=True)
        vertical = [f for f in files if f.get("height", 0) > f.get("width", 0)]
        pool = vertical if vertical else files

        video_file = pool[0]
        url = video_file.get("link")
        if not url:
            return None

        cache = _cache_path(f"pexels:{chosen_id}", "mp4")
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

    def search_image(self, query: str, orientation: str = "vertical") -> Optional[Path]:
        try:
            return self._search_image_retry(query, orientation)
        except Exception as e:
            logger.warning(f"Pixabay image falhou pra {query!r}: {e}")
            return None

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, min=2, max=8), reraise=True)
    def _search_image_retry(self, query: str, orientation: str) -> Optional[Path]:
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
        hits = data.get("hits") or []
        if not hits:
            logger.warning(f"Pixabay: sem imagens pra {query!r}")
            return None

        chosen = random.choice(hits[:10])
        chosen_id = chosen.get("id")
        url = chosen.get("largeImageURL") or chosen.get("webformatURL")
        if not url or not chosen_id:
            return None
        cache = _cache_path(f"pixabay_img:{chosen_id}", "jpg")
        if cache.exists():
            return cache
        return _download(url, cache)

    def search_video(self, query: str) -> Optional[Path]:
        try:
            return self._search_video_retry(query)
        except Exception as e:
            logger.warning(f"Pixabay video falhou pra {query!r}: {e}")
            return None

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, min=2, max=8), reraise=True)
    def _search_video_retry(self, query: str) -> Optional[Path]:
        logger.info(f"Pixabay: buscando vídeo {query!r}")
        params = {
            "key": self.api_key,
            "q": query,
            "per_page": 10,
        }
        r = httpx.get(self.VIDEO_URL, params=params, timeout=30.0)
        r.raise_for_status()
        data = r.json()
        hits = data.get("hits") or []
        if not hits:
            return None
        chosen = random.choice(hits[:5])
        chosen_id = chosen.get("id")
        videos = chosen.get("videos") or {}
        if not chosen_id:
            return None
        for quality in ("medium", "small", "large", "tiny"):
            v = videos.get(quality)
            if not v:
                continue
            url = v.get("url") if isinstance(v, dict) else v
            if not url:
                continue
            cache = _cache_path(f"pixabay_vid:{chosen_id}", "mp4")
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
        # URL-encode pra suportar nomes com acento/caracteres especiais
        encoded = quote(name.replace(" ", "_"), safe="_")
        url = f"https://{lang}.wikipedia.org/api/rest_v1/page/summary/{encoded}"
        try:
            r = httpx.get(url, timeout=15.0, follow_redirects=True)
            if r.status_code != 200:
                return None
            data = r.json()
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
