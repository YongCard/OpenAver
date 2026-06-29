from core.scrapers.stash import StashScraper


def test_stash_scene_maps_to_video(monkeypatch):
    def fake_graphql(self, query, variables=None):
        return {
            "findScenes": {
                "scenes": [{
                    "id": "42",
                    "title": "Work Those Curves",
                    "date": "2024-02-02",
                    "details": "Training scene",
                    "urls": ["https://example.test/scene/42"],
                    "paths": {"screenshot": "http://127.0.0.1:9999/screenshot.jpg"},
                    "studio": {"name": "The Real Workout"},
                    "performers": [{"name": "Octavia Red"}],
                    "tags": [{"name": "Workout"}],
                }]
            }
        }

    monkeypatch.setattr(StashScraper, "_graphql", fake_graphql)
    scraper = StashScraper(stash_config={"enabled": True, "url": "http://127.0.0.1:9999"})

    video = scraper.search("therealworkout.24.02.02.octavia.red.work.those.curves.mp4")

    assert video is not None
    assert video.number == "WEST-THEREALWORKOUT-20240202-OCTAVIA-RED"
    assert video.source == "stash"
    assert video.maker == "The Real Workout"
    assert video.actresses[0].name == "Octavia Red"
    assert video.cover_url.endswith("screenshot.jpg")
    assert video.tags == ["Workout"]


def test_stash_relative_screenshot_becomes_absolute(monkeypatch):
    def fake_graphql(self, query, variables=None):
        return {
            "findScenes": {
                "scenes": [{
                    "id": "42",
                    "title": "Work Those Curves",
                    "date": "2024-02-02",
                    "paths": {"screenshot": "/scene/42/screenshot"},
                    "studio": {"name": "The Real Workout"},
                    "performers": [{"name": "Octavia Red"}],
                    "tags": [],
                }]
            }
        }

    monkeypatch.setattr(StashScraper, "_graphql", fake_graphql)
    scraper = StashScraper(stash_config={"enabled": True, "url": "http://192.0.2.12:9999"})

    video = scraper.search("therealworkout.24.02.02.octavia.red.work.those.curves.mp4")

    assert video.cover_url == "http://192.0.2.12:9999/scene/42/screenshot"


def test_stash_west_number_uses_searchable_query(monkeypatch):
    captured = {}

    def fake_graphql(self, query, variables=None):
        captured.update(variables or {})
        return {
            "findScenes": {
                "scenes": [{
                    "id": "42",
                    "title": "Dylann Vox",
                    "date": "2019-08-28",
                    "paths": {"screenshot": "http://127.0.0.1:9999/screenshot.jpg"},
                    "studio": {"name": "BangBus"},
                    "performers": [{"name": "Dylann Vox"}],
                    "tags": [],
                }]
            }
        }

    monkeypatch.setattr(StashScraper, "_graphql", fake_graphql)
    scraper = StashScraper(stash_config={"enabled": True, "url": "http://127.0.0.1:9999"})

    video = scraper.search("WEST-BANGBUS-20190828-DYLANN-VOX")

    assert video.number == "WEST-BANGBUS-20190828-DYLANN-VOX"
    assert captured["filter"] == "BANGBUS 2019 08 28 DYLANN VOX"


def test_stash_search_by_filename_matches_basename(monkeypatch):
    calls = []

    def fake_graphql(self, query, variables=None):
        calls.append(query)
        return {
            "findScenes": {
                "scenes": [{
                    "id": "2",
                    "title": "Stripper With Double D's Hops on The Bus",
                    "date": "2019-08-28",
                    "paths": {"screenshot": "http://127.0.0.1:9999/scene/2/screenshot"},
                    "files": [{
                        "path": "/data/MediaShare/Media/Sample/欧美/bangbus.19.08.28.dylann.vox.mp4",
                        "basename": "bangbus.19.08.28.dylann.vox.mp4",
                    }],
                    "studio": {"name": "Bang Bus"},
                    "performers": [{"name": "Skylar Vox"}],
                    "tags": [{"name": "Bus"}],
                }]
            }
        }

    monkeypatch.setattr(StashScraper, "_graphql", fake_graphql)
    scraper = StashScraper(stash_config={"enabled": True, "url": "http://127.0.0.1:9999"})

    video = scraper.search_by_filename(
        "bangbus.19.08.28.dylann.vox.mp4",
        fallback_number="WEST-BANGBUS-20190828-DYLANN-VOX",
    )

    assert video.number == "WEST-BANGBUS-20190828-DYLANN-VOX"
    assert video.title.startswith("Stripper")
    assert video.maker == "Bang Bus"
    assert len(calls) == 1


