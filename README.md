# NEXUS — Operations Command Dashboard

A cyberpunk-style analytical control room dashboard built with Flask.

## Features
- **Background video** — drop a `bg.mp4` into `static/videos/` and it plays at 18% opacity behind everything
- **Fallback particle canvas** — animated nodes + edges when no video is present
- **Live camera grid** — 8 simulated feeds with alert states
- **Network topology SVG** — animated packet flow between nodes
- **Real-time KPI cards** — CPU, Memory, Bandwidth, Threats (polled every 2.5s)
- **Dual sparkline chart** — live CPU vs Memory history
- **3 donut charts** — resource usage gauges
- **Live event log** — auto-scrolling alert feed

## Setup

```bash
pip install -r requirements.txt
python app.py
```

Then open **http://localhost:5000**

## Adding a background video

Place any `.mp4` file at:
```
static/videos/bg.mp4
```

It will play automatically at low opacity (18%) creating the immersive control-room feel from the reference image. Recommended: a dark data-center or network loop. Free sources: Pexels, Pixabay.

## Customisation

| What | Where |
|------|-------|
| Color palette | CSS `:root` variables in `dashboard.html` |
| Poll interval | `setInterval(fetchMetrics, 2500)` |
| Camera zones | `/api/camera_feed` in `app.py` |
| Alert messages | `EVENTS` array in dashboard script |
| Node layout | `nodeData` / `edges` arrays in dashboard script |
