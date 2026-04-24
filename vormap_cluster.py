#!/usr/bin/env python3
"""vormap_cluster — Autonomous Spatial Clustering Analyzer.

Discovers natural clusters in 2-D point sets using multiple algorithms
(k-means, DBSCAN) with automatic parameter selection, silhouette
scoring, and an interactive HTML report.

Features
--------
* **Auto-k selection** — sweeps k=2..K_max and picks the k with the
  best silhouette score, so the user doesn't need to guess.
* **DBSCAN** — density-based clustering with automatic eps estimation
  via the k-distance graph elbow heuristic.
* **Comparison mode** — runs both algorithms and highlights agreement/
  disagreement between them.
* **Proactive recommendations** — suggests whether data has clear
  clusters, is uniformly distributed, or has outlier-heavy pockets.
* **Interactive HTML report** — Canvas scatter plot coloured by cluster,
  silhouette bar chart, summary stats, recommendations.
* **CLI + importable API**.

Usage
-----
    python vormap_cluster.py points.csv                   # auto everything
    python vormap_cluster.py points.csv --k 4             # force k-means k=4
    python vormap_cluster.py points.csv --algo dbscan     # DBSCAN only
    python vormap_cluster.py points.csv --algo both -o report.html
    python vormap_cluster.py --demo                       # built-in demo
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import random
import statistics
import sys
from typing import Dict, List, Optional, Tuple

# ── helpers ──────────────────────────────────────────────────────────

def _euclidean(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    return math.hypot(b[0] - a[0], b[1] - a[1])


def _dist_matrix(points: List[Tuple[float, float]]) -> List[List[float]]:
    """Precompute symmetric pairwise distance matrix (O(n²) once).

    Avoids redundant sqrt recomputation across DBSCAN, silhouette, and
    eps estimation — each of which previously built its own O(n²) distances.
    """
    n = len(points)
    mat: List[List[float]] = [[0.0] * n for _ in range(n)]
    for i in range(n):
        xi, yi = points[i]
        row = mat[i]
        for j in range(i + 1, n):
            d = math.hypot(points[j][0] - xi, points[j][1] - yi)
            row[j] = d
            mat[j][i] = d
    return mat


def _mean_point(pts: List[Tuple[float, float]]) -> Tuple[float, float]:
    n = len(pts)
    if n == 0:
        return (0.0, 0.0)
    return (sum(p[0] for p in pts) / n, sum(p[1] for p in pts) / n)


# ── k-means ──────────────────────────────────────────────────────────

def kmeans(points: List[Tuple[float, float]], k: int,
           max_iter: int = 300, seed: int = 42) -> Tuple[List[int], List[Tuple[float, float]]]:
    """Run k-means clustering.  Returns (labels, centroids)."""
    n = len(points)
    rng = random.Random(seed)
    # k-means++ init
    centroids: List[Tuple[float, float]] = [points[rng.randrange(n)]]
    for _ in range(1, k):
        dists = [min(_euclidean(p, c) ** 2 for c in centroids) for p in points]
        total = sum(dists)
        if total < 1e-12:
            centroids.append(points[rng.randrange(n)])
            continue
        r = rng.random() * total
        cumul = 0.0
        for i, d in enumerate(dists):
            cumul += d
            if cumul >= r:
                centroids.append(points[i])
                break
        else:
            centroids.append(points[-1])

    labels = [0] * n
    for _ in range(max_iter):
        # assign
        changed = False
        for i, p in enumerate(points):
            best_c = min(range(k), key=lambda c: _euclidean(p, centroids[c]))
            if best_c != labels[i]:
                labels[i] = best_c
                changed = True
        if not changed:
            break
        # update centroids
        for c in range(k):
            members = [points[i] for i in range(n) if labels[i] == c]
            if members:
                centroids[c] = _mean_point(members)
    return labels, centroids


# ── DBSCAN ───────────────────────────────────────────────────────────

def dbscan(points: List[Tuple[float, float]], eps: float,
           min_samples: int = 5,
           _dmat: Optional[List[List[float]]] = None) -> List[int]:
    """Density-based clustering.  Returns labels (-1 = noise).

    Accepts an optional precomputed distance matrix (*_dmat*) to avoid
    redundant O(n²) distance recomputation when the caller already has one.
    """
    n = len(points)
    dmat = _dmat if _dmat is not None else _dist_matrix(points)
    labels = [-2] * n  # unvisited

    # Precompute neighbour lists once — O(n²) scan but only one pass
    # instead of O(n) per _region_query call (which was invoked O(n) times).
    neighbours_of: List[List[int]] = [[] for _ in range(n)]
    for i in range(n):
        row = dmat[i]
        nbs = neighbours_of[i]
        for j in range(n):
            if row[j] <= eps:
                nbs.append(j)

    cluster_id = -1
    for i in range(n):
        if labels[i] != -2:
            continue
        neighbours = neighbours_of[i]
        if len(neighbours) < min_samples:
            labels[i] = -1  # noise
            continue
        cluster_id += 1
        labels[i] = cluster_id
        seed_set = list(neighbours)
        j = 0
        while j < len(seed_set):
            q = seed_set[j]
            if labels[q] == -1:
                labels[q] = cluster_id
            elif labels[q] == -2:
                labels[q] = cluster_id
                q_neighbours = neighbours_of[q]
                if len(q_neighbours) >= min_samples:
                    seed_set.extend(q_neighbours)
            j += 1
    return labels


def estimate_eps(points: List[Tuple[float, float]], k: int = 5,
                 _dmat: Optional[List[List[float]]] = None) -> float:
    """Estimate DBSCAN eps via k-distance elbow heuristic."""
    n = len(points)
    dmat = _dmat if _dmat is not None else _dist_matrix(points)
    k_dists = []
    for i in range(n):
        row = dmat[i]
        # Sort distances excluding self (row[i]==0); O(n log n) per point
        # but avoids recomputing sqrt for every pair.
        dists = sorted(row[j] for j in range(n) if j != i)
        k_dists.append(dists[min(k - 1, len(dists) - 1)])
    k_dists.sort()
    # find elbow: max second derivative
    if len(k_dists) < 3:
        return k_dists[-1] if k_dists else 1.0
    best_idx = len(k_dists) // 2
    best_val = 0.0
    for i in range(1, len(k_dists) - 1):
        second_deriv = k_dists[i + 1] - 2 * k_dists[i] + k_dists[i - 1]
        if second_deriv > best_val:
            best_val = second_deriv
            best_idx = i
    return k_dists[best_idx] * 0.5


# ── Silhouette score ─────────────────────────────────────────────────

def silhouette_scores(points: List[Tuple[float, float]],
                      labels: List[int],
                      _dmat: Optional[List[List[float]]] = None) -> List[float]:
    """Per-point silhouette coefficients.  Ignores noise (-1).

    Uses a precomputed distance matrix when available, avoiding O(n²)
    redundant distance calls.  Also pre-groups point indices by cluster
    so each point's a_i / b_i computation is a simple sum over the
    pre-built index lists instead of scanning all n points per cluster.
    """
    n = len(points)
    unique = set(labels) - {-1}
    if len(unique) < 2:
        return [0.0] * n

    dmat = _dmat if _dmat is not None else _dist_matrix(points)

    # Pre-group indices by cluster label — avoids repeated linear scans.
    cluster_members: Dict[int, List[int]] = {c: [] for c in unique}
    for idx, lbl in enumerate(labels):
        if lbl in cluster_members:
            cluster_members[lbl].append(idx)

    scores = [0.0] * n
    for i in range(n):
        li = labels[i]
        if li == -1:
            scores[i] = -1.0
            continue
        same = cluster_members[li]
        same_len = len(same) - 1  # exclude self
        if same_len <= 0:
            scores[i] = 0.0
            continue
        row = dmat[i]
        a_i = (sum(row[j] for j in same) ) / same_len  # row[i]==0 so sum is fine minus 0
        # Actually row[i]==0 adds 0 to sum, but we divided by same_len (count-1),
        # so we need to not include self. Just subtract 0 — it's fine.
        b_i = float("inf")
        for c in unique:
            if c == li:
                continue
            members = cluster_members[c]
            if members:
                avg_d = sum(row[j] for j in members) / len(members)
                if avg_d < b_i:
                    b_i = avg_d
        if b_i == float("inf"):
            b_i = 0.0
        scores[i] = (b_i - a_i) / max(a_i, b_i, 1e-12)
    return scores


def mean_silhouette(points, labels, _dmat=None) -> float:
    ss = [s for s, l in zip(silhouette_scores(points, labels, _dmat=_dmat), labels) if l != -1]
    return statistics.mean(ss) if ss else 0.0


# ── Auto-k ───────────────────────────────────────────────────────────

def auto_k(points: List[Tuple[float, float]], k_max: int = 10) -> Tuple[int, Dict[int, float]]:
    """Find optimal k for k-means via silhouette sweep.  Returns (best_k, scores_dict).

    Precomputes the distance matrix once and reuses it for every
    silhouette evaluation — previously each k recomputed O(n²) distances.
    """
    k_max = min(k_max, len(points) - 1, 15)
    dmat = _dist_matrix(points)
    scores: Dict[int, float] = {}
    for k in range(2, k_max + 1):
        labels, _ = kmeans(points, k)
        scores[k] = mean_silhouette(points, labels, _dmat=dmat)
    best_k = max(scores, key=scores.get) if scores else 2
    return best_k, scores


# ── Analysis & recommendations ───────────────────────────────────────

def analyze(points: List[Tuple[float, float]],
            algo: str = "both",
            forced_k: Optional[int] = None) -> dict:
    """Run clustering analysis.  Returns a rich result dict."""
    result: dict = {"n_points": len(points), "algo": algo, "recommendations": []}

    # k-means
    if algo in ("kmeans", "both"):
        if forced_k:
            best_k = forced_k
            k_scores = {forced_k: 0.0}
        else:
            best_k, k_scores = auto_k(points)
        km_labels, km_centroids = kmeans(points, best_k)
        km_sil = mean_silhouette(points, km_labels)
        k_scores[best_k] = km_sil
        km_cluster_sizes = {}
        for l in km_labels:
            km_cluster_sizes[l] = km_cluster_sizes.get(l, 0) + 1
        result["kmeans"] = {
            "k": best_k,
            "labels": km_labels,
            "centroids": [(round(c[0], 2), round(c[1], 2)) for c in km_centroids],
            "silhouette": round(km_sil, 4),
            "k_scores": {k: round(v, 4) for k, v in sorted(k_scores.items())},
            "cluster_sizes": km_cluster_sizes,
        }

    # DBSCAN — share a single distance matrix across eps estimation,
    # clustering, and silhouette scoring (3× O(n²) → 1× O(n²)).
    if algo in ("dbscan", "both"):
        dmat = _dist_matrix(points)
        eps = estimate_eps(points, _dmat=dmat)
        db_labels = dbscan(points, eps, _dmat=dmat)
        db_sil = mean_silhouette(points, db_labels, _dmat=dmat)
        n_clusters = len(set(db_labels) - {-1})
        n_noise = db_labels.count(-1)
        db_cluster_sizes = {}
        for l in db_labels:
            if l >= 0:
                db_cluster_sizes[l] = db_cluster_sizes.get(l, 0) + 1
        result["dbscan"] = {
            "eps": round(eps, 4),
            "n_clusters": n_clusters,
            "n_noise": n_noise,
            "labels": db_labels,
            "silhouette": round(db_sil, 4),
            "cluster_sizes": db_cluster_sizes,
        }

    # Agreement analysis
    if algo == "both" and "kmeans" in result and "dbscan" in result:
        n = len(points)
        agree = sum(1 for i in range(n)
                     if result["dbscan"]["labels"][i] >= 0
                     and result["kmeans"]["labels"][i] == result["dbscan"]["labels"][i])
        non_noise = sum(1 for l in result["dbscan"]["labels"] if l >= 0)
        agreement = agree / non_noise if non_noise > 0 else 0.0
        result["agreement"] = round(agreement, 4)

    # Recommendations
    recs = result["recommendations"]
    if "kmeans" in result:
        sil = result["kmeans"]["silhouette"]
        if sil > 0.7:
            recs.append("Strong cluster structure detected — data has well-separated groups.")
        elif sil > 0.4:
            recs.append("Moderate clustering — groups exist but overlap somewhat.")
        elif sil > 0.2:
            recs.append("Weak clustering — consider whether grouping is meaningful.")
        else:
            recs.append("No clear cluster structure — data may be uniformly distributed.")

    if "dbscan" in result:
        noise_pct = result["dbscan"]["n_noise"] / len(points) * 100
        if noise_pct > 30:
            recs.append(f"High noise ({noise_pct:.0f}%) — many points don't belong to dense regions. Consider spatial outlier analysis.")
        if result["dbscan"]["n_clusters"] == 0:
            recs.append("DBSCAN found no clusters — try adjusting eps or min_samples, or data may lack density variation.")
        elif result["dbscan"]["n_clusters"] == 1:
            recs.append("DBSCAN found only 1 cluster — data may be a single dense blob with outliers.")

    if "kmeans" in result and "dbscan" in result:
        if result.get("agreement", 0) < 0.3:
            recs.append("Low agreement between k-means and DBSCAN — clusters may be non-convex or have variable density.")
        elif result.get("agreement", 0) > 0.7:
            recs.append("High algorithm agreement — cluster structure is robust across methods.")

    return result


# ── HTML report ──────────────────────────────────────────────────────

_COLORS = [
    "#4e79a7", "#f28e2b", "#e15759", "#76b7b2", "#59a14f",
    "#edc948", "#b07aa1", "#ff9da7", "#9c755f", "#bab0ac",
    "#6a3d9a", "#33a02c", "#fb9a99", "#1f78b4", "#ff7f00",
]


def generate_html(points: List[Tuple[float, float]], result: dict) -> str:
    """Generate interactive HTML report."""
    pts_json = json.dumps([(round(p[0], 2), round(p[1], 2)) for p in points])
    result_safe = {k: v for k, v in result.items()
                   if k not in ("kmeans", "dbscan")}
    # Strip labels from serialized (too large), keep rest
    km_info = None
    db_info = None
    if "kmeans" in result:
        km_info = {k: v for k, v in result["kmeans"].items() if k != "labels"}
        km_info["labels"] = result["kmeans"]["labels"]
    if "dbscan" in result:
        db_info = {k: v for k, v in result["dbscan"].items() if k != "labels"}
        db_info["labels"] = result["dbscan"]["labels"]

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Spatial Clustering Report — vormap_cluster</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:'Segoe UI',system-ui,sans-serif;background:#0f172a;color:#e2e8f0;min-height:100vh}}
.header{{background:linear-gradient(135deg,#1e293b,#334155);padding:28px 32px;border-bottom:2px solid #6366f1}}
.header h1{{font-size:1.6rem;font-weight:700;color:#a5b4fc}}
.header p{{color:#94a3b8;margin-top:4px;font-size:.9rem}}
.grid{{display:grid;grid-template-columns:1fr 1fr;gap:20px;padding:24px 32px;max-width:1400px;margin:0 auto}}
.card{{background:#1e293b;border-radius:12px;border:1px solid #334155;padding:20px;overflow:hidden}}
.card h2{{font-size:1.05rem;color:#a5b4fc;margin-bottom:12px;display:flex;align-items:center;gap:8px}}
.full{{grid-column:1/-1}}
canvas{{width:100%;border-radius:8px;background:#0f172a;cursor:crosshair}}
.stat{{display:inline-block;background:#334155;border-radius:8px;padding:10px 16px;margin:4px;text-align:center}}
.stat .val{{font-size:1.3rem;font-weight:700;color:#a5b4fc}}
.stat .lbl{{font-size:.75rem;color:#94a3b8;margin-top:2px}}
.rec{{background:#1e1b4b;border-left:3px solid #6366f1;padding:10px 14px;margin:6px 0;border-radius:0 8px 8px 0;font-size:.88rem;color:#c7d2fe}}
.tabs{{display:flex;gap:4px;margin-bottom:14px}}
.tab{{padding:6px 16px;border-radius:6px;cursor:pointer;font-size:.85rem;border:1px solid #475569;color:#94a3b8;background:transparent;transition:.2s}}
.tab.active{{background:#6366f1;color:#fff;border-color:#6366f1}}
.sil-bar{{display:flex;align-items:center;gap:6px;margin:2px 0;font-size:.78rem}}
.sil-bar .bar{{height:10px;border-radius:4px;min-width:2px}}
table{{width:100%;border-collapse:collapse;font-size:.85rem}}
th,td{{padding:6px 10px;text-align:left;border-bottom:1px solid #334155}}
th{{color:#a5b4fc;font-weight:600}}
@media(max-width:800px){{.grid{{grid-template-columns:1fr}}}}
</style></head><body>
<div class="header">
<h1>🔬 Spatial Clustering Report</h1>
<p>vormap_cluster — {result['n_points']} points analyzed</p>
</div>
<div class="grid" id="grid"></div>
<script>
const pts={pts_json};
const km={json.dumps(km_info)};
const db={json.dumps(db_info)};
const meta={json.dumps(result_safe)};
const colors={json.dumps(_COLORS)};
const grid=document.getElementById('grid');

function h(tag,cls,html){{const e=document.createElement(tag);if(cls)e.className=cls;if(html)e.innerHTML=html;return e}}
function card(title,cls){{const c=h('div','card'+(cls?' '+cls:''));c.appendChild(h('h2','','<span>'+title+'</span>'));return c}}

// Stats card
const statsCard=card('📊 Overview','full');
let statsHtml='';
statsHtml+='<div class="stat"><div class="val">'+pts.length+'</div><div class="lbl">Points</div></div>';
if(km)statsHtml+='<div class="stat"><div class="val">'+km.k+'</div><div class="lbl">K-Means K</div></div><div class="stat"><div class="val">'+km.silhouette+'</div><div class="lbl">K-Means Silhouette</div></div>';
if(db)statsHtml+='<div class="stat"><div class="val">'+db.n_clusters+'</div><div class="lbl">DBSCAN Clusters</div></div><div class="stat"><div class="val">'+db.n_noise+'</div><div class="lbl">Noise Points</div></div><div class="stat"><div class="val">'+db.silhouette+'</div><div class="lbl">DBSCAN Silhouette</div></div>';
if(meta.agreement!=null)statsHtml+='<div class="stat"><div class="val">'+(meta.agreement*100).toFixed(1)+'%</div><div class="lbl">Agreement</div></div>';
statsCard.appendChild(h('div','',statsHtml));
grid.appendChild(statsCard);

// Scatter plot
const scatterCard=card('🗺️ Cluster Map','full');
const tabDiv=h('div','tabs');
let activeAlgo=km?'kmeans':'dbscan';
function mkTab(name,label){{const b=h('button','tab'+(name===activeAlgo?' active':''),label);b.onclick=()=>{{activeAlgo=name;document.querySelectorAll('.tab').forEach(t=>t.classList.remove('active'));b.classList.add('active');drawScatter()}};tabDiv.appendChild(b)}}
if(km)mkTab('kmeans','K-Means');
if(db)mkTab('dbscan','DBSCAN');
scatterCard.appendChild(tabDiv);
const cv=document.createElement('canvas');cv.width=900;cv.height=500;
scatterCard.appendChild(cv);grid.appendChild(scatterCard);

function drawScatter(){{
const ctx=cv.getContext('2d');const W=cv.width,H=cv.height;
ctx.fillStyle='#0f172a';ctx.fillRect(0,0,W,H);
const labels=activeAlgo==='kmeans'?km.labels:db.labels;
let xmin=Infinity,xmax=-Infinity,ymin=Infinity,ymax=-Infinity;
pts.forEach(p=>{{if(p[0]<xmin)xmin=p[0];if(p[0]>xmax)xmax=p[0];if(p[1]<ymin)ymin=p[1];if(p[1]>ymax)ymax=p[1]}});
const pad=40;const dx=xmax-xmin||1,dy=ymax-ymin||1;
function tx(x){{return pad+(x-xmin)/dx*(W-2*pad)}}
function ty(y){{return H-pad-(y-ymin)/dy*(H-2*pad)}}
// grid
ctx.strokeStyle='#1e293b';ctx.lineWidth=1;
for(let i=0;i<=4;i++){{const x=pad+i*(W-2*pad)/4;ctx.beginPath();ctx.moveTo(x,pad);ctx.lineTo(x,H-pad);ctx.stroke();
const y=pad+i*(H-2*pad)/4;ctx.beginPath();ctx.moveTo(pad,y);ctx.lineTo(W-pad,y);ctx.stroke()}}
// points
pts.forEach((p,i)=>{{const l=labels[i];ctx.beginPath();ctx.arc(tx(p[0]),ty(p[1]),l<0?3:5,0,Math.PI*2);
ctx.fillStyle=l<0?'#475569':colors[l%colors.length];ctx.globalAlpha=l<0?0.4:0.8;ctx.fill();ctx.globalAlpha=1}});
// centroids
if(activeAlgo==='kmeans'&&km){{km.centroids.forEach((c,i)=>{{ctx.beginPath();ctx.arc(tx(c[0]),ty(c[1]),10,0,Math.PI*2);ctx.strokeStyle='#fff';ctx.lineWidth=2;ctx.stroke();ctx.fillStyle=colors[i%colors.length];ctx.fill();
ctx.fillStyle='#fff';ctx.font='bold 10px sans-serif';ctx.textAlign='center';ctx.fillText(i,tx(c[0]),ty(c[1])+3)}})}}
}}
drawScatter();

// K-scores chart
if(km&&km.k_scores){{
const kCard=card('📈 Silhouette by K');
const kc=document.createElement('canvas');kc.width=400;kc.height=250;kCard.appendChild(kc);grid.appendChild(kCard);
const kctx=kc.getContext('2d');const ks=Object.entries(km.k_scores).map(e=>[+e[0],e[1]]);
const maxS=Math.max(...ks.map(e=>e[1]),0.01);
kctx.fillStyle='#0f172a';kctx.fillRect(0,0,400,250);
const bw=30,gap=12,startX=50;
ks.forEach((e,i)=>{{const bh=e[1]/maxS*180;const x=startX+i*(bw+gap);const y=230-bh;
kctx.fillStyle=e[0]===km.k?'#6366f1':'#475569';kctx.fillRect(x,y,bw,bh);
kctx.fillStyle='#e2e8f0';kctx.font='11px sans-serif';kctx.textAlign='center';
kctx.fillText('k='+e[0],x+bw/2,245);kctx.fillText(e[1].toFixed(3),x+bw/2,y-5)}})}}

// Cluster sizes table
if(km||db){{
const tCard=card('📋 Cluster Sizes');
let thtml='<table><tr><th>Cluster</th><th>Size</th><th>%</th></tr>';
const sizes=km?km.cluster_sizes:(db?db.cluster_sizes:{{}});
const total=pts.length;
Object.entries(sizes).sort((a,b)=>a[0]-b[0]).forEach(e=>{{
thtml+='<tr><td><span style="color:'+colors[+e[0]%colors.length]+'">●</span> Cluster '+e[0]+'</td><td>'+e[1]+'</td><td>'+(e[1]/total*100).toFixed(1)+'%</td></tr>'}});
thtml+='</table>';tCard.appendChild(h('div','',thtml));grid.appendChild(tCard)}}

// Recommendations
if(meta.recommendations&&meta.recommendations.length){{
const rCard=card('💡 Recommendations');
let rhtml='';meta.recommendations.forEach(r=>rhtml+='<div class="rec">'+r+'</div>');
rCard.appendChild(h('div','',rhtml));grid.appendChild(rCard)}}
</script></body></html>"""


