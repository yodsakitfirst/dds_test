"""Low-latency calibrated window correlation and paced file/RTSP acquisition."""
from collections import deque
from contextlib import contextmanager
import warnings
from queue import Empty, Full, Queue
from threading import Event, Thread
from time import monotonic

import av
import cv2
import numpy as np

from heatmap import VectorOverlay, sample_outline


@contextmanager
def latest_pairs(source, network):
    """Keep only the latest adjacent pair while analysis or rendering is busy."""
    queue = Queue(1)
    stop = Event()
    ended = Event()
    errors = []

    def read():
        try:
            options = {"rtsp_transport":"tcp"} if network else {}
            with av.open(str(source), options=options, timeout=(6, 5)) as media:
                origin = previous = None
                for frame in media.decode(video=0):
                    if stop.is_set():
                        break
                    if frame.time is None:
                        raise ValueError("Source has no presentation timestamps.")
                    t = float(frame.time)
                    if origin is None:
                        origin, wall = t, monotonic()
                    if not network and stop.wait(max(0, wall+t-origin-monotonic())):
                        break
                    if previous is not None:
                        pt, pf = previous
                        if t <= pt:
                            raise ValueError("Source timestamps do not increase.")
                        pair = (pf,frame,t-pt,t-origin)
                        try:
                            queue.put_nowait(pair)
                        except Full:
                            try:
                                queue.get_nowait()
                            except Empty:
                                pass
                            queue.put_nowait(pair)
                    previous = t,frame
        except Exception as exc:
            errors.append("Stream unavailable. Check the RTSP address, credentials, network and camera timestamps." if network else str(exc))
        finally:
            ended.set()

    thread = Thread(target=read,daemon=True)
    thread.start()

    def pairs():
        while not ended.is_set() or not queue.empty():
            try:
                yield queue.get(timeout=.2)
            except Empty:
                continue
        if errors:
            raise ValueError(errors[0])
    try:
        yield pairs()
    finally:
        stop.set()
        thread.join(timeout=7)


