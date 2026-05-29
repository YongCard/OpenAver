"""TASK-61c-5: GET /api/scraper-sources 端點測試。

過濾 = Rule 3：enabled=True AND is_beta=False AND manual_only=False AND
available=True。複用 get_enabled_source_ids（含 manual_only + available gate），
端點額外排除 is_beta。B1 availability_map=None（不 gate metatube）。

注入自訂 sources 用 PUT /api/config → GET /api/scraper-sources 斷言過濾結果。
"""


def _base_config(client):
    return client.get("/api/config").json()["data"]


SOURCE_FIELDS = {"id", "display_name", "type", "enabled", "order", "is_censored"}


def test_200_and_schema(client, temp_config_path):
    """200 + response schema：sources list 每筆含 6 欄位 + total_enabled int。"""
    resp = client.get("/api/scraper-sources")
    assert resp.status_code == 200
    data = resp.json()

    assert "sources" in data and isinstance(data["sources"], list)
    assert "total_enabled" in data and isinstance(data["total_enabled"], int)
    for s in data["sources"]:
        assert set(s.keys()) == SOURCE_FIELDS
        assert isinstance(s["id"], str)
        assert isinstance(s["display_name"], str)
        assert isinstance(s["type"], str)
        assert isinstance(s["enabled"], bool)
        assert isinstance(s["order"], int)
        assert isinstance(s["is_censored"], bool)


def test_default_config_all_eight_builtin(client, temp_config_path):
    """B1 availability_map=None → 8 個預設 builtin（全 enabled、非 beta、非 manual）皆現。"""
    data = client.get("/api/scraper-sources").json()
    ids = [s["id"] for s in data["sources"]]
    assert len(ids) == 8
    assert data["total_enabled"] == 8
    # builtin 全 enabled
    assert all(s["enabled"] is True for s in data["sources"])
    # 依 order 升冪
    orders = [s["order"] for s in data["sources"]]
    assert orders == sorted(orders)


def test_disabled_source_not_in_response(client, temp_config_path):
    """enabled=false 的 source 不出現。"""
    cfg = _base_config(client)
    target_id = cfg["sources"][0]["id"]
    cfg["sources"][0]["enabled"] = False
    assert client.put("/api/config", json=cfg).status_code == 200

    data = client.get("/api/scraper-sources").json()
    ids = [s["id"] for s in data["sources"]]
    assert target_id not in ids
    assert data["total_enabled"] == 7


def test_beta_source_not_in_response(client, temp_config_path):
    """is_beta=true 的 source 不出現（即使 enabled=true）。"""
    cfg = _base_config(client)
    cfg["sources"].append(
        {
            "id": "mt_beta",
            "type": "metatube",
            "display_name_key": "",
            "display_name_raw": "Beta Source",
            "enabled": True,
            "order": len(cfg["sources"]),
            "config": {"censored_type": "censored"},
            "is_beta": True,
            "manual_only": False,
        }
    )
    assert client.put("/api/config", json=cfg).status_code == 200

    data = client.get("/api/scraper-sources").json()
    ids = [s["id"] for s in data["sources"]]
    assert "mt_beta" not in ids
    # 8 builtin 仍在
    assert data["total_enabled"] == 8


def test_manual_only_source_not_in_response(client, temp_config_path):
    """manual_only=true 的 source 不出現（B4 預埋；B1 用構造 config 驗證）。"""
    cfg = _base_config(client)
    cfg["sources"].append(
        {
            "id": "mt_manual",
            "type": "metatube",
            "display_name_key": "",
            "display_name_raw": "Manual Only Source",
            "enabled": True,
            "order": len(cfg["sources"]),
            "config": {"censored_type": "censored"},
            "is_beta": False,
            "manual_only": True,
        }
    )
    assert client.put("/api/config", json=cfg).status_code == 200

    data = client.get("/api/scraper-sources").json()
    ids = [s["id"] for s in data["sources"]]
    assert "mt_manual" not in ids
    assert data["total_enabled"] == 8


def test_availability_map_none_metatube_appears(client, temp_config_path):
    """B1 default（availability_map=None，無 gate）→ enabled 非 beta 非 manual 的
    metatube source DOES appear，證明 B1 gate 關閉。"""
    cfg = _base_config(client)
    cfg["sources"].append(
        {
            "id": "mt_active",
            "type": "metatube",
            "display_name_key": "",
            "display_name_raw": "Active Metatube",
            "enabled": True,
            "order": len(cfg["sources"]),
            "config": {"censored_type": "uncensored"},
            "is_beta": False,
            "manual_only": False,
        }
    )
    assert client.put("/api/config", json=cfg).status_code == 200

    data = client.get("/api/scraper-sources").json()
    by_id = {s["id"]: s for s in data["sources"]}
    assert "mt_active" in by_id
    assert by_id["mt_active"]["display_name"] == "Active Metatube"  # render_name → display_name_raw
    assert by_id["mt_active"]["is_censored"] is False  # derive from config censored_type
    assert data["total_enabled"] == 9
