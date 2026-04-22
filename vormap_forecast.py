"""Spatial Point Pattern Forecaster — predict future distributions from history.

Takes a time-series of point cloud snapshots and forecasts where points
will likely appear next.  This is an *agentic* capability — the tool
proactively predicts spatial changes so you can act before they happen.

Five forecasting channels:

- **Density Trend** — grid-based density at each timestep, linear
  extrapolation per cell to predict future density surface.
- **Centroid Trajectory** — track centroid movement, predict next position.
- **Spread Forecast** — track standard distance over time, predict
  expansion or contraction.
- **Hotspot Emergence** — detect cells with accelerating density, flag
  as emerging hotspots.
- **Void Prediction** — detect cells losing density, predict future voids.

Usage (Python API)::

    from vormap_forecast import ForecastModel

    fm = ForecastModel(grid_res=12)
    fm.add_snapshot("snapshot_t1.txt")
    fm.add_snapshot("snapshot_t2.txt")
    fm.add_snapshot("snapshot_t3.txt")

    result = fm.forecast(steps=1)
    print(result.trend)           # e.g. "expanding"
    print(result.confidence)      # 0.0–1.0
    for hs in result.hotspots:
        print(f"  hotspot at ({hs.x:.2f}, {hs.y:.2f}) intensity={hs.intensity:.3f}")
    for r in result.recommendations:
        print(f"  >> {r}")

    result.to_json("forecast.json")
    result.to_html("forecast.html")

CLI::

    voronoimap datauni5.txt 5 --forecast-history data1.txt,data2.txt,data3.txt
    voronoimap datauni5.txt 5 --forecast-steps 2
    voronoimap datauni5.txt 5 --forecast-html forecast.html
    voronoimap datauni5.txt 5 --forecast-json forecast.json
    voronoimap datauni5.txt 5 --forecast-grid 15
"""

import json
import math
import os
from collections import namedtuple
from datetime import datetime

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

Hotspot = namedtuple("Hotspot", ["x", "y", "intensity"])
VoidZone = namedtuple("VoidZone", ["x", "y", "loss_rate"])


class ForecastResult:
    """Container for forecast output."""

    __slots__ = ("timestamp", "density_grid", "hotspots", "voids", "trend",
                 "confidence", "recommendations", "centroid_predicted",
                 "centroid_history", "spread_predicted", "spread_history",
                 "grid_res", "bounds", "steps")

    def __init__(self, grid_res=12):
        self.timestamp = datetime.utcnow().isoformat() + "Z"
        self.density_grid = []
        self.hotspots = []
        self.voids = []
        self.trend = "stable"
        self.confidence = 0.0
        self.recommendations = []
        self.centroid_predicted = (0.0, 0.0)
        self.centroid_history = []
        self.spread_predicted = 0.0
        self.spread_history = []
        self.grid_res = grid_res
        self.bounds = (0.0, 0.0, 1.0, 1.0)
        self.steps = 1

    # -- export -------------------------------------------------------------

    def to_dict(self):
        """Serialize to dictionary."""
        return {
            "timestamp": self.timestamp,
            "trend": self.trend,
            "confidence": round(self.confidence, 4),
            "steps": self.steps,
            "grid_res": self.grid_res,
            "bounds": list(self.bounds),
            "centroid_predicted": list(self.centroid_predicted),
            "centroid_history": [list(c) for c in self.centroid_history],
            "spread_predicted": round(self.spread_predicted, 4),
            "spread_history": [round(s, 4) for s in self.spread_history],
            "hotspots": [{"x": round(h.x, 4), "y": round(h.y, 4),
                          "intensity": round(h.intensity, 4)} for h in self.hotspots],
            "voids": [{"x": round(v.x, 4), "y": round(v.y, 4),
                       "loss_rate": round(v.loss_rate, 4)} for v in self.voids],
            "density_grid": [[round(c, 4) for c in row] for row in self.density_grid],
            "recommendations": self.recommendations,
        }

    def to_json(self, path):
        """Write JSON report."""
        with open(path, "w") as f:
            json.dump(self.to_dict(), f, indent=2)
        return path

    def to_html(self, path):
        """Write self-contained interactive HTML report."""
        data_json = json.dumps(self.to_dict())
        html = _HTML_TEMPLATE.replace("/*DATA_PLACEHOLDER*/", data_json)
        with open(path, "w") as f:
            f.write(html)
        return path


