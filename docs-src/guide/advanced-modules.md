# Advanced Modules

Deep reference for VoronoiMap's simulation, autonomous analysis, and governance
modules. These modules go beyond static spatial analysis — they run autonomous
multi-step investigations, simulate dynamic systems on Voronoi topologies, and
enforce spatial constraints.

For core extension modules (viz, clustering, queries, interpolation, etc.), see
[Extension Modules API](extensions.md).

---

## Spatial Causality — `vormap_causality`

Answers *"what would happen if we add / remove / move points?"* by simulating
Voronoi diagrams before and after an intervention, then quantifying causal
effects on spatial metrics.

### Intervention Types

| Type | Description |
|------|-------------|
| `"add"` | Insert new points into the diagram |
| `"remove"` | Remove existing points |
| `"relocate"` | Move points from source to destination coordinates |

### Core API

```python
from vormap_causality import (
    analyze_causality, Intervention,
    estimate_treatment_effect, difference_in_differences,
    synthetic_control, detect_spillovers, rank_interventions,
)

points = [(100, 200), (400, 300), (700, 500), (200, 700), (600, 100)]
bounds = (0, 1000, 0, 1000)

# Define interventions
add_iv = Intervention("add", points=[(500, 500), (300, 100)])
remove_iv = Intervention("remove", points=[(400, 300)])
relocate_iv = Intervention("relocate",
                           points=[(100, 200)],
                           targets=[(150, 250)])

# Full analysis — runs all six engines
report = analyze_causality(points, [add_iv, remove_iv], bounds=bounds)
print(report.summary())
report.to_json("causality.json")
report.to_html("causality.html")
```

### Analysis Engines

| Engine | Function | What It Measures |
|--------|----------|-----------------|
| Treatment Effect | `estimate_treatment_effect()` | ATE and ATT for area, compactness, neighbors, NN distance |
| Difference-in-Differences | `difference_in_differences()` | Isolates intervention impact by comparing treated vs control |
| Synthetic Control | `synthetic_control()` | Constructs a counterfactual from a donor pool of unaffected cells |
| Spillover Detection | `detect_spillovers()` | Indirect effects radiating through k-hop neighborhoods |
| Intervention Ranking | `rank_interventions()` | Scores and ranks candidate interventions by expected impact |
| HTML Dashboard | via `report.to_html()` | 4-tab interactive visualization summarizing all analyses |

### Individual Engine Example

```python
effect = estimate_treatment_effect(points, add_iv, "area", bounds=bounds)
print(f"ATE = {effect.ate:.3f}, spillover = {effect.spillover:.3f}")

did = difference_in_differences(points, add_iv, bounds=bounds)
spill = detect_spillovers(points, add_iv, hops=2, bounds=bounds)
ranked = rank_interventions(
    points, [add_iv, remove_iv, relocate_iv],
    objective="area_equity", bounds=bounds,
)
```

### CLI

```bash
voronoimap data.txt 5 --causality add --causality-points "500,500;300,100"
voronoimap data.txt 5 --causality remove --causality-points "400,300"
voronoimap data.txt 5 --causality relocate \
    --causality-points "100,200" --causality-targets "150,250"
voronoimap data.txt 5 --causality add \
    --causality-points "500,500" --causality-html out.html
```

---

## Resilience Analysis — `vormap_resilience`

Stress-tests a spatial layout by simulating point failures and measuring
impact on the spatial network. Identifies single points of failure and
recommends optimal redundancy placements.

### Quick Start

```python
from vormap_resilience import analyze_resilience

result = analyze_resilience("points.txt")
print(f"Resilience score: {result.resilience_score}/100")

for cp in result.critical_points[:5]:
    print(f"  Point {cp.index} @ ({cp.x:.1f}, {cp.y:.1f}) "
          f"impact={cp.impact_score:.3f}")
```

### Detailed API

```python
from vormap_resilience import ResilienceAnalyzer

ra = ResilienceAnalyzer(points=[(100, 200), (300, 400), (500, 600)])
result = ra.analyze(cascade_depth=3, suggest_redundancy=True)
result.to_json("resilience.json")
result.to_html("resilience.html")
```

### Analysis Channels

| Channel | Description |
|---------|-------------|
| Impact Scoring | Remove each point individually, measure area redistribution impact |
| Criticality Ranking | Rank all points by composite failure impact score |
| Cascade Analysis | Simulate sequential failures to measure cascading degradation |
| Redundancy Mapping | Suggest backup point placements that minimize failure impact |
| Resilience Score | Overall 0–100 score for layout's failure tolerance |
| What-If Scenarios | Simulate removal of user-specified point subsets |

### CLI

```bash
python vormap_resilience.py points.txt
python vormap_resilience.py points.txt --cascade-depth 5
python vormap_resilience.py points.txt --redundancy --top 10
python vormap_resilience.py points.txt --what-if 0,3,7
python vormap_resilience.py points.txt --html resilience.html
```

