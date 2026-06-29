"""
設定 API 路由

端點：
- GET    /api/config                    — 取得所有設定
- PUT    /api/config                    — 更新所有設定
- DELETE /api/config                    — 恢復原廠設定（刪除 config.json）
- GET    /api/tutorial-status           — 取得新手引導完成狀態
- POST   /api/tutorial-completed        — 標記新手引導已完成
- POST   /api/tutorial-reset            — 重置新手引導狀態
- PUT    /api/config/general/{field}    — 更新 general 區塊單一欄位（sidebar_collapsed/theme/font_size）
- GET    /api/version                   — 取得應用程式版本資訊
- GET    /api/config/format-variables   — 取得刮削路徑/檔名格式可用變數
- GET    /api/ollama/models             — 取得 Ollama 可用模型列表
- POST   /api/ollama/test               — 測試 Ollama 模型是否能正常回應
- POST   /api/proxy/test                — 測試 Proxy 連線（透過 DMM 驗證）
"""

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, StrictBool, StrictStr, ValidationError
import asyncio
import httpx
import threading
import uuid

from core.logger import get_logger
from core.config import (
    AppConfig,
    load_config,
    mutate_config,
    reset_config_file,
)
from core.source_config import MAX_ENABLED_SOURCES
from core.translate_service import LANGUAGE_PROMPTS

logger = get_logger(__name__)

router = APIRouter(prefix="/api", tags=["config"])

# 序列化「server_mode 切換交易」的單一鎖（Codex P2）：toggle（start/persist/stop）與
# reset（clear/stop）共用此鎖，避免併發 enable/disable/reset 交錯導致 config↔listener
# 分離（如 stale enable 在 disable 之後才寫回 true）。鎖序：本鎖 → _config_write_lock
# →lan_listener._lock（單向，無死鎖）。本機端點、執行於 threadpool，故用 threading.Lock。
_server_mode_toggle_lock = threading.Lock()

# Import reset function (避免循環導入，在需要時才導入)
def _reset_translate_service():
    """延遲導入並調用 reset_translate_service()"""
    from web.routers.translate import reset_translate_service
    reset_translate_service()


@router.get("/config")
def get_config() -> dict:
    """取得所有設定"""
    return {"success": True, "data": load_config()}


@router.put("/config")
async def update_config(request: Request):
    """更新所有設定"""
    try:
        raw_payload = await request.json()
        config = AppConfig.model_validate(raw_payload)
    except ValidationError as e:
        details = e.errors(include_input=False)
        logger.warning("儲存設定 payload 驗證失敗: %s", details)
        return JSONResponse(
            status_code=422,
            content={
                "success": False,
                "error": "設定內容格式錯誤",
                "code": "config_validation_failed",
                "details": details,
            },
        )
    except Exception as e:
        logger.warning("讀取設定 payload 失敗: %s", e)
        return JSONResponse(
            status_code=400,
            content={
                "success": False,
                "error": "設定內容格式錯誤",
                "code": "config_payload_invalid",
                "details": str(e),
            },
        )

    # Cap 守衛（CD-61-16，endpoint-level；非 model_validator）：
    # 同時啟用且非 manual_only 的來源數不得超過 MAX_ENABLED_SOURCES。
    # 防止前端繞過 UI 直接 PUT。manual_only 不計入 cap basis（CD-61-17）。
    cap_basis = sum(1 for s in config.sources if s.enabled and not s.manual_only)
    if cap_basis > MAX_ENABLED_SOURCES:
        return JSONResponse(
            status_code=400,
            content={
                "success": False,
                "error": "啟用來源數超過上限",
                "code": "cap_exceeded",
                "details": {"max": MAX_ENABLED_SOURCES},
            },
        )
    try:
        payload = config.model_dump()
        # server_mode is toggle-lifecycle state owned exclusively by
        # PUT /api/config/general/server_mode (which calls lan_listener.start/stop
        # atomically with the persist). A full-config save must NEVER overwrite the
        # currently-persisted value — whether the incoming payload omits the field
        # (Pydantic default → False) or contains a stale/incorrect value.
        # Read the canonical persisted value inside mutate_config so the
        # read-preserve-write is atomic under _config_write_lock.
        def _write_preserving_server_mode(cfg: dict) -> None:
            current_server_mode = cfg.get("general", {}).get("server_mode", False)
            payload["general"]["server_mode"] = current_server_mode
            _dedupe_gallery_directories(payload)
            cfg.update(payload)

        mutate_config(_write_preserving_server_mode)
        _reset_translate_service()  # 重置翻譯服務，讓新配置生效
        return {"success": True, "message": "設定已儲存"}
    except Exception as e:
        logger.exception("儲存設定失敗")
        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "error": "儲存設定失敗",
                "code": "config_save_failed",
                "details": str(e),
            },
        )