class LiveField:
    def __init__(self,cfg,level,outline=None,sections=(),drawn_lines=(),frame_shape=None):
        self.pixel_mode = cfg is None
        self.shape = frame_shape if self.pixel_mode else (cfg.height, cfg.width)
        height, width = self.shape
        rows, cols = self.shape if self.pixel_mode else cfg.shape
        stride = max(1, int(np.ceil(max(rows, cols) / 850)))
        row, col = np.indices(((rows+stride-1)//stride, (cols+stride-1)//stride))
        if self.pixel_mode:
            u, v = col*stride, row*stride
            self.resolution = stride  # Original video pixels per tracking pixel.
            self.window = 24
        else:
            xs, ys = cfg.transform * ((col+.5)*stride, (row+.5)*stride)
            z = np.full(xs.shape, cfg.get_z_a(level))
            u, v = cfg.project_grid(xs, ys, z)
            self.resolution = cfg.resolution*stride
            self.window = max(12, int(round(cfg.window_size/stride)))
        self.maps = u.astype("float32"), v.astype("float32")
        valid = np.isfinite(u+v) & (u>=1) & (v>=1) & (u<width-1) & (v<height-1)
        step = max(6, self.window // 2)
        self.rows = np.arange(step//2, row.shape[0]-step//2, step)
        self.cols = np.arange(step//2, col.shape[1]-step//2, step)
        if not len(self.rows) or not len(self.cols):
            raise ValueError("Calibration area is too small for its correlation window.")
        if min(len(self.rows),len(self.cols))<2:
            raise ValueError("At least two tracking rows and columns are needed.")
        centers = np.ix_(self.rows,self.cols)
        self.overlay = VectorOverlay(u[centers],v[centers],step*self.resolution,
                                     step*self.resolution,self.shape,outline)
        self.valid = valid
        self.background = None
        self.history = deque(maxlen=12)
        self.sections = []
        water_z = None if self.pixel_mode else cfg.get_z_a(level)
        profiles = [(xyz[:, :2], xyz[:, 2] < water_z) for xyz in sections]
        for line in drawn_lines:
            ends = np.asarray(line, dtype=float)
            if ends.shape != (2, 2) or not np.isfinite(ends).all() or ((ends < 0) | (ends > 1)).any():
                raise ValueError("Line endpoints must be inside the camera image.")
            ends *= [width-1, height-1]
            length = np.linalg.norm(ends[1]-ends[0])
            if length < 1:
                raise ValueError("Choose two distinct endpoints.")
            points = np.linspace(*ends, max(2, int(length / 25) + 1))
            if self.pixel_mode:
                self.add_section(points / stride, points, np.ones(len(points), dtype=bool))
                continue
            world = cfg.unproject_points(points.tolist(), float(water_z))
            if not np.isfinite(world).all():
                raise ValueError("The drawn line cannot be projected onto the water surface.")
            profiles.append((world[:, :2], np.ones(len(world), dtype=bool)))
        for coords, wet in profiles:
            # Station positions on the water plane and normal to the surveyed line.
            c, r = (~cfg.transform) * (coords[:, 0], coords[:, 1])
            grid = np.column_stack((c, r)) / stride - .5
            xy = np.column_stack((coords, np.full(len(coords), water_z)))
            uv = np.asarray(cfg.project_points(xy.tolist()))
            self.add_section(grid, uv, wet)

    def add_section(self, grid, uv, wet):
        tangent = np.gradient(grid, axis=0)
        normal = np.column_stack((-tangent[:, 1], tangent[:, 0]))
        normal /= np.maximum(np.linalg.norm(normal, axis=1, keepdims=True), 1e-12)
        self.sections.append((grid, normal, uv, wet))

    def measure(self, prev, current, dt, maximum):
        if prev.shape[:2]!=self.shape or current.shape[:2]!=self.shape:
            raise ValueError("Camera resolution differs from the selected calibration.")
        a,b = [cv2.remap(cv2.cvtColor(frame,cv2.COLOR_BGR2GRAY),*self.maps,
                          cv2.INTER_LINEAR).astype("float32") for frame in (prev,current)]
        if self.background is None:
            self.background = (a + b) * .5
        # Remove stationary bed detail before measuring the moving surface texture.
        prev_signal = a - self.background
        curr_signal = b - self.background
        cv2.accumulateWeighted((a + b) * .5, self.background, .12)
        velocities = np.full((len(self.rows), len(self.cols), 2), np.nan)
        half = self.window // 2
        margin = max(3, half // 2)
        for i, y in enumerate(self.rows):
            for j, x in enumerate(self.cols):
                if not self.overlay.visible[i, j]:
                    continue
                radius = half + margin
                if y < radius or x < radius or y+radius > a.shape[0] or x+radius > a.shape[1]:
                    continue
                region = np.s_[y-radius:y+radius, x-radius:x+radius]
                template = prev_signal[y-half:y+half, x-half:x+half]
                if not self.valid[region].all() or template.std() < 2:
                    continue
                corr = cv2.matchTemplate(curr_signal[region], template, cv2.TM_CCOEFF_NORMED)
                _, score, _, (cx, cy) = cv2.minMaxLoc(corr)
                if score < .5 or min(cx, cy) == 0 or max(cx, cy) >= margin*2:
                    continue
                dx = _peak_offset(*corr[cy, cx-1:cx+2])
                dy = _peak_offset(*corr[cy-1:cy+2, cx])
                velocities[i, j] = (np.array([cx+dx, cy+dy])-margin)*self.resolution/dt

        self.history.append(velocities)
        history = np.stack(self.history)
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", message="All-NaN slice encountered")
            median = np.nanmedian(history, axis=0)
        # At least three observations agreeing to within 60 degrees of the median.
        speed = np.linalg.norm(median, axis=-1)
        agreement = (history*median).sum(axis=-1) > .5*np.linalg.norm(history, axis=-1)*speed
        count = np.isfinite(history[..., 0]).sum(axis=0)
        median[(agreement.sum(axis=0) < np.maximum(3, .65*count)) | (speed < .025)] = np.nan
        image = self.overlay.paint_flow(current, median, maximum, self.sections, self.rows, self.cols,
                                        unit="px/s" if self.pixel_mode else "m/s")
        coverage = np.isfinite(median[..., 0]).sum() / max(1, self.overlay.visible.sum())
        return image, float(coverage)


def _peak_offset(left, center, right):
    curvature = left - 2*center + right
    return .5*(left-right)/curvature if curvature < -1e-6 else 0.


class Stabilizer:
    """Register frames to static landmarks outside the configured water polygon."""
    def __init__(self,frame,polygon):
        self.ref = cv2.cvtColor(frame,cv2.COLOR_BGR2GRAY)
        mask = np.full(self.ref.shape,255,np.uint8)
        cv2.fillPoly(mask,[np.int32(polygon)],0)
        self.points = cv2.goodFeaturesToTrack(self.ref,150,.01,10,mask=mask)
        if self.points is None or len(self.points)<6:
            raise ValueError("Insufficient stationary landmarks for camera stabilization.")

    def apply(self,frame):
        gray = cv2.cvtColor(frame,cv2.COLOR_BGR2GRAY)
        points,ok,_ = cv2.calcOpticalFlowPyrLK(self.ref,gray,self.points,None)
        good = ok.ravel().astype(bool)
        if good.sum()<6:
            raise ValueError("Lost stationary landmarks. Check camera position.")
        matrix,inliers = cv2.estimateAffinePartial2D(points[good],self.points[good])
        if matrix is None or inliers.sum()<6:
            raise ValueError("Unable to stabilize the camera view.")
        return cv2.warpAffine(frame,matrix,(gray.shape[1],gray.shape[0]))


def replay_frames(source, network, cfg, stage, maximum, duration, polygon, should_stop, drawn_lines=None):
    """Yield measured frames paced by source timestamps for display or export."""
    from acquisition import SAMPLE
    outline = sample_outline(source, stage)
    sections = []
    # Surveys belong to this specific recording/site; use them at any gauge stage.
    if cfg is not None and drawn_lines is None and sample_outline(source, 0) is not None:
        from analysis import survey
        sections = [survey(path) for path in sorted(SAMPLE.glob("*.csv"))]
    field = None
    first = last = None
    stabilizer = None
    with latest_pairs(source, network) as pairs:
        for prev, current, dt, stamp in pairs:
            if should_stop():
                break
            if first is None:
                first = stamp
            if stamp - first >= duration:
                break
            if last is not None and stamp - last < .12:
                continue
            a, b = (frame.to_ndarray(format="bgr24") for frame in (prev, current))
            if field is None:
                field = LiveField(cfg, stage, outline if cfg is not None else None, sections,
                                  drawn_lines or (), frame_shape=a.shape[:2])
            if polygon is not None and stabilizer is None:
                stabilizer = Stabilizer(a, polygon)
            if stabilizer is not None:
                a, b = stabilizer.apply(a), stabilizer.apply(b)
            image, coverage = field.measure(a, b, dt, maximum)
            last = stamp
            yield image, stamp - first, coverage, dt


class LiveRun:
    def __init__(self):
        self.stop = Event()
        self.seen = monotonic()
        self.snapshot = None
        self.done = False
        self.error = None
        self.export = None

    def process(self,source,network,cfg,stage,maximum,duration,path,polygon,drawn_lines=None):
        from heatmap import mp4_writer
        try:
            def should_stop():
                return self.stop.is_set() or monotonic() - self.seen > 20

            with mp4_writer(path, 8) as write:
                for image, elapsed, coverage, dt in replay_frames(
                    source, network, cfg, stage, maximum, duration, polygon, should_stop, drawn_lines
                ):
                    write(image, elapsed)
                    w = min(image.shape[1], 960)
                    thumb = cv2.resize(image, (w, round(image.shape[0] * w / image.shape[1])))
                    ok, jpeg = cv2.imencode(".jpg", thumb)
                    if ok:
                        self.snapshot = jpeg.tobytes(), elapsed, coverage, dt
            self.export = path
        except Exception as exc:
            self.error = str(exc)
        finally:
            self.done = True
