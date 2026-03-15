"""Voronoi Map Algebra — spatial operations on cell attribute layers.

Performs map algebra on Voronoi tessellations, treating each cell's
attribute value as a spatial field.  Supports local, focal, and zonal
operations — the three pillars of raster map algebra adapted for
irregular Voronoi grids.

**Local operations** transform cell values independently:
  arithmetic, thresholding, reclassification, normalisation.

**Focal operations** compute statistics from each cell's neighbourhood:
  mean, median, min, max, range, std, majority, diversity, slope.

**Zonal operations** aggregate values within defined zones:
  mean, sum, min, max, count, std, range, dominant.

Usage (Python API)::

    from vormap_mapalgebra import (
        CellLayer, local_add, local_threshold, local_reclassify,
        focal_mean, focal_slope, zonal_stats,
    )

    # Create layers from cell data
    population = CellLayer.from_dict(pop_data, adjacency)
    income = CellLayer.from_dict(income_data, adjacency)

    # Local: combine layers
    density = local_divide(population, income)

    # Focal: smooth with neighbourhood average
    smoothed = focal_mean(population)

    # Zonal: aggregate by zone
    zone_stats = zonal_stats(population, zone_assignments)

Usage (CLI)::

    python -m replication mapalgebra --help        # (if registered)

    # Standalone
    python vormap_mapalgebra.py focal-mean data.json --output smoothed.json
    python vormap_mapalgebra.py reclassify data.json --breaks 10,50,100
    python vormap_mapalgebra.py zonal-stats data.json --zones zones.json
"""


import argparse
import json
import math
from collections import Counter
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional


# ── Cell Layer ───────────────────────────────────────────────────────


@dataclass
class CellLayer:
    """A spatial attribute layer over Voronoi cells.

    Parameters
    ----------
    values : dict[int, float]
        Mapping of cell index → attribute value.
    adjacency : dict[int, list[int]]
        Mapping of cell index → list of neighbouring cell indices.
    name : str
        Layer name (for display/export).
    nodata : float or None
        Value representing missing data.  Cells with this value are
        excluded from focal/zonal computations.
    """

    values: Dict[int, float]
    adjacency: Dict[int, List[int]]
    name: str = "layer"
    nodata: Optional[float] = None

    @classmethod
    def from_dict(
        cls,
        values: Dict[int, float],
        adjacency: Dict[int, List[int]],
        name: str = "layer",
        nodata: Optional[float] = None,
    ) -> "CellLayer":
        """Create a layer from a value dictionary and adjacency graph."""
        return cls(
            values=dict(values),
            adjacency={k: list(v) for k, v in adjacency.items()},
            name=name,
            nodata=nodata,
        )

    @classmethod
    def from_json(cls, path: str) -> "CellLayer":
        """Load a layer from a JSON file.

        Expected format::

            {
                "name": "population",
                "nodata": -999,
                "values": {"0": 100, "1": 200, ...},
                "adjacency": {"0": [1, 2], "1": [0, 3], ...}
            }
        """
        with open(path) as f:
            data = json.load(f)
        values = {int(k): float(v) for k, v in data["values"].items()}
        adjacency = {int(k): [int(n) for n in v] for k, v in data["adjacency"].items()}
        return cls(
            values=values,
            adjacency=adjacency,
            name=data.get("name", "layer"),
            nodata=data.get("nodata"),
        )

    def _valid_value(self, cell: int) -> bool:
        """True if cell exists and value is not nodata/NaN."""
        if cell not in self.values:
            return False
        v = self.values[cell]
        if self.nodata is not None and v == self.nodata:
            return False
        if isinstance(v, float) and math.isnan(v):
            return False
        return True

    def valid_cells(self) -> List[int]:
        """Return sorted list of cells with valid values."""
        return sorted(c for c in self.values if self._valid_value(c))

    def valid_neighbours(self, cell: int) -> List[int]:
        """Return neighbours of *cell* that have valid values."""
        return [n for n in self.adjacency.get(cell, []) if self._valid_value(n)]

    def copy(self, name: Optional[str] = None) -> "CellLayer":
        """Deep copy with optional new name."""
        return CellLayer(
            values=dict(self.values),
            adjacency={k: list(v) for k, v in self.adjacency.items()},
            name=name or self.name,
            nodata=self.nodata,
        )

    def to_dict(self) -> dict:
        """Serialise to a JSON-safe dictionary."""
        d: dict = {"name": self.name, "values": self.values, "adjacency": self.adjacency}
        if self.nodata is not None:
            d["nodata"] = self.nodata
        return d

    def to_json(self, path: str, indent: int = 2) -> None:
        """Write layer to a JSON file."""
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=indent)

    def stats(self) -> Dict[str, float]:
        """Basic descriptive statistics of valid values."""
        vals = [self.values[c] for c in self.valid_cells()]
        if not vals:
            return {"count": 0, "min": 0.0, "max": 0.0, "mean": 0.0, "std": 0.0, "sum": 0.0}
        n = len(vals)
        s = sum(vals)
        mean = s / n
        std = math.sqrt(sum((v - mean) ** 2 for v in vals) / n) if n > 1 else 0.0
        return {
            "count": n,
            "min": min(vals),
            "max": max(vals),
            "mean": round(mean, 6),
            "std": round(std, 6),
            "sum": round(s, 6),
        }

    def __len__(self) -> int:
        return len(self.values)

    def __repr__(self) -> str:
        return f"CellLayer(name={self.name!r}, cells={len(self.values)})"


