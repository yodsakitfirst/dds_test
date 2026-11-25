"""Low-latency calibrated window correlation and paced file/RTSP acquisition."""
from contextlib import contextmanager
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
    def __init__(self,cfg,level,outline=None):
        # Coarsen large grids for a responsive preview while keeping metric scale.
        rows,cols = cfg.shape
        stride = max(1,int(np.ceil(max(rows,cols)/420)))
        self.resolution = cfg.resolution*stride
        row,col = np.indices(((rows+stride-1)//stride,(cols+stride-1)//stride))
        xs,ys = cfg.transform*((col+.5)*stride,(row+.5)*stride)
        z = np.full(xs.shape,cfg.get_z_a(level))
        u,v = cfg.project_grid(xs,ys,z)
        self.maps = u.astype("float32"),v.astype("float32")
        self.shape = cfg.height,cfg.width
        # Inside these windows both images must contain calibrated source pixels.
        valid = np.isfinite(u+v) & (u>=1) & (v>=1) & (u<cfg.width-1) & (v<cfg.height-1)
        self.window = max(12,int(round(cfg.window_size/stride)))
        step = self.window
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
        self.hann = cv2.createHanningWindow((step,step),cv2.CV_32F)

    def measure(self, prev, current, dt, maximum):
        if prev.shape[:2]!=self.shape or current.shape[:2]!=self.shape:
            raise ValueError("Camera resolution differs from the selected calibration.")
        a,b = [cv2.remap(cv2.cvtColor(frame,cv2.COLOR_BGR2GRAY),*self.maps,
                          cv2.INTER_LINEAR).astype("float32") for frame in (prev,current)]
        vx = np.full((len(self.rows),len(self.cols)),np.nan)
        vy = np.full_like(vx,np.nan)
        for i,y in enumerate(self.rows):
            for j,x in enumerate(self.cols):
                top,left = y-self.window//2,x-self.window//2
                sl = np.s_[top:top+self.window,left:left+self.window]
                if not self.valid[sl].all() or min(a[sl].std(),b[sl].std())<3:
                    continue
                shift,score = cv2.phaseCorrelate(a[sl].copy(),b[sl].copy(),self.hann)
                if score >= .4 and max(map(abs,shift))<self.window/4:
                    vx[i,j],vy[i,j] = np.asarray(shift)*self.resolution/dt
        return self.overlay.paint(current,vx,vy,maximum),float(np.isfinite(vx).mean())

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


class LiveRun:
    def __init__(self):
        self.stop = Event()
        self.seen = monotonic()
        self.snapshot = None
        self.done = False
        self.error = None
        self.export = None

    def process(self,source,network,cfg,stage,maximum,duration,path,polygon):
        from heatmap import mp4_writer
        try:
            field = LiveField(cfg,stage,sample_outline(source,stage))
            first = stabilizer = None
            with latest_pairs(source,network) as pairs, mp4_writer(path,8) as write:
                for prev,current,dt,stamp in pairs:
                    if self.stop.is_set() or monotonic()-self.seen>20:
                        break
                    if first is not None and stamp-first >= duration:
                        break
                    if first is not None and stamp-last<.12:
                        continue
                    a,b = (f.to_ndarray(format="bgr24") for f in (prev,current))
                    if first is None:
                        first = stamp
                        if polygon is not None:
                            stabilizer = Stabilizer(a,polygon)
                    if stabilizer is not None:
                        a,b = stabilizer.apply(a),stabilizer.apply(b)
                    image,coverage = field.measure(a,b,dt,maximum)
                    write(image,stamp-first)
                    w = min(image.shape[1],960)
                    thumb = cv2.resize(image,(w,round(image.shape[0]*w/image.shape[1])))
                    ok,jpeg = cv2.imencode(".jpg",thumb)
                    if ok:
                        self.snapshot = jpeg.tobytes(),stamp-first,coverage,dt
                    last = stamp
            self.export = path
        except Exception as exc:
            self.error = str(exc)
        finally:
            self.done = True
