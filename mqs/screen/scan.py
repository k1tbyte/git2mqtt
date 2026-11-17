"""Local side of the pull channel: screenshot -> frame bytes (numpy). Finds the grid by its rings, no settings."""
import numpy as np

from .grid import BITS, PAL, RING, whiten

_PAL = np.array(PAL, np.float32)


def decode(img: np.ndarray) -> bytes | None:
    """Frame bytes (zero-padded to capacity) of the grid in an RGB screenshot, None when no intact grid is seen.
    Wrong cells are not detected here: the link's CRC rejects the frame."""
    white, black = img.min(axis=2) > 200, img.max(axis=2) < 80
    columns = white.sum(0)
    if columns.max() < 50:
        return None
    sides = np.flatnonzero(columns > .7 * columns.max())  # the full-height white edges; chrome adds to every column
    x0, x1 = sides[0], sides[-1] + 1
    edges = _y_edges(white, black, x0, x1)
    if edges is None:
        return None
    y0, y1, cw = edges
    cols, rows = round((x1 - x0) / cw), round((y1 - y0) / cw)
    n = (cols - 2 * RING) * (rows - 2 * RING)
    if n <= 0:
        return None
    cx = (x0 + (np.arange(RING, cols - RING) + .5) * (x1 - x0) / cols).astype(int)
    cy = (y0 + (np.arange(RING, rows - RING) + .5) * (y1 - y0) / rows).astype(int)
    h = int(cw * .25)  # average the cell centre: edges blur, centres stay pure
    height, width = img.shape[:2]
    acc = sum(img[np.clip(cy[:, None] + dy, 0, height - 1), np.clip(cx[None, :] + dx, 0, width - 1)].astype(np.float32)
              for dy in range(-h, h + 1) for dx in range(-h, h + 1)) / (2 * h + 1) ** 2
    cells = ((acc[:, :, None, :] - _PAL) ** 2).sum(-1).argmin(-1).astype(np.uint8)
    bits = np.unpackbits(cells.reshape(-1, 1), axis=1)[:, 8 - BITS:].reshape(-1)
    return whiten(np.packbits(bits[:n * BITS // 8 * 8]).tobytes())


def _y_edges(white, black, x0, x1):
    """(top, bottom, cell px) of the grid. Chrome next to the white edge (title bar, taskbar) hides its outer side,
    so use the white->black row transitions between the rings; the black ring's thickness is the cell size."""
    xa, xb = x0 + (x1 - x0) // 10, x1 - (x1 - x0) // 10  # skip the vertical rings
    white_rows, black_rows = white[:, xa:xb].mean(1) > .8, black[:, xa:xb].mean(1) > .8
    tops = np.flatnonzero(white_rows[:-1] & black_rows[1:]) + 1
    for top in tops:
        cw = _run(black_rows, top, 1)
        if cw < 2 or top < cw or not white_rows[top - cw:top].all() or not _is_data(white_rows, black_rows, top + cw):
            continue
        for bottom in np.flatnonzero(black_rows[:-1] & white_rows[1:]) + 1:
            if (bottom > top + 2 * cw and _run(black_rows, bottom - 1, -1) == cw and white_rows[bottom:bottom + cw].all()
                    and _is_data(white_rows, black_rows, bottom - cw - 1)):
                return top - cw, bottom + cw, cw
    return None


def _is_data(white_rows, black_rows, y: int) -> bool:
    """A row of the data area is neither white nor black: that tells the rings from a black margin or chrome."""
    return 0 <= y < len(white_rows) and not white_rows[y] and not black_rows[y]


def _run(mask, start: int, step: int) -> int:
    n = 0
    while 0 <= start + n * step < len(mask) and mask[start + n * step]:
        n += 1
    return n
