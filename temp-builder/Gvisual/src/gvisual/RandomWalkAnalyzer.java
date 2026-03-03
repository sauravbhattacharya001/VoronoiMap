package gvisual;

import edu.uci.ics.jung.graph.Graph;
import java.util.*;

/**
 * Analyzes random walk behavior on graphs — a fundamental tool for
 * understanding information diffusion, network navigability, and
 * structural properties of social networks.
 *
 * <p>Random walks model how a "walker" traverses a graph by repeatedly
 * moving to a uniformly random neighbor. The statistics of these walks
 * reveal deep structural properties:</p>
 *
 * <ul>
 *   <li><strong>Hitting time</strong> — expected steps to reach node t from
 *       node s. Asymmetric: H(s,t) ≠ H(t,s) in general.</li>
 *   <li><strong>Commute distance</strong> — H(s,t) + H(t,s). Symmetric and
 *       satisfies the triangle inequality; a true metric on graphs.</li>
 *   <li><strong>Cover time</strong> — expected steps to visit every node
 *       starting from a given source.</li>
 *   <li><strong>Mixing time</strong> — steps until the walk's distribution
 *       is close to stationary (within total variation ε).</li>
 *   <li><strong>Return time</strong> — expected steps to return to the start
 *       node. For undirected graphs, equals 2|E|/deg(v).</li>
 *   <li><strong>Stationary distribution</strong> — the long-run fraction of
 *       time spent at each node. For undirected graphs, proportional to degree.</li>
 * </ul>
 *
 * <p>Applications in social network analysis:</p>
 * <ul>
 *   <li>Measuring "closeness" via commute distance (robust to noise)</li>
 *   <li>Detecting bottlenecks via high hitting times</li>
 *   <li>Understanding how quickly information spreads (mixing time)</li>
 *   <li>Identifying well-connected regions (low cover time)</li>
 * </ul>
 *
 * @author zalenix
 */
public class RandomWalkAnalyzer {

    private final Random rng;
    private final int defaultSimulations;

    /**
     * Creates an analyzer with default settings (10000 simulations, random seed).
     */
    public RandomWalkAnalyzer() {
        this(10000, new Random());
    }

    /**
     * Creates an analyzer with specified simulation count and RNG.
     *
     * @param simulations number of Monte Carlo simulations per estimate
     * @param rng         random number generator
     */
    public RandomWalkAnalyzer(int simulations, Random rng) {
        if (simulations < 1) throw new IllegalArgumentException("simulations must be >= 1");
        if (rng == null) throw new IllegalArgumentException("rng must not be null");
        this.defaultSimulations = simulations;
        this.rng = rng;
    }

    // ── Hitting Time ───────────────────────────────────────────────────

    /**
     * Estimates the expected hitting time from source to target via Monte Carlo.
     *
     * @param graph  the graph
     * @param source start node
     * @param target destination node
     * @return estimated expected number of steps, or Double.POSITIVE_INFINITY if unreachable
     */
    public <V, E> double hittingTime(Graph<V, E> graph, V source, V target) {
        validateGraph(graph);
        validateNode(graph, source, "source");
        validateNode(graph, target, "target");
        if (source.equals(target)) return 0.0;

        long totalSteps = 0;
        int reached = 0;
        int maxSteps = graph.getVertexCount() * graph.getVertexCount() * 10;

        for (int sim = 0; sim < defaultSimulations; sim++) {
            int steps = simulateWalkToTarget(graph, source, target, maxSteps);
            if (steps >= 0) {
                totalSteps += steps;
                reached++;
            }
        }

        return reached == 0 ? Double.POSITIVE_INFINITY : (double) totalSteps / reached;
    }

    /**
     * Computes hitting times from a source to all other nodes.
     *
     * @param graph  the graph
     * @param source start node
     * @return map of target → expected hitting time
     */
    public <V, E> Map<V, Double> hittingTimesFrom(Graph<V, E> graph, V source) {
        validateGraph(graph);
        validateNode(graph, source, "source");

        Map<V, Double> result = new LinkedHashMap<>();
        for (V target : graph.getVertices()) {
            result.put(target, hittingTime(graph, source, target));
        }
        return result;
    }

    // ── Commute Distance ───────────────────────────────────────────────

    /**
     * Computes the commute distance between two nodes: H(s,t) + H(t,s).
     *
     * @param graph  the graph
     * @param nodeA  first node
     * @param nodeB  second node
     * @return commute distance (symmetric)
     */
    public <V, E> double commuteDistance(Graph<V, E> graph, V nodeA, V nodeB) {
        return hittingTime(graph, nodeA, nodeB) + hittingTime(graph, nodeB, nodeA);
    }

