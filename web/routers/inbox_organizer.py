"""JavSP-style inbox organizer API for the personal OpenAver branch."""

import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import threading
from typing import Any
import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from core.inbox_organizer import (
    DEFAULT_APPLY_BATCH_SIZE,
    InboxOrganizerError,
    apply_inbox_manifest,
    get_inbox_roots,
    inventory_inbox,
    offline_plan_inbox,
    plan_inbox,
    rollback_inbox_manifest,
    search_inbox,
)
from core.logger import get_logger

logger = get_logger(__name__)
router = APIRouter(prefix="/api/inbox-organizer", tags=["inbox-organizer"])

_SEARCH_JOB_LOCK = threading.RLock()
_SEARCH_JOB: dict[str, Any] | None = None
_SEARCH_THREAD: threading.Thread | None = None
_MAX_LOG_LINES = 20


class InboxRootRequest(BaseModel):
    root: str | None = None


class InboxSearchRequest(BaseModel):
    entries: list[dict[str, Any]] = Field(default_factory=list)
    source: str = "auto"
    selected_ids: list[str] | None = None


class InboxPlanRequest(BaseModel):
    entries: list[dict[str, Any]] = Field(default_factory=list)
    run_id: str | None = None


class InboxApplyRequest(BaseModel):
    manifest: str = Field(..., min_length=1)
    confirm: bool = False
    batch_size: int = Field(default=DEFAULT_APPLY_BATCH_SIZE, ge=1, le=10000)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _entry_label(entry: dict[str, Any]) -> str:
    return (
        str(entry.get("manual_number") or entry.get("number") or entry.get("filename") or entry.get("source") or "")
        or "unknown"
    )


def _job_summary(entries: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "total": len(entries),
        "found_count": sum(1 for item in entries if item.get("status") == "found"),
        "failed_count": sum(1 for item in entries if item.get("status") in {"not_found", "search_failed"}),
        "needs_review_count": sum(
            1
            for item in entries
            if item.get("status") not in {"found", "identified", "planned", "moved", "queued", "searching"}
        ),
        "needs_number_count": sum(1 for item in entries if item.get("status") == "needs_number"),
    }


def _append_log(job: dict[str, Any], level: str, message: str, **extra: Any) -> None:
    logs = job.setdefault("logs", [])
    logs.append({"time": _utc_now(), "level": level, "message": message, **extra})
    if len(logs) > _MAX_LOG_LINES:
        del logs[:-_MAX_LOG_LINES]
    job["updated_at"] = _utc_now()


def _snapshot(job: dict[str, Any] | None) -> dict[str, Any]:
    if not job:
        return {"job": None}
    data = deepcopy(job)
    data["summary"] = _job_summary(data.get("entries", []))
    return {"job": data}


def _sync_search_job_after_apply(result: dict[str, Any]) -> None:
    """Remove successfully organized entries from the restored background result."""
    global _SEARCH_JOB
    if not _SEARCH_JOB:
        return
    moved_entries = [
        item for item in result.get("entries", [])
        if item.get("status") == "moved"
    ]
    if not moved_entries:
        return
    moved_ids = {str(item.get("id")) for item in moved_entries if item.get("id")}
    moved_sources = {str(item.get("source")) for item in moved_entries if item.get("source")}
    with _SEARCH_JOB_LOCK:
        entries = _SEARCH_JOB.get("entries") or []
        _SEARCH_JOB["entries"] = [
            item for item in entries
            if str(item.get("id")) not in moved_ids and str(item.get("source")) not in moved_sources
        ]
        _append_log(_SEARCH_JOB, "success", "organized_entries_removed", count=len(moved_entries))


def _target_ids(entries: list[dict[str, Any]], selected_ids: list[str] | None) -> set[str]:
    if selected_ids:
        return {str(item) for item in selected_ids}
    return {str(entry.get("id")) for entry in entries if entry.get("id")}


def _run_search_job(job: dict[str, Any]) -> None:
    entries = job["entries"]
    selected = set(job["selected_ids"])
    total = job["total"]
    source = job["source"]
    processed = 0
    _append_log(job, "info", "search_started", total=total, source=source)
    try:
        for index, entry in enumerate(entries):
            if str(entry.get("id")) not in selected:
                continue
            if job.get("cancel_requested"):
                entry["status"] = "canceled"
                entry["reason"] = "canceled"
                continue
            processed += 1
            label = _entry_label(entry)
            job["current"] = processed
            job["current_label"] = label
            entry["status"] = "searching"
            entry["reason"] = ""
            _append_log(job, "info", "searching", current=processed, total=total, label=label)
            try:
                result = search_inbox([entry], source=source)
                searched = (result.get("entries") or [entry])[0]
                entries[index] = searched
                if searched.get("status") == "found":
                    metadata = searched.get("metadata") or {}
                    _append_log(
                        job,
                        "success",
                        "found",
                        label=_entry_label(searched),
                        source=searched.get("source_id") or metadata.get("_source") or metadata.get("source") or source,
                        translation_status=metadata.get("_translation_status", ""),
                    )
                else:
                    _append_log(
                        job,
                        "warning",
                        searched.get("reason") or searched.get("status") or "not_found",
                        label=_entry_label(searched),
                    )
            except Exception:
                logger.exception("Inbox organizer background search item failed")
                entry["status"] = "search_failed"
                entry["reason"] = "search_failed"
                entry["metadata"] = None
                _append_log(job, "error", "search_failed", label=label)

        job["phase"] = "canceled" if job.get("cancel_requested") else "done"
        job["current_label"] = ""
        _append_log(job, "info", "search_canceled" if job["phase"] == "canceled" else "search_done")
    except Exception:
        logger.exception("Inbox organizer background search failed")
        job["phase"] = "failed"
        job["current_label"] = ""
        _append_log(job, "error", "search_failed")


