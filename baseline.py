"""Replay the computational cells of the pinned notebooks in an isolated directory."""
import copy
import json
import os
import shutil
from pathlib import Path
from uuid import uuid4

import matplotlib
matplotlib.use("Agg")

ROOT = Path(__file__).resolve().parent
examples = ROOT / ".upstream/examples"
run = ROOT / "runs" / ("baseline-" + uuid4().hex)
inputs = run / "ngwerere"
inputs.mkdir(parents=True)
for path in (ROOT / "sample").iterdir():
    if path.suffix in {".mp4", ".csv", ".json"}:
        shutil.copy(path, inputs)
os.chdir(run)

namespace = {"copy": copy}
def cell(book, index, before=None):
    notebook = next(examples.glob(f"{book}_*.ipynb"))
    source = "".join(json.loads(notebook.read_text(encoding="utf8"))["cells"][index]["source"])
    if before:
        source = source.split(before)[0]
    exec(compile(source, str(notebook), "exec"), namespace)

for idx in (1, 3, 7, 11, 13, 17):
    cell("02", idx)
shutil.move("ngwerere_piv.nc", inputs)
cell("03", 1)
cell("03", 3)
cell("03", 10, "# Now first average")
cell("03", 16)
shutil.move("ngwerere_masked.nc", inputs)
cell("04", 1)
cell("04", 3)
cell("04", 5, "# let's have a look")
for idx in (7, 9, 17):
    cell("04", idx)
values = [float(namespace[key].river_flow.sel(quantile=0.5)) for key in ("ds_points_q", "ds_points_q2")]
(run / "median.json").write_text(json.dumps(values))
print(run, values)