    /**
     * Computes the full commute distance matrix for all node pairs.
     *
     * @param graph the graph
     * @return map of (nodeA, nodeB) → commute distance
     */
    public <V, E> Map<V, Map<V, Double>> commuteDistanceMatrix(Graph<V, E> graph) {
        validateGraph(graph);
        List<V> nodes = new ArrayList<>(graph.getVertices());
        Map<V, Map<V, Double>> matrix = new LinkedHashMap<>();

        Map<V, Map<V, Double>> hitting = new LinkedHashMap<>();
        for (V v : nodes) {
            hitting.put(v, hittingTimesFrom(graph, v));
        }

        for (V a : nodes) {
            Map<V, Double> row = new LinkedHashMap<>();
            for (V b : nodes) {
                row.put(b, hitting.get(a).get(b) + hitting.get(b).get(a));
            }
            matrix.put(a, row);
        }
        return matrix;
    }

    // ── Cover Time ─────────────────────────────────────────────────────

    /**
     * Estimates the expected cover time — steps to visit every reachable node.
     *
     * @param graph  the graph
     * @param source start node
     * @return estimated expected cover time
     */
    public <V, E> double coverTime(Graph<V, E> graph, V source) {
        validateGraph(graph);
        validateNode(graph, source, "source");

        long totalSteps = 0;
        int maxSteps = graph.getVertexCount() * graph.getVertexCount() * 20;

        for (int sim = 0; sim < defaultSimulations; sim++) {
            totalSteps += simulateCoverWalk(graph, source, maxSteps);
        }

        return (double) totalSteps / defaultSimulations;
    }

    /**
     * Computes cover time from each node in the graph.
     *
     * @param graph the graph
     * @return map of source → expected cover time
     */
    public <V, E> Map<V, Double> coverTimeFromAll(Graph<V, E> graph) {
        validateGraph(graph);
        Map<V, Double> result = new LinkedHashMap<>();
        for (V v : graph.getVertices()) {
            result.put(v, coverTime(graph, v));
        }
        return result;
    }

    // ── Return Time ────────────────────────────────────────────────────

    /**
     * Estimates the expected return time — steps for a walk to return to start.
     * For undirected graphs, the exact value is 2|E|/deg(v).
     *
     * @param graph the graph
     * @param node  the node
     * @return estimated expected return time
     */
    public <V, E> double returnTime(Graph<V, E> graph, V node) {
        validateGraph(graph);
        validateNode(graph, node, "node");

        int degree = graph.degree(node);
        if (degree == 0) return Double.POSITIVE_INFINITY;

        long totalSteps = 0;
        int maxSteps = graph.getVertexCount() * graph.getVertexCount() * 10;

        for (int sim = 0; sim < defaultSimulations; sim++) {
            totalSteps += simulateReturnWalk(graph, node, maxSteps);
        }

        return (double) totalSteps / defaultSimulations;
    }

    // ── Mixing Time ────────────────────────────────────────────────────

    /**
     * Estimates the mixing time — steps until the walk distribution is within
     * epsilon (total variation distance) of the stationary distribution.
     *
     * @param graph   the graph
     * @param epsilon convergence threshold (e.g. 0.01)
     * @return estimated mixing time in steps
     */
    public <V, E> int mixingTime(Graph<V, E> graph, double epsilon) {
        validateGraph(graph);
        if (epsilon <= 0 || epsilon >= 1)
            throw new IllegalArgumentException("epsilon must be in (0,1)");

        int n = graph.getVertexCount();
        if (n == 0) return 0;

        List<V> nodeList = new ArrayList<>(graph.getVertices());
        Map<V, Integer> nodeIndex = new HashMap<>();
        for (int i = 0; i < nodeList.size(); i++) {
            nodeIndex.put(nodeList.get(i), i);
        }

        Map<V, Double> stationary = stationaryDistribution(graph);
        double[] stationaryArr = new double[n];
        for (int i = 0; i < n; i++) {
            stationaryArr[i] = stationary.get(nodeList.get(i));
        }

        double[][] P = buildTransitionMatrix(graph, nodeList, nodeIndex);

        int maxTime = n * n * 5;
        double[][] dist = new double[n][n];
        for (int i = 0; i < n; i++) {
            dist[i][i] = 1.0;
        }

        for (int t = 1; t <= maxTime; t++) {
            double[][] newDist = new double[n][n];
            for (int start = 0; start < n; start++) {
                for (int j = 0; j < n; j++) {
                    for (int k = 0; k < n; k++) {
                        newDist[start][j] += dist[start][k] * P[k][j];
                    }
                }
            }
            dist = newDist;

            double maxTV = 0;
            for (int start = 0; start < n; start++) {
                double tv = 0;
                for (int j = 0; j < n; j++) {
                    tv += Math.abs(dist[start][j] - stationaryArr[j]);
                }
                tv /= 2.0;
                maxTV = Math.max(maxTV, tv);
            }

            if (maxTV <= epsilon) return t;
        }

        return maxTime;
    }