# ---------------------------------------------------------------------------
# Forecast Model
# ---------------------------------------------------------------------------

class ForecastModel:
    """Time-series spatial forecaster."""

    __slots__ = ("grid_res", "_snapshots", "_labels")

    def __init__(self, grid_res=12):
        self.grid_res = grid_res
        self._snapshots = []
        self._labels = []

    # -- input --------------------------------------------------------------

    def add_snapshot(self, source, label=None):
        """Add a time-ordered snapshot (filepath or list of (x,y) tuples)."""
        if isinstance(source, str):
            points = _load_points(source)
            if label is None:
                label = os.path.basename(source)
        else:
            points = list(source)
            if label is None:
                label = f"snapshot_{len(self._snapshots)}"
        self._snapshots.append(points)
        self._labels.append(label)
        return self

    # -- forecast -----------------------------------------------------------

    def forecast(self, steps=1):
        """Run forecast and return ForecastResult."""
        if len(self._snapshots) < 2:
            raise ValueError("Need at least 2 snapshots to forecast")

        res = self.grid_res
        result = ForecastResult(grid_res=res)
        result.steps = steps

        # Compute bounds across all snapshots
        all_pts = [p for snap in self._snapshots for p in snap]
        if not all_pts:
            raise ValueError("No points in snapshots")
        xs = [p[0] for p in all_pts]
        ys = [p[1] for p in all_pts]
        margin = 0.05
        xmin, xmax = min(xs), max(xs)
        ymin, ymax = min(ys), max(ys)
        xspan = xmax - xmin or 1.0
        yspan = ymax - ymin or 1.0
        xmin -= xspan * margin
        xmax += xspan * margin
        ymin -= yspan * margin
        ymax += yspan * margin
        result.bounds = (xmin, ymin, xmax, ymax)

        # Build density grids per snapshot
        grids = []
        for snap in self._snapshots:
            grid = [[0.0] * res for _ in range(res)]
            for px, py in snap:
                ci = int((px - xmin) / (xmax - xmin) * res)
                ri = int((py - ymin) / (ymax - ymin) * res)
                ci = max(0, min(res - 1, ci))
                ri = max(0, min(res - 1, ri))
                grid[ri][ci] += 1.0
            # Normalize
            total = sum(sum(row) for row in grid) or 1.0
            grid = [[c / total for c in row] for row in grid]
            grids.append(grid)

        # Channel 1: Density Trend — linear extrapolation per cell
        # Cache slopes/intercepts for reuse in hotspot, void, and confidence
        predicted = [[0.0] * res for _ in range(res)]
        cell_slopes = [[0.0] * res for _ in range(res)]
        cell_intercepts = [[0.0] * res for _ in range(res)]
        n = len(grids)
        for r in range(res):
            for c in range(res):
                series = [grids[t][r][c] for t in range(n)]
                slope, intercept = _linear_fit(series)
                cell_slopes[r][c] = slope
                cell_intercepts[r][c] = intercept
                val = intercept + slope * (n - 1 + steps)
                predicted[r][c] = max(0.0, val)
        # Renormalize
        total_pred = sum(sum(row) for row in predicted) or 1.0
        predicted = [[c / total_pred for c in row] for row in predicted]
        result.density_grid = predicted

        # Channel 2: Centroid Trajectory
        centroids = []
        for snap in self._snapshots:
            if snap:
                cx = sum(p[0] for p in snap) / len(snap)
                cy = sum(p[1] for p in snap) / len(snap)
            else:
                cx, cy = 0.0, 0.0
            centroids.append((cx, cy))
        result.centroid_history = centroids
        if len(centroids) >= 2:
            cx_series = [c[0] for c in centroids]
            cy_series = [c[1] for c in centroids]
            sx, ix = _linear_fit(cx_series)
            sy, iy = _linear_fit(cy_series)
            pred_cx = ix + sx * (n - 1 + steps)
            pred_cy = iy + sy * (n - 1 + steps)
            result.centroid_predicted = (pred_cx, pred_cy)

        # Channel 3: Spread Forecast
        spreads = []
        for i, snap in enumerate(self._snapshots):
            cx, cy = centroids[i]
            if len(snap) > 1:
                sd = math.sqrt(sum((p[0] - cx) ** 2 + (p[1] - cy) ** 2
                                   for p in snap) / len(snap))
            else:
                sd = 0.0
            spreads.append(sd)
        result.spread_history = spreads
        ss, si = _linear_fit(spreads)
        result.spread_predicted = max(0.0, si + ss * (n - 1 + steps))

        # Determine trend
        spread_change = result.spread_predicted - spreads[-1] if spreads else 0.0
        last_c = centroids[-1] if centroids else (0.0, 0.0)
        pred_c = result.centroid_predicted
        shift_dist = math.sqrt((pred_c[0] - last_c[0]) ** 2 +
                               (pred_c[1] - last_c[1]) ** 2)
        diag = math.sqrt(xspan ** 2 + yspan ** 2)
        rel_shift = shift_dist / diag if diag > 0 else 0.0
        rel_spread = spread_change / (spreads[-1] if spreads and spreads[-1] > 0 else 1.0)

        if rel_spread > 0.1:
            result.trend = "expanding"
        elif rel_spread < -0.1:
            result.trend = "contracting"
        elif rel_shift > 0.05:
            # Determine direction
            dx = pred_c[0] - last_c[0]
            dy = pred_c[1] - last_c[1]
            angle = math.atan2(dy, dx) * 180 / math.pi
            if -45 <= angle < 45:
                result.trend = "shifting_E"
            elif 45 <= angle < 135:
                result.trend = "shifting_N"
            elif -135 <= angle < -45:
                result.trend = "shifting_S"
            else:
                result.trend = "shifting_W"
        else:
            result.trend = "stable"

        # Channel 4: Hotspot Emergence (reuse cached slopes)
        if n >= 3:
            for r in range(res):
                for c in range(res):
                    slope = cell_slopes[r][c]
                    if slope > 0.005 and predicted[r][c] > 1.5 / (res * res):
                        hx = xmin + (c + 0.5) / res * (xmax - xmin)
                        hy = ymin + (r + 0.5) / res * (ymax - ymin)
                        result.hotspots.append(Hotspot(hx, hy, slope))
            result.hotspots.sort(key=lambda h: h.intensity, reverse=True)
            result.hotspots = result.hotspots[:10]

        # Channel 5: Void Prediction (reuse cached slopes)
        if n >= 3:
            for r in range(res):
                for c in range(res):
                    slope = cell_slopes[r][c]
                    if slope < -0.003 and grids[-1][r][c] > 0.001:
                        vx = xmin + (c + 0.5) / res * (xmax - xmin)
                        vy = ymin + (r + 0.5) / res * (ymax - ymin)
                        result.voids.append(VoidZone(vx, vy, abs(slope)))
            result.voids.sort(key=lambda v: v.loss_rate, reverse=True)
            result.voids = result.voids[:10]

        # Confidence scoring (reuse cached slopes/intercepts)
        # Higher with more snapshots, lower with high variance in fits
        snapshot_factor = min(1.0, (n - 1) / 5.0)
        # Check density prediction consistency
        total_residual = 0.0
        count_residual = 0
        for r in range(res):
            for c in range(res):
                slope = cell_slopes[r][c]
                intercept = cell_intercepts[r][c]
                for t in range(n):
                    fitted = intercept + slope * t
                    total_residual += abs(fitted - grids[t][r][c])
                count_residual += n
        mean_residual = total_residual / count_residual if count_residual else 0.0
        fit_factor = max(0.0, 1.0 - mean_residual * 50)
        result.confidence = round(snapshot_factor * 0.4 + fit_factor * 0.6, 4)

        # Recommendations
        result.recommendations = _generate_recommendations(result)

        return result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_points(filepath):
    """Load points from whitespace-separated text file."""
    points = []
    with open(filepath, "r") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            if len(parts) >= 2:
                try:
                    x, y = float(parts[0]), float(parts[1])
                    points.append((x, y))
                except ValueError:
                    continue
    return points


