"""Local file acquisition. Analysis accepts any completed clip with this interface."""
import hashlib
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent
SAMPLE = ROOT / "sample"
LOCAL = ROOT / "local"

def digest(data):
    return hashlib.sha256(data).hexdigest()

def save_content(folder, data, suffix):
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / (digest(data) + suffix)
    if not target.exists():
        with NamedTemporaryFile(dir=folder, delete=False) as tmp:
            tmp.write(data)
        os.replace(tmp.name, target)
    return target

def inspect_clip(path):
    cap = cv2.VideoCapture(str(path))
    try:
        ok, image = cap.read()
        if not ok:
            raise ValueError("Cannot decode the MP4. Supply a complete, playable video.")
        fps = cap.get(cv2.CAP_PROP_FPS)
        count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if not np.isfinite(fps) or fps <= 0 or count < 9:
            raise ValueError("Supply a clip with frame timestamps and at least nine frames.")
        return cv2.cvtColor(image, cv2.COLOR_BGR2RGB), count, fps
    finally:
        cap.release()
