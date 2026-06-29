"""Manifest-driven library migration API."""

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi import Query
from pydantic import BaseModel, Field

from core.duplicate_delete import (
    DuplicateDeleteError,
    apply_duplicate_delete,
    preview_duplicate_delete,
)
from core.duplicate_numbers import find_duplicate_numbers
from core.empty_folders import (
    EmptyFolderError,
    apply_empty_folders,
    preview_empty_folders,
)
from core.library_migration import (
    MigrationConflictError,
    MigrationError,
    apply_manifest,
    inventory_library,
    plan_library,
    rollback_manifest,
    verify_manifest,
)
from core.logger import get_logger
from core.title_placeholder import (
    TitlePlaceholderError,
    apply_title_placeholder_manifest,
    preview_title_placeholders,
    rollback_title_placeholder_manifest,
)

logger = get_logger(__name__)
router = APIRouter(prefix="/api/library-migration", tags=["library-migration"])


class InventoryRequest(BaseModel):
    root: str = Field(..., min_length=1)
    run_id: str | None = None
    include_manual: bool = False


class PlanRequest(BaseModel):
    run_dir: str = Field(..., min_length=1)
    max_path: int = Field(default=240, ge=120, le=1024)
    unknown_actor: str = Field(default="未知女優", min_length=1, max_length=80)
    manual_folder: str = Field(default="#待整理", min_length=1, max_length=80)


class ManifestRequest(BaseModel):
    manifest: str = Field(..., min_length=1)


class ApplyRequest(ManifestRequest):
    confirm_run: str = Field(..., min_length=1, max_length=64)
    batch_size: int = Field(default=20, ge=1, le=20)


class DuplicateDeleteRequest(BaseModel):
    path: str = Field(..., min_length=1)


class DuplicateDeleteApplyRequest(DuplicateDeleteRequest):
    confirm: bool = False


class EmptyFoldersPreviewRequest(BaseModel):
    paths: list[str] | None = None


class EmptyFoldersApplyRequest(EmptyFoldersPreviewRequest):
    confirm: bool = False


class TitlePlaceholderPreviewRequest(BaseModel):
    run_id: str | None = None


class TitlePlaceholderApplyRequest(BaseModel):
    manifest: str = Field(..., min_length=1)
    confirm: bool = False
    batch_size: int = Field(default=20, ge=1, le=20)


def _raise_api_error(exc: Exception) -> None:
    if isinstance(exc, TitlePlaceholderError):
        logger.warning("Title placeholder request rejected", exc_info=True)
        code = str(exc) or "title_placeholder_error"
        messages = {
            "confirmation_required": "請先確認移動標題未定影片",
            "gallery_not_configured": "尚未設定資料庫目錄",
            "manifest_not_found": "找不到固定清單，請重新檢測",
            "invalid_manifest": "標題未定隔離清單格式無效，請重新檢測",
            "sidecar_target_exists": "伴隨檔案目標已存在，已跳過該影片",
        }
        raise HTTPException(
            status_code=400,
            detail={"code": code, "message": messages.get(code, "標題未定隔離請求無效")},
        ) from exc
    if isinstance(exc, EmptyFolderError):
        logger.warning("Empty folder request rejected", exc_info=True)
        code = str(exc) or "empty_folder_error"
        messages = {
            "confirmation_required": "請先確認清理空資料夾",
            "folder_not_empty": "所選資料夾已不是空資料夾，請重新檢測",
            "path_outside_gallery": "只能清理資料庫目錄內的空資料夾",
            "protected_folder": "受保護目錄不會被清理",
            "gallery_not_configured": "尚未設定資料庫目錄",
        }
        raise HTTPException(
            status_code=400,
            detail={"code": code, "message": messages.get(code, "空資料夾清理請求無效")},
        ) from exc
    if isinstance(exc, DuplicateDeleteError):
        logger.warning("Duplicate delete request rejected", exc_info=True)
        raise HTTPException(status_code=400, detail="重複影片刪除請求無效") from exc
    if isinstance(exc, MigrationConflictError):
        logger.warning("Library migration conflict", exc_info=True)
        raise HTTPException(status_code=409, detail="遷移計畫存在衝突，未執行任何移動") from exc
    if isinstance(exc, MigrationError):
        logger.warning("Invalid library migration request", exc_info=True)
        raise HTTPException(status_code=400, detail="遷移請求無效") from exc
    logger.exception("Unexpected library migration failure")
    raise HTTPException(status_code=500, detail="遷移操作失敗") from exc