# ── Local Operations ─────────────────────────────────────────────────
#
# Local ops transform each cell's value independently.
# Binary ops align two layers on matching cell indices.


def _binary_op(
    a: CellLayer, b: CellLayer, op: Callable[[float, float], float], name: str
) -> CellLayer:
    """Apply a binary operation cell-by-cell on two aligned layers."""
    common = set(a.values) & set(b.values)
    result: Dict[int, float] = {}
    for cell in common:
        va = a.values[cell]
        vb = b.values[cell]
        # Propagate nodata
        skip_a = (a.nodata is not None and va == a.nodata) or (isinstance(va, float) and math.isnan(va))
        skip_b = (b.nodata is not None and vb == b.nodata) or (isinstance(vb, float) and math.isnan(vb))
        if skip_a or skip_b:
            result[cell] = a.nodata if a.nodata is not None else float("nan")
        else:
            result[cell] = op(va, vb)
    return CellLayer(values=result, adjacency=a.adjacency, name=name, nodata=a.nodata)


def local_add(a: CellLayer, b: CellLayer) -> CellLayer:
    """Add two layers: result[i] = a[i] + b[i]."""
    return _binary_op(a, b, lambda x, y: x + y, f"{a.name}+{b.name}")


def local_subtract(a: CellLayer, b: CellLayer) -> CellLayer:
    """Subtract: result[i] = a[i] - b[i]."""
    return _binary_op(a, b, lambda x, y: x - y, f"{a.name}-{b.name}")


def local_multiply(a: CellLayer, b: CellLayer) -> CellLayer:
    """Multiply: result[i] = a[i] * b[i]."""
    return _binary_op(a, b, lambda x, y: x * y, f"{a.name}*{b.name}")


def local_divide(a: CellLayer, b: CellLayer) -> CellLayer:
    """Divide: result[i] = a[i] / b[i].  Returns nodata for division by zero."""
    nodata = a.nodata if a.nodata is not None else float("nan")

    def safe_div(x: float, y: float) -> float:
        if y == 0:
            return nodata
        return x / y

    return _binary_op(a, b, safe_div, f"{a.name}/{b.name}")


def local_max(a: CellLayer, b: CellLayer) -> CellLayer:
    """Cell-wise maximum of two layers."""
    return _binary_op(a, b, max, f"max({a.name},{b.name})")


def local_min(a: CellLayer, b: CellLayer) -> CellLayer:
    """Cell-wise minimum of two layers."""
    return _binary_op(a, b, min, f"min({a.name},{b.name})")


def local_scale(layer: CellLayer, factor: float) -> CellLayer:
    """Multiply every valid cell value by a scalar."""
    result = layer.copy(name=f"{layer.name}*{factor}")
    for cell in result.valid_cells():
        result.values[cell] *= factor
    return result