def test_stash_search_by_filename_matches_case_insensitive_basename(monkeypatch):
    def fake_graphql(self, query, variables=None):
        return {
            "findScenes": {
                "scenes": [{
                    "id": "1",
                    "title": "Scene",
                    "date": "2015-03-13",
                    "paths": {"screenshot": "/scene/1/screenshot"},
                    "files": [{
                        "path": "/data/PublicBang.15.03.13.Lolly.Gartner.1080p-KTR.mp4",
                        "basename": "PublicBang.15.03.13.Lolly.Gartner.1080p-KTR.mp4",
                    }],
                    "studio": {"name": "Public Bang"},
                    "performers": [{"name": "Lolly Gartner"}],
                    "tags": [],
                }]
            }
        }

    monkeypatch.setattr(StashScraper, "_graphql", fake_graphql)
    scraper = StashScraper(stash_config={"enabled": True, "url": "http://127.0.0.1:9999"})

    video = scraper.search_by_filename("publicbang.15.03.13.lolly.gartner.1080p-ktr.mp4")

    assert video is not None
    assert video.title == "Scene"


def test_stash_search_by_filename_falls_back_to_keyword(monkeypatch):
    calls = []

    def fake_graphql(self, query, variables=None):
        calls.append(variables or {})
        if "perPage" in (variables or {}):
            return {"findScenes": {"scenes": []}}
        return {
            "findScenes": {
                "scenes": [{
                    "id": "3",
                    "title": "How to Beat the Heat",
                    "date": "2022-10-04",
                    "paths": {"screenshot": "http://127.0.0.1:9999/scene/3/screenshot"},
                    "files": [],
                    "studio": {"name": "I Made Porn"},
                    "performers": [{"name": "Octavia Red"}],
                    "tags": [],
                }]
            }
        }

    monkeypatch.setattr(StashScraper, "_graphql", fake_graphql)
    scraper = StashScraper(stash_config={"enabled": True, "url": "http://127.0.0.1:9999"})

    video = scraper.search_by_filename("imadeporn.22.10.04.octavia.red.how.to.beat.the.heat.mp4")

    assert video.title == "How to Beat the Heat"
    assert calls[0] == {"perPage": 100}
    assert "I Made Porn" not in calls[1].get("filter", "")


def test_stash_disabled_returns_none():
    scraper = StashScraper(stash_config={"enabled": False, "url": "http://127.0.0.1:9999"})

    assert scraper.search("bangbus.19.08.28.dylann.vox.mp4") is None


def test_stash_graphql_uses_proxy(monkeypatch):
    calls = {}

    class FakeResponse:
        status_code = 200

        def json(self):
            return {"data": {"version": {"version": "0.27.0"}}}

    def fake_post(*args, **kwargs):
        calls.update(kwargs)
        return FakeResponse()

    monkeypatch.setattr("core.scrapers.stash.requests.post", fake_post)
    scraper = StashScraper(stash_config={
        "enabled": True,
        "url": "http://127.0.0.1:9999",
        "proxy_url": "http://127.0.0.1:7890",
    })

    result = scraper.test_connection()

    assert result["success"] is True
    assert calls["proxies"] == {
        "http": "http://127.0.0.1:7890",
        "https": "http://127.0.0.1:7890",
    }


def test_stash_graphql_empty_proxy_disables_env_proxy(monkeypatch):
    calls = {}

    class FakeResponse:
        status_code = 200

        def json(self):
            return {"data": {"version": {"version": "0.27.0"}}}

    def fake_post(*args, **kwargs):
        calls.update(kwargs)
        return FakeResponse()

    monkeypatch.setattr("core.scrapers.stash.requests.post", fake_post)
    scraper = StashScraper(stash_config={
        "enabled": True,
        "url": "http://127.0.0.1:9999",
        "proxy_url": "",
    })

    result = scraper.test_connection()

    assert result["success"] is True
    assert calls["proxies"] == {"http": None, "https": None}


def test_stash_graphql_error_returns_code(monkeypatch):
    def fake_post(*args, **kwargs):
        raise Exception("boom")

    monkeypatch.setattr("core.scrapers.stash.requests.post", fake_post)
    scraper = StashScraper(stash_config={"enabled": True, "url": "http://127.0.0.1:9999"})

    result = scraper.test_connection()

    assert result["success"] is False
    assert result["code"] == "stash_request_failed"
    assert "Stash" in result["message"]
