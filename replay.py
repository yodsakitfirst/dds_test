"""Replay a calibrated MP4 through the live river-velocity pipeline."""
import argparse
import math
from pathlib import Path
from uuid import uuid4

import pyorc

from acquisition import ROOT, SAMPLE
from analysis import STABILIZE
from heatmap import mp4_writer
from live import replay_frames


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sample = SAMPLE / "ngwerere_20191103.mp4"
    calibration = SAMPLE / "ngwerere.json"
    parser.add_argument("video", nargs="?", type=Path, default=sample)
    parser.add_argument("--camera", type=Path, help="matching pyOpenRiverCam camera JSON")
    parser.add_argument("--stage", type=float, help="recording-time gauge stage in metres")
    parser.add_argument("--seconds", type=float, default=10, help="maximum replay duration")
    args = parser.parse_args()
    if not args.video.is_file():
        parser.error(f"Video file does not exist: {args.video}")
    if args.video.resolve() != sample and (args.camera is None or args.stage is None):
        parser.error("Supply --camera and --stage for your recording.")
    if not math.isfinite(args.seconds) or args.seconds <= 0:
        parser.error("--seconds must be positive and finite.")

    config_path = args.camera or calibration
    stage = 0.0 if args.stage is None else args.stage
    if not math.isfinite(stage):
        parser.error("--stage must be finite.")
    try:
        cfg = pyorc.load_camera_config(str(config_path))
        polygon = STABILIZE if config_path.read_bytes() == calibration.read_bytes() else cfg.stabilize
        output = ROOT / "runs" / uuid4().hex / "live-heatmap.mp4"
        output.parent.mkdir(parents=True)
        frames = replay_frames(args.video.resolve(), False, cfg, stage, 0.6,
                               args.seconds, polygon, lambda: False)
        count = 0
        with mp4_writer(output, 8) as write:
            for image, elapsed, coverage, _ in frames:
                write(image, elapsed)
                count += 1
                print(f"\r{elapsed:.1f} s | {coverage:.0%} of visible grid tracked", end="", flush=True)
        print(f"\nSaved {count} updates to {output}")
    except (ValueError, OSError) as exc:
        parser.exit(1, f"\nReplay failed: {exc}\n")


if __name__ == "__main__":
    main()
