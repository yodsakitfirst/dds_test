"""Camera-space heatmaps and timestamped, browser-compatible MP4 encoding."""
from contextlib import contextmanager
from fractions import Fraction
from pathlib import Path
from uuid import uuid4

import av
import cv2
import numpy as np


# Conservative visible-water outline traced on the first stabilized Ngwerere frame.
# Display mask only; the source measurements and discharge are kept independently.
SAMPLE_SHORE = np.float32([
    (121,0),(312,0),(314,56),(333,119),(359,155),(391,203),(419,222),
    (456,253),(492,269),(534,297),(569,333),(628,371),(667,449),
    (222,449),(225,385),(230,331),(229,275),(207,225),(177,191),
    (158,156),(127,130),(132,92),(124,55),(106,31)
])


def sample_outline(source, level):
    from acquisition import SAMPLE
    if level == 0 and (str(source) == str(SAMPLE/"ngwerere_20191103.mp4")
                       or Path(str(source)).stem == "25628c864ba3c65c36e5345101e9d12d2bdb494fa06ad1fb0759b65ec40eb488"):
        return SAMPLE_SHORE
    return None


class VectorOverlay:
    """Use the local camera projection to orient each velocity glyph."""
    def __init__(self, u, v, dx, dy, shape, outline=None):
        self.centers = np.stack((u, v), axis=-1)
        # Both measurement engines report displacement along image columns and rows
        # of the rectified grid, before projection into the oblique camera view.
        self.col = np.gradient(self.centers, axis=1) / dx
        self.row = np.gradient(self.centers, axis=0) / dy
        h,w = shape
        self.visible = (u>=0) & (u<w) & (v>=0) & (v<h)
        self.mask = None
        if outline is not None:
            self.mask = np.zeros(shape,np.uint8)
            cv2.fillPoly(self.mask,[np.int32(outline*[w/800,h/450])],255)
            idx = np.nonzero(self.visible)
            self.visible[idx] &= self.mask[v[idx].astype(int),u[idx].astype(int)]>0
        spacing = np.minimum(np.linalg.norm(self.col*dx,axis=-1),
                             np.linalg.norm(self.row*dy,axis=-1))
        self.length = np.clip(.85*spacing,5, max(12,w*.018))
        self.palette = cv2.applyColorMap(np.arange(256,dtype=np.uint8)[:,None],
                                         cv2.COLORMAP_TURBO).reshape(-1,3)

    def paint_flow(self, frame, vectors, maximum, sections, rows, cols):
        """Draw the rolling field and measured section-normal velocities."""
        output = frame.copy()
        height, width = frame.shape[:2]

        def arrow(origin, motion, speed, thickness):
            if not np.isfinite(motion).all() or np.linalg.norm(motion) < 2:
                return
            color = self.palette[int(255 * np.clip(speed / maximum, 0, 1))].tolist()
            start = tuple(np.rint(origin).astype(int))
            end = tuple(np.rint(origin + motion).astype(int))
            cv2.arrowedLine(output, start, end, color, thickness, cv2.LINE_AA, tipLength=.18)

        vx, vy = vectors[..., 0], vectors[..., 1]
        motion = self.col * vx[..., None] + self.row * vy[..., None]
        for i, j in zip(*np.where(self.visible & np.isfinite(vx))):
            arrow(self.centers[i, j], .55 * motion[i, j], np.hypot(vx[i,j], vy[i,j]), 1)
        if self.mask is not None:
            cv2.copyTo(frame, cv2.bitwise_not(self.mask), output)
        cv2.addWeighted(output, .58, frame, .42, 0, output)

        for grid, normals, uv, wet in sections:
            if np.isfinite(uv).all():
                cv2.polylines(output, [np.int32(np.rint(uv))], False, (240,165,20), 3, cv2.LINE_AA)
            for (x, y), normal, origin, submerged in zip(grid, normals, uv, wet):
                if not submerged or not np.isfinite(origin).all():
                    continue
                i = int(np.argmin(abs(rows - y)))
                j = int(np.argmin(abs(cols - x)))
                if abs(rows[i]-y) > rows[1]-rows[0] or abs(cols[j]-x) > cols[1]-cols[0]:
                    continue
                # Use only supported neighbouring measurements at this station.
                patch = vectors[max(0,i-1):i+2, max(0,j-1):j+2].reshape(-1, 2)
                valid = patch[np.isfinite(patch).all(axis=1)]
                if len(valid) < 3:
                    continue
                component = float(np.median(valid @ normal))
                direction = self.col[i,j]*normal[0] + self.row[i,j]*normal[1]
                arrow(origin, direction*component, abs(component), max(2, width//550))

        length, bar = min(250, height//3), max(12, width//80)
        x, bottom = max(16, width//24), height-height//18
        for pos in range(length):
            color = self.palette[round(255*pos/max(1,length-1))].tolist()
            cv2.line(output,(x,bottom-pos),(x+bar,bottom-pos),color,1)
        for fraction in np.linspace(0,1,7):
            y = bottom - round(fraction*length)
            label = f"{fraction*maximum:.2f}"
            for color, thickness in [((20,20,20), 4), ((255,255,255), 1)]:
                cv2.putText(output,label,(x+bar+6,y+5),cv2.FONT_HERSHEY_SIMPLEX,.65,color,thickness,cv2.LINE_AA)
        for color, thickness in [((20,20,20), 4), ((255,255,255), 1)]:
            cv2.putText(output,"m/s",(x,bottom+24),cv2.FONT_HERSHEY_SIMPLEX,.65,color,thickness,cv2.LINE_AA)
        return output

    def paint(self, frame, vx, vy, maximum):
        output = frame.copy()
        speed = np.hypot(vx,vy)
        motion = self.col*vx[...,None] + self.row*vy[...,None]
        norm = np.linalg.norm(motion,axis=-1)
        valid = self.visible & np.isfinite(norm) & np.isfinite(self.length)
        origins = self.centers[valid]
        fraction = np.clip(speed[valid]/maximum,0,1)
        length = self.length[valid]*(.4+.6*np.sqrt(fraction))
        directions = motion[valid]/np.maximum(norm[valid,None],1e-12)
        ends = np.rint(origins+directions*length[:,None]).astype(int)
        origins = np.rint(origins).astype(int)
        colors = self.palette[np.uint8(fraction*255)].tolist()
        for start,end,color in zip(origins,ends,colors):
            cv2.arrowedLine(output,tuple(start),tuple(end),color,2,cv2.LINE_AA,tipLength=.38)
        if self.mask is not None:
            cv2.copyTo(frame,cv2.bitwise_not(self.mask),output)
        h,w = output.shape[:2]
        width = min(w-20,260)
        if width>0 and h>=65:
            cv2.rectangle(output,(4,4),(width+16,61),(28,28,28),-1)
            output[12:24,10:10+width] = cv2.resize(self.palette[None],(width,12))
            cv2.putText(output,f"0                 {maximum:.2f}+ m/s",(10,49),
                        cv2.FONT_HERSHEY_SIMPLEX,.62,(255,255,255),1,cv2.LINE_AA)
        return output


@contextmanager
def mp4_writer(path, fps):
    temp = path.with_name(uuid4().hex + ".mp4")
    base = Fraction(1, 90000)
    try:
        with av.open(str(temp), "w", options={"movflags":"+faststart"}) as container:
            stream = container.add_stream("libx264", rate=Fraction(str(fps)).limit_denominator(1001))
            stream.pix_fmt = "yuv420p"
            stream.options = {"preset":"veryfast","crf":"22"}
            stream.codec_context.time_base = base
            last = -1

            def write(image, seconds):
                nonlocal last
                if last < 0:
                    h,w = image.shape[:2]
                    stream.width,stream.height = w+w%2,h+h%2
                h,w = image.shape[:2]
                image = cv2.copyMakeBorder(image,0,h%2,0,w%2,cv2.BORDER_REPLICATE)
                frame = av.VideoFrame.from_ndarray(image,format="bgr24")
                pts = round(seconds/base)
                if pts <= last:
                    raise ValueError("Video timestamps must increase.")
                frame.pts,frame.time_base = pts,base
                last = pts
                for packet in stream.encode(frame):
                    container.mux(packet)
            yield write
            if last < 0:
                raise ValueError("No frames available for export.")
            for packet in stream.encode():
                container.mux(packet)
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def filtered_movie(video, ds, times, level, path):
    """Render the filtered vector for each pair on its ending stabilized frame."""
    xs,ys = ds.xs.values,ds.ys.values
    if min(xs.shape)<2:
        raise ValueError("At least two rows and columns are needed for the overlay.")
    cfg = video.camera_config
    u,v = cfg.project_grid(xs,ys,np.full(xs.shape,cfg.get_z_a(level)))
    overlay = VectorOverlay(u,v,abs(float(ds.x[1]-ds.x[0])),abs(float(ds.y[1]-ds.y[0])),
                            (cfg.height,cfg.width),sample_outline(video.fn,level))
    speed = np.hypot(ds.v_x.values,ds.v_y.values)
    if not np.allclose(ds.time.values,times[1:]):
        raise ValueError("Velocity and video timestamps disagree.")
    finite = speed[np.isfinite(speed)]
    maximum = max(.1,float(np.percentile(finite,99))) if finite.size else 1.
    with mp4_writer(path,video.fps) as write:
        for start in range(0,len(times),8):
            for i,frame in enumerate(video.get_frames_chunk(start,min(start+8,len(times)),method="bgr"),start):
                output = overlay.paint(frame,ds.v_x.values[i-1],ds.v_y.values[i-1],maximum) if i else frame
                write(output,float(times[i]-times[0]))

