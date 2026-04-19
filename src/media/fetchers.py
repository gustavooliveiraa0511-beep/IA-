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
        variation_index: Optional[int] = None,
    ) -> Optional[Path]:
        """
        Busca vídeo curto no Pexels e baixa.
        Retorna Path local ou None se não encontrar/falhar.

        Se `variation_index` for passado, seleciona o N-ésimo resultado
        (determinístico). Caso contrário escolhe random. Útil pra sub-clips
        da mesma cena pegarem mídias diferentes.
        """
        try:
            return self._search_video_with_retry(
                query, orientation, min_duration, max_duration, variation_index
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
        variation_index: Optional[int] = None,
    ) -> Optional[Path]:
        logger.info(f"Pexels: buscando {query!r} (var={variation_index})")
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

        # Pool dos top 8 (mais variedade que top 5 pra sub-clips).
        pool_cands = candidates[:8]
        if variation_index is not None and pool_cands:
            chosen = pool_cands[variation_index % len(pool_cands)]
        else:
            chosen = random.choice(pool_cands[:5])
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

    def search_image(
        self,
        query: str,
        orientation: str = "vertical",
        variation_index: Optional[int] = None,
    ) -> Optional[Path]:
        try:
            return self._search_image_retry(query, orientation, variation_index)
        except Exception as e:
            logger.warning(f"Pixabay image falhou pra {query!r}: {e}")
            return None

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, min=2, max=8), reraise=True)
    def _search_image_retry(
        self,
        query: str,
        orientation: str,
        variation_index: Optional[int] = None,
    ) -> Optional[Path]:
        logger.info(f"Pixabay: buscando imagem {query!r} (var={variation_index})")
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

        pool_cands = hits[:15]
        if variation_index is not None and pool_cands:
            chosen = pool_cands[variation_index % len(pool_cands)]
        else:
            chosen = random.choice(pool_cands[:10])
        chosen_id = chosen.get("id")
        url = chosen.get("largeImageURL") or chosen.get("webformatURL")
        if not url or not chosen_id:
            return None
        cache = _cache_path(f"pixabay_img:{chosen_id}", "jpg")
        if cache.exists():
            return cache
        return _download(url, cache)

    def search_video(
        self, query: str, variation_index: Optional[int] = None
    ) -> Optional[Path]:
        try:
            return self._search_video_retry(query, variation_index)
        except Exception as e:
            logger.warning(f"Pixabay video falhou pra {query!r}: {e}")
            return None

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, min=2, max=8), reraise=True)
    def _search_video_retry(
        self, query: str, variation_index: Optional[int] = None
    ) -> Optional[Path]:
        logger.info(f"Pixabay: buscando vídeo {query!r} (var={variation_index})")
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
        pool_cands = hits[:8]
        if variation_index is not None and pool_cands:
            chosen = pool_cands[variation_index % len(pool_cands)]
        else:
            chosen = random.choice(pool_cands[:5])
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
    """
    Decide qual fetcher usar com fallback automático e evita repetir mídia
    entre cenas consecutivas (diversidade visual).
    """

    def __init__(self) -> None:
        self.pexels = PexelsFetcher() if config.pexels_api_key else None
        self.pixabay = PixabayFetcher() if config.pixabay_api_key else None
        self.wiki = WikipediaPhotoFetcher()
        # Paths já usados neste job — evita reuso adjacente.
        self._used_video_paths: set[str] = set()
        self._used_image_paths: set[str] = set()

    def reset_used(self) -> None:
        """Reseta estado de diversidade (chamar no início de cada job)."""
        self._used_video_paths.clear()
        self._used_image_paths.clear()

    def fetch_video(
        self,
        queries: str | list[str],
        variation_index: Optional[int] = None,
    ) -> Optional[Path]:
        """
        Tenta cada query em ordem até achar vídeo. Primeira query deve ser a
        mais genérica (mais resultados), últimas mais específicas.
        Pexels primeiro (qualidade melhor), cai pra Pixabay.

        Se `variation_index` for passado, seleciona resultado determinístico
        pra sub-clips pegarem mídias diferentes (em vez de random).
        """
        for q in _as_list(queries):
            if self.pexels:
                result = self.pexels.search_video(q, variation_index=variation_index)
                if result and self._is_fresh(result, self._used_video_paths):
                    self._used_video_paths.add(str(result))
                    return result
            if self.pixabay:
                result = self.pixabay.search_video(q, variation_index=variation_index)
                if result and self._is_fresh(result, self._used_video_paths):
                    self._used_video_paths.add(str(result))
                    return result
        # Se nenhuma query deu mídia fresca, aceita até repetida
        for q in _as_list(queries):
            if self.pexels:
                r = self.pexels.search_video(q, variation_index=variation_index)
                if r:
                    return r
            if self.pixabay:
                r = self.pixabay.search_video(q, variation_index=variation_index)
                if r:
                    return r
        return None

    def fetch_image(
        self,
        queries: str | list[str],
        variation_index: Optional[int] = None,
    ) -> Optional[Path]:
        if not self.pixabay:
            return None
        for q in _as_list(queries):
            result = self.pixabay.search_image(q, variation_index=variation_index)
            if result and self._is_fresh(result, self._used_image_paths):
                self._used_image_paths.add(str(result))
                return result
        # Aceita repetida
        for q in _as_list(queries):
            r = self.pixabay.search_image(q, variation_index=variation_index)
            if r:
                return r
        return None

    def fetch_person(self, name: str) -> Optional[Path]:
        return self.wiki.fetch_person_photo(name)

    @staticmethod
    def _is_fresh(path: Path, used: set[str]) -> bool:
        return str(path) not in used


def _as_list(queries: str | list[str] | None) -> list[str]:
    """Normaliza input em list[str], filtrando vazios."""
    if not queries:
        return []
    if isinstance(queries, str):
        return [queries] if queries.strip() else []
    return [q for q in queries if q and q.strip()]