@router.delete("/config")
def reset_config() -> dict:
    """恢復原廠設定 - 刪除 config.json"""
    try:
        # reset 清除 server_mode（defaults → false）→ listener 必須同步停止，否則
        # runtime（listener 跑）≠ persisted 分離。clear+stop 與 toggle 交易共用
        # _server_mode_toggle_lock 序列化（Codex P2），防 reset 與併發 enable 交錯。
        # stop() idempotent：listener 未跑時 no-op，安全。
        from web.lan_listener import lan_listener
        with _server_mode_toggle_lock:
            reset_config_file()  # 鎖內 exists/unlink，無 TOCTOU（CD-66b-1）
            lan_listener.stop()
        _reset_translate_service()  # 清除舊服務實例（與 server_mode 無關，鎖外）
        return {"success": True, "message": "已恢復預設設定"}
    except Exception as e:
        logger.error("恢復預設設定失敗: %s", e)
        return {"success": False, "error": "恢復預設設定失敗"}


@router.get("/tutorial-status")
def get_tutorial_status() -> dict:
    """取得新手引導完成狀態"""
    config = load_config()
    completed = config.get("general", {}).get("tutorial_completed", False)
    return {"success": True, "completed": completed}


@router.post("/tutorial-completed")
def mark_tutorial_completed() -> dict:
    """標記新手引導已完成（僅在點擊完成時呼叫）"""
    def _mut(cfg):
        cfg.setdefault("general", {})["tutorial_completed"] = True
    mutate_config(_mut)
    return {"success": True}


@router.post("/tutorial-reset")
def reset_tutorial() -> dict:
    """重置新手引導狀態（供設定頁使用）"""
    def _mut(cfg):
        cfg.setdefault("general", {})["tutorial_completed"] = False
    mutate_config(_mut)
    return {"success": True}


class GeneralFieldRequest(BaseModel):
    # StrictBool | StrictStr：strict union 不做型別強制 —— JSON true/false → bool，
    # 字串 → str，數字（如 1）兩者皆不接受 → Pydantic 422。藉此讓 server_mode 的整數
    # 輸入在 schema 層即被擋（避免 `1` 被 lax 模式悄悄轉成 True）。既有 str/bool 欄位
    # （theme/locale/font_size/sidebar_collapsed）值型別本就符合，無回歸。
    value: StrictBool | StrictStr


class StashTestRequest(BaseModel):
    url: StrictStr
    api_key: StrictStr = ""
    proxy_url: StrictStr = ""


class NasShareRequest(BaseModel):
    id: StrictStr = ""
    name: StrictStr = ""
    host: StrictStr
    share: StrictStr
    subpath: StrictStr = ""
    username: StrictStr = ""
    password: StrictStr = ""
    enabled: StrictBool = True
    add_to_gallery: StrictBool = True


@router.post("/settings/stash/test")
def test_stash_connection(request: StashTestRequest) -> dict:
    """測試 Stash GraphQL 連線，不寫入設定。"""
    from core.scrapers.stash import StashScraper

    scraper = StashScraper(stash_config={
        "enabled": True,
        "url": request.url,
        "api_key": request.api_key,
        "proxy_url": request.proxy_url,
    })
    return scraper.test_connection()


