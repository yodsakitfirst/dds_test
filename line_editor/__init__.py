"""Draw cross-sections on a source frame."""
import base64
from io import BytesIO

import av
import streamlit as st
import streamlit.components.v1 as components

from acquisition import ROOT, digest

line_editor = components.declare_component("section_lines", path=str(ROOT / "line_editor"))


def draw_lines(source, network, cfg, site):
    identity = digest(repr((str(source), network, site)).encode())
    if st.session_state.get("drawing_source") != identity:
        st.session_state.drawing_source = identity
        st.session_state.pop("drawing_image", None)
        st.session_state.drawings = []
    if "drawing_image" not in st.session_state:
        try:
            with av.open(str(source), options={"rtsp_transport": "tcp"} if network else {},
                         timeout=(6, 5)) as media:
                frame = next(media.decode(video=0)).to_image()
            if frame.size != (cfg.width, cfg.height):
                st.error("Select a calibration matching the camera image dimensions.")
                return []
            frame.thumbnail((960, 720))
            encoded = BytesIO()
            frame.save(encoded, format="JPEG", quality=88)
            st.session_state.drawing_image = base64.b64encode(encoded.getvalue()).decode("ascii")
        except (OSError, StopIteration, ValueError):
            st.error("Could not load a camera frame for drawing. Check the source and try again.")
            return []
    st.caption("Drag across the water, or click each endpoint. Repeat to add another line.")
    lines = line_editor(image=st.session_state.drawing_image, initial=st.session_state.drawings,
                        default=st.session_state.drawings, key="editor_" + identity)
    st.session_state.drawings = lines
    return lines
