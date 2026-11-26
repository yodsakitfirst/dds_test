export default function ({parentElement, data, setStateValue}) {
  const photo = parentElement.querySelector("img");
  const overlay = parentElement.querySelector("canvas");
  const status = parentElement.querySelector("output");
  const [undo, clear] = parentElement.querySelectorAll("button");
  // Streamlit can rerender with older server state while a click is in flight.
  let points = overlay.selectedPoints ?? data.points.slice();

  function paint() {
    overlay.width = photo.naturalWidth;
    overlay.height = photo.naturalHeight;
    const ctx = overlay.getContext("2d");
    const pixels = points.map(([u,v]) => [u*overlay.width, v*overlay.height]);
    if (data.polygon && pixels.length) {
      ctx.beginPath();
      ctx.moveTo(...pixels[0]);
      for (const p of pixels.slice(1)) ctx.lineTo(...p);
      if (pixels.length === 4) ctx.closePath();
      ctx.strokeStyle = "#00c8ff"; ctx.lineWidth = 3; ctx.stroke();
    }
    pixels.forEach(([x,y],i) => {
      ctx.fillStyle = "#143536"; ctx.beginPath(); ctx.arc(x,y,10,0,2*Math.PI); ctx.fill();
      ctx.fillStyle = "white"; ctx.font = "bold 14px system-ui"; ctx.textAlign = "center";
      ctx.fillText(String(i+1),x,y+5);
    });
    undo.disabled = clear.disabled = !points.length;
    status.textContent = points.length + " points selected";
  }
  function change(next) {
    overlay.selectedPoints = points = next;
    paint();
    setStateValue("points", points);
  }
  photo.onload = paint;
  photo.onerror = () => { status.textContent = "Could not display the preview image."; };
  if (photo.dataset.source !== data.source) {
    photo.dataset.source = data.source;
    photo.src = "data:image/jpeg;base64," + data.image;
  }
  if (photo.complete && photo.naturalWidth) paint();
  overlay.setAttribute("aria-label", data.polygon ? "Analysis corners" : "Calibration landmarks");
  overlay.onclick = event => {
    if (!photo.naturalWidth || points.length >= (data.limit ?? Infinity)) return;
    const r = overlay.getBoundingClientRect();
    change([...points, [(event.clientX-r.left)/r.width, (event.clientY-r.top)/r.height]]);
  };
  undo.onclick = () => change(points.slice(0,-1));
  clear.onclick = () => change([]);
}
