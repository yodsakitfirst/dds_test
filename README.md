# River video measurements

A local Streamlit workbench for calibrated MP4 surface velocimetry and surveyed discharge. The four Ngwerere sample files are included.

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

## Data and reproducibility

The sample and notebooks come from [localdevices/pyorc at dabc714](https://github.com/localdevices/pyorc/tree/dabc7140c21c4a515138609e100da0448e57fbe4/examples). The upstream license is included in `sample/LICENSE`. To retrieve them and rerun the computational baseline:

```powershell
git clone --depth 1 --branch v0.9.6 https://github.com/localdevices/pyorc.git .upstream
git -C .upstream rev-parse HEAD
Copy-Item .upstream/examples/ngwerere/ngwerere_20191103.mp4,.upstream/examples/ngwerere/ngwerere.json,.upstream/examples/ngwerere/ngwerere_cross_section.csv,.upstream/examples/ngwerere/ngwerere_cross_section_2.csv sample
.\.venv\Scripts\python.exe baseline.py
```

Verify HEAD is `dabc7140c21c4a515138609e100da0448e57fbe4`. The replay executes the calculation cells from notebooks 02–04 and skips presentation-only cells. It stages only the video, calibration and surveys. Fresh raw and masked NetCDF outputs are passed between calculation stages in a unique directory under `runs/`. The relaxed angle-mask call in notebook 03 returns a mask that the notebook discards. The app follows that effective filter sequence. Frame-pair timing comes from decoded presentation timestamps. Normalization, NumPy projection, default Numba PIV, four-step transect smoothing and gap filling follow the notebooks.

Site data and uploaded clips live in `local/`. Each analysis writes its raw and filtered vectors, PNG map and JSON summary to a new directory in `runs/`. The UI downloads the PNG and JSON. Future RTSP capture belongs in `acquisition.py`: finish writing a timestamped clip, then pass its path through the same preview, calibration and analysis workflow.

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
