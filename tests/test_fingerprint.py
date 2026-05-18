"""Tests for vormap_fingerprint — spatial distribution fingerprinting."""

import math
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vormap_fingerprint import (
    fingerprint,
    compare,
    classify_vector,
    DIMENSION_NAMES,
    _nn_distances,
    _mean,
    _var,
    _std,
    _skewness,
    _kurtosis,
    _bbox,
    _quadrat_vmr,
    _hopkins,
    _clark_evans,
    _angular_entropy,
    _hull_efficiency,
    _lacunarity,
    _fractal_dimension,
)


# ---------------------------------------------------------------------------
# Helper fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def grid_points():
    """Regular 5x5 grid — very regular spatial pattern."""
    return [(i, j) for i in range(5) for j in range(5)]


@pytest.fixture
def cluster_points():
    """Two tight clusters — clustered spatial pattern."""
    import random
    rng = random.Random(123)
    pts = []
    for _ in range(15):
        pts.append((1.0 + rng.gauss(0, 0.1), 1.0 + rng.gauss(0, 0.1)))
    for _ in range(15):
        pts.append((5.0 + rng.gauss(0, 0.1), 5.0 + rng.gauss(0, 0.1)))
    return pts


@pytest.fixture
def random_points():
    """Uniform random points — CSR pattern."""
    import random
    rng = random.Random(99)
    return [(rng.uniform(0, 10), rng.uniform(0, 10)) for _ in range(50)]


@pytest.fixture
def tmp_pointfile(grid_points):
    """Write grid points to a temp file for file-based testing."""
    fd, path = tempfile.mkstemp(suffix=".txt")
    with os.fdopen(fd, "w") as f:
        for x, y in grid_points:
            f.write(f"{x} {y}\n")
    yield path
    os.unlink(path)


# ---------------------------------------------------------------------------
# Low-level helper tests
# ---------------------------------------------------------------------------

class TestHelpers:
    def test_mean_basic(self):
        assert _mean([1, 2, 3, 4, 5]) == 3.0

    def test_mean_single(self):
        assert _mean([7.0]) == 7.0

    def test_var_constant(self):
        assert _var([5, 5, 5]) == 0.0

    def test_var_known(self):
        # Var of [1,2,3,4,5] = 2.0 (population variance)
        assert abs(_var([1, 2, 3, 4, 5]) - 2.0) < 1e-9

    def test_std_basic(self):
        assert abs(_std([1, 2, 3, 4, 5]) - math.sqrt(2.0)) < 1e-9

    def test_skewness_symmetric(self):
        # Symmetric distribution → skewness ≈ 0
        vals = [-2, -1, 0, 1, 2]
        assert abs(_skewness(vals)) < 1e-9

    def test_skewness_right(self):
        # Right-skewed distribution
        vals = [1, 1, 1, 1, 1, 10]
        assert _skewness(vals) > 0

    def test_kurtosis_normal_ish(self):
        # Uniform-ish distribution has negative excess kurtosis
        vals = list(range(100))
        k = _kurtosis(vals)
        assert k < 0  # Platykurtic

    def test_bbox(self):
        pts = [(0, 0), (5, 3), (2, 7)]
        assert _bbox(pts) == (0, 0, 5, 7)


# ---------------------------------------------------------------------------
# Nearest-neighbor distances
# ---------------------------------------------------------------------------

class TestNNDistances:
    def test_two_points(self):
        pts = [(0, 0), (3, 4)]
        dists = _nn_distances(pts)
        assert len(dists) == 2
        assert abs(dists[0] - 5.0) < 1e-9
        assert abs(dists[1] - 5.0) < 1e-9

    def test_grid_nn(self, grid_points):
        dists = _nn_distances(grid_points)
        # All NN distances in a unit grid should be 1.0
        assert all(abs(d - 1.0) < 1e-9 for d in dists)

    def test_returns_correct_count(self, random_points):
        dists = _nn_distances(random_points)
        assert len(dists) == len(random_points)


# ---------------------------------------------------------------------------
# Fingerprint dimensions
# ---------------------------------------------------------------------------

class TestQuadratVMR:
    def test_regular_low_vmr(self, grid_points):
        # Regular grid should have low VMR
        vmr = _quadrat_vmr(grid_points)
        assert vmr < 1.5  # close to 1 for uniform

    def test_clustered_high_vmr(self, cluster_points):
        # Clustered points → high VMR
        vmr = _quadrat_vmr(cluster_points)
        assert vmr > 2.0

    def test_too_few_points(self):
        assert _quadrat_vmr([(0, 0)]) == 0.0


class TestHopkins:
    def test_deterministic_seed(self, grid_points):
        # Should be deterministic (uses Random(42))
        h1 = _hopkins(grid_points)
        h2 = _hopkins(grid_points)
        assert h1 == h2

    def test_regular_low_hopkins(self, grid_points):
        # Regular pattern → Hopkins < 0.5
        h = _hopkins(grid_points)
        assert h < 0.5

    def test_clustered_high_hopkins(self, cluster_points):
        # Clustered → Hopkins > 0.5
        h = _hopkins(cluster_points)
        assert h > 0.5

    def test_insufficient_points(self):
        assert _hopkins([(0, 0), (1, 1)]) == 0.5


