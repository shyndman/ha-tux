from ha_tux.media.album_art import youtube_thumbnail_url

_THUMB = "https://i.ytimg.com/vi/S0zHEEG-oF4/hq720.jpg"


def test_youtube_thumbnail_url() -> None:
    assert (
        youtube_thumbnail_url("https://www.youtube.com/watch?v=S0zHEEG-oF4") == _THUMB
    )
    assert (
        youtube_thumbnail_url("https://music.youtube.com/watch?v=S0zHEEG-oF4") == _THUMB
    )
    assert youtube_thumbnail_url("https://youtu.be/S0zHEEG-oF4") == _THUMB
    assert youtube_thumbnail_url("https://www.youtube.com/") is None
    assert youtube_thumbnail_url("https://example.com/watch?v=abc") is None
    assert youtube_thumbnail_url(None) is None
    assert youtube_thumbnail_url("") is None
