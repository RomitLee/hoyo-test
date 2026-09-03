import numpy as np

from hoyo_analyzer.roi import PixelROI, RelativeROI, crop_roi


def test_roi_clamps_and_copies():
    image = np.arange(24, dtype=np.uint8).reshape(4, 6)
    result = crop_roi(image, PixelROI(-2, 1, 4, 4))
    assert result.shape == (3, 2)
    result[0, 0] = 255
    assert image[1, 0] != 255


def test_relative_roi():
    image = np.zeros((100, 200, 3), dtype=np.uint8)
    result = crop_roi(image, RelativeROI(0.25, 0.1, 0.5, 0.2))
    assert result.shape[:2] == (20, 100)
