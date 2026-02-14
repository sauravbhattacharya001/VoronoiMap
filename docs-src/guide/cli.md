# CLI Reference

VoronoiMap provides the `voronoimap` command (or `python vormap.py`) for estimation from the terminal.

## Usage

```
voronoimap [-h] [--runs RUNS] [--bounds S N W E] datafile n
```

## Positional Arguments

| Argument | Type | Description |
|----------|------|-------------|
| `datafile` | string | Point data filename inside the `data/` directory (e.g., `datauni5.txt`) |
| `n` | integer | Expected number of Voronoi regions / sample size |

## Optional Arguments

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--runs` | integer | 1 | Number of independent estimation runs |
| `--bounds S N W E` | 4 floats | Auto-detected | Explicit search space boundaries (south, north, west, east). Disables auto-detection. |
| `-h, --help` | — | — | Show help message and exit |

## Examples

### Basic Estimation

```bash
voronoimap datauni5.txt 5
```

### Multiple Runs

Run the estimation 3 times independently for better statistical confidence:

```bash
voronoimap datauni5.txt 5 --runs 3
```

Output:
```
Run 1: regions=5  max_edges=7  avg_edges=5.2
Run 2: regions=5  max_edges=6  avg_edges=4.8
Run 3: regions=6  max_edges=8  avg_edges=5.5
```

### Custom Search Bounds

When your data spans a specific coordinate range:

```bash
voronoimap geodata.txt 100 --bounds -90 90 -180 180
```

!!! warning "Bounds and accuracy"
    If bounds are too large relative to the data extent, many random samples will land far from data points, reducing estimation accuracy. If too small, data points may fall outside the search space. Use auto-detection (the default) unless you have a specific reason to override.

## Output Format

Each run prints a line with:

```
regions max_edges avg_edges oracle_calls
Run N: regions=R  max_edges=M  avg_edges=A
```

| Field | Description |
|-------|-------------|
| `regions` | Estimated number of Voronoi regions |
| `max_edges` | Maximum vertex count on any region in this run |
| `avg_edges` | Average vertex count across sampled regions |
| `oracle_calls` | Total nearest-neighbor queries made |

## Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | Invalid arguments or file not found |
| 2 | Data file validation error (empty, malformed, path traversal) |
