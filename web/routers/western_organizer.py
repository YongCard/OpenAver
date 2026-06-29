"""Western scene organizer API."""

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from core.western_organizer import (
    DEFAULT_APPLY_BATCH_SIZE,
    WesternOrganizerError,
    apply_western_manifest,
    preview_western_organize,
)

router = APIRouter(prefix="/api/western-organizer", tags=["western-organizer"])


class WesternPreviewRequest(BaseModel):
    selected_paths: list[str] = Field(default_factory=list)
    run_id: str | None = None


class WesternApplyRequest(BaseModel):
    manifest: str = Field(..., min_length=1)
    confirm: bool = False
    batch_size: int = Field(default=DEFAULT_APPLY_BATCH_SIZE, ge=1, le=10000)


def _raise_api_error(exc: Exception) -> None:
    if isinstance(exc, WesternOrganizerError):
        code = str(exc) or "western_organizer_error"
        messages = {
            "confirmation_required": "請先確認整理本批歐美影片",
            "gallery_not_configured": "尚未設定影片庫資料夾",
            "manifest_not_found": "找不到整理預覽，請重新生成",
            "invalid_manifest": "整理預覽格式無效，請重新生成",
            "target_exists": "目標檔案已存在，未覆蓋任何檔案",
        }
        raise HTTPException(
            status_code=400,
            detail={"code": code, "message": messages.get(code, "歐美整理請求無效")},
        ) from exc
    raise HTTPException(status_code=500, detail="歐美整理操作失敗") from exc


@router.post("/preview")
async def preview(request: WesternPreviewRequest) -> dict[str, Any]:
    try:
        return await asyncio.to_thread(
            preview_western_organize,
            selected_paths=request.selected_paths,
            run_id=request.run_id,
        )
    except Exception as exc:
        _raise_api_error(exc)


@router.post("/apply")
async def apply(request: WesternApplyRequest) -> dict[str, Any]:
    try:
        return await asyncio.to_thread(
            apply_western_manifest,
            request.manifest,
            confirm=request.confirm,
            batch_size=request.batch_size,
        )
    except Exception as exc:
        _raise_api_error(exc)