def _nas_share_payload(request: NasShareRequest, share_id: str | None = None, last_status: str = "") -> dict:
    from core.nas import build_unc_path, credential_target

    sid = share_id or request.id or uuid.uuid4().hex
    name = request.name.strip() or f"{request.host.strip()}\\{request.share.strip()}"
    return {
        "id": sid,
        "name": name,
        "host": request.host.strip().strip("\\/"),
        "share": request.share.strip().strip("\\/"),
        "subpath": request.subpath.strip().replace("/", "\\").strip("\\"),
        "username": request.username.strip(),
        "enabled": request.enabled,
        "add_to_gallery": request.add_to_gallery,
        "last_status": last_status,
        "credential_target": credential_target(request.host),
        "unc_path": build_unc_path(request.host, request.share, request.subpath),
    }


def _dedupe_gallery_directories(cfg: dict) -> None:
    from core.library_categories import dedupe_scan_directories

    gallery = cfg.setdefault("gallery", {})
    dirs = gallery.setdefault("directories", [])
    if isinstance(dirs, list):
        gallery["directories"] = dedupe_scan_directories(dirs, cfg)


@router.post("/settings/nas/test")
def test_nas_connection(request: NasShareRequest) -> dict:
    """測試 NAS 連線；password 只作為一次性輸入，不寫入 config。"""
    from core.nas import test_share

    result = test_share(
        request.host,
        request.share,
        request.subpath,
        request.username,
        request.password,
    )
    payload = result.to_dict()
    return {**payload, "share": _nas_share_payload(request, last_status=result.code)}


@router.post("/settings/nas/save")
def save_nas_share(request: NasShareRequest) -> dict:
    """保存 NAS share metadata，Windows 下可同步保存 Credential Manager secret。"""
    from core.nas import build_unc_path, save_windows_credential

    credential_result = None
    if request.password:
        credential_result = save_windows_credential(request.host, request.username, request.password)
        if not credential_result.success:
            return credential_result.to_dict()

    share_id = request.id or uuid.uuid4().hex
    payload = _nas_share_payload(request, share_id=share_id, last_status="saved")
    # Internal-only display helpers must not be persisted in config.
    persisted = {k: v for k, v in payload.items() if k not in {"unc_path"}}

    def _mut(cfg: dict) -> None:
        nas = cfg.setdefault("nas", {})
        shares = nas.setdefault("shares", [])
        shares[:] = [
            s for s in shares
            if isinstance(s, dict) and s.get("id") != share_id
        ]
        shares.append(persisted)
        if request.add_to_gallery:
            gallery = cfg.setdefault("gallery", {})
            dirs = gallery.setdefault("directories", [])
            unc_path = build_unc_path(request.host, request.share, request.subpath)
            if unc_path and unc_path not in dirs:
                dirs.append(unc_path)
            _dedupe_gallery_directories(cfg)

    mutate_config(_mut)
    return {
        "success": True,
        "code": "ok",
        "message": "NAS 設定已保存",
        "share": payload,
        "credential_saved": bool(credential_result and credential_result.success),
    }


@router.delete("/settings/nas/{share_id}")
def delete_nas_share(share_id: str) -> dict:
    """移除 OpenAver 保存的 NAS metadata；不刪 Credential Manager 或媒體檔案。"""
    removed = False

    def _mut(cfg: dict) -> None:
        nonlocal removed
        nas = cfg.setdefault("nas", {})
        shares = nas.setdefault("shares", [])
        if not isinstance(shares, list):
            nas["shares"] = []
            return
        before = len(shares)
        shares[:] = [
            s for s in shares
            if not (isinstance(s, dict) and s.get("id") == share_id)
        ]
        removed = len(shares) != before

    mutate_config(_mut)
    return {
        "success": True,
        "code": "ok",
        "removed": removed,
        "message": "NAS 設定已移除" if removed else "NAS 設定不存在",
    }


