from core.database import Video, VideoRepository, init_db
from core.duplicate_numbers import find_duplicate_numbers


def _repo(tmp_path) -> VideoRepository:
    db_path = tmp_path / "openaver.db"
    init_db(db_path)
    return VideoRepository(db_path)


def _add(repo: VideoRepository, path: str, number: str | None, *, size: int = 0) -> None:
    repo.upsert(Video(path=path, number=number, size_bytes=size, mtime=100.0))


def test_duplicate_plain_files_are_reported(tmp_path):
    repo = _repo(tmp_path)
    first = tmp_path / "library" / "ABC-123.mp4"
    second = tmp_path / "library" / "ABC-123-copy.mp4"
    first.parent.mkdir()
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    _add(repo, str(first), "ABC-123")
    _add(repo, str(second), "ABC-123")

    result = find_duplicate_numbers(repo=repo, include_missing_paths=False)

    assert result["summary"]["duplicate_group_count"] == 1
    assert result["groups"][0]["number"] == "ABC-123"
    assert result["groups"][0]["classification"] == "duplicate"
    assert result["groups"][0]["reason"] == "multiple_unlabeled_files"
    items = result["groups"][0]["items"]
    assert [item["show_open_folder"] for item in items] == [True, False]
    assert all(item["delete_allowed"] for item in items)


def test_complementary_multipart_is_hidden_by_default(tmp_path):
    repo = _repo(tmp_path)
    _add(repo, "/library/ABC-123-CD1.mp4", "ABC-123")
    _add(repo, "/library/ABC-123-CD2.mp4", "ABC-123")

    result = find_duplicate_numbers(repo=repo, include_missing_paths=False)

    assert result["summary"]["duplicate_group_count"] == 0
    assert result["summary"]["multipart_group_count"] == 1
    assert result["summary"]["hidden_multipart_count"] == 1
    assert result["groups"] == []


def test_complementary_multipart_can_be_included(tmp_path):
    repo = _repo(tmp_path)
    _add(repo, "/library/ABC-123-Part1.mp4", "ABC-123")
    _add(repo, "/library/ABC-123-Part2.mp4", "ABC-123")

    result = find_duplicate_numbers(repo=repo, include_multipart=True, include_missing_paths=False)

    assert result["groups"][0]["classification"] == "multipart"
    assert result["groups"][0]["part_labels"] == ["Part1", "Part2"]
    assert all(item["delete_allowed"] is False for item in result["groups"][0]["items"])


def test_duplicate_same_part_is_reported(tmp_path):
    repo = _repo(tmp_path)
    _add(repo, "/library/ABC-123-CD1.mp4", "ABC-123")
    _add(repo, "/library/ABC-123-CD1-copy.mp4", "ABC-123")

    result = find_duplicate_numbers(repo=repo, include_missing_paths=False)

    assert result["summary"]["duplicate_group_count"] == 1
    assert result["groups"][0]["reason"] == "duplicate_part:CD1"


def test_variant_tags_are_marked_but_still_reported(tmp_path):
    repo = _repo(tmp_path)
    _add(repo, "/library/ABC-123.mp4", "ABC-123")
    _add(repo, "/library/ABC-123-sub-4k.mp4", "ABC-123")

    result = find_duplicate_numbers(repo=repo, include_missing_paths=False)

    assert result["summary"]["duplicate_group_count"] == 1
    assert result["groups"][0]["classification"] == "duplicate"
    assert result["groups"][0]["variant_tags"] == ["4k", "subtitle"]


def test_case_insensitive_numbers_are_grouped(tmp_path):
    repo = _repo(tmp_path)
    _add(repo, "/library/sone-205.mp4", "sone-205")
    _add(repo, "/library/SONE-205-copy.mp4", "SONE-205")

    result = find_duplicate_numbers(repo=repo, include_missing_paths=False)

    assert result["summary"]["duplicate_group_count"] == 1
    assert result["groups"][0]["canonical_number"] == "SONE-205"


def test_empty_and_single_numbers_are_ignored(tmp_path):
    repo = _repo(tmp_path)
    _add(repo, "/library/no-number.mp4", None)
    _add(repo, "/library/ABC-123.mp4", "ABC-123")

    result = find_duplicate_numbers(repo=repo, include_missing_paths=False)

    assert result["summary"]["duplicate_group_count"] == 0
    assert result["groups"] == []


def test_missing_paths_are_counted_without_crashing(tmp_path):
    repo = _repo(tmp_path)
    _add(repo, "/library/ABC-123.mp4", "ABC-123")
    _add(repo, "/library/ABC-123-copy.mp4", "ABC-123")

    result = find_duplicate_numbers(repo=repo)

    assert result["summary"]["missing_path_count"] == 2
    assert all(item["exists"] is False for item in result["groups"][0]["items"])
    assert all(item["delete_allowed"] is False for item in result["groups"][0]["items"])
