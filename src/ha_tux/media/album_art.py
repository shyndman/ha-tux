import base64
import logging
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlparse

from ha_tux.media.mpris import SupportedImageMimeType

LOGGER = logging.getLogger(__name__)

DATA_URL_PREFIX = "data:"
BASE64_MARKER = ";base64,"
HTTP_SCHEMES = frozenset({"http", "https"})
LOCAL_FILE_HOSTS = frozenset({"", "localhost"})
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
JPEG_SIGNATURE = b"\xff\xd8\xff"
GIF87A_SIGNATURE = b"GIF87a"
GIF89A_SIGNATURE = b"GIF89a"
RIFF_SIGNATURE = b"RIFF"
WEBP_SIGNATURE = b"WEBP"
RIFF_WEBP_FORMAT_OFFSET = 8
MIN_WEBP_SIGNATURE_BYTES = 12


@dataclass(frozen=True, slots=True)
class AlbumArtPayload:
    url: str
    remotely_accessible: bool


@dataclass(frozen=True, slots=True)
class AlbumArtCacheKey:
    path: Path
    mtime_ns: int
    size: int


@dataclass(frozen=True, slots=True)
class _CachedAlbumArt:
    original_url: str
    key: AlbumArtCacheKey
    payload: AlbumArtPayload


class AlbumArtResolver:
    def __init__(self) -> None:
        self._cached_file: _CachedAlbumArt | None = None

    def resolve(self, art_url: str | None) -> AlbumArtPayload:
        if art_url is None or art_url == "":
            return AlbumArtPayload(url="", remotely_accessible=False)

        parsed = urlparse(art_url)
        if parsed.scheme in HTTP_SCHEMES:
            return AlbumArtPayload(url=art_url, remotely_accessible=True)

        if parsed.scheme != "file":
            LOGGER.warning(
                "Unsupported album art URL scheme", extra={"scheme": parsed.scheme}
            )
            return AlbumArtPayload(url="", remotely_accessible=False)

        if parsed.netloc not in LOCAL_FILE_HOSTS:
            LOGGER.warning(
                "Unsupported album art file host", extra={"host": parsed.netloc}
            )
            return AlbumArtPayload(url="", remotely_accessible=False)

        return self._resolve_file_url(art_url, Path(unquote(parsed.path)))

    def _resolve_file_url(self, original_url: str, path: Path) -> AlbumArtPayload:
        try:
            stat = path.stat()
        except OSError:
            LOGGER.exception("Unable to stat album art file", extra={"path": str(path)})
            return AlbumArtPayload(url="", remotely_accessible=False)

        if not path.is_file():
            LOGGER.warning(
                "Album art path is not a regular file", extra={"path": str(path)}
            )
            return AlbumArtPayload(url="", remotely_accessible=False)

        key = AlbumArtCacheKey(path=path, mtime_ns=stat.st_mtime_ns, size=stat.st_size)
        cached = self._cached_file
        if (
            cached is not None
            and cached.original_url == original_url
            and cached.key == key
        ):
            return cached.payload

        try:
            file_bytes = path.read_bytes()
        except OSError:
            LOGGER.exception("Unable to read album art file", extra={"path": str(path)})
            return AlbumArtPayload(url="", remotely_accessible=False)

        mime_type = detect_image_mime_type(file_bytes)
        if mime_type is None:
            LOGGER.warning("Unsupported album art file type", extra={"path": str(path)})
            return AlbumArtPayload(url="", remotely_accessible=False)

        encoded = base64.b64encode(file_bytes).decode("ascii")
        payload = AlbumArtPayload(
            url=f"{DATA_URL_PREFIX}{mime_type}{BASE64_MARKER}{encoded}",
            remotely_accessible=True,
        )
        self._cached_file = _CachedAlbumArt(
            original_url=original_url,
            key=key,
            payload=payload,
        )
        return payload


def detect_image_mime_type(file_bytes: bytes) -> SupportedImageMimeType | None:
    if file_bytes.startswith(PNG_SIGNATURE):
        return "image/png"

    if file_bytes.startswith(JPEG_SIGNATURE):
        return "image/jpeg"

    if file_bytes.startswith(GIF87A_SIGNATURE) or file_bytes.startswith(
        GIF89A_SIGNATURE
    ):
        return "image/gif"

    if (
        len(file_bytes) >= MIN_WEBP_SIGNATURE_BYTES
        and file_bytes.startswith(RIFF_SIGNATURE)
        and file_bytes[RIFF_WEBP_FORMAT_OFFSET:MIN_WEBP_SIGNATURE_BYTES]
        == WEBP_SIGNATURE
    ):
        return "image/webp"

    return None
