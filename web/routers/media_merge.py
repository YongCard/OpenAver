"""Video merge API backed by local FFmpeg."""

import asyncio
import json
import os
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from core.config import load_config, mutate_config
from core.media_merger import MediaMergeError, merge_videos, preview_merge, resolve_ffmpeg
from core.logger import get_logger

logger = get_logger(__name__)
router = APIRouter(prefix="/api/media-merge", tags=["media-merge"])


class MergePreviewRequest(BaseModel):
    paths: list[str] = Field(default_factory=list)


class MergeRunRequest(BaseModel):
    paths: list[str] = Field(default_factory=list)
    output_path: str | None = None
    overwrite: bool = False
    cleanup_sources: bool = False
    cleanup_sidecars: bool | None = None
    remember_cleanup: bool = False


def _raise_media_error(exc: Exception) -> None:
    if isinstance(exc, MediaMergeError):
        code = str(exc) or "media_merge_error"
        messages = {
            "ffmpeg_not_found": "找不到 FFmpeg。请将 ffmpeg.exe 放在 tools/ffmpeg/bin，或安装到系统 PATH。",
            "input_not_found": "输入视频不存在",
            "input_not_video": "输入文件不是已支持的视频格式",
            "too_few_inputs": "请至少选择两个视频片段",
            "too_many_inputs": "一次最多合并 9 个片段",
            "output_exists": "输出文件已存在",
            "output_matches_input": "输出文件不能覆盖源分段视频",
            "output_permission_denied": "没有输出目录写入权限，请换一个输出位置或检查目录权限",
            "ffprobe_not_found": "找不到 ffprobe，无法验证合并结果",
            "ffprobe_failed": "ffprobe 验证合并结果失败",
            "output_missing": "合并后的输出文件不存在或为空",
            "output_no_video": "合并后的文件没有视频流",
            "duration_mismatch": "合并后时长和源分段总时长不一致",
            "ffmpeg_failed": "FFmpeg 合并失败，请确认分段编码一致",
        }
        detail = {"code": code, "message": messages.get(code, "视频合并请求无效")}
        log_tail = getattr(exc, "log_tail", "")
        if log_tail:
            detail["log_tail"] = log_tail
        raise HTTPException(
            status_code=400,
            detail=detail,
        ) from exc
    logger.exception("Unexpected media merge failure")
    raise HTTPException(status_code=500, detail="视频合并失败") from exc


def _media_error_payload(exc: Exception) -> dict[str, Any]:
    if isinstance(exc, MediaMergeError):
        try:
            _raise_media_error(exc)
        except HTTPException as http_exc:
            return http_exc.detail if isinstance(http_exc.detail, dict) else {"code": str(exc), "message": str(http_exc.detail)}
    logger.exception("Unexpected media merge failure")
    return {
        "code": "media_merge_failed",
        "message": "视频合并失败",
        "log_tail": f"{exc.__class__.__name__}: {exc}",
    }


def _sse_event(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _remember_cleanup_preference(value: bool) -> None:
    def _save_cleanup(cfg: dict) -> None:
        cfg.setdefault("media_merge", {})["cleanup_sources_default"] = bool(value)

    mutate_config(_save_cleanup)


@router.get("/ffmpeg")
async def ffmpeg_status() -> dict[str, Any]:
    data = await asyncio.to_thread(resolve_ffmpeg)
    config = load_config()
    media_config = config.get("media_merge", {}) if isinstance(config, dict) else {}
    data["cleanup_sources_default"] = media_config.get("cleanup_sources_default") is True
    data["cleanup_supported"] = os.name == "nt"
    return data


@router.post("/preview")
async def preview(request: MergePreviewRequest) -> dict[str, Any]:
    try:
        config = load_config()
        data = await asyncio.to_thread(preview_merge, request.paths, config)
        return {"success": True, "data": data}
    except Exception as exc:
        _raise_media_error(exc)


@router.post("/run")
async def run(request: MergeRunRequest) -> dict[str, Any]:
    try:
        config = load_config()
        data = await asyncio.to_thread(
            merge_videos,
            request.paths,
            request.output_path,
            config,
            overwrite=request.overwrite,
            cleanup_sources=request.cleanup_sources,
            cleanup_sidecars=request.cleanup_sidecars,
        )
        if request.remember_cleanup:
            await asyncio.to_thread(_remember_cleanup_preference, request.cleanup_sources)
        return {"success": True, "data": data}
    except Exception as exc:
        _raise_media_error(exc)


@router.post("/run-stream")
async def run_stream(request: MergeRunRequest) -> StreamingResponse:
    async def event_stream():
        queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        loop = asyncio.get_running_loop()

        def emit(event: dict[str, Any]) -> None:
            loop.call_soon_threadsafe(queue.put_nowait, event)

        def worker() -> None:
            try:
                config = load_config()
                result = merge_videos(
                    request.paths,
                    request.output_path,
                    config,
                    overwrite=request.overwrite,
                    cleanup_sources=request.cleanup_sources,
                    cleanup_sidecars=request.cleanup_sidecars,
                    progress_callback=emit,
                )
                if request.remember_cleanup:
                    _remember_cleanup_preference(request.cleanup_sources)
                emit({"type": "done", "data": result})
            except Exception as exc:
                emit({"type": "error", **_media_error_payload(exc)})
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, None)

        task = asyncio.create_task(asyncio.to_thread(worker))
        try:
            while True:
                event = await queue.get()
                if event is None:
                    break
                yield _sse_event(event)
        finally:
            await task

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )
