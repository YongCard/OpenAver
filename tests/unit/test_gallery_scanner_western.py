from core.gallery_scanner import VideoScanner


def test_parse_filename_western_scene_generates_west_id():
    info = VideoScanner().parse_filename("bangbus.19.08.28.dylann.vox.mp4")

    assert info.num == "WEST-BANGBUS-20190828-DYLANN-VOX"
    assert info.maker == "Bangbus"
    assert info.actor == "Dylann Vox"
    assert info.date == "2019-08-28"
    assert "Stash" in info.genre