@router.post("/settings/nas/connect")
def connect_nas_share(request: NasShareRequest) -> dict:
    """連接 NAS share。若未傳 password，Windows 會使用 Credential Manager / 既有 session。"""
    from core.nas import ensure_share_connected

    result = ensure_share_connected(
        request.host,
        request.share,
        request.username,
        request.password,
    )
    return result.to_dict()


@router.get("/settings/nas/status")
def get_nas_status() -> dict:
    """取得目前 config 中 NAS 條目的可讀狀態。"""
    from core.nas import status_for_share

    config = load_config()
    shares = config.get("nas", {}).get("shares", [])
    statuses = [
        status_for_share(s)
        for s in shares
        if isinstance(s, dict)
    ]
    return {"success": True, "data": statuses}


@router.put("/config/general/{field}")
def update_general_field(field: str, request: GeneralFieldRequest, raw_request: Request) -> dict:
    """更新 general 區塊單一欄位（輕量端點，供 UI toggle 即時同步）

    註：保持同步 def —— body 內 mutate_config 走檔案 I/O，依 async-offload 守衛
    （feature/71）須在 Starlette threadpool 執行，不可改 async def 卡 event loop。
    """
    allowed = {"sidebar_collapsed", "theme", "font_size", "locale", "server_mode"}
    if field not in allowed:
        return {"success": False, "error": f"不允許更新欄位: {field}"}
    try:
        # server_mode 嚴格 bool 驗正（TASK-80a-T1）：StrictBool|StrictStr 已擋整數，
        # 此處再擋字串 —— 關鍵安全性：字串 "false" 是 truthy，若存進 config 會讓
        # middleware `bool(server_mode)` 誤判為開啟對外。非 bool 字串 → 400。
        if field == "server_mode" and not isinstance(request.value, bool):
            raise HTTPException(status_code=400, detail="server_mode 必須為布林值")

        # server_mode 是主機決定，遠端連入的客人不得切換（spec「遠端自鎖不防護」的更乾淨版本）。
        # 僅允許 loopback 來源切換；fail-closed：client None → 視為非 loopback → 拒絕。
        # 不信任 X-Forwarded-For，純用 TCP 對端 raw_request.client.host。
        if field == "server_mode":
            _client = raw_request.client
            _client_host = _client.host if _client else None
            if _client_host not in ("127.0.0.1", "::1"):
                logger.warning(
                    "拒絕非本機切換 server_mode（來源 %s）：僅主機可切換", _client_host
                )
                # reason 供前端對應專屬 i18n 訊息（remote_only），而非通用「請稍後再試」
                return {"success": False, "reason": "remote_forbidden",
                        "error": "server_mode 僅能在主機本機切換"}

        # server_mode 專屬分支：start/stop LAN listener，確保 runtime ≠ persisted 不分離。
        # 整個交易在 _server_mode_toggle_lock 內序列化（Codex P2）：防併發 enable/disable
        # /reset 交錯（如 stale enable 在 disable 之後才寫回 true → config 開、listener 關）。
        if field == "server_mode":
            from web.lan_listener import lan_listener
            with _server_mode_toggle_lock:
                if request.value is True:
                    # Enable 順序：先 start()（失敗→乾淨回傳，config 不變），再 persist。
                    # persist 失敗→ rollback stop()（best-effort）避免 listener ON / config false。
                    try:
                        lan_port = lan_listener.start()
                    except Exception as e:                    # noqa: BLE001
                        logger.error("server_mode 啟用失敗: %s", e)
                        return {"success": False, "error": "無法啟動 LAN 伺服器"}
                    try:
                        mutate_config(lambda cfg: cfg.setdefault("general", {}).update({"server_mode": True}))
                    except Exception as e:                    # noqa: BLE001
                        # Rollback：listener 已啟動但 config 寫入失敗 → 停止 listener 保持一致
                        logger.error("server_mode persist 失敗，rollback stop(): %s", e)
                        try:
                            lan_listener.stop()
                        except Exception:                     # noqa: BLE001,S110 — rollback best-effort
                            pass
                        return {"success": False, "error": "無法儲存伺服器模式設定"}
                    from web.lan_listener import get_lan_ip
                    lan_ip = get_lan_ip()
                    return {"success": True, "lan_port": lan_port, "lan_ip": lan_ip}
                else:
                    # Disable 順序：先 persist false，再 stop()。
                    # 原因：middleware lan_access_gate 讀 load_config().get("server_mode")，
                    # persist false 後 gate 立即阻擋新 LAN 連線（defense-in-depth），
                    # 即使 stop() 尚未完成也已安全。若 persist 失敗 → config 仍 true，
                    # listener 仍跑，兩者一致（不需 rollback）。
                    try:
                        mutate_config(lambda cfg: cfg.setdefault("general", {}).update({"server_mode": False}))
                    except Exception as e:                    # noqa: BLE001
                        logger.error("server_mode disable persist 失敗: %s", e)
                        return {"success": False, "error": "無法儲存伺服器模式設定"}
                    lan_listener.stop()
                    return {"success": True, "lan_port": None}

        # locale 驗證在 mutate 前（保留既有順序：驗證 → 寫入 → translate reset）
        if field == "locale" and request.value not in ("zh-TW", "zh-CN", "ja", "en"):
            logger.warning("嘗試設定不支援的語系: %s", request.value)
            return {"success": False, "error": "不支援的語系"}

        def _mut(cfg):
            cfg.setdefault("general", {})[field] = request.value
        mutate_config(_mut)

        if field == "locale":
            _reset_translate_service()
        return {"success": True}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("更新設定欄位失敗: %s", e)
        return {"success": False, "error": "更新設定欄位失敗"}


