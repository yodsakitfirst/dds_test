"""Build a pyOpenRiverCam configuration from image clicks and survey coordinates."""
import base64
from io import BytesIO

import cv2
import numpy as np
import pyorc
import streamlit as st
from PIL import Image
from pyproj import CRS

from acquisition import ROOT


point_editor = st.components.v2.component(
    "camera_point_picker",
    html="""<section><img alt="River calibration frame"><canvas></canvas></section>
            <footer><button>Undo</button><button>Clear</button><output aria-live="polite"></output></footer>""",
    css="""section { position: relative; max-width: 900px }
           img { width: 100%; display: block; border-radius: 6px }
           canvas { position: absolute; inset: 0; width: 100%; height: 100%; cursor: crosshair }
           footer { display: flex; gap: .6rem; align-items: center; margin: .4rem 0 }
           button { padding: .25rem .7rem; border-radius: .3rem; cursor: pointer }""",
    js=(ROOT / "line_editor" / "points.js").read_text(encoding="utf8"),
)


def build_config(shape, src, dst, corners, elevation, stage, resolution, crs):
    for label, points in [("landmarks", src), ("survey coordinates", dst), ("analysis corners", corners)]:
        if not np.isfinite(points).all() or len(np.unique(points, axis=0)) != len(points):
            raise ValueError(f"Use distinct, finite {label}.")
        if np.linalg.matrix_rank(points[:, :2] - points[0, :2]) < 2:
            raise ValueError(f"Spread the {label} across the river area.")
    if not cv2.isContourConvex(corners.astype("float32")):
        raise ValueError("Pick four analysis corners in order around the river area.")
    if crs:
        crs = CRS.from_user_input(crs)
        if not crs.is_projected or any(a.unit_conversion_factor != 1 for a in crs.axis_info[:2]):
            raise ValueError("Use a projected CRS in metres, or leave it blank for local measurements.")
    cfg = pyorc.CameraConfig(height=shape[0], width=shape[1], resolution=resolution, window_size=25,
                             crs=crs or None, corners=corners.tolist(),
                             gcps=dict(src=src.tolist(), dst=dst.tolist(), z_0=elevation, h_ref=stage))
    if min(cfg.shape) < cfg.window_size or not np.isfinite(cfg.bbox.area):
        raise ValueError("The selected area is too small for tracking. Check corners, scale and grid resolution.")
    xyz = dst if dst.shape[1] == 3 else np.column_stack((dst, np.full(len(dst), elevation)))
    projected = np.asarray(cfg.project_points(xyz.tolist(), within_image=True))
    error = np.sqrt(np.mean(np.sum((projected-src)**2, axis=1)))
    if not np.isfinite(error):
        raise ValueError("Some survey points project behind the camera. Check the point matches and elevations.")
    return cfg.to_json(), error


@st.cache_data(show_spinner=False)
def preview_image(image):
    thumb = Image.fromarray(image)
    thumb.thumbnail((1100, 825))
    buf = BytesIO()
    thumb.save(buf, format="JPEG", quality=90)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def generate_setup(image, clip_id):
    st.subheader("Create a camera setup")
    st.write("Click landmarks to record their pixel positions. Enter the matching field measurements in metres. "
             "For local coordinates, choose a landmark as (0, 0), choose a direction for X, "
             "and measure each landmark’s displacement along X and the perpendicular Y axis.")
    method = st.radio("Survey method", ["4 landmarks at water level (X, Y)", "6 or more surveyed landmarks (X, Y, Z)"])
    planar = method.startswith("4")
    key = f"{clip_id}_{planar}"
    photo = preview_image(image)
    height, width = image.shape[:2]

    def pick(label, polygon=False):
        widget_key = f"{key}_{label}"
        points = st.session_state.get(widget_key, {}).get("points", [])
        result = point_editor(
            data=dict(image=photo, source=clip_id, points=points, polygon=polygon,
                      limit=4 if planar or polygon else None),
            default={"points": []}, key=widget_key, on_points_change=lambda: None,
        )
        return np.asarray(result.points, dtype=float).reshape(-1, 2) * [width-1, height-1]

    st.markdown("**1. Click the measured landmarks**")
    src = pick("landmarks")
    dst = []
    for i, (u, v) in enumerate(src):
        st.caption(f"Landmark {i+1} · pixel ({u:.0f}, {v:.0f})")
        axes = "XY" if planar else "XYZ"
        row = [column.number_input(f"{axis} (m) · {i+1}", value=None, format="%.3f",
                                   key=f"{key}_{i}_{u:.4f}_{v:.4f}_{axis}")
               for column, axis in zip(st.columns(len(axes)), axes)]
        dst.append(row)

    st.markdown("**2. Outline the analysis area**")
    st.caption("Click four corners on the river photo, one click per corner. Facing the direction the water flows, start on the left bank, click farther downstream on that bank, cross to the right bank, then go upstream on the right bank. The fourth click closes the outline.")
    corners = pick("corners", polygon=True)
    st.markdown("**3. Set the calibration water level**")
    a, b = st.columns(2)
    elevation = a.number_input("Water elevation in survey coordinates (m)", value=None, key=key+"elevation")
    stage = b.number_input("Gauge reading at calibration (m)", value=None, key=key+"stage")
    st.caption("The left box is the water surface height in your survey. The right box is the reading on the river gauge at the same time. For one clip using local XY measurements and no existing height or gauge reference, you can call this clip’s water surface zero in both boxes.")
    with st.expander("Coordinate system and grid resolution"):
        crs = st.text_input("Projected CRS (optional for local coordinates)", placeholder="e.g. EPSG:32735", key=key+"crs")
        resolution = st.number_input("Projected grid spacing (m)", min_value=.001, value=.05, format="%.3f", key=key+"res")
    ready = (len(src) >= (4 if planar else 6) and len(corners) == 4 and elevation is not None
             and stage is not None and all(None not in row for row in dst))
    signature = repr((key, src.tolist(), dst, corners.tolist(), elevation, stage, resolution, crs))
    if st.button("Generate camera config", disabled=not ready):
        with st.spinner("Fitting camera to the measured landmarks"):
            data, error = build_config((height, width), src, np.array(dst), corners, elevation, stage, resolution, crs.strip())
        st.session_state.generated_setup = signature, data, error
    result = st.session_state.get("generated_setup")
    if result and result[0] == signature:
        _, data, error = result
        st.caption(f"Landmark reprojection RMS error: {error:.2f} pixels. This measures fit to the entered points.")
        st.download_button("Download camera config JSON", data, "river-camera.json", "application/json")
        return data.encode("utf8")