class TestClarkEvans:
    def test_regular_high_ce(self, grid_points):
        # Regular grid → CE > 1
        ce = _clark_evans(grid_points)
        assert ce > 1.0

    def test_clustered_low_ce(self, cluster_points):
        # Clustered → CE < 1
        ce = _clark_evans(cluster_points)
        assert ce < 1.0

    def test_single_point(self):
        assert _clark_evans([(0, 0)]) == 1.0


class TestAngularEntropy:
    def test_uniform_high_entropy(self, grid_points):
        # Grid points are fairly uniform around centroid
        ae = _angular_entropy(grid_points)
        assert ae > 0.8  # High entropy = uniform angular distribution

    def test_collinear_low_entropy(self):
        # All points on x-axis → concentrated in 1-2 sectors
        pts = [(i, 0) for i in range(20)]
        ae = _angular_entropy(pts)
        assert ae < 0.5

    def test_too_few_points(self):
        assert _angular_entropy([(0, 0), (1, 1)]) == 0.0


class TestHullEfficiency:
    def test_square_full_efficiency(self):
        # Square corners fill entire bounding box → efficiency = 1.0
        pts = [(0, 0), (1, 0), (1, 1), (0, 1)]
        he = _hull_efficiency(pts)
        assert abs(he - 1.0) < 0.01

    def test_triangle_half_efficiency(self):
        # Right triangle: area = 0.5 * bbox area
        pts = [(0, 0), (4, 0), (0, 4)]
        he = _hull_efficiency(pts)
        assert abs(he - 0.5) < 0.01

    def test_insufficient_points(self):
        assert _hull_efficiency([(0, 0), (1, 1)]) == 0.0


class TestLacunarity:
    def test_returns_three_values(self, grid_points):
        lac = _lacunarity(grid_points)
        assert len(lac) == 3

    def test_regular_lower_lacunarity(self, grid_points):
        lac_reg = _lacunarity(grid_points)
        # Lacunarity ≥ 1.0 by definition
        assert all(l >= 1.0 for l in lac_reg)

    def test_too_few_points(self):
        lac = _lacunarity([(0, 0)])
        assert lac == [0.0, 0.0, 0.0]


class TestFractalDimension:
    def test_grid_dimension(self, grid_points):
        fd = _fractal_dimension(grid_points)
        # Small 5x5 grid may have low estimated dimension; just ensure positive
        assert fd > 0.0

    def test_random_cloud_dimension(self):
        # Dense random 2D cloud gives positive fractal dimension
        import random
        rng = random.Random(42)
        pts = [(rng.uniform(0, 100), rng.uniform(0, 100)) for _ in range(500)]
        fd = _fractal_dimension(pts)
        assert 0 < fd < 3.0  # Positive and bounded

    def test_collinear_lower_dimension(self):
        # Points on a line → lower fractal dimension
        pts = [(i * 0.01, 0) for i in range(100)]
        fd = _fractal_dimension(pts)
        assert fd < 1.5

    def test_too_few_points(self):
        assert _fractal_dimension([(0, 0), (1, 1)]) == 0.0


# ---------------------------------------------------------------------------
# Core API
# ---------------------------------------------------------------------------

class TestFingerprint:
    def test_from_pts(self, grid_points):
        fp = fingerprint("test_grid", pts=grid_points)
        assert fp["name"] == "test_grid"
        assert fp["n_points"] == 25
        assert len(fp["vector"]) == len(DIMENSION_NAMES)
        assert set(fp["dimensions"].keys()) == set(DIMENSION_NAMES)
        assert "label" in fp["classification"]
        assert "confidence" in fp["classification"]

    def test_from_file(self, tmp_pointfile):
        fp = fingerprint(tmp_pointfile)
        assert fp["n_points"] == 25
        assert fp["name"] == os.path.basename(tmp_pointfile)

    def test_insufficient_points(self):
        fp = fingerprint("sparse", pts=[(0, 0), (1, 1)])
        assert fp["n_points"] == 2
        assert fp["classification"]["label"] == "Insufficient data"
        assert all(v == 0.0 for v in fp["vector"])

    def test_vector_length_matches_dim_names(self, random_points):
        fp = fingerprint("rand", pts=random_points)
        assert len(fp["vector"]) == len(DIMENSION_NAMES)

    def test_values_are_finite(self, random_points):
        fp = fingerprint("rand", pts=random_points)
        assert all(math.isfinite(v) for v in fp["vector"])


