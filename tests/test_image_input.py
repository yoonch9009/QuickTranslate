from __future__ import annotations

import base64
import unittest

from PySide6.QtGui import QColor, QImage

from quicktranslate.image_input import MAX_IMAGE_DIMENSION, encode_image_data_url


class ImageInputTests(unittest.TestCase):
    def test_qimage_is_encoded_as_jpeg_data_url(self) -> None:
        image = QImage(320, 180, QImage.Format.Format_ARGB32)
        image.fill(QColor("white"))

        data_url = encode_image_data_url(image)
        encoded = data_url.removeprefix("data:image/jpeg;base64,")
        decoded = base64.b64decode(encoded)

        self.assertTrue(data_url.startswith("data:image/jpeg;base64,"))
        self.assertTrue(decoded.startswith(b"\xff\xd8\xff"))

    def test_large_image_is_scaled_before_encoding(self) -> None:
        image = QImage(5000, 100, QImage.Format.Format_RGB32)
        image.fill(QColor("white"))

        data_url = encode_image_data_url(image)
        decoded = base64.b64decode(data_url.split(",", 1)[1])
        loaded = QImage.fromData(decoded, "JPEG")

        self.assertEqual(max(loaded.width(), loaded.height()), MAX_IMAGE_DIMENSION)


if __name__ == "__main__":
    unittest.main()