def _linear_fit(series):
    """Simple linear regression. Returns (slope, intercept)."""
    n = len(series)
    if n == 0:
        return 0.0, 0.0
    if n == 1:
        return 0.0, series[0]
    sx = sum(range(n))
    sy = sum(series)
    sxx = sum(i * i for i in range(n))
    sxy = sum(i * series[i] for i in range(n))
    denom = n * sxx - sx * sx
    if abs(denom) < 1e-12:
        return 0.0, sy / n
    slope = (n * sxy - sx * sy) / denom
    intercept = (sy - slope * sx) / n
    return slope, intercept


def _generate_recommendations(result):
    """Produce proactive recommendations based on forecast."""
    recs = []
    if result.trend == "expanding":
        recs.append("Distribution is expanding — consider widening your study area "
                    "or increasing sampling density at the periphery.")
    elif result.trend == "contracting":
        recs.append("Distribution is contracting — points are concentrating. "
                    "Investigate potential clustering drivers.")
    elif result.trend.startswith("shifting_"):
        direction = result.trend.split("_")[1]
        recs.append(f"Distribution is shifting {direction} — consider adjusting "
                    "your spatial frame to track the movement.")
    if result.hotspots:
        top = result.hotspots[0]
        recs.append(f"Emerging hotspot detected near ({top.x:.2f}, {top.y:.2f}) "
                    f"with growth rate {top.intensity:.4f}/step — monitor closely.")
    if result.voids:
        top = result.voids[0]
        recs.append(f"Emerging void near ({top.x:.2f}, {top.y:.2f}) — "
                    f"density declining at {top.loss_rate:.4f}/step. "
                    "Points may disappear from this region.")
    if result.confidence < 0.4:
        recs.append("Low forecast confidence — consider adding more historical "
                    "snapshots to improve prediction accuracy.")
    if not recs:
        recs.append("Distribution appears stable. No immediate action needed.")
    return recs