@router.get("/config/general/lan-port")
def get_lan_port() -> dict:
    """取得 LAN listener 目前使用的 port + LAN IP（server mode 啟用中回值，否則 null）

    lan_ip 獨立於 listener 狀態：IP 可偵測→回真值，IP 不可偵測→回 null。
    搭配前端 `?? null` 清除邏輯：listener 停止但 IP 可偵測 → lanIp 保留、lanPort
    null → 顯示「listener_down」；IP 真的無法偵測 → lanIp null → 顯示「no_lan_ip」。
    """
    from web.lan_listener import lan_listener, get_lan_ip
    running = lan_listener.is_running
    return {
        "lan_port": lan_listener.lan_port if running else None,
        "lan_ip": get_lan_ip(),  # 獨立於 running：null 僅在 IP 真的無法偵測時
    }


@router.get("/version", operation_id="get_config_version")
async def get_version() -> dict:
    """取得版本資訊"""
    from core.version import VERSION_INFO
    return {"success": True, **VERSION_INFO}


@router.get("/config/format-variables")
async def get_format_variables() -> dict:
    """取得可用的格式變數"""
    return {
        "variables": [
            {"name": "{num}", "description": "番號", "example": "SONE-205"},
            {"name": "{title}", "description": "標題", "example": "新人出道..."},
            {"name": "{actor}", "description": "演員（第一位）", "example": "三上悠亜"},
            {"name": "{actors}", "description": "所有演員", "example": "三上悠亜, 明日花"},
            {"name": "{maker}", "description": "片商", "example": "S1"},
            {"name": "{date}", "description": "發行日期", "example": "2024-01-15"},
            {"name": "{year}", "description": "年份", "example": "2024"},
            {"name": "{month}", "description": "月份（2位）", "example": "01"},
            {"name": "{day}", "description": "日（2位）", "example": "15"},
            {"name": "{suffix}", "description": "版本標記（自動偵測）", "example": "-4k"},
        ]
    }