def _raise_api_error(exc: Exception) -> None:
    if isinstance(exc, InboxOrganizerError):
        logger.warning("Inbox organizer request rejected", exc_info=True)
        code = str(exc) or "inbox_organizer_error"
        messages = {
            "confirmation_required": "請先確認整理本批影片",
            "gallery_not_configured": "尚未設定資料庫目錄",
            "invalid_root": "資料庫根目錄無效",
            "root_not_configured": "只能整理 OpenAver 設定中的資料庫目錄",
            "manifest_not_found": "找不到固定整理清單，請重新生成預覽",
            "invalid_manifest": "整理清單格式無效，請重新生成預覽",
            "target_exists": "目標檔案已存在，未覆蓋任何檔案",
        }
        raise HTTPException(
            status_code=400,
            detail={"code": code, "message": messages.get(code, "待整理工作流請求無效")},
        ) from exc
    logger.exception("Unexpected inbox organizer failure")
    raise HTTPException(status_code=500, detail="待整理工作流操作失敗") from exc


@router.get("/roots")
async def roots() -> dict[str, Any]:
    try:
        return await asyncio.to_thread(get_inbox_roots)
    except Exception as exc:
        _raise_api_error(exc)


@router.post("/inventory")
async def inventory(request: InboxRootRequest) -> dict[str, Any]:
    try:
        return await asyncio.to_thread(inventory_inbox, root=request.root)
    except Exception as exc:
        _raise_api_error(exc)


@router.post("/search")
async def search(request: InboxSearchRequest) -> dict[str, Any]:
    try:
        return await asyncio.to_thread(
            search_inbox,
            request.entries,
            source=request.source or "auto",
        )
    except Exception as exc:
        _raise_api_error(exc)


@router.post("/search-jobs")
async def start_search_job(request: InboxSearchRequest) -> dict[str, Any]:
    global _SEARCH_JOB, _SEARCH_THREAD
    with _SEARCH_JOB_LOCK:
        if _SEARCH_JOB and _SEARCH_JOB.get("phase") == "running":
            return _snapshot(_SEARCH_JOB)
        if _SEARCH_JOB and _SEARCH_JOB.get("phase") == "canceling":
            return _snapshot(_SEARCH_JOB)
        entries = [dict(item) for item in request.entries]
        selected = _target_ids(entries, request.selected_ids)
        for entry in entries:
            if str(entry.get("id")) in selected:
                entry["status"] = "queued"
                entry["reason"] = ""
        _SEARCH_JOB = {
            "job_id": uuid.uuid4().hex,
            "phase": "running",
            "source": request.source or "auto",
            "selected_ids": sorted(selected),
            "created_at": _utc_now(),
            "updated_at": _utc_now(),
            "current": 0,
            "total": len(selected),
            "current_label": "",
            "cancel_requested": False,
            "logs": [],
            "entries": entries,
        }
        _SEARCH_THREAD = threading.Thread(
            target=_run_search_job,
            args=(_SEARCH_JOB,),
            name=f"openaver-inbox-search-{_SEARCH_JOB['job_id'][:8]}",
            daemon=True,
        )
        _SEARCH_THREAD.start()
        return _snapshot(_SEARCH_JOB)


@router.get("/search-jobs/current")
async def current_search_job() -> dict[str, Any]:
    return _snapshot(_SEARCH_JOB)


@router.get("/search-jobs/{job_id}")
async def get_search_job(job_id: str) -> dict[str, Any]:
    if not _SEARCH_JOB or _SEARCH_JOB.get("job_id") != job_id:
        raise HTTPException(
            status_code=404,
            detail={"code": "job_not_found", "message": "找不到待整理背景搜尋任務"},
        )
    return _snapshot(_SEARCH_JOB)


@router.post("/search-jobs/{job_id}/cancel")
async def cancel_search_job(job_id: str) -> dict[str, Any]:
    if not _SEARCH_JOB or _SEARCH_JOB.get("job_id") != job_id:
        raise HTTPException(
            status_code=404,
            detail={"code": "job_not_found", "message": "找不到待整理背景搜尋任務"},
        )
    if _SEARCH_JOB.get("phase") == "running":
        _SEARCH_JOB["cancel_requested"] = True
        _SEARCH_JOB["phase"] = "canceling"
        _append_log(_SEARCH_JOB, "warning", "cancel_requested")
    return _snapshot(_SEARCH_JOB)


@router.post("/plan")
async def plan(request: InboxPlanRequest) -> dict[str, Any]:
    try:
        return await asyncio.to_thread(
            plan_inbox,
            request.entries,
            run_id=request.run_id,
        )
    except Exception as exc:
        _raise_api_error(exc)


@router.post("/offline-plan")
async def offline_plan(request: InboxPlanRequest) -> dict[str, Any]:
    try:
        return await asyncio.to_thread(
            offline_plan_inbox,
            request.entries,
            run_id=request.run_id,
        )
    except Exception as exc:
        _raise_api_error(exc)


@router.post("/apply")
async def apply(request: InboxApplyRequest) -> dict[str, Any]:
    try:
        result = await asyncio.to_thread(
            apply_inbox_manifest,
            request.manifest,
            confirm=request.confirm,
            batch_size=request.batch_size,
        )
        _sync_search_job_after_apply(result)
        return result
    except Exception as exc:
        _raise_api_error(exc)


@router.post("/rollback")
async def rollback(request: InboxApplyRequest) -> dict[str, Any]:
    try:
        return await asyncio.to_thread(
            rollback_inbox_manifest,
            request.manifest,
            confirm=request.confirm,
            batch_size=request.batch_size,
        )
    except Exception as exc:
        _raise_api_error(exc)