---

## Anomaly Forensics — `vormap_forensics`

An autonomous spatial data forensic investigator. Runs a six-phase pipeline
that classifies anomaly root causes, traces evidence chains, and delivers
confidence-scored verdicts.

### Quick Start

```python
from vormap_forensics import investigate

verdict = investigate("points.txt")
print(f"Integrity: {verdict.integrity_score}/100  Risk: {verdict.risk_level}")
for a in verdict.anomalies:
    print(f"  [{a.severity}] {a.anomaly_type}: {a.root_cause} "
          f"(confidence {a.cause_confidence:.0%})")
```

### Investigation Phases

| Phase | What Happens |
|-------|-------------|
| **Scene Survey** | Baseline statistics — count, bounds, NN distances, density grid |
| **Anomaly Detection** | Density hotspots/voids, spacing outliers, tight clusters, boundary accumulation |
| **Evidence Collection** | Density ratios, NN deviations, spatial autocorrelation, geometric regularity |
| **Root Cause Classification** | 7 cause types with confidence scores (see below) |
| **Causal Chain Construction** | Links related anomalies into explanatory chains |
| **Verdict Generation** | Integrity score 0–100, risk level, diagnosis, remediation recommendations |

### Root Cause Types

| Cause | Description |
|-------|-------------|
| Data Corruption | Random errors or truncation in coordinate values |
| Systematic Drift | Progressive shift in point placement over time |
| Equipment Artifact | Sensor or device-specific placement patterns |
| Boundary Effect | Anomalous concentration near domain boundaries |
| Intentional Injection | Deliberately placed outlier points |
| Sampling Bias | Non-uniform sampling methodology |
| Natural Clustering | Genuine spatial clustering (not an error) |

### Detailed API

```python
from vormap_forensics import ForensicsEngine

engine = ForensicsEngine(points=[(0, 0), (5, 5), (10, 10)])
verdict = engine.investigate()
engine.to_json("forensics.json")
engine.to_html("forensics.html")
```

---

## Swarm Intelligence — `vormap_swarm`

Treats Voronoi cells as intelligent swarm agents that sense neighbors,
propagate signals, and collectively solve spatial problems through emergent
behavior.

### Behavior Modes

| Mode | Description |
|------|-------------|
| `"consensus"` | Cells vote on classifications; faction boundaries emerge at opinion frontiers |
| `"balance"` | Cells redistribute energy to minimize variance; identifies stubborn hotspots |
| `"alert"` | Anomaly cells broadcast signals that propagate with decay; finds relay bottlenecks |
| `"territory"` | Weighted cells negotiate influence zones; stable borders and contested zones emerge |
| `"pathfind"` | Stigmergic pheromone signals discover optimal routes; highway corridors emerge |

### API

```python
from vormap_swarm import SwarmEngine, swarm_simulate

# Quick one-liner
result = swarm_simulate("points.txt", behavior="consensus")
print(f"Converged: {result.convergence_history[-1]:.1%} "
      f"in {result.ticks_run} ticks")

# Detailed API
engine = SwarmEngine(
    points=[(0, 0), (10, 0), (5, 8)],
    behavior="balance",
)
result = engine.run()
engine.to_json("swarm.json")
engine.to_html("swarm.html")
```

### CLI

```bash
python vormap_swarm.py points.txt --behavior consensus
python vormap_swarm.py points.txt --behavior balance --max-ticks 200
python vormap_swarm.py points.txt --json result.json --html dashboard.html
python vormap_swarm.py --demo
```

---

## Spatial Narrative — `vormap_narrative`

A data journalist for your point patterns. Reads a dataset, runs multiple
spatial analyses, and weaves findings into coherent, human-readable prose.

### Narrative Sections

The generator covers: overall impression, clustering behavior (Hopkins
statistic), regularity tendencies (Clark-Evans R), outlier landscape,
directional bias, hotspot summary, Voronoi cell statistics, and proactive
recommendations.

### API

```python
from vormap_narrative import narrate

story = narrate("datauni5.txt")
print(story["text"])       # Full prose narrative
print(story["sections"])   # Structured section data
```

### Output Formats

| Format | How |
|--------|-----|
| Plain text | Default `narrate()` output |
| Markdown | `narrate("data.txt", format="markdown")` |
| HTML report | `narrate("data.txt", html="report.html")` — interactive report with point-pattern mini-map |
| JSON | `narrate("data.txt", json=True)` — structured data for programmatic consumption |

### CLI

```bash
python vormap_narrative.py datauni5.txt
python vormap_narrative.py datauni5.txt --format markdown
python vormap_narrative.py datauni5.txt --html report.html
python vormap_narrative.py datauni5.txt --json
```

---

## Spatial Equilibrium — `vormap_equilibrium`

Simulates force fields across Voronoi cells and classifies spatial
stability. Includes basin mapping and tipping point detection.

### Key Concepts