@router.get("/ollama/models")
async def get_ollama_models(url: str) -> dict:
    """取得 Ollama 可用模型列表"""
    try:
        # 確保 URL 格式正確
        url = url.rstrip('/')
        api_url = f"{url}/api/tags"

        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(api_url)
            resp.raise_for_status()
            data = resp.json()

            models = [m['name'] for m in data.get('models', [])]
            return {"success": True, "models": models}

    except httpx.TimeoutException:
        return {"success": False, "error": "連線逾時"}
    except httpx.ConnectError:
        return {"success": False, "error": "無法連線到 Ollama"}
    except Exception as e:
        logger.error("取得 Ollama 模型列表失敗: %s", e)
        return {"success": False, "error": "取得模型列表失敗"}


class OllamaTestRequest(BaseModel):
    url: str
    model: str


@router.post("/ollama/test")
async def test_ollama_model(request: OllamaTestRequest) -> dict:
    """測試 Ollama 模型是否能正常回應"""
    try:
        url = request.url.rstrip('/')

        config = await asyncio.to_thread(load_config)
        locale = config.get("general", {}).get("locale", "zh-TW")
        lang_config = LANGUAGE_PROMPTS.get(locale, LANGUAGE_PROMPTS["zh-TW"])
        lang_name = lang_config["name"]

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{url}/api/chat",
                json={
                    "model": request.model,
                    "messages": [
                        {"role": "user", "content": f"將以下日文翻譯成{lang_name}，只輸出翻譯結果：新人女優デビュー"}
                    ],
                    "stream": False,
                    "options": {
                        "num_predict": 500,  # think mode 需要足夠 token（翻譯本身 ~20 tok，其餘供推理）
                        "temperature": 0.3
                    }
                }
            )
            resp.raise_for_status()
            data = resp.json()

            result = data.get("message", {}).get("content", "").strip()
            if result:
                return {"success": True, "result": f"回應：{result}"}
            else:
                return {"success": False, "error": "模型無回應"}

    except httpx.TimeoutException:
        return {"success": False, "error": "連線逾時"}
    except httpx.ConnectError:
        return {"success": False, "error": "無法連線到 Ollama"}
    except Exception as e:
        logger.error("測試 Ollama 模型失敗: %s", e)
        return {"success": False, "error": "測試模型失敗"}


class ProxyTestRequest(BaseModel):
    proxy_url: str


@router.post("/proxy/test")
def test_proxy(request: ProxyTestRequest) -> dict:
    """測試 Proxy 連線（透過 DMM GraphQL endpoint 驗證）"""
    import requests

    is_direct = request.proxy_url.strip().lower() == 'direct'
    if is_direct:
        proxies = {'http': None, 'https': None}
        success_message = "直連測試成功（DMM 可達）"
        non_jp_message = "直連可達，但 DMM 回傳 403（非日本 IP，建議使用 Proxy）"
    else:
        proxies = {'http': request.proxy_url, 'https': request.proxy_url}
        success_message = "Proxy 連線成功（DMM 可達）"
        non_jp_message = "Proxy 連線成功，但 DMM 回傳 403（可能非日本 IP）"

    try:
        resp = requests.post(
            "https://api.video.dmm.co.jp/graphql",
            json={"query": "{ __typename }"},
            headers={"Content-Type": "application/json"},
            proxies=proxies,
            timeout=10
        )
        if resp.status_code == 200:
            return {"success": True, "reason": "ok", "message": success_message}
        elif resp.status_code == 403:
            return {"success": False, "reason": "non_jp", "message": non_jp_message}
        else:
            return {"success": False, "reason": "unexpected_status", "message": f"DMM 回傳異常狀態碼: {resp.status_code}"}
    except requests.exceptions.Timeout:
        return {"success": False, "reason": "unreachable", "message": "連線失敗: 連線逾時"}
    except requests.exceptions.ConnectionError:
        return {"success": False, "reason": "unreachable", "message": "連線失敗: 無法連線到目標主機"}
    except Exception:
        return {"success": False, "reason": "unreachable", "message": "連線失敗: 請檢查輸入格式"}
