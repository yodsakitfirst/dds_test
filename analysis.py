"""Calibrated frame-pair PIV and optional surveyed discharge."""
import json
from importlib.metadata import version
from uuid import uuid4

import dask
import numpy as np
import pandas as pd
import pyorc
import xarray as xr
from matplotlib.figure import Figure

from acquisition import ROOT, SAMPLE, digest

MASKS = ["corr", "minmax", "rolling", "outliers", "variance", "count"]
STABILIZE = [[150, 0], [500, 1079], [1750, 1079], [900, 0]]

def survey(path):
    xyz = pd.read_csv(path)[["x", "y", "z"]].to_numpy(dtype=float)
    if len(xyz) < 3 or not np.isfinite(xyz).all() or (np.linalg.norm(np.diff(xyz[:, :2], axis=0), axis=1) == 0).any():
        raise ValueError("Supply at least three distinct successive survey stations with finite x,y,z in metres.")
    return xyz

def integrate(ds, path, cfg, level, alpha):
    xyz = survey(path)
    tr = ds.velocimetry.get_transect(*xyz.T, rolling=4)
    wet = np.asarray(cfg.get_depth(tr.zcoords.values, level)) > 0
    median = tr.v_eff_nofill.sel(quantile=0.5).values
    known = np.isfinite(median) & wet
    if not known.any():
        raise ValueError("No tracked velocity crosses the wetted section. Check alignment, stage and tracers.")
    if wet[0] or wet[-1]:
        raise ValueError("Survey must reach both dry banks at this water level.")
    tr.transect.get_q(v_corr=alpha, fill_method="log_interp")
    if not np.isfinite(tr.q.sel(quantile=0.5).values[wet]).all():
        raise ValueError("Gaps remain after interpolation. Supply a better tracked interval.")
    tr.transect.get_river_flow()
    return dict(median_m3_s=float(tr.river_flow.sel(quantile=0.5)), wet_stations=int(wet.sum()),
                filled_wet_stations=int(wet.sum()-known.sum()), fill_method="log_interp",
                first_xy_m=xyz[0,:2].tolist(), last_xy_m=xyz[-1,:2].tolist(),
                bearing_deg_from_grid_north=float(np.degrees(np.arctan2(*(xyz[-1,:2]-xyz[0,:2]))) % 360))

def analyze(clip, config, settings, sections, progress):
    run = ROOT / "runs" / uuid4().hex
    run.mkdir(parents=True)
    cfg = pyorc.load_camera_config(str(config))
    start, end = settings["frames"]
    level = settings["stage_m"]
    stabilized = config.read_bytes() == (SAMPLE/"ngwerere.json").read_bytes()
    progress("Decoding selected frames and checking timestamps")
    video = pyorc.Video(str(clip), camera_config=cfg, start_frame=start, end_frame=end,
                        h_a=level, stabilize=STABILIZE if stabilized else getattr(cfg, "stabilize", None))
    frames = video.get_frames()
    times = frames.time.values
    dt = np.diff(times)
    if len(times) != end-start+1 or not np.isfinite(dt).all() or (dt <= 0).any():
        raise ValueError("Selected frames have missing or invalid timestamps. Repair the MP4 or shorten the interval.")
    progress("Normalizing, rectifying and correlating frame pairs")
    with dask.config.set(scheduler="synchronous"):
        projected = frames.frames.normalize(samples=min(15,len(times))).frames.project(method="numpy")
        projected.frames.get_piv().to_netcdf(run/"piv.nc")
    progress("Filtering unreliable vectors")
    ds = xr.load_dataset(run/"piv.nc")
    for mask in MASKS:
        getattr(ds.velocimetry.mask, mask)(inplace=True)
    ds.velocimetry.mask.window_mean(wdw=2, tolerance=0.5, reduce_time=True, inplace=True)
    ds.velocimetry.set_encoding()
    ds.to_netcdf(run/"masked.nc")
    ds = xr.load_dataset(run/"masked.nc")
    mean = ds.mean("time", keep_attrs=True)
    speed = np.hypot(mean.v_x, mean.v_y)
    coverage = float(np.isfinite(speed).mean())
    fig = Figure(figsize=(8, 5), layout="constrained")
    ax = fig.subplots()
    ax.set_facecolor("#d8dbdb")
    mesh = ax.pcolormesh(ds.x, ds.y, speed, cmap="viridis", shading="nearest", vmin=0)
    fig.colorbar(mesh, ax=ax, label="Mean vector magnitude (m/s)")
    step = max(1, int(np.ceil(max(speed.shape)/35)))
    sel = dict(x=slice(None,None,step), y=slice(None,None,step))
    field = mean.isel(**sel)
    if coverage:
        ax.quiver(field.x, field.y, field.v_x, field.v_y, angles="xy", color="white")
    ax.set(aspect="equal", xlabel="Local x (m)", ylabel="Local y (m)", title=f'{settings["site"]} · frames {start}–{end} · grey = no data')
    fig.savefig(run/"velocity.png", dpi=145)
    flags = []
    if coverage < .25:
        flags.append("Sparse coverage below 25%. Inspect tracers, framing and calibration." if coverage else "Tracking failed. No usable vectors.")
    summary = dict(**settings, run=run.name, pyopenrivercam=version("pyopenrivercam"),
        time_s=[float(times[0]),float(times[-1])], frame_pair_dt_s_min_max=[float(dt.min()),float(dt.max())],
        frame_pairs=len(dt), usable_area_fraction=coverage, quality_flags=flags, discharge={},
        surface_speed_median_m_s=float(speed.median()) if coverage else None,
        processing=dict(normalization_samples=min(15,len(times)), projection="numpy", engine="numba",
                        resolution_m=cfg.resolution, window_px=cfg.window_size, masks=MASKS,
                        spatial_window=dict(wdw=2,tolerance=.5,reduce_time=True),
                        stabilize=video.stabilize if isinstance(video.stabilize,list) else (STABILIZE if stabilized else None),
                        transect_rolling=4))
    progress("Checking surveyed sections and writing exports")
    if settings["request_discharge"] and not sections:
        flags.append("Supply a bank-to-bank x,y,z CSV and confirm its image alignment for discharge.")
    for name, path in sections.items():
        try:
            summary["discharge"][name] = integrate(ds, path, cfg, level, settings["alpha"])
        except Exception as exc:
            flags.append(f"{name}: {exc}")
    (run/"summary.json").write_text(json.dumps(summary,indent=2,allow_nan=False),encoding="utf8")
    return run