- **Force Fields** — compute attractive/repulsive forces between cells
- **Stability Classification** — label each cell as stable, unstable, or
  metastable based on its force environment
- **Basin Mapping** — identify attraction basins that cells converge toward
- **Tipping Points** — detect thresholds where small perturbations cause
  large-scale reorganization

### API

```python
from vormap_equilibrium import EquilibriumAnalyzer

analyzer = EquilibriumAnalyzer(points=seeds, bounds=bounds)
result = analyzer.analyze()
print(f"Stable cells: {result.stable_count}/{result.total_cells}")
print(f"Tipping points found: {len(result.tipping_points)}")
result.to_html("equilibrium.html")
```

---

## Spatial Metabolism — `vormap_metabolism`

Models resource production, consumption, and trade flows across Voronoi
cells. Identifies bottlenecks and surplus/deficit regions.

### API

```python
from vormap_metabolism import MetabolismEngine

engine = MetabolismEngine(
    points=seeds,
    production_rates=[...],   # per-cell production
    consumption_rates=[...],  # per-cell consumption
)
result = engine.simulate(steps=50)
print(f"Total traded: {result.total_flow:.1f}")
for b in result.bottlenecks:
    print(f"  Bottleneck at cell {b.index}: throughput {b.throughput:.1f}")
```

---

## Spatial Nervous System — `vormap_nervous`

Neural signal propagation on Voronoi topology. Simulates reflex arcs,
rhythm analysis, and Hebbian plasticity where frequently co-activated
connections strengthen over time.

### API

```python
from vormap_nervous import NervousSystem

ns = NervousSystem(points=seeds)
ns.stimulate(cell_index=0, intensity=1.0)
result = ns.propagate(steps=20)
print(f"Activated cells: {result.activated_count}/{result.total_cells}")
print(f"Dominant rhythm: {result.dominant_frequency:.2f} Hz")
```

---

## Spatial Guardian — `vormap_guardian`

Constraint enforcement and auto-repair engine. Define spatial constraints
(minimum spacing, maximum density, boundary exclusion zones) and the
guardian automatically detects violations and prescribes fixes.

### API

```python
from vormap_guardian import Guardian, MinSpacingConstraint, MaxDensityConstraint

guardian = Guardian(points=seeds)
guardian.add_constraint(MinSpacingConstraint(min_distance=10.0))
guardian.add_constraint(MaxDensityConstraint(max_per_unit=5))

report = guardian.audit()
print(f"Violations: {report.violation_count}")

# Auto-repair
fixed_points = guardian.repair()
```

---

## Spatial Negotiator — `vormap_negotiator`

Multi-party conflict resolution engine for spatial resource allocation
disputes. Models competing stakeholders with preferences and constraints,
then finds compromise solutions.

### API

```python
from vormap_negotiator import NegotiationEngine, Stakeholder

stakeholders = [
    Stakeholder("City", preferred_regions=[0, 1, 2], weight=1.0),
    Stakeholder("County", preferred_regions=[2, 3, 4], weight=0.8),
]

engine = NegotiationEngine(points=seeds, stakeholders=stakeholders)
result = engine.negotiate(rounds=50)
print(f"Agreement reached: {result.converged}")
print(f"Satisfaction: {result.mean_satisfaction:.0%}")
```

---

## Maze Generator — `vormap_maze`

Generates mazes on Voronoi tessellations using DFS, Kruskal, and Prim
algorithms. Exports SVG and includes a BFS solver.

### API

```python
from vormap_maze import generate_maze

maze = generate_maze(
    num_points=100,
    algorithm="dfs",      # "dfs", "kruskal", or "prim"
    width=800,
    height=600,
    seed=42,
)
maze.to_svg("maze.svg")

# Solve the maze
solution = maze.solve(start=0, end=maze.num_cells - 1)
maze.to_svg("maze_solved.svg", solution=solution)
```

### CLI

```bash
python vormap_maze.py --points 100 --algorithm dfs --svg maze.svg
python vormap_maze.py --points 200 --algorithm kruskal --solve --svg maze.svg
```

---

## Fracture Simulator — `vormap_fracture`

Simulates realistic material fracture patterns on Voronoi tessellations.
Models stress propagation and crack formation.

### API

```python
from vormap_fracture import FractureSimulator

sim = FractureSimulator(
    num_points=500,
    material="ceramic",   # "ceramic", "glass", "metal"
)
result = sim.fracture(
    impact_point=(400, 300),
    force=1.0,
)
result.to_svg("fracture.svg")
print(f"Fragments: {result.fragment_count}")
print(f"Largest fragment area: {result.largest_fragment_area:.1f}")
```

---

## What's Next

- See the [Module Catalog](module-catalog.md) for the complete list of all 138 modules
- See the [Extension Modules API](extensions.md) for core modules (viz, clustering, queries, etc.)
- Read the [Tutorials](tutorials.md) for step-by-step workflows
- Check the [Algorithm Overview](../algorithm/overview.md) for theoretical foundations