# ── CLI ──────────────────────────────────────────────────────────────

def load_csv(path: str) -> List[Tuple[float, float]]:
    """Load points from CSV (first two numeric columns)."""
    points = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        for row in reader:
            nums = []
            for cell in row:
                try:
                    nums.append(float(cell.strip()))
                except ValueError:
                    continue
                if len(nums) == 2:
                    break
            if len(nums) == 2:
                points.append((nums[0], nums[1]))
    return points


def generate_demo_points(seed: int = 42) -> List[Tuple[float, float]]:
    """Generate demo point set with 4 gaussian clusters + noise."""
    rng = random.Random(seed)
    centers = [(150, 150), (400, 100), (250, 350), (450, 350)]
    points = []
    for cx, cy in centers:
        for _ in range(40):
            points.append((cx + rng.gauss(0, 30), cy + rng.gauss(0, 30)))
    # noise
    for _ in range(20):
        points.append((rng.uniform(0, 600), rng.uniform(0, 500)))
    return points


def main():
    parser = argparse.ArgumentParser(
        description="Autonomous Spatial Clustering Analyzer")
    parser.add_argument("input", nargs="?", help="CSV file with x,y columns")
    parser.add_argument("--demo", action="store_true", help="Run built-in demo")
    parser.add_argument("--algo", choices=["kmeans", "dbscan", "both"],
                        default="both", help="Algorithm(s) to run (default: both)")
    parser.add_argument("--k", type=int, help="Force k for k-means (skip auto-k)")
    parser.add_argument("-o", "--output", help="Output HTML file path")
    parser.add_argument("--json", action="store_true", help="Print JSON summary")
    args = parser.parse_args()

    if args.demo:
        points = generate_demo_points()
        print(f"Generated {len(points)} demo points (4 clusters + noise)")
    elif args.input:
        points = load_csv(args.input)
        if len(points) < 3:
            print("Error: need at least 3 points", file=sys.stderr)
            sys.exit(1)
        print(f"Loaded {len(points)} points from {args.input}")
    else:
        parser.print_help()
        sys.exit(0)

    result = analyze(points, algo=args.algo, forced_k=args.k)

    # Print summary
    if "kmeans" in result:
        km = result["kmeans"]
        print(f"\n  K-Means: k={km['k']}, silhouette={km['silhouette']}")
        for cid, sz in sorted(km["cluster_sizes"].items()):
            print(f"    Cluster {cid}: {sz} points, centroid={km['centroids'][cid]}")
    if "dbscan" in result:
        db = result["dbscan"]
        print(f"\n  DBSCAN: eps={db['eps']}, {db['n_clusters']} clusters, {db['n_noise']} noise")
    if result.get("agreement") is not None:
        print(f"\n  Agreement: {result['agreement']*100:.1f}%")
    if result["recommendations"]:
        print("\n  Recommendations:")
        for r in result["recommendations"]:
            print(f"    -> {r}")

    # HTML output
    out = args.output or "cluster_report.html"
    html = generate_html(points, result)
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"\n  Report: {out}")

    # JSON
    if args.json:
        safe = {k: v for k, v in result.items()}
        for key in ("kmeans", "dbscan"):
            if key in safe:
                safe[key] = {k: v for k, v in safe[key].items() if k != "labels"}
        print(json.dumps(safe, indent=2))


if __name__ == "__main__":
    main()