@router.post("/inventory")
async def create_inventory(request: InventoryRequest) -> dict[str, Any]:
    try:
        return await asyncio.to_thread(
            inventory_library,
            request.root,
            request.run_id,
            include_manual=request.include_manual,
        )
    except Exception as exc:
        _raise_api_error(exc)


@router.post("/plan")
async def create_plan(request: PlanRequest) -> dict[str, Any]:
    try:
        return await asyncio.to_thread(
            plan_library,
            request.run_dir,
            max_path=request.max_path,
            unknown_actor=request.unknown_actor,
            manual_folder=request.manual_folder,
        )
    except Exception as exc:
        _raise_api_error(exc)


@router.post("/apply")
async def apply_plan(request: ApplyRequest) -> dict[str, Any]:
    try:
        return await asyncio.to_thread(
            apply_manifest, request.manifest, request.confirm_run, request.batch_size,
        )
    except Exception as exc:
        _raise_api_error(exc)


@router.post("/verify")
async def verify_plan(request: ManifestRequest) -> dict[str, Any]:
    try:
        return await asyncio.to_thread(verify_manifest, request.manifest)
    except Exception as exc:
        _raise_api_error(exc)


@router.post("/rollback")
async def rollback_plan(request: ApplyRequest) -> dict[str, Any]:
    try:
        return await asyncio.to_thread(
            rollback_manifest, request.manifest, request.confirm_run, request.batch_size,
        )
    except Exception as exc:
        _raise_api_error(exc)


@router.get("/duplicates")
async def get_duplicate_numbers(
    include_multipart: bool = Query(default=False),
    include_missing_paths: bool = Query(default=True),
    limit: int = Query(default=500, ge=1, le=5000),
) -> dict[str, Any]:
    try:
        return await asyncio.to_thread(
            find_duplicate_numbers,
            include_multipart=include_multipart,
            include_missing_paths=include_missing_paths,
            limit=limit,
        )
    except Exception as exc:
        _raise_api_error(exc)


@router.post("/duplicate-delete/preview")
async def preview_duplicate_delete_plan(request: DuplicateDeleteRequest) -> dict[str, Any]:
    try:
        return await asyncio.to_thread(preview_duplicate_delete, request.path)
    except Exception as exc:
        _raise_api_error(exc)


@router.post("/duplicate-delete/apply")
async def apply_duplicate_delete_plan(request: DuplicateDeleteApplyRequest) -> dict[str, Any]:
    try:
        return await asyncio.to_thread(
            apply_duplicate_delete,
            request.path,
            confirm=request.confirm,
        )
    except Exception as exc:
        _raise_api_error(exc)


@router.post("/empty-folders/preview")
async def preview_empty_folder_cleanup(request: EmptyFoldersPreviewRequest) -> dict[str, Any]:
    try:
        return await asyncio.to_thread(preview_empty_folders, paths=request.paths)
    except Exception as exc:
        _raise_api_error(exc)


@router.post("/empty-folders/apply")
async def apply_empty_folder_cleanup(request: EmptyFoldersApplyRequest) -> dict[str, Any]:
    try:
        return await asyncio.to_thread(
            apply_empty_folders,
            confirm=request.confirm,
            paths=request.paths,
        )
    except Exception as exc:
        _raise_api_error(exc)


@router.post("/title-placeholder/preview")
async def preview_title_placeholder_isolation(
    request: TitlePlaceholderPreviewRequest,
) -> dict[str, Any]:
    try:
        return await asyncio.to_thread(
            preview_title_placeholders,
            run_id=request.run_id,
        )
    except Exception as exc:
        _raise_api_error(exc)


@router.post("/title-placeholder/apply")
async def apply_title_placeholder_isolation(
    request: TitlePlaceholderApplyRequest,
) -> dict[str, Any]:
    try:
        return await asyncio.to_thread(
            apply_title_placeholder_manifest,
            request.manifest,
            confirm=request.confirm,
            batch_size=request.batch_size,
        )
    except Exception as exc:
        _raise_api_error(exc)


@router.post("/title-placeholder/rollback")
async def rollback_title_placeholder_isolation(
    request: TitlePlaceholderApplyRequest,
) -> dict[str, Any]:
    try:
        return await asyncio.to_thread(
            rollback_title_placeholder_manifest,
            request.manifest,
            confirm=request.confirm,
            batch_size=request.batch_size,
        )
    except Exception as exc:
        _raise_api_error(exc)
