"""
T60-2 / B1 regression — `POST /api/gallery/generate-from-ids` DB-miss scrape 路徑
須使用 scraper 回傳的 `tags` key（而非不存在的 `genres` key）建構 VideoInfo.genre。

Bug 來源：`web/routers/scanner.py:1243` 之前讀 `r.get('genres', [])` →
scrapers/models.py:46 實際 key 為 'tags' → VideoInfo.genre 永遠空字串 →
NFO `<genre>` 欄位空白（用戶可見）。

策略：捕獲傳給 HTMLGenerator.generate() 的 VideoInfo 物件，直接斷言 .genre 內容。
"""
from unittest.mock import MagicMock, patch


def _capture_videos_from_generate(mock_generator):
    """從 mock generator.generate(all_videos, ...) 呼叫中抽出 all_videos 參數。"""
    assert mock_generator.generate.called, "HTMLGenerator.generate() 未被呼叫"
    call_args = mock_generator.generate.call_args
    # signature: generate(all_videos, html_path, title=...)
    return call_args.args[0]


class TestScannerGenerateFromIdsTags:
    """DB-miss scrape 路徑必須讀取 scraper 的 'tags' key 填入 genre 欄位。"""

    def test_db_miss_tags_populated_into_genre(self, client, monkeypatch, tmp_path):
        """scraper 回 tags=['巨乳','OL']，VideoInfo.genre 應為 '巨乳,OL'。"""
        mock_repo = MagicMock()
        mock_repo.get_by_numbers.return_value = {}  # DB miss

        mock_generator = MagicMock()
        output_dir = tmp_path / "output"
        output_dir.mkdir()

        monkeypatch.setattr("web.routers.scanner.load_config", lambda: {
            "gallery": {"output_dir": str(output_dir), "path_mappings": {}},
            "general": {"theme": "light"}
        })

        scraper_result = {
            'number': 'SONE-100',
            'title': 'Scraped Title',
            'date': '2026-01-01',
            'tags': ['巨乳', 'OL', '單體作品'],
        }

        with patch('web.routers.scanner.VideoRepository', return_value=mock_repo), \
             patch('web.routers.scanner.HTMLGenerator', return_value=mock_generator), \
             patch('web.routers.scanner.smart_search', return_value=[scraper_result]):
            response = client.post(
                '/api/gallery/generate-from-ids',
                json={'numbers': ['SONE-100']}
            )

        assert response.status_code == 200
        videos = _capture_videos_from_generate(mock_generator)
        assert len(videos) == 1
        assert videos[0].genre == '巨乳,OL,單體作品'

    def test_db_miss_empty_tags_returns_empty_genre(self, client, monkeypatch, tmp_path):
        """scraper 回 tags=[]，VideoInfo.genre 應為空字串（不崩潰、不寫入殘渣）。"""
        mock_repo = MagicMock()
        mock_repo.get_by_numbers.return_value = {}

        mock_generator = MagicMock()
        output_dir = tmp_path / "output"
        output_dir.mkdir()

        monkeypatch.setattr("web.routers.scanner.load_config", lambda: {
            "gallery": {"output_dir": str(output_dir), "path_mappings": {}},
            "general": {"theme": "light"}
        })

        scraper_result = {
            'number': 'SONE-200',
            'title': 'No Tags',
            'date': '2026-02-01',
            'tags': [],
        }

        with patch('web.routers.scanner.VideoRepository', return_value=mock_repo), \
             patch('web.routers.scanner.HTMLGenerator', return_value=mock_generator), \
             patch('web.routers.scanner.smart_search', return_value=[scraper_result]):
            response = client.post(
                '/api/gallery/generate-from-ids',
                json={'numbers': ['SONE-200']}
            )

        assert response.status_code == 200
        videos = _capture_videos_from_generate(mock_generator)
        assert len(videos) == 1
        assert videos[0].genre == ''

    def test_db_hit_path_unaffected(self, client, monkeypatch, tmp_path):
        """DB-hit 路徑不走 scrape（regression guard：本修改不應影響 DB hit）。"""
        from core.database import Video
        from core.path_utils import to_file_uri

        video = Video(
            id=1, path=to_file_uri('/video/SONE-300.mp4'), title='DB Title',
            original_title='', actresses=[], number='SONE-300',
            maker='Sony', release_date='2026-03-01', tags=['DB標籤'],
            size_bytes=1000, mtime=0.0, cover_path='', nfo_mtime=None,
            director='', duration=None, series='', label=''
        )

        mock_repo = MagicMock()
        mock_repo.get_by_numbers.return_value = {'SONE-300': [video]}

        mock_generator = MagicMock()
        output_dir = tmp_path / "output"
        output_dir.mkdir()

        monkeypatch.setattr("web.routers.scanner.load_config", lambda: {
            "gallery": {"output_dir": str(output_dir), "path_mappings": {}},
            "general": {"theme": "light"}
        })

        with patch('web.routers.scanner.VideoRepository', return_value=mock_repo), \
             patch('web.routers.scanner.HTMLGenerator', return_value=mock_generator), \
             patch('web.routers.scanner.smart_search', return_value=[]) as mock_scrape:
            response = client.post(
                '/api/gallery/generate-from-ids',
                json={'numbers': ['SONE-300']}
            )

        assert response.status_code == 200
        mock_scrape.assert_not_called()
        videos = _capture_videos_from_generate(mock_generator)
        assert len(videos) == 1
        # DB-hit 走 v.tags（list）→ ','.join
        assert videos[0].genre == 'DB標籤'