def local_offset(layer: CellLayer, offset: float) -> CellLayer:
    """Add a scalar to every valid cell value."""
    result = layer.copy(name=f"{layer.name}+{offset}")
    for cell in result.valid_cells():
        result.values[cell] += offset
    return result


def local_abs(layer: CellLayer) -> CellLayer:
    """Absolute value of each cell."""
    result = layer.copy(name=f"|{layer.name}|")
    for cell in result.valid_cells():
        result.values[cell] = abs(result.values[cell])
    return result


def local_power(layer: CellLayer, exponent: float) -> CellLayer:
    """Raise each cell value to the given power.

    Negative base values with fractional exponents would produce complex
    numbers, so those cells are set to nodata instead (consistent with
    how ``local_log`` handles non-positive values).
    """
    nodata = layer.nodata if layer.nodata is not None else float("nan")
    result = layer.copy(name=f"{layer.name}^{exponent}")
    is_integer_exp = float(exponent) == int(exponent)
    for cell in result.valid_cells():
        v = result.values[cell]
        if v < 0 and not is_integer_exp:
            result.values[cell] = nodata
        elif v == 0 and exponent < 0:
            result.values[cell] = nodata
        else:
            result.values[cell] = v ** exponent
    return result


def local_log(layer: CellLayer) -> CellLayer:
    """Natural log of each cell value.  Non-positive values → nodata."""
    nodata = layer.nodata if layer.nodata is not None else float("nan")
    result = layer.copy(name=f"ln({layer.name})")
    for cell in result.valid_cells():
        v = result.values[cell]
        result.values[cell] = math.log(v) if v > 0 else nodata
    return result


def local_normalise(layer: CellLayer) -> CellLayer:
    """Min-max normalise to [0, 1]."""
    vals = [layer.values[c] for c in layer.valid_cells()]
    if not vals:
        return layer.copy(name=f"norm({layer.name})")
    lo, hi = min(vals), max(vals)
    span = hi - lo
    result = layer.copy(name=f"norm({layer.name})")
    if span == 0:
        for cell in result.valid_cells():
            result.values[cell] = 0.0
    else:
        for cell in result.valid_cells():
            result.values[cell] = (result.values[cell] - lo) / span
    return result


def local_standardise(layer: CellLayer) -> CellLayer:
    """Z-score standardise (mean=0, std=1)."""
    vals = [layer.values[c] for c in layer.valid_cells()]
    if len(vals) < 2:
        return layer.copy(name=f"zscore({layer.name})")
    mean = sum(vals) / len(vals)
    std = math.sqrt(sum((v - mean) ** 2 for v in vals) / len(vals))
    result = layer.copy(name=f"zscore({layer.name})")
    if std == 0:
        for cell in result.valid_cells():
            result.values[cell] = 0.0
    else:
        for cell in result.valid_cells():
            result.values[cell] = (result.values[cell] - mean) / std
    return result


def local_threshold(
    layer: CellLayer, threshold: float, above: float = 1.0, below: float = 0.0
) -> CellLayer:
    """Binary threshold: above → *above*, else → *below*."""
    result = layer.copy(name=f"threshold({layer.name},{threshold})")
    for cell in result.valid_cells():
        result.values[cell] = above if result.values[cell] >= threshold else below
    return result


def local_clamp(layer: CellLayer, lo: float, hi: float) -> CellLayer:
    """Clamp values to [lo, hi]."""
    result = layer.copy(name=f"clamp({layer.name},{lo},{hi})")
    for cell in result.valid_cells():
        result.values[cell] = max(lo, min(hi, result.values[cell]))
    return result


def local_reclassify(
    layer: CellLayer,
    breaks: List[float],
    labels: Optional[List[float]] = None,
) -> CellLayer:
    """Reclassify continuous values into discrete classes.

    Parameters
    ----------
    breaks : list[float]
        Class boundaries in ascending order.  Values < breaks[0] → class 0,
        values in [breaks[0], breaks[1]) → class 1, etc.
    labels : list[float] or None
        Optional class labels (len = len(breaks) + 1).  If None, uses
        0, 1, 2, ...
    """
    n_classes = len(breaks) + 1
    if labels is not None and len(labels) != n_classes:
        raise ValueError(f"Need {n_classes} labels for {len(breaks)} breaks, got {len(labels)}")
    _labels = labels if labels is not None else [float(i) for i in range(n_classes)]
    sorted_breaks = sorted(breaks)

    result = layer.copy(name=f"reclass({layer.name})")
    for cell in result.valid_cells():
        v = result.values[cell]
        cls = 0
        for brk in sorted_breaks:
            if v >= brk:
                cls += 1
            else:
                break
        result.values[cell] = _labels[cls]
    return result


