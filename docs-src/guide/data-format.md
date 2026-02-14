# Data Format

VoronoiMap reads point data from text files in the `data/` directory.

## File Format

Each line contains one point with **space-separated** longitude and latitude coordinates:

```
<longitude> <latitude>
```

### Example: `data/sample5.txt`

```
100.0 200.0
400.0 600.0
800.0 300.0
200.0 800.0
700.0 700.0
```

## Rules

| Rule | Behavior |
|------|----------|
| Empty lines | Silently skipped |
| Lines with < 2 tokens | Silently skipped |
| Non-numeric values | Silently skipped |
| `NaN` or `Inf` coordinates | Silently skipped |
| Absolute paths | **Rejected** with `ValueError` |
| Path traversal (`..`) | **Rejected** with `ValueError` |

!!! info "Graceful handling"
    Malformed lines are skipped rather than causing a crash. This means you can have header lines or comments in your data file — they'll just be ignored. However, a file with **zero** valid points will raise a `ValueError`.

## Coordinate System

VoronoiMap works in arbitrary 2D Euclidean space. Coordinates can represent:

- Geographic (longitude, latitude)
- Cartesian (x, y)
- Any planar coordinate system

The algorithm is scale-invariant — it computes bounds automatically from data extents.

## Auto-Bounds Detection

By default, `load_data()` automatically computes the search space from data extents with 10% padding:

```python
# For points spanning [100, 800] × [200, 800]:
# Padding = max(700 * 0.1, 1.0) = 70
# Bounds become [130, 870] × [30, 870]
```

This ensures Voronoi cells near the boundary are not clipped. You can override with `set_bounds()` or `--bounds` on the CLI.

## File Location

All data files must be in the `data/` subdirectory relative to the working directory:

```
project/
├── vormap.py
├── data/
│   ├── sample5.txt
│   ├── large_dataset.txt
│   └── geo_points.txt
└── ...
```

!!! warning "Security"
    The `data/` directory constraint is enforced for security. Filenames like `../../etc/passwd` or `/etc/shadow` are rejected to prevent path traversal attacks.
