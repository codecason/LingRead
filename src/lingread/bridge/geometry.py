"""Local page-to-screen registration; no screenshot leaves the machine."""
import cv2
import numpy as np


def register_page(page_gray, screen_gray, expected_scale):
    """Return (left, top, scale, inlier_count), or None if evidence is weak.

    Coordinates are relative to the captured viewport. A negative top is normal
    when the document has been scrolled. SIFT handles scale and antialias changes.
    """
    def features(image):
        factor = min(1.0, 1400 / max(image.shape))
        reduced = cv2.resize(image, None, fx=factor, fy=factor) if factor < 1 else image
        keys, desc = cv2.SIFT_create(nfeatures=2500).detectAndCompute(reduced, None)
        return keys, desc, factor
    pk, pd, pf = features(page_gray)
    sk, sd, sf = features(screen_gray)
    if pd is None or sd is None or len(pd) < 8 or len(sd) < 8:
        return None
    matcher = cv2.BFMatcher()
    pairs = matcher.knnMatch(pd, sd, k=2)
    # A full page contains many repeated glyphs outside the visible crop.
    # Require reciprocal nearest neighbours before calculating the inlier ratio.
    reverse = {m.queryIdx: m.trainIdx for m in matcher.match(sd, pd)}
    good = [a for pair in pairs if len(pair) == 2 for a, b in [pair]
            if a.distance < .68 * b.distance and reverse.get(a.trainIdx) == a.queryIdx]
    if len(good) < 8:
        return None
    source = np.float32([pk[m.queryIdx].pt for m in good]) / pf
    target = np.float32([sk[m.trainIdx].pt for m in good]) / sf
    matrix, mask = cv2.estimateAffinePartial2D(source, target, method=cv2.RANSAC,
                                              ransacReprojThreshold=2.5, maxIters=2000)
    if matrix is None or mask is None:
        return None
    count = int(mask.sum())
    scale = float(matrix[0, 0])
    if (count < 8 or count / len(good) < .4 or scale <= 0
            or abs(matrix[0, 1]) > .015 * scale
            or abs(scale / expected_scale - 1) > .15):
        return None
    points = source[mask.ravel().astype(bool)]
    if np.ptp(points[:, 0]) < 40 or np.ptp(points[:, 1]) < 20:
        return None
    return float(matrix[0, 2]), float(matrix[1, 2]), scale, count


def capture_viewport(rect):
    from PIL import ImageGrab
    image = ImageGrab.grab(bbox=tuple(int(v) for v in rect), all_screens=True)
    return np.asarray(image.convert('L'))


def render_page(page):
    import pymupdf
    pix = page.get_pixmap(matrix=pymupdf.Matrix(2, 2), colorspace=pymupdf.csGRAY, alpha=False)
    return np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width).copy()