    // ── Stationary Distribution ────────────────────────────────────────

    /**
     * Computes the stationary distribution. For undirected graphs, this is
     * proportional to node degree: π(v) = deg(v) / (2|E|).
     *
     * @param graph the graph
     * @return map of node → stationary probability
     */
    public <V, E> Map<V, Double> stationaryDistribution(Graph<V, E> graph) {
        validateGraph(graph);
        Map<V, Double> dist = new LinkedHashMap<>();

        int totalDegree = 0;
        for (V v : graph.getVertices()) {
            totalDegree += graph.degree(v);
        }

        if (totalDegree == 0) {
            double uniform = 1.0 / graph.getVertexCount();
            for (V v : graph.getVertices()) {
                dist.put(v, uniform);
            }
            return dist;
        }

        for (V v : graph.getVertices()) {
            dist.put(v, (double) graph.degree(v) / totalDegree);
        }
        return dist;
    }

    // ── Walk Trace ─────────────────────────────────────────────────────

    /**
     * Performs a single random walk and returns the full path.
     *
     * @param graph  the graph
     * @param source start node
     * @param steps  number of steps to walk
     * @return list of visited nodes (length = steps + 1)
     */
    public <V, E> List<V> walkTrace(Graph<V, E> graph, V source, int steps) {
        validateGraph(graph);
        validateNode(graph, source, "source");
        if (steps < 0) throw new IllegalArgumentException("steps must be >= 0");

        List<V> trace = new ArrayList<>(steps + 1);
        V current = source;
        trace.add(current);

        for (int i = 0; i < steps; i++) {
            List<V> neighbors = new ArrayList<>(graph.getNeighbors(current));
            if (neighbors.isEmpty()) break;
            current = neighbors.get(rng.nextInt(neighbors.size()));
            trace.add(current);
        }

        return trace;
    }

    /**
     * Computes visit frequency from a walk — how often each node is visited
     * as a fraction of total steps.
     *
     * @param graph  the graph
     * @param source start node
     * @param steps  number of steps
     * @return map of node → visit fraction
     */
    public <V, E> Map<V, Double> visitFrequency(Graph<V, E> graph, V source, int steps) {
        List<V> trace = walkTrace(graph, source, steps);
        Map<V, Double> freq = new LinkedHashMap<>();
        for (V v : graph.getVertices()) {
            freq.put(v, 0.0);
        }
        for (V v : trace) {
            freq.put(v, freq.get(v) + 1);
        }
        double total = trace.size();
        for (V v : freq.keySet()) {
            freq.put(v, freq.get(v) / total);
        }
        return freq;
    }

    // ── Summary ────────────────────────────────────────────────────────

    /**
     * Computes a summary of random walk statistics for the graph.
     *
     * @param graph the graph
     * @return summary object with key metrics
     */
    public <V, E> WalkSummary<V> summarize(Graph<V, E> graph) {
        validateGraph(graph);
        Map<V, Double> stationary = stationaryDistribution(graph);

        V mostVisited = null;
        double maxProb = -1;
        V leastVisited = null;
        double minProb = Double.MAX_VALUE;

        for (Map.Entry<V, Double> e : stationary.entrySet()) {
            if (e.getValue() > maxProb) {
                maxProb = e.getValue();
                mostVisited = e.getKey();
            }
            if (e.getValue() < minProb) {
                minProb = e.getValue();
                leastVisited = e.getKey();
            }
        }

        V bestSource = mostVisited;
        double avgCoverTime = coverTime(graph, bestSource);

        return new WalkSummary<>(
            graph.getVertexCount(),
            graph.getEdgeCount(),
            stationary,
            mostVisited, maxProb,
            leastVisited, minProb,
            avgCoverTime,
            bestSource
        );
    }

    // ── Result Types ───────────────────────────────────────────────────

    public static class WalkSummary<V> {
        private final int nodeCount;
        private final int edgeCount;
        private final Map<V, Double> stationaryDistribution;
        private final V mostVisitedNode;
        private final double mostVisitedProb;
        private final V leastVisitedNode;
        private final double leastVisitedProb;
        private final double coverTimeFromBest;
        private final V coverTimeSource;

