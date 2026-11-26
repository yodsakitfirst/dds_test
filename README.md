# River video measurements

A local Streamlit workbench for calibrated MP4 surface velocimetry and surveyed discharge. The four Ngwerere sample files are included.

## Test live processing with an MP4

From a Python 3.12 environment with the requirements installed, replay the bundled Ngwerere MP4 at camera speed:

```sh
python replay.py
```

The replay prints elapsed video time and tracking coverage, then saves the timestamped overlay to a fresh `runs/<id>/live-heatmap.mp4`. It defaults to ten seconds and uses the same adjacent-frame estimator as the **Live heatmap** dashboard. For your own fixed-camera recording, supply the matching calibration and gauge stage:

```sh
python replay.py river.mp4 --camera site-camera.json --stage 0.42 --seconds 30
```

For a live visual preview, launch `python -m streamlit run app.py`, open **Live heatmap**, select **Ngwerere sample**, confirm the camera view, and click **Start live heatmap**.

## Run on Windows

From this folder, using PowerShell and 64-bit Python 3.12:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m streamlit run app.py --server.address 127.0.0.1
```

Open the local address printed by Streamlit. Choose **Try the Ngwerere sample**, leave frames 0–125 and the calibration stage selected, inspect the camera view and both survey lines, confirm alignment, then choose **Analyze recording**. The first run can take several minutes for numerical compilation.

For another recording, choose **Upload my MP4**, select the matching named setup or import its JSON, verify the camera has not moved and enter the stage for that recording. A matching image resolution alone cannot establish alignment. Each site needs a pyOpenRiverCam calibration with image size, surveyed ground control, water plane reference, metric analysis polygon and resolution. For discharge, import an ordered bank-to-bank CSV with columns `x,y,z`. Supply x and y in the camera's projected metric coordinate system and bed elevation z in the calibration's survey datum. Both ends must reach dry banks. For Ngwerere the coordinates are WGS 84 / UTM 35S (EPSG:32735), gauge stage 0 m corresponds to surveyed elevation 1182.2 m. For other recordings the water surface elevation is z₀ + stage − calibration stage.

The map shows the magnitude and direction of the time-mean filtered vector at each grid cell. Grey indicates missing coverage. The reported area fraction counts cells with a finite mean vector over the calibrated grid. Stage is recording-specific. Alpha defaults to 0.90. Discharge uses the section-normal component and logarithmic interpolation over missing wetted stations. Keep the survey point order consistent for signed comparisons. Tutorial agreement tests computational reproducibility. Independent field measurements are required to establish accuracy.

### Generate a camera configuration from a video

Choose **Upload my MP4 → Camera setup → Create a setup**. Click four measured landmarks at the calibration water level, or six or more surveyed landmarks with elevations. Each click records a pixel position. Enter the matching measured X and Y in metres (and Z for the elevation method). Local coordinates can use a chosen origin and two perpendicular axes. Click four corners around the water area, enter the survey water elevation and gauge reading, and select **Generate camera config**. Review the landmark fit, then download the pyOpenRiverCam JSON or save it as a named site for the live dashboard.

## Data and reproducibility

The sample and notebooks come from [localdevices/pyorc at dabc714](https://github.com/localdevices/pyorc/tree/dabc7140c21c4a515138609e100da0448e57fbe4/examples). The upstream license is included in `sample/LICENSE`. To retrieve them and rerun the computational baseline:

```powershell
git clone --depth 1 --branch v0.9.6 https://github.com/localdevices/pyorc.git .upstream
git -C .upstream rev-parse HEAD
Copy-Item .upstream/examples/ngwerere/ngwerere_20191103.mp4,.upstream/examples/ngwerere/ngwerere.json,.upstream/examples/ngwerere/ngwerere_cross_section.csv,.upstream/examples/ngwerere/ngwerere_cross_section_2.csv sample
.\.venv\Scripts\python.exe baseline.py
```

Verify HEAD is `dabc7140c21c4a515138609e100da0448e57fbe4`. The replay executes the calculation cells from notebooks 02–04 and skips presentation-only cells. It stages only the video, calibration and surveys. Fresh raw and masked NetCDF outputs are passed between calculation stages in a unique directory under `runs/`. The relaxed angle-mask call in notebook 03 returns a mask that the notebook discards. The app follows that effective filter sequence. Frame-pair timing comes from decoded presentation timestamps. Normalization, NumPy projection, default Numba PIV, four-step transect smoothing and gap filling follow the notebooks.

Site data and uploaded clips live in `local/`. Each analysis writes its raw and filtered vectors, PNG map and JSON summary to a new directory in `runs/`. The UI offers PNG, JSON and synchronized MP4 downloads. See the live dashboard instructions below for RTSP capture.

## Observed Ngwerere run

Windows x64, Python 3.12, pyOpenRiverCam 0.9.6, FF-PIV 0.2.1, OpenCV 5.1.0.95. The browser processed 126 frames spanning 0–4.167 s. The median magnitude of the time-mean surface vectors was 0.20023 m/s across valid cells, with 56.36% coverage of the calibrated grid.

| Survey | Published median (m³/s) | Notebook replay (m³/s) | App export (m³/s) | Interpolated wetted stations |
| --- | ---: | ---: | ---: | ---: |
| Section 1 | 0.16406 | 0.15699 | 0.15699 | 11 of 25 |
| Section 2 | 0.16357 | 0.17585 | 0.17585 | 9 of 22 |

The app and fresh notebook replay agree to the exported precision. Compared with the published example, section 1 is 4.3% lower and section 2 is 7.5% higher. Inspection of the archived masked file showed a 59 × 66 grid with 0.13 m spacing. The pinned software produced a 55 × 61 grid with 0.14 m spacing from the same nominal 25-pixel window. This changes which vectors contribute to each transect. The comparison does not isolate how much of the difference comes from grid spacing versus other numerical changes.

The browser-downloaded [map](reference/sample-velocity.png) and [run summary](reference/sample-summary.json) are included. To repeat browser verification with the app already running and Microsoft Edge installed:

```powershell
.\.venv\Scripts\python.exe -m pip install playwright==1.65.2
.\.venv\Scripts\python.exe verify_ui.py
```

The end-to-end Edge check passed. Uploading the identical MP4 as `renamed-river.mp4` in velocity-only mode produced an identical PNG. Changing the selection to frames 0–124 and clearing the surveys produced a fresh run with 59.14% coverage, a missing-survey message and a downloadable velocity map. The UI rejected an unreadable MP4 and a calibration declaring 640 × 1080 for the 1920 × 1080 sample. Both errors hid prior results.

## Video heatmaps and live dashboard

After **Analyze recording**, use **Download heatmap MP4** to save the synchronized camera view. Each frame after the first shows the filtered surface speeds for the pair ending on that frame. The colour scale stays fixed throughout the clip and saturates at the 99th percentile of valid speeds. Untracked locations show the camera image. Arrow direction follows motion in the camera view, and colour indicates speed. For the bundled Ngwerere clip at its calibration stage, a manually traced water outline limits the arrows to the visible channel. It affects the display only. Other camera views use their calibrated area.

To preview any fixed-camera river MP4 without measurements, choose **Live heatmap → Upload MP4 → Image motion (px/s)**. The arrows and colour scale show apparent image motion in original video pixels per second. You can draw sections and export the replay.

For calibrated speeds, open **Live heatmap** in the sidebar and select a calibrated MP4 or an RTSP camera. Import a new camera calibration on the main page first. Select the matching site, enter stage, confirm the view, and choose **Start live heatmap**. To place your own sections, select **Draw my own section lines** before starting. Drag on the camera image or click two endpoints for each straight line. Use **Undo** or **Clear** to redraw. These lines replace the sample surveys in the live overlay and export. Their arrows show the component of measured surface velocity crossing each line. Use **Stop and save MP4**, or let the capture duration expire, then download the recording. The RTSP camera must be reachable from the machine hosting Streamlit and provide increasing presentation timestamps. The capture URL is used for the connection and is omitted from exports.

Live mode subtracts a running background and measures patch displacement by normalized cross-correlation on overlapping rectified windows. The display uses the median velocity from up to 12 recent updates and suppresses cells without repeated directional agreement. The first few frames therefore have no velocity arrows. For the bundled sample, the two blue lines follow the surveyed sections and bold arrows show the measured section-normal velocity at supported wet stations. The live colour scale defaults to 0–0.6 m/s. The viewer displays the newest adjacent pair available and may skip updates to keep latency down. It uses source timestamps for speeds and export timing. The offline MP4 uses the full-clip pyOpenRiverCam filters and stabilization. RTSP capture currently exports the live speed overlay only. Update an existing installation with `.\.venv\Scripts\python.exe -m pip install -r requirements.txt` to install the H.264 encoder.

