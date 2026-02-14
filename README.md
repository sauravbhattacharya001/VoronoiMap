# VoronoiMap

[![CI](https://github.com/sauravbhattacharya001/VoronoiMap/actions/workflows/ci.yml/badge.svg)](https://github.com/sauravbhattacharya001/VoronoiMap/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/sauravbhattacharya001/VoronoiMap/graph/badge.svg)](https://codecov.io/gh/sauravbhattacharya001/VoronoiMap)

Voronoi partitioning of a geometric 2-dimensional space. This project implements an algorithm that estimates aggregate statistics of an unknown set of objects embedded in the Euclidean plane using a nearest-neighbor oracle and Voronoi partitioning.

## Background

A partially informed attacker can estimate aggregate statistics of an unknown set of objects using available partial data (a nearest-neighbor oracle). The algorithm `EstimateSUM` constructs Voronoi regions around each discovered point and uses area ratios to estimate the total number of objects in the search space.

📄 **Full report:** [http://docdro.id/5HXe2wV](http://docdro.id/5HXe2wV)

## How It Works

1. Random points are sampled within a bounded 2D region (1000 × 2000 by default)
2. For each sample, the nearest neighbor is found via an oracle that reads from data files
3. The Voronoi region around each nearest neighbor is approximated using binary search along perpendicular bisectors
4. Region areas are computed using the [Shoelace formula](https://en.wikipedia.org/wiki/Shoelace_formula)
5. The sum estimate is derived from the ratio of total area to individual Voronoi cell areas

## Requirements

- Python 3.6+
- **Optional:** `numpy` and `scipy` for O(log n) nearest-neighbor lookups via KDTree (falls back to brute-force O(n) scan if not installed)

```bash
# Install optional dependencies for better performance
pip install -r requirements.txt
```

## Usage

### Data Format

Place data files in a `data/` directory. Each file should contain one point per line with space-separated longitude and latitude coordinates:

```
100.5 200.3
450.2 750.1
800.0 500.0
```

### Running

```bash
# Basic usage
python vormap.py datauni5.txt 5

# Multiple independent runs
python vormap.py datauni5.txt 5 --runs 3
```

You can also use the module programmatically:

```python
from vormap import get_sum, find_area, get_NN, load_data

# Estimate the number of objects in a dataset
# get_sum takes a filename (it calls load_data internally)
estimated_count, max_edges, avg_edges = get_sum("datauni5.txt", 5)

# Load data for direct function calls
data = load_data("datauni5.txt")

# Find area of a single Voronoi region
area, vertices = find_area(data, 100.5, 200.3)

# Query nearest neighbor
nearest_lng, nearest_lat = get_NN(data, 500.0, 500.0)
```

## Project Structure

```
VoronoiMap/
├── vormap.py       # Main algorithm implementation
├── data/           # Data files (not tracked in git)
├── .gitignore
├── LICENSE
└── README.md
```

## Key Parameters

| Parameter    | Default  | Description                          |
|-------------|----------|--------------------------------------|
| `IND_S`     | 0.0      | Southern boundary of search space    |
| `IND_N`     | 1000.0   | Northern boundary of search space    |
| `IND_W`     | 0.0      | Western boundary of search space     |
| `IND_E`     | 2000.0   | Eastern boundary of search space     |
| `BIN_PREC`  | 1e-6     | Binary search precision threshold    |
| `MAX_RETRIES` | 50     | Maximum retry attempts for estimation |

## License

MIT — see [LICENSE](LICENSE) for details.
