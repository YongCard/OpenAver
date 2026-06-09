"""
web/routers/cf.py — TASK-70-T6
================================
CF transport 控制端點（與 scraper router 語意正交）。

端點：
  GET  /api/cf/status?key=javlibrary  — poll loop 用：transport 是否就緒
  POST /api/cf/abandon?key=javlibrary — 前端逾時/取消；後端 emit 通知
"""
from fastapi import APIRouter

from core.cf_transport import get_cf_transport
from web.routers.notifications import emit_notification

router = APIRouter(prefix="/api/cf", tags=["cf"])


@router.get("/status")
def cf_status(key: str = "javlibrary") -> dict:
    """poll loop 用：transport 是否就緒。key 通用，不寫死 javlibrary。"""
    t = get_cf_transport()
    if t is None:
        return {"ready": False, "unavailable": True}
    try:
        return {"ready": bool(t.is_ready(key))}
    except Exception:
        return {"ready": False}


@router.post("/abandon")
def cf_abandon(key: str = "javlibrary") -> dict:
    """前端 poll 逾時/取消時呼叫；後端代 emit_notification（前端無直接通道）。"""
    emit_notification(
        "warn",
        "notif.jl_cf_timeout",
        message="CF 驗證未在時限內完成，本次 JavLibrary 抓取取消。",
        task_type="cf_abandon",
    )
    return {"ok": True}