        public WalkSummary(int nodeCount, int edgeCount,
                           Map<V, Double> stationaryDistribution,
                           V mostVisitedNode, double mostVisitedProb,
                           V leastVisitedNode, double leastVisitedProb,
                           double coverTimeFromBest, V coverTimeSource) {
            this.nodeCount = nodeCount;
            this.edgeCount = edgeCount;
            this.stationaryDistribution = Collections.unmodifiableMap(stationaryDistribution);
            this.mostVisitedNode = mostVisitedNode;
            this.mostVisitedProb = mostVisitedProb;
            this.leastVisitedNode = leastVisitedNode;
            this.leastVisitedProb = leastVisitedProb;
            this.coverTimeFromBest = coverTimeFromBest;
            this.coverTimeSource = coverTimeSource;
        }

        public int getNodeCount() { return nodeCount; }
        public int getEdgeCount() { return edgeCount; }
        public Map<V, Double> getStationaryDistribution() { return stationaryDistribution; }
        public V getMostVisitedNode() { return mostVisitedNode; }
        public double getMostVisitedProb() { return mostVisitedProb; }
        public V getLeastVisitedNode() { return leastVisitedNode; }
        public double getLeastVisitedProb() { return leastVisitedProb; }
        public double getCoverTimeFromBest() { return coverTimeFromBest; }
        public V getCoverTimeSource() { return coverTimeSource; }

        @Override
        public String toString() {
            return String.format(
                "WalkSummary{nodes=%d, edges=%d, mostVisited=%s(%.4f), " +
                "leastVisited=%s(%.4f), coverTime=%.1f from %s}",
                nodeCount, edgeCount,
                mostVisitedNode, mostVisitedProb,
                leastVisitedNode, leastVisitedProb,
                coverTimeFromBest, coverTimeSource
            );
        }
    }

    // ── Private Helpers ────────────────────────────────────────────────

    private <V, E> int simulateWalkToTarget(Graph<V, E> graph, V source, V target, int maxSteps) {
        V current = source;
        for (int step = 1; step <= maxSteps; step++) {
            List<V> neighbors = new ArrayList<>(graph.getNeighbors(current));
            if (neighbors.isEmpty()) return -1;
            current = neighbors.get(rng.nextInt(neighbors.size()));
            if (current.equals(target)) return step;
        }
        return -1;
    }

    private <V, E> long simulateCoverWalk(Graph<V, E> graph, V source, int maxSteps) {
        Set<V> visited = new HashSet<>();
        V current = source;
        visited.add(current);

        Set<V> reachable = new HashSet<>();
        Queue<V> bfsQueue = new LinkedList<>();
        bfsQueue.add(source);
        reachable.add(source);
        while (!bfsQueue.isEmpty()) {
            V v = bfsQueue.poll();
            for (V n : graph.getNeighbors(v)) {
                if (reachable.add(n)) bfsQueue.add(n);
            }
        }
        int targetCount = reachable.size();

        if (visited.size() >= targetCount) return 0;

        for (int step = 1; step <= maxSteps; step++) {
            List<V> neighbors = new ArrayList<>(graph.getNeighbors(current));
            if (neighbors.isEmpty()) return step;
            current = neighbors.get(rng.nextInt(neighbors.size()));
            visited.add(current);
            if (visited.size() >= targetCount) return step;
        }
        return maxSteps;
    }

    private <V, E> long simulateReturnWalk(Graph<V, E> graph, V node, int maxSteps) {
        V current = node;
        List<V> neighbors = new ArrayList<>(graph.getNeighbors(current));
        if (neighbors.isEmpty()) return maxSteps;
        current = neighbors.get(rng.nextInt(neighbors.size()));

        for (int step = 2; step <= maxSteps; step++) {
            if (current.equals(node)) return step;
            neighbors = new ArrayList<>(graph.getNeighbors(current));
            if (neighbors.isEmpty()) return maxSteps;
            current = neighbors.get(rng.nextInt(neighbors.size()));
        }
        return maxSteps;
    }

    private <V, E> double[][] buildTransitionMatrix(Graph<V, E> graph, List<V> nodeList, Map<V, Integer> nodeIndex) {
        int n = nodeList.size();
        double[][] P = new double[n][n];

        for (int i = 0; i < n; i++) {
            V v = nodeList.get(i);
            Collection<V> neighbors = graph.getNeighbors(v);
            int deg = neighbors.size();
            if (deg == 0) {
                P[i][i] = 1.0;
            } else {
                for (V nb : neighbors) {
                    P[i][nodeIndex.get(nb)] += 1.0 / deg;
                }
            }
        }
        return P;
    }

    private <V, E> void validateGraph(Graph<V, E> graph) {
        if (graph == null) throw new IllegalArgumentException("graph must not be null");
    }

    private <V, E> void validateNode(Graph<V, E> graph, V node, String name) {
        if (node == null) throw new IllegalArgumentException(name + " must not be null");
        if (!graph.containsVertex(node))
            throw new IllegalArgumentException(name + " not found in graph: " + node);
    }
}
