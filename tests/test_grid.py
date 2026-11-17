import os

import numpy as np
import pytest

from mqs.screen.grid import CELL, capacity, render
from mqs.screen.scan import decode


def screenshot(frame, cols, rows, noise=20, seed=0):
    """What the local capture sees: the grid scaled to real pixels, white chrome above and below, noise."""
    ppm = render(frame, cols, rows)
    pixels = np.frombuffer(ppm[ppm.index(b"255\n") + 4:], np.uint8).reshape(rows, cols, 3)
    img = np.pad(pixels.repeat(CELL, 0).repeat(CELL, 1), ((21, 21), (37, 37), (0, 0))).astype(np.float32)
    img[:15] = img[-15:] = 255  # title bar and taskbar, white like the outer ring
    img += np.random.default_rng(seed).normal(0, noise, img.shape)
    return np.clip(img, 0, 255).astype(np.uint8)


@pytest.mark.parametrize("cols,rows", [(60, 30), (120, 70), (480, 252)])
def test_frame_survives_render_and_scan(cols, rows):
    frame = os.urandom(capacity(cols, rows) - 5)

    assert decode(screenshot(frame, cols, rows)) == frame + bytes(5)


def test_blank_screen_has_no_grid():
    assert decode(np.zeros((300, 300, 3), np.uint8)) is None


def test_oversized_frame_is_refused():
    with pytest.raises(ValueError):
        render(bytes(capacity(60, 30) + 1), 60, 30)


def test_white_page_above_dark_taskbar_is_not_a_grid():
    img = np.zeros((800, 800, 3), np.uint8)
    img[:750] = 255

    assert decode(img) is None
