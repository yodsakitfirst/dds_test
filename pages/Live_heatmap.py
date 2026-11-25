from threading import Thread
from time import monotonic, sleep
from urllib.parse import urlsplit
from uuid import uuid4

import pyorc
import streamlit as st

from acquisition import LOCAL, ROOT, SAMPLE, save_content
from analysis import STABILIZE
from live import LiveRun
from line_editor import draw_lines

st.set_page_config(page_title="Live heatmap",layout="wide")
st.title("Live heatmap")
st.caption("Arrows show surface motion supported across recent frame pairs. Bold arrows show velocity across the selected section lines. Colour indicates speed in m/s. Keep the camera fixed.")
job = st.session_state.get("live_run")
busy = job is not None and not job.done

def monitor(job):
    if st.button("Stop and save MP4"):
        job.stop.set()
    player = st.empty()
    status = st.empty()
    status.caption("Waiting for a timed frame pair…")
    last = None
    while not job.done:
        job.seen = monotonic()
        snapshot = job.snapshot
        if snapshot is not None and snapshot is not last:
            image,t,coverage,dt = snapshot
            player.image(image)
            status.caption(f"{t:.1f} s elapsed · {coverage:.0%} of visible grid cells tracked · frame-pair interval {dt*1000:.1f} ms")
            last = snapshot
        # Yield regularly so Stop and navigation work during a slow connection.
        st.session_state.get("live_run")
        sleep(.1)
    st.rerun()
if busy:
    monitor(job)
else:
    if job:
        if job.error:
            st.error(job.error)
        if job.export:
            st.success("Recording saved")
            st.video(str(job.export))
            st.download_button("Download live heatmap MP4",job.export.read_bytes(),file_name=job.export.name,mime="video/mp4")
    mode = st.radio("Source",["Ngwerere sample","Upload MP4","RTSP camera"],horizontal=True)
    network = mode=="RTSP camera"
    source = None
    sites = {"Ngwerere":SAMPLE/"ngwerere.json"}
    sites.update({p.parent.name:p for p in (LOCAL/"sites").glob("*/camera.json")})
    if mode=="Ngwerere sample":
        site = "Ngwerere"
        source = SAMPLE/"ngwerere_20191103.mp4"
    else:
        site = st.selectbox("Saved calibration (import new sites on the main page)",list(sites),index=None)
        if network:
            address = st.text_input("RTSP camera URL",type="password",placeholder="rtsp://camera/stream")
            if address and urlsplit(address).scheme in {"rtsp","rtsps"}:
                source = address
        else:
            upload = st.file_uploader("River MP4",type="mp4")
            if upload:
                source = save_content(LOCAL/"clips",upload.getbuffer(),".mp4")
    if site and source:
        cfg = pyorc.load_camera_config(str(sites[site]))
        stage = st.number_input("Recording-time gauge stage (m)",value=0.0 if mode=="Ngwerere sample" else None)
        maximum = st.number_input("Colour scale maximum (m/s)",.1,20.,0.6)
        duration = st.number_input("Capture duration (seconds)",1,900,10)
        custom = st.checkbox("Draw my own section lines")
        drawn_lines = draw_lines(source, network, cfg, site) if custom else None
        confirmed = st.checkbox("The camera view matches this calibration")
        if st.button("Start live heatmap",disabled=not confirmed or stage is None or (custom and not drawn_lines)):
            folder = ROOT/"runs"/uuid4().hex
            folder.mkdir(parents=True)
            job = LiveRun()
            st.session_state.live_run = job
            polygon = STABILIZE if site=="Ngwerere" else cfg.stabilize
            Thread(target=job.process,args=(source,network,cfg,stage,maximum,duration,
                   folder/"live-heatmap.mp4",polygon,drawn_lines),daemon=True).start()
            st.rerun()


