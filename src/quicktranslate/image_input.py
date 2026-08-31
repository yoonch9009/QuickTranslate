from __future__ import annotations

import base64

from PySide6.QtCore import QBuffer, QIODevice, Qt
from PySide6.QtGui import QImage, QPainter

MAX_IMAGE_DIMENSION = 4096
JPEG_QUALITY = 92
LARGE_IMAGE_BYTES = 12 * 1024 * 1024


def encode_image_data_url(image: QImage) -> str:
    if image.isNull():
        raise ValueError("클립보드 이미지가 비어 있습니다.")

    prepared = image
    if max(image.width(), image.height()) > MAX_IMAGE_DIMENSION:
        prepared = image.scaled(
            MAX_IMAGE_DIMENSION,
            MAX_IMAGE_DIMENSION,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )

    flattened = QImage(prepared.size(), QImage.Format.Format_RGB32)
    flattened.fill(Qt.GlobalColor.white)
    painter = QPainter(flattened)
    painter.drawImage(0, 0, prepared)
    painter.end()

    encoded = _encode_jpeg(flattened, JPEG_QUALITY)
    if len(encoded) > LARGE_IMAGE_BYTES:
        encoded = _encode_jpeg(flattened, 80)
    return "data:image/jpeg;base64," + base64.b64encode(encoded).decode("ascii")


def _encode_jpeg(image: QImage, quality: int) -> bytes:
    buffer = QBuffer()
    if not buffer.open(QIODevice.OpenModeFlag.WriteOnly):
        raise ValueError("이미지 버퍼를 열 수 없습니다.")
    try:
        if not image.save(buffer, "JPEG", quality):
            raise ValueError("클립보드 이미지를 JPEG로 변환할 수 없습니다.")
        return bytes(buffer.data())
    finally:
        buffer.close()
