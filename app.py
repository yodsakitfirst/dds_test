from hashlib import sha256
import json
import re

import numpy as np
import pyorc
import streamlit as st
from PIL import Image, ImageDraw

from acquisition import LOCAL, SAMPLE, digest, inspect_clip, save_content
from analysis import analyze, survey

st.set_page_config(page_title="River video measurements", layout="wide")
st.title("River video measurements")

@st.cache_data(show_spinner=False)
def preview(path, fingerprint):
    return inspect_clip(path)

def main():
    mode = st.radio("Video source", ["Try the Ngwerere sample", "Upload my MP4"], horizontal=True)
    is_sample = mode.startswith("Try")
    sites = LOCAL / "sites"
    sites.mkdir(parents=True, exist_ok=True)
    if is_sample:
        clip = SAMPLE / "ngwerere_20191103.mp4"
        name = clip.name
        site = "Ngwerere"
    else:
        upload = st.file_uploader("River recording", type="mp4")
        if upload is None:
            return
        clip = save_content(LOCAL/"clips", upload.getbuffer(), ".mp4")
        name = upload.name
    clip_id = sha256(clip.read_bytes()).hexdigest() if is_sample else clip.stem
    image, count, fps = preview(clip, clip_id)
    height, width = image.shape[:2]
    st.caption(f"{name} · {width} × {height} · {count} frames · nominal {fps:.3f} fps")
    if not is_sample:
        names = sorted(p.name for p in sites.iterdir() if (p/"camera.json").is_file())
        if "saved_site" in st.session_state:
            st.session_state.camera_site = st.session_state.pop("saved_site")
        site = st.selectbox("Camera setup", ["Choose a setup", "Ngwerere", *names, "Import a setup"], key="camera_site")
        if site == "Choose a setup":
            st.image(image, width=640)
            return
        if site == "Import a setup":
            label = st.text_input("New site name")
            camera = st.file_uploader("pyOpenRiverCam camera configuration", type="json")
            if st.button("Save site") and camera:
                if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 _-]{0,59}",label) or label.casefold() in {"ngwerere","choose a setup","import a setup"}:
                    raise ValueError("Choose a unique site name using letters, digits, spaces, underscores or hyphens.")
                path = save_content(LOCAL/"imports",camera.getbuffer(),".json")
                pyorc.load_camera_config(str(path))
                (sites/label).mkdir(exist_ok=False)
                (sites/label/"camera.json").write_bytes(path.read_bytes())
                st.session_state.saved_site = label
                st.rerun()
            return
    config = SAMPLE/"ngwerere.json" if site=="Ngwerere" else sites/site/"camera.json"
    cfg = pyorc.load_camera_config(str(config))
    if (width,height) != (cfg.width,cfg.height):
        raise ValueError(f"Camera configuration expects {cfg.width} × {cfg.height}. Select the setup matching this {width} × {height} video.")
    identity = digest(json.dumps([mode,name,clip_id,site,digest(config.read_bytes())]).encode())
    st.subheader("Measurement conditions")
    start, end = st.slider("Analysis interval (inclusive frame numbers)",0,count-1,(0,min(125,count-1)),key="interval"+identity)
    ref = cfg.gcps.get("h_ref")
    z0 = cfg.gcps.get("z_0")
    st.caption(f"Gauge stage in metres relative to the site gauge datum. Calibration stage {ref} m corresponds to survey elevation {z0} m.")
    use_ref = ref is not None and st.checkbox("Use calibration water level for this recording",value=is_sample,key="ref"+identity)
    level = float(ref) if use_ref else st.number_input("Recording-time gauge stage (m)",value=None,format="%.4f",key="stage"+identity)
    want = st.checkbox("Estimate discharge",value=is_sample,key="q"+identity)
    sections = {}
    alpha = None
    overlay = Image.fromarray(image)
    draw = ImageDraw.Draw(overlay)
    if want:
        alpha = st.number_input("Surface-to-depth velocity coefficient (alpha)",0.01,1.0,0.9,0.01)
        st.caption("Survey CSV columns x,y,z in metres. Use the camera’s projected CRS and the same vertical reference as its calibration elevation.")
        folder = sites/site/"sections"
        options = {p.stem:p for p in sorted(folder.glob("*.csv"))}
        defaults = {}
        if site == "Ngwerere":
            defaults = {p.stem:p for p in sorted(SAMPLE.glob("*.csv"))}
            options.update(defaults)
        selected = st.multiselect("Saved sections",options,default=list(defaults),key="sections"+identity)
        candidates = {k:options[k] for k in selected}
        for file in st.file_uploader("Import site surveys",type="csv",accept_multiple_files=True,key="survey"+identity):
            try:
                path = save_content(LOCAL/"imports",file.getbuffer(),".csv")
                survey(path)
                label = re.sub(r"[^A-Za-z0-9_-]", "_", file.name[:-4])[:60]+"-"+path.stem[:10]
                folder.mkdir(parents=True,exist_ok=True)
                target = folder/(label+".csv")
                if not target.exists():
                    target.write_bytes(path.read_bytes())
                candidates[label] = target
            except Exception as exc:
                st.warning(f"{file.name}: {exc}")
        for idx,(label,path) in enumerate(candidates.items()):
            try:
                xyz = survey(path)
                uv = cfg.project_points(xyz.tolist())
                if not np.isfinite(uv).all():
                    raise ValueError("Check survey coordinates and camera CRS.")
                color = ["#ffbe39","#29cfff","#ff76af"][idx%3]
                draw.line([tuple(p) for p in uv],fill=color,width=6)
                draw.text(tuple(uv[0]),str(idx+1),fill=color,stroke_width=2,stroke_fill="black",font_size=35)
                st.caption(f"{idx+1} · {label} · {color}")
                sections[label] = path
            except Exception as exc:
                st.warning(f"{label}: {exc}")
    st.image(overlay,caption="Camera view · survey lines follow CSV point order",width=760)
    confirmed = st.checkbox("I confirm this camera view matches the selected calibration.",key="view"+identity)
    hashes = {k:digest(v.read_bytes()) for k,v in sections.items()}
    aligned = not sections or st.checkbox("I verified the displayed sections cross the river at the surveyed locations.",key="align"+identity+str(hashes))
    settings = dict(source=name, source_sha256=clip_id, site=site, calibration_sha256=digest(config.read_bytes()),
                    stage_m=level, calibration_stage_used=use_ref, calibration_stage_m=ref, calibration_elevation_m=z0,
                    frames=[start,end], dimensions_px=[width,height], nominal_fps=fps,
                    request_discharge=want, alpha=alpha, section_sha256=hashes, alignment_confirmed=aligned)
    token = digest(json.dumps(settings,sort_keys=True).encode())
    if level is None:
        st.info("Enter the recording stage or explicitly select the calibration level to rectify the water surface.")
    if end-start<8:
        st.warning("Select at least nine consecutive frames for temporal filtering.")
    if st.button("Analyze recording",type="primary",disabled=not confirmed or level is None or end-start<8):
        st.session_state.pop("result",None)
        with st.status("Processing recording",expanded=True) as status:
            run = analyze(clip,config,settings,sections if aligned else {},status.write)
            status.update(label="Run complete",state="complete",expanded=False)
        st.session_state.result = (token,run)
    result = st.session_state.get("result")
    if not result:
        return
    if result[0]!=token or not confirmed:
        st.info("Inputs changed. Analyze this selection to display its results.")
        return
    run = result[1]
    summary = json.loads((run/"summary.json").read_text(encoding="utf8"))
    st.subheader("Measurement results")
    st.caption(f'{name} · {site} · frames {start}–{end} · run {run.name}')
    st.metric("Area with usable mean vectors", f'{summary["usable_area_fraction"]:.1%}')
    for flag in summary["quality_flags"]:
        st.warning(flag)
    st.image(str(run/"velocity.png"))
    movie = run/"heatmap.mp4"
    if movie.exists():
        st.video(str(movie))
        st.download_button("Download heatmap MP4",movie.read_bytes(),file_name=run.name+"-heatmap.mp4",mime="video/mp4")
    for label, section in summary["discharge"].items():
        st.metric(label, f'{section["median_m3_s"]:.5f} m³/s')
        st.caption(f'First-to-last grid bearing {section["bearing_deg_from_grid_north"]:.1f}° · logarithmic interpolation at {section["filled_wet_stations"]} of {section["wet_stations"]} wetted stations.')
    for filename, mime in [("velocity.png","image/png"),("summary.json","application/json")]:
        st.download_button("Download "+filename,(run/filename).read_bytes(),file_name=run.name+"-"+filename,mime=mime)

try:
    main()
except Exception as exc:
    st.session_state.pop("result",None)
    st.error(f"Measurement stopped. {type(exc).__name__}: {exc}")