def local_apply(layer: CellLayer, fn: Callable[[float], float], name: str = "custom") -> CellLayer:
    """Apply an arbitrary function to each valid cell value."""
    result = layer.copy(name=name)
    for cell in result.valid_cells():
        result.values[cell] = fn(result.values[cell])
    return result


# ── Focal Operations ─────────────────────────────────────────────────
#
# Focal ops compute a value for each cell based on its neighbourhood.


def _focal_op(
    layer: CellLayer,
    op: Callable[[List[float]], float],
    name: str,
    include_self: bool = True,
) -> CellLayer:
    """Generic focal operation."""
    result = layer.copy(name=name)
    for cell in layer.valid_cells():
        neighbours = layer.valid_neighbours(cell)
        if include_self:
            vals = [layer.values[cell]] + [layer.values[n] for n in neighbours]
        else:
            vals = [layer.values[n] for n in neighbours]
        if vals:
            result.values[cell] = op(vals)
    return result


def focal_mean(layer: CellLayer, include_self: bool = True) -> CellLayer:
    """Neighbourhood mean (spatial smoothing)."""
    return _focal_op(layer, lambda vs: sum(vs) / len(vs), f"focal_mean({layer.name})", include_self)


def focal_median(layer: CellLayer, include_self: bool = True) -> CellLayer:
    """Neighbourhood median (edge-preserving smooth)."""
    def median(vs: List[float]) -> float:
        s = sorted(vs)
        n = len(s)
        if n % 2 == 1:
            return s[n // 2]
        return (s[n // 2 - 1] + s[n // 2]) / 2.0

    return _focal_op(layer, median, f"focal_median({layer.name})", include_self)


def focal_max(layer: CellLayer, include_self: bool = True) -> CellLayer:
    """Neighbourhood maximum."""
    return _focal_op(layer, max, f"focal_max({layer.name})", include_self)


def focal_min(layer: CellLayer, include_self: bool = True) -> CellLayer:
    """Neighbourhood minimum."""
    return _focal_op(layer, min, f"focal_min({layer.name})", include_self)


def focal_range(layer: CellLayer, include_self: bool = True) -> CellLayer:
    """Neighbourhood range (max - min)."""
    return _focal_op(layer, lambda vs: max(vs) - min(vs), f"focal_range({layer.name})", include_self)


def focal_std(layer: CellLayer, include_self: bool = True) -> CellLayer:
    """Neighbourhood standard deviation."""
    def std_fn(vs: List[float]) -> float:
        if len(vs) < 2:
            return 0.0
        m = sum(vs) / len(vs)
        return math.sqrt(sum((v - m) ** 2 for v in vs) / len(vs))

    return _focal_op(layer, std_fn, f"focal_std({layer.name})", include_self)


def focal_sum(layer: CellLayer, include_self: bool = True) -> CellLayer:
    """Neighbourhood sum."""
    return _focal_op(layer, sum, f"focal_sum({layer.name})", include_self)


def focal_count(layer: CellLayer, include_self: bool = True) -> CellLayer:
    """Count of valid neighbours (+ self if include_self)."""
    return _focal_op(layer, lambda vs: float(len(vs)), f"focal_count({layer.name})", include_self)


def focal_majority(layer: CellLayer, include_self: bool = True) -> CellLayer:
    """Most common value in neighbourhood (for categorical data)."""
    def majority(vs: List[float]) -> float:
        counts = Counter(vs)
        return counts.most_common(1)[0][0]

    return _focal_op(layer, majority, f"focal_majority({layer.name})", include_self)


def focal_diversity(layer: CellLayer, include_self: bool = True) -> CellLayer:
    """Count of unique values in neighbourhood (richness)."""
    return _focal_op(
        layer,
        lambda vs: float(len(set(vs))),
        f"focal_diversity({layer.name})",
        include_self,
    )


def focal_slope(layer: CellLayer) -> CellLayer:
    """Maximum absolute gradient to any neighbour.

    Approximates slope as max(|value[cell] - value[neighbour]|) over
    all neighbours.  Useful for detecting steep transitions.
    """
    result = layer.copy(name=f"focal_slope({layer.name})")
    for cell in layer.valid_cells():
        v = layer.values[cell]
        neighbours = layer.valid_neighbours(cell)
        if neighbours:
            result.values[cell] = max(abs(v - layer.values[n]) for n in neighbours)
        else:
            result.values[cell] = 0.0
    return result


# ── Zonal Operations ─────────────────────────────────────────────────
#
# Zonal ops aggregate values within predefined zones (groups of cells).


@dataclass
class ZonalResult:
    """Statistics for one zone."""

    zone_id: Any
    count: int = 0
    total: float = 0.0
    mean: float = 0.0
    min_val: float = 0.0
    max_val: float = 0.0
    std: float = 0.0
    range: float = 0.0
    dominant: float = 0.0  # Most frequent value

    def to_dict(self) -> dict:
        return {
            "zone_id": self.zone_id,
            "count": self.count,
            "sum": round(self.total, 6),
            "mean": round(self.mean, 6),
            "min": round(self.min_val, 6),
            "max": round(self.max_val, 6),
            "std": round(self.std, 6),
            "range": round(self.range, 6),
            "dominant": self.dominant,
        }


def zonal_stats(
    layer: CellLayer,
    zones: Dict[int, Any],
) -> Dict[Any, ZonalResult]:
    """Compute per-zone aggregate statistics.

    Parameters
    ----------
    layer : CellLayer
        The attribute layer to summarise.
    zones : dict[int, Any]
        Mapping of cell index → zone identifier (int, str, etc.).
        Cells not in *zones* are ignored.

    Returns
    -------
    dict[Any, ZonalResult]
        Statistics keyed by zone identifier.
    """
    # Group values by zone
    zone_vals: Dict[Any, List[float]] = {}
    for cell, zone_id in zones.items():
        if layer._valid_value(cell):
            zone_vals.setdefault(zone_id, []).append(layer.values[cell])

    results: Dict[Any, ZonalResult] = {}
    for zone_id, vals in zone_vals.items():
        n = len(vals)
        s = sum(vals)
        mean = s / n if n else 0.0
        std = math.sqrt(sum((v - mean) ** 2 for v in vals) / n) if n > 1 else 0.0
        counts = Counter(vals)
        dominant = counts.most_common(1)[0][0] if counts else 0.0
        lo = min(vals) if vals else 0.0
        hi = max(vals) if vals else 0.0

        results[zone_id] = ZonalResult(
            zone_id=zone_id,
            count=n,
            total=s,
            mean=mean,
            min_val=lo,
            max_val=hi,
            std=std,
            range=hi - lo,
            dominant=dominant,
        )
    return results


def zonal_apply(
    layer: CellLayer,
    zones: Dict[int, Any],
    stat: str = "mean",
) -> CellLayer:
    """Replace each cell's value with its zone's aggregate statistic.

    Parameters
    ----------
    stat : str
        One of: mean, sum, min, max, count, std, range, dominant.
    """
    stats = zonal_stats(layer, zones)
    stat_map = {
        "mean": "mean", "sum": "total", "min": "min_val", "max": "max_val",
        "count": "count", "std": "std", "range": "range", "dominant": "dominant",
    }
    if stat not in stat_map:
        raise ValueError(f"Unknown stat {stat!r}; choose from {list(stat_map)}")

    attr = stat_map[stat]
    result = layer.copy(name=f"zonal_{stat}({layer.name})")
    for cell, zone_id in zones.items():
        if zone_id in stats:
            result.values[cell] = float(getattr(stats[zone_id], attr))
    return result


# ── Layer Combination ────────────────────────────────────────────────


def weighted_overlay(
    layers: List[CellLayer],
    weights: List[float],
    name: str = "weighted_overlay",
) -> CellLayer:
    """Weighted overlay combination of multiple layers.

    Each layer is normalised to [0, 1] then combined using the given
    weights.  Classic multi-criteria decision analysis (MCDA) suitability
    modelling technique.

    Parameters
    ----------
    layers : list[CellLayer]
        Input layers (must share cell indices).
    weights : list[float]
        Relative importance weights (will be normalised to sum to 1).
    """
    if len(layers) != len(weights):
        raise ValueError(f"Need {len(layers)} weights, got {len(weights)}")
    if not layers:
        raise ValueError("Need at least one layer")
    if any(w < 0 for w in weights):
        raise ValueError(
            "Negative weights are not supported in weighted overlay. "
            "All weights must be >= 0 (use local_subtract for inverse criteria)."
        )

    # Normalise weights to sum to 1
    total_w = sum(weights)
    if total_w == 0:
        raise ValueError("Weights cannot all be zero")
    norm_weights = [w / total_w for w in weights]

    # Normalise each layer to [0, 1]
    normalised = [local_normalise(lay) for lay in layers]

    # Get common cells
    common = set(normalised[0].values)
    for lay in normalised[1:]:
        common &= set(lay.values)

    result_vals: Dict[int, float] = {}
    for cell in common:
        v = 0.0
        skip = False
        for lay, w in zip(normalised, norm_weights):
            if not lay._valid_value(cell):
                skip = True
                break
            v += lay.values[cell] * w
        if not skip:
            result_vals[cell] = v

    return CellLayer(
        values=result_vals,
        adjacency=layers[0].adjacency,
        name=name,
        nodata=layers[0].nodata,
    )


def layer_stack(layers: List[CellLayer]) -> Dict[int, List[float]]:
    """Stack multiple layers into per-cell value vectors.

    Useful for multivariate analysis.  Returns only cells present
    in ALL layers.
    """
    if not layers:
        return {}
    common = set(layers[0].values)
    for lay in layers[1:]:
        common &= set(lay.values)
    return {
        cell: [lay.values[cell] for lay in layers]
        for cell in sorted(common)
    }


# ── Export ────────────────────────────────────────────────────────────


def export_algebra_json(layer: CellLayer, path: str) -> None:
    """Export a layer to JSON."""
    layer.to_json(path)


def export_algebra_csv(layer: CellLayer, path: str) -> None:
    """Export layer values to CSV."""
    with open(path, "w", encoding="utf-8") as f:
        f.write("cell,value\n")
        for cell in sorted(layer.values):
            f.write(f"{cell},{layer.values[cell]}\n")


def export_zonal_csv(results: Dict[Any, ZonalResult], path: str) -> None:
    """Export zonal statistics to CSV."""
    with open(path, "w", encoding="utf-8") as f:
        f.write("zone_id,count,sum,mean,min,max,std,range,dominant\n")
        for zone_id in sorted(results, key=str):
            r = results[zone_id]
            f.write(
                f"{zone_id},{r.count},{r.total:.6f},{r.mean:.6f},"
                f"{r.min_val:.6f},{r.max_val:.6f},{r.std:.6f},"
                f"{r.range:.6f},{r.dominant}\n"
            )


def format_algebra_report(layer: CellLayer, detail: bool = False) -> str:
    """Human-readable text report of a layer."""
    s = layer.stats()
    lines = [
        f"Layer: {layer.name}",
        f"Cells: {s['count']}",
        f"Range: [{s['min']:.4f}, {s['max']:.4f}]",
        f"Mean:  {s['mean']:.4f}  (std: {s['std']:.4f})",
        f"Sum:   {s['sum']:.4f}",
    ]
    if detail and s["count"] > 0:
        lines.append("")
        lines.append("Cell values:")
        for cell in sorted(layer.values):
            if layer._valid_value(cell):
                lines.append(f"  [{cell:4d}] {layer.values[cell]:.4f}")
    return "\n".join(lines)


# ── CLI ──────────────────────────────────────────────────────────────


def main(argv: Optional[List[str]] = None) -> None:
    """CLI entry point for map algebra operations."""
    parser = argparse.ArgumentParser(description="Voronoi Map Algebra")
    sub = parser.add_subparsers(dest="command")

    # focal-mean
    p_fm = sub.add_parser("focal-mean", help="Neighbourhood mean smoothing")
    p_fm.add_argument("input", help="Input layer JSON")
    p_fm.add_argument("--output", "-o", help="Output JSON path")
    p_fm.add_argument("--exclude-self", action="store_true")

    # focal-median
    p_fmd = sub.add_parser("focal-median", help="Neighbourhood median filter")
    p_fmd.add_argument("input", help="Input layer JSON")
    p_fmd.add_argument("--output", "-o", help="Output JSON path")

    # focal-slope
    p_fs = sub.add_parser("focal-slope", help="Maximum gradient to neighbours")
    p_fs.add_argument("input", help="Input layer JSON")
    p_fs.add_argument("--output", "-o", help="Output JSON path")

    # reclassify
    p_rc = sub.add_parser("reclassify", help="Reclassify continuous → discrete")
    p_rc.add_argument("input", help="Input layer JSON")
    p_rc.add_argument("--breaks", required=True, help="Comma-separated break values")
    p_rc.add_argument("--labels", help="Comma-separated class labels")
    p_rc.add_argument("--output", "-o", help="Output JSON path")

    # normalise
    p_norm = sub.add_parser("normalise", help="Min-max normalise to [0, 1]")
    p_norm.add_argument("input", help="Input layer JSON")
    p_norm.add_argument("--output", "-o", help="Output JSON path")

    # threshold
    p_th = sub.add_parser("threshold", help="Binary threshold")
    p_th.add_argument("input", help="Input layer JSON")
    p_th.add_argument("--value", type=float, required=True, help="Threshold value")
    p_th.add_argument("--output", "-o", help="Output JSON path")

    # zonal-stats
    p_zs = sub.add_parser("zonal-stats", help="Zonal statistics")
    p_zs.add_argument("input", help="Input layer JSON")
    p_zs.add_argument("--zones", required=True, help="Zones JSON (cell_id → zone_id mapping)")
    p_zs.add_argument("--output", "-o", help="Output CSV path")

    # stats
    p_st = sub.add_parser("stats", help="Layer statistics")
    p_st.add_argument("input", help="Input layer JSON")
    p_st.add_argument("--json", action="store_true", help="JSON output")

    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return

    if args.command == "stats":
        layer = CellLayer.from_json(args.input)
        if args.json:
            print(json.dumps(layer.stats(), indent=2))
        else:
            print(format_algebra_report(layer))
        return

    if args.command == "focal-mean":
        layer = CellLayer.from_json(args.input)
        result = focal_mean(layer, include_self=not args.exclude_self)
    elif args.command == "focal-median":
        layer = CellLayer.from_json(args.input)
        result = focal_median(layer)
    elif args.command == "focal-slope":
        layer = CellLayer.from_json(args.input)
        result = focal_slope(layer)
    elif args.command == "reclassify":
        layer = CellLayer.from_json(args.input)
        breaks = [float(x) for x in args.breaks.split(",")]
        labels = [float(x) for x in args.labels.split(",")] if args.labels else None
        result = local_reclassify(layer, breaks, labels)
    elif args.command == "normalise":
        layer = CellLayer.from_json(args.input)
        result = local_normalise(layer)
    elif args.command == "threshold":
        layer = CellLayer.from_json(args.input)
        result = local_threshold(layer, args.value)
    elif args.command == "zonal-stats":
        layer = CellLayer.from_json(args.input)
        with open(args.zones) as f:
            zone_data = json.load(f)
        zones = {int(k): v for k, v in zone_data.items()}
        stats = zonal_stats(layer, zones)
        if args.output:
            export_zonal_csv(stats, args.output)
            print(f"Zonal stats written to {args.output}")
        else:
            for zone_id, r in sorted(stats.items(), key=lambda x: str(x[0])):
                print(f"Zone {zone_id}: n={r.count} mean={r.mean:.4f} std={r.std:.4f}")
        return
    else:
        parser.print_help()
        return

    if hasattr(args, "output") and args.output:
        result.to_json(args.output)
        print(f"Result written to {args.output}")
    else:
        print(format_algebra_report(result))


if __name__ == "__main__":
    main()
