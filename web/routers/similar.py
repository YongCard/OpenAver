"""
web/routers/similar.py
GET /api/similar-covers 端點（57b-T2）。

兩個端點：
- GET /api/similar-covers/by-number/{number}  ← 必須在前（防路由衝突）
- GET /api/similar-covers/{video_id}

v0.8.7 rule-based ranker 取代 v0.8.6 CLIP embedding。
API response shape 完全不變（CD-57b-5）。
"""
from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query

from core.database import VideoRepository
from core.logger import get_logger
from core.path_utils import uri_to_fs_path
from core.similar.ranker_cache import SimilarRankerCache

logger = get_logger(__name__)

router = APIRouter(prefix="/api", tags=["similar"])


def _build_cover_url(cover_path: str) -> str:
    """file:/// URI → /api/gallery/image?path=<URL-encoded local FS path>。
    邊界：cover_path 為空字串 / None → 回傳空字串。
    """
    if not cover_path:
        return ""
    local_path = uri_to_fs_path(cover_path)
    return f"/api/gallery/image?path={quote(local_path, safe='')}"


def _compute_similar_covers(video_id: int, limit: int) -> dict:
    """核心業務邏輯：根據 video_id 取 target，呼叫 ranker 取得相似影片，組裝 response。

    Args:
        video_id: 目標影片 id
        limit: 回傳結果數量上限

    Returns:
        符合 v0.8.6 response shape 的 dict

    Raises:
        HTTPException 404: target 不存在
    """
    repo = VideoRepository()
    target = repo.get_by_id(video_id)
    if target is None:
        raise HTTPException(status_code=404, detail="找不到影片")

    ranker = SimilarRankerCache.get()
    results_videos = ranker.rank(target, top_k=limit)

    return {
        "video_id": video_id,
        "model_id": "rule-based:v1",
        "query_video": {
            "video_id": target.id,
            "number": target.number,
            "title": target.title,
            "cover_url": _build_cover_url(target.cover_path),
        },
        "results": [
            {
                "video_id": v.id,
                "number": v.number,
                "title": v.title,
                "cover_path": v.cover_path,
                "cover_url": _build_cover_url(v.cover_path),
                "cosine_score": ranker._score(target, v),
                "penalty_applied": False,  # rule-based 無 penalty 概念，保留 key 為 fixture 相容
                "actresses": v.actresses if isinstance(v.actresses, list) else [],
            }
            for v in results_videos
        ],
    }


# by-number 端點必須在 {video_id} 之前定義（防 FastAPI 路由衝突）
@router.get("/similar-covers/by-number/{number}")
def get_similar_covers_by_number(
    number: str,
    limit: int = Query(default=12, ge=1, le=50),
) -> dict:
    """GET /api/similar-covers/by-number/{number}

    根據番號查詢相似影片。番號大小寫不敏感。

    Returns:
        200: v0.8.6 response shape
        404: 查無番號
    """
    repo = VideoRepository()
    video = repo.get_by_number(number)
    if video is None:
        raise HTTPException(status_code=404, detail="找不到影片")
    return _compute_similar_covers(video.id, limit)


@router.get("/similar-covers/{video_id}")
def get_similar_covers_by_id(
    video_id: int,
    limit: int = Query(default=12, ge=1, le=50),
) -> dict:
    """GET /api/similar-covers/{video_id}

    根據影片 id 查詢相似影片。

    Returns:
        200: v0.8.6 response shape
        404: 查無 video_id
    """
    return _compute_similar_covers(video_id, limit)
