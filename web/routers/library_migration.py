"""Manifest-driven library migration API."""

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

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

logger = get_logger(__name__)
router = APIRouter(prefix="/api/library-migration", tags=["library-migration"])


class InventoryRequest(BaseModel):
    root: str = Field(..., min_length=1)
    run_id: str | None = None


class PlanRequest(BaseModel):
    run_dir: str = Field(..., min_length=1)
    max_path: int = Field(default=240, ge=120, le=1024)
    unknown_actor: str = Field(default="未知女優", min_length=1, max_length=80)
    manual_folder: str = Field(default="#待人工整理", min_length=1, max_length=80)


class ManifestRequest(BaseModel):
    manifest: str = Field(..., min_length=1)


class ApplyRequest(ManifestRequest):
    confirm_run: str = Field(..., min_length=1, max_length=64)
    batch_size: int = Field(default=20, ge=1, le=20)


def _raise_api_error(exc: Exception) -> None:
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
        return await asyncio.to_thread(inventory_library, request.root, request.run_id)
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