# ---------------------------------------------------------------------------
# HTML Template
# ---------------------------------------------------------------------------

_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>VoronoiMap Spatial Forecast</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,sans-serif;background:#0f172a;color:#e2e8f0;padding:24px}
h1{font-size:1.6rem;margin-bottom:8px;color:#38bdf8}
.subtitle{color:#94a3b8;margin-bottom:24px}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:20px;max-width:1200px;margin:0 auto}
.card{background:#1e293b;border-radius:12px;padding:20px;border:1px solid #334155}
.card h2{font-size:1.1rem;color:#7dd3fc;margin-bottom:12px}
canvas{width:100%;border-radius:8px;background:#0f172a}
.metric{display:flex;justify-content:space-between;padding:8px 0;border-bottom:1px solid #334155}
.metric:last-child{border-bottom:none}
.metric .label{color:#94a3b8}
.metric .value{color:#f1f5f9;font-weight:600}
.rec{background:#1e3a5f;border-left:3px solid #38bdf8;padding:10px 14px;margin-bottom:8px;border-radius:4px;font-size:0.9rem}
.trend-badge{display:inline-block;padding:4px 12px;border-radius:20px;font-size:0.85rem;font-weight:600}
.trend-expanding{background:#065f46;color:#6ee7b7}
.trend-contracting{background:#7f1d1d;color:#fca5a5}
.trend-stable{background:#1e3a5f;color:#7dd3fc}
.trend-shifting{background:#78350f;color:#fcd34d}
.confidence-bar{height:8px;border-radius:4px;background:#334155;margin-top:8px}
.confidence-fill{height:100%;border-radius:4px;transition:width 0.5s}
@media(max-width:768px){.grid{grid-template-columns:1fr}}
</style>
</head>
<body>
<h1>&#x1F52E; Spatial Point Pattern Forecast</h1>
<p class="subtitle">Generated by VoronoiMap vormap_forecast</p>
<div class="grid">
<div class="card">
<h2>Predicted Density Heatmap</h2>
<canvas id="heatmap" width="400" height="400"></canvas>
</div>
<div class="card">
<h2>Forecast Summary</h2>
<div id="metrics"></div>
</div>
<div class="card">
<h2>Centroid Trajectory</h2>
<canvas id="trajectory" width="400" height="300"></canvas>
</div>
<div class="card">
<h2>Recommendations</h2>
<div id="recs"></div>
</div>
</div>
<script>
const DATA = /*DATA_PLACEHOLDER*/{};
(function(){
const d = DATA;
// Heatmap
const hc = document.getElementById('heatmap');
const ctx = hc.getContext('2d');
const g = d.density_grid || [];
const res = g.length;
if(res > 0){
  const cw = hc.width / res;
  const ch = hc.height / res;
  let mx = 0;
  for(let r=0;r<res;r++) for(let c=0;c<res;c++) mx = Math.max(mx, g[r][c]);
  for(let r=0;r<res;r++){
    for(let c=0;c<res;c++){
      const v = mx > 0 ? g[r][c]/mx : 0;
      const h = Math.round(200 - v * 200);
      ctx.fillStyle = `hsl(${h}, 80%, ${20 + v*50}%)`;
      ctx.fillRect(c*cw, r*ch, cw+1, ch+1);
    }
  }
  // Draw hotspots
  const bounds = d.bounds || [0,0,1,1];
  const bw = bounds[2]-bounds[0], bh = bounds[3]-bounds[1];
  (d.hotspots||[]).forEach(hs => {
    const px = (hs.x - bounds[0])/bw * hc.width;
    const py = (hs.y - bounds[1])/bh * hc.height;
    ctx.beginPath(); ctx.arc(px,py,8,0,Math.PI*2);
    ctx.strokeStyle='#f59e0b'; ctx.lineWidth=2; ctx.stroke();
    ctx.fillStyle='rgba(245,158,11,0.3)'; ctx.fill();
  });
}
// Metrics
const mDiv = document.getElementById('metrics');
const trendClass = d.trend === 'stable' ? 'trend-stable' :
  d.trend === 'expanding' ? 'trend-expanding' :
  d.trend === 'contracting' ? 'trend-contracting' : 'trend-shifting';
const conf = Math.round((d.confidence||0)*100);
const confColor = conf > 70 ? '#22c55e' : conf > 40 ? '#eab308' : '#ef4444';
mDiv.innerHTML = `
<div class="metric"><span class="label">Trend</span><span class="trend-badge ${trendClass}">${d.trend||'—'}</span></div>
<div class="metric"><span class="label">Confidence</span><span class="value">${conf}%</span></div>
<div class="confidence-bar"><div class="confidence-fill" style="width:${conf}%;background:${confColor}"></div></div>
<div class="metric"><span class="label">Steps Ahead</span><span class="value">${d.steps||1}</span></div>
<div class="metric"><span class="label">Predicted Centroid</span><span class="value">(${(d.centroid_predicted||[0,0])[0].toFixed(2)}, ${(d.centroid_predicted||[0,0])[1].toFixed(2)})</span></div>
<div class="metric"><span class="label">Predicted Spread</span><span class="value">${(d.spread_predicted||0).toFixed(3)}</span></div>
<div class="metric"><span class="label">Hotspots</span><span class="value">${(d.hotspots||[]).length}</span></div>
<div class="metric"><span class="label">Emerging Voids</span><span class="value">${(d.voids||[]).length}</span></div>
`;
// Trajectory
const tc = document.getElementById('trajectory');
const tctx = tc.getContext('2d');
const hist = d.centroid_history || [];
const pred = d.centroid_predicted || [0,0];
if(hist.length > 0){
  const all = hist.concat([pred]);
  const axs = all.map(p=>p[0]), ays = all.map(p=>p[1]);
  const axmin=Math.min(...axs), axmax=Math.max(...axs);
  const aymin=Math.min(...ays), aymax=Math.max(...ays);
  const aw=axmax-axmin||1, ah=aymax-aymin||1;
  const pad=30;
  const mapX = x => pad + (x-axmin)/aw*(tc.width-2*pad);
  const mapY = y => pad + (y-aymin)/ah*(tc.height-2*pad);
  tctx.strokeStyle='#64748b'; tctx.lineWidth=1;
  tctx.beginPath();
  hist.forEach((p,i)=>{i===0?tctx.moveTo(mapX(p[0]),mapY(p[1])):tctx.lineTo(mapX(p[0]),mapY(p[1]))});
  tctx.stroke();
  hist.forEach((p,i)=>{
    tctx.beginPath(); tctx.arc(mapX(p[0]),mapY(p[1]),4,0,Math.PI*2);
    tctx.fillStyle='#38bdf8'; tctx.fill();
  });
  // Predicted
  tctx.setLineDash([5,5]);
  tctx.beginPath();
  tctx.moveTo(mapX(hist[hist.length-1][0]),mapY(hist[hist.length-1][1]));
  tctx.lineTo(mapX(pred[0]),mapY(pred[1]));
  tctx.strokeStyle='#f59e0b'; tctx.lineWidth=2; tctx.stroke();
  tctx.setLineDash([]);
  tctx.beginPath(); tctx.arc(mapX(pred[0]),mapY(pred[1]),6,0,Math.PI*2);
  tctx.fillStyle='#f59e0b'; tctx.fill();
  tctx.font='11px sans-serif'; tctx.fillStyle='#94a3b8';
  tctx.fillText('predicted',mapX(pred[0])+10,mapY(pred[1])+4);
}
// Recommendations
const rDiv = document.getElementById('recs');
rDiv.innerHTML = (d.recommendations||[]).map(r=>`<div class="rec">${r}</div>`).join('');
})();
</script>
</body>
</html>"""


# ---------------------------------------------------------------------------
# CLI entry point (standalone usage)
# ---------------------------------------------------------------------------

def main():
    """Standalone CLI for vormap_forecast."""
    import sys

    args = sys.argv[1:]
    if not args or "--help" in args:
        print(__doc__)
        return

    history_files = []
    steps = 1
    grid_res = 12
    out_json = None
    out_html = None

    i = 0
    while i < len(args):
        if args[i] == "--forecast-history" and i + 1 < len(args):
            history_files = args[i + 1].split(",")
            i += 2
        elif args[i] == "--forecast-steps" and i + 1 < len(args):
            steps = int(args[i + 1])
            i += 2
        elif args[i] == "--forecast-grid" and i + 1 < len(args):
            grid_res = int(args[i + 1])
            i += 2
        elif args[i] == "--forecast-json" and i + 1 < len(args):
            out_json = args[i + 1]
            i += 2
        elif args[i] == "--forecast-html" and i + 1 < len(args):
            out_html = args[i + 1]
            i += 2
        else:
            # Treat positional args as history files
            history_files.append(args[i])
            i += 1

    if len(history_files) < 2:
        print("Error: need at least 2 snapshot files for forecasting.")
        sys.exit(1)

    fm = ForecastModel(grid_res=grid_res)
    for f in history_files:
        fm.add_snapshot(f)

    result = fm.forecast(steps=steps)

    print(f"Trend: {result.trend}")
    print(f"Confidence: {result.confidence:.1%}")
    print(f"Predicted centroid: ({result.centroid_predicted[0]:.3f}, "
          f"{result.centroid_predicted[1]:.3f})")
    print(f"Predicted spread: {result.spread_predicted:.4f}")
    if result.hotspots:
        print(f"Hotspots ({len(result.hotspots)}):")
        for h in result.hotspots[:5]:
            print(f"  ({h.x:.3f}, {h.y:.3f}) intensity={h.intensity:.4f}")
    if result.voids:
        print(f"Voids ({len(result.voids)}):")
        for v in result.voids[:5]:
            print(f"  ({v.x:.3f}, {v.y:.3f}) loss_rate={v.loss_rate:.4f}")
    print("\nRecommendations:")
    for r in result.recommendations:
        print(f"  • {r}")

    if out_json:
        result.to_json(out_json)
        print(f"\nJSON report: {out_json}")
    if out_html:
        result.to_html(out_html)
        print(f"HTML report: {out_html}")


if __name__ == "__main__":
    main()
