from base64 import b64encode
from pathlib import Path

from ha_tux.media.album_art import AlbumArtResolver, detect_image_mime_type

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"payload"
JPEG_BYTES = b"\xff\xd8\xffpayload"
GIF_BYTES = b"GIF89apayload"
WEBP_BYTES = b"RIFFxxxxWEBPpayload"


def test_detect_image_mime_type_from_bytes() -> None:
    assert detect_image_mime_type(PNG_BYTES) == "image/png"
    assert detect_image_mime_type(JPEG_BYTES) == "image/jpeg"
    assert detect_image_mime_type(GIF_BYTES) == "image/gif"
    assert detect_image_mime_type(WEBP_BYTES) == "image/webp"
    assert detect_image_mime_type(b"not an image") is None


def test_http_album_art_is_remotely_accessible() -> None:
    payload = AlbumArtResolver().resolve("https://example.test/art.png")

    assert payload.url == "https://example.test/art.png"
    assert payload.remotely_accessible is True


def test_file_album_art_converts_to_data_url(tmp_path: Path) -> None:
    art = tmp_path / "chrome-temp-art"
    _ = art.write_bytes(PNG_BYTES)

    payload = AlbumArtResolver().resolve(art.as_uri())

    assert (
        payload.url == f"data:image/png;base64,{b64encode(PNG_BYTES).decode('ascii')}"
    )
    assert payload.remotely_accessible is True


def test_missing_album_art_file_clears_image(tmp_path: Path) -> None:
    payload = AlbumArtResolver().resolve((tmp_path / "missing").as_uri())

    assert payload.url == ""
    assert payload.remotely_accessible is False


def test_unsupported_album_art_file_clears_image(tmp_path: Path) -> None:
    art = tmp_path / "not-image"
    _ = art.write_bytes(b"not image")

    payload = AlbumArtResolver().resolve(art.as_uri())

    assert payload.url == ""
    assert payload.remotely_accessible is False


def test_album_art_cache_reuses_unchanged_file(tmp_path: Path) -> None:
    art = tmp_path / "chrome-temp-art"
    _ = art.write_bytes(PNG_BYTES)
    resolver = AlbumArtResolver()

    first = resolver.resolve(art.as_uri())
    second = resolver.resolve(art.as_uri())

    assert first == second