class TestClassifyVector:
    def test_clustered_detection(self):
        dims = {
            "hopkins": 0.85, "clark_evans": 0.3, "quadrat_vmr": 5.0,
            "lacunarity_3": 2.0, "lacunarity_10": 1.5,
        }
        cls = classify_vector(dims)
        assert cls["label"] == "Clustered"
        assert 0 < cls["confidence"] <= 99

    def test_regular_detection(self):
        dims = {
            "hopkins": 0.2, "clark_evans": 1.8, "quadrat_vmr": 0.3,
            "lacunarity_3": 1.2, "lacunarity_10": 1.1,
        }
        cls = classify_vector(dims)
        assert cls["label"] in ("Regular", "Dispersed")

    def test_random_detection(self):
        dims = {
            "hopkins": 0.5, "clark_evans": 1.0, "quadrat_vmr": 1.0,
            "lacunarity_3": 1.5, "lacunarity_10": 1.5,
        }
        cls = classify_vector(dims)
        assert cls["label"] == "Random/CSR"

    def test_multiscale_detection(self):
        dims = {
            "hopkins": 0.75, "clark_evans": 0.6, "quadrat_vmr": 4.0,
            "lacunarity_3": 5.0, "lacunarity_10": 1.0,
        }
        cls = classify_vector(dims)
        # Multi-scale gets score from both VMR>3 and lacunarity spread
        assert cls["label"] in ("Clustered", "Multi-scale")


class TestCompare:
    def test_identical_fingerprints(self, grid_points):
        fp = fingerprint("g1", pts=grid_points)
        result = compare(fp, fp)
        assert result["similarity"] == 100.0
        assert result["cosine"] == 1.0
        assert result["weighted_distance"] == 0.0
        assert all(v == 0.0 for v in result["deltas"].values())

    def test_similar_patterns(self, grid_points):
        # Slightly offset grid should be very similar
        fp1 = fingerprint("g1", pts=grid_points)
        offset_pts = [(x + 100, y + 100) for x, y in grid_points]
        fp2 = fingerprint("g2", pts=offset_pts)
        result = compare(fp1, fp2)
        # Translation-invariant → high similarity
        assert result["similarity"] > 80.0

    def test_different_patterns(self, grid_points, cluster_points):
        fp1 = fingerprint("grid", pts=grid_points)
        fp2 = fingerprint("cluster", pts=cluster_points)
        result = compare(fp1, fp2)
        assert result["similarity"] < 80.0

    def test_deltas_structure(self, grid_points, random_points):
        fp1 = fingerprint("g", pts=grid_points)
        fp2 = fingerprint("r", pts=random_points)
        result = compare(fp1, fp2)
        assert "deltas" in result
        assert set(result["deltas"].keys()) == set(DIMENSION_NAMES)

    def test_symmetry(self, grid_points, random_points):
        fp1 = fingerprint("g", pts=grid_points)
        fp2 = fingerprint("r", pts=random_points)
        r12 = compare(fp1, fp2)
        r21 = compare(fp2, fp1)
        # Cosine similarity is symmetric
        assert r12["cosine"] == r21["cosine"]
        # Similarity score is symmetric (weighted distance is symmetric)
        assert abs(r12["similarity"] - r21["similarity"]) < 0.01


# ---------------------------------------------------------------------------
# File I/O edge cases
# ---------------------------------------------------------------------------

class TestFileInput:
    def test_comments_and_blanks_skipped(self):
        fd, path = tempfile.mkstemp(suffix=".txt")
        with os.fdopen(fd, "w") as f:
            f.write("# Header comment\n")
            f.write("\n")
            f.write("0 0\n")
            f.write("# Another comment\n")
            f.write("1 1\n")
            f.write("2 2\n")
            f.write("3 3\n")
        fp = fingerprint(path)
        assert fp["n_points"] == 4
        os.unlink(path)

    def test_comma_separated(self):
        fd, path = tempfile.mkstemp(suffix=".txt")
        with os.fdopen(fd, "w") as f:
            for i in range(5):
                f.write(f"{i},{i*2}\n")
        fp = fingerprint(path)
        assert fp["n_points"] == 5
        os.unlink(path)

    def test_invalid_lines_skipped(self):
        fd, path = tempfile.mkstemp(suffix=".txt")
        with os.fdopen(fd, "w") as f:
            f.write("0 0\n")
            f.write("not a number\n")
            f.write("1 1\n")
            f.write("abc def\n")
            f.write("2 2\n")
            f.write("3 3\n")
        fp = fingerprint(path)
        assert fp["n_points"] == 4
        os.unlink(path)


# ---------------------------------------------------------------------------
# Edge cases and robustness
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_collocated_points(self):
        """All points at same location — degenerate case."""
        pts = [(5.0, 5.0)] * 10
        fp = fingerprint("collocated", pts=pts)
        # Should not crash; all NN distances are 0
        assert fp["n_points"] == 10
        assert all(math.isfinite(v) for v in fp["vector"])

    def test_large_subsample(self):
        """More than 500 points triggers subsampling."""
        import random
        rng = random.Random(7)
        pts = [(rng.uniform(0, 100), rng.uniform(0, 100)) for _ in range(600)]
        fp = fingerprint("large", pts=pts)
        assert fp["n_points"] == 600
        assert all(math.isfinite(v) for v in fp["vector"])

    def test_three_points_minimum(self):
        """Exactly 3 points — minimum for fingerprinting."""
        pts = [(0, 0), (1, 0), (0, 1)]
        fp = fingerprint("triangle", pts=pts)
        assert fp["n_points"] == 3
        assert fp["classification"]["label"] != "Insufficient data"
