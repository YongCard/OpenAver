"""
Tags API Router — /api/tags

端點：
    GET /api/tags/top — NFO tag 頻次排序（不含 user_tags），AI agent 用於跨語言同義詞候選分析
"""

import sqlite3

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from core.database import get_db_path
from core.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/tags", tags=["tags"])


@router.get("/top")
def get_top_tags(
    limit: int = Query(100, ge=1, le=500),
    min_count: int = Query(2, ge=1),
):
    """頻次排序 NFO tags（不含 user_tags），AI agent 可用於跨語言同義詞候選分析。"""
    try:
        db_path = get_db_path()
        with sqlite3.connect(str(db_path)) as conn:
            cur = conn.cursor()

            # top N tags（套 min_count + limit）
            cur.execute(
                """
                SELECT je.value AS tag, COUNT(*) AS cnt
                FROM videos, json_each(videos.tags) AS je
                WHERE je.value IS NOT NULL AND je.value != ''
                GROUP BY je.value
                HAVING cnt >= ?
                ORDER BY cnt DESC, je.value ASC
                LIMIT ?
                """,
                (min_count, limit),
            )
            items = [{"tag": row[0], "count": row[1]} for row in cur.fetchall()]

            # total unique（不套 min_count）
            cur.execute(
                """
                SELECT COUNT(DISTINCT je.value) AS total
                FROM videos, json_each(videos.tags) AS je
                WHERE je.value IS NOT NULL AND je.value != ''
                """
            )
            total = cur.fetchone()[0]

        return {
            "success": True,
            "items": items,
            "total_unique_tags": total,
            "applied_min_count": min_count,
        }
    except Exception:
        logger.exception("[tags] top 查詢失敗")
        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "items": [],
                "total_unique_tags": 0,
                "applied_min_count": min_count,
                "error": "操作失敗",
            },
        )
