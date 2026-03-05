package gvisual;

import edu.uci.ics.jung.graph.Graph;
import edu.uci.ics.jung.graph.UndirectedSparseGraph;
import java.util.*;

/**
 * Vertex and Edge Connectivity analyzer for undirected graphs.
 *
 * <p>Computes fundamental connectivity measures:</p>
 * <ul>
 *   <li><b>Vertex connectivity (κ)</b> — minimum number of vertices whose
 *       removal disconnects the graph (or isolates a vertex)</li>
 *   <li><b>Edge connectivity (λ)</b> — minimum number of edges whose
 *       removal disconnects the graph</li>
 *   <li><b>Minimum vertex cut</b> — an actual set of vertices achieving κ</li>
 *   <li><b>Minimum edge cut</b> — an actual set of edges achieving λ</li>
 *   <li><b>Pairwise connectivity</b> — κ(s,t) and λ(s,t) between two vertices</li>
 *   <li><b>k-connectivity test</b> — whether the graph is k-vertex-connected</li>
 *   <li><b>Whitney's inequality</b> — verifies κ ≤ λ ≤ δ</li>
 *   <li><b>All minimum vertex cuts</b> — enumerates all minimum-size separating sets</li>
 *   <li><b>Vertex criticality</b> — connectivity drop per vertex removal</li>
 *   <li><b>Vertex/edge-disjoint paths</b> — Menger's theorem verification</li>
 * </ul>
 *
 * <p>Uses Edmonds-Karp (BFS-based Ford-Fulkerson) max-flow on a transformed
 * network where each vertex v is split into v_in → v_out with capacity 1,
 * and each original edge becomes two unit-capacity arcs.</p>
 *
 * <p>Usage:</p>
 * <pre>
 * VertexConnectivityAnalyzer analyzer = new VertexConnectivityAnalyzer(graph);
 * int kappa = analyzer.vertexConnectivity();
 * int lambda = analyzer.edgeConnectivity();
 * Set&lt;String&gt; minCut = analyzer.minimumVertexCut();
 * Set&lt;edge&gt; minEdgeCut = analyzer.minimumEdgeCut();
 * boolean is3connected = analyzer.isKVertexConnected(3);
 * PairwiseResult pr = analyzer.pairwiseConnectivity("A", "B");
 * String report = analyzer.generateReport();
 * </pre>
 *
 * @author zalenix
 */
public class VertexConnectivityAnalyzer {

    private final Graph<String, edge> graph;
    private static final int ALL_CUTS_LIMIT = 20;

    public VertexConnectivityAnalyzer(Graph<String, edge> graph) {
        if (graph == null) {
            throw new IllegalArgumentException("Graph must not be null");
        }
        this.graph = graph;
    }

    // ---- Result classes ----

    public static class ConnectivityResult {
        private final int vertexConnectivity;
        private final int edgeConnectivity;
        private final int minDegree;
        private final Set<String> minimumVertexCut;
        private final Set<edge> minimumEdgeCut;
        private final boolean whitneyHolds;
        private final int vertexCount;
        private final int edgeCount;
        private final boolean isConnected;

        public ConnectivityResult(int vertexConnectivity, int edgeConnectivity,
                                   int minDegree, Set<String> minimumVertexCut,
                                   Set<edge> minimumEdgeCut, boolean whitneyHolds,
                                   int vertexCount, int edgeCount, boolean isConnected) {
            this.vertexConnectivity = vertexConnectivity;
            this.edgeConnectivity = edgeConnectivity;
            this.minDegree = minDegree;
            this.minimumVertexCut = Collections.unmodifiableSet(new LinkedHashSet<>(minimumVertexCut));
            this.minimumEdgeCut = Collections.unmodifiableSet(new LinkedHashSet<>(minimumEdgeCut));
            this.whitneyHolds = whitneyHolds;
            this.vertexCount = vertexCount;
            this.edgeCount = edgeCount;
            this.isConnected = isConnected;
        }

        public int getVertexConnectivity() { return vertexConnectivity; }
        public int getEdgeConnectivity() { return edgeConnectivity; }
        public int getMinDegree() { return minDegree; }
        public Set<String> getMinimumVertexCut() { return minimumVertexCut; }
        public Set<edge> getMinimumEdgeCut() { return minimumEdgeCut; }
        public boolean isWhitneyHolds() { return whitneyHolds; }
        public int getVertexCount() { return vertexCount; }
        public int getEdgeCount() { return edgeCount; }
        public boolean isConnected() { return isConnected; }
    }

    public static class PairwiseResult {
        private final String source;
        private final String target;
        private final int vertexConnectivity;
        private final int edgeConnectivity;
        private final Set<String> minimumVertexCut;
        private final Set<edge> minimumEdgeCut;
        private final List<List<String>> vertexDisjointPaths;
        private final List<List<String>> edgeDisjointPaths;

        public PairwiseResult(String source, String target,
                               int vertexConnectivity, int edgeConnectivity,
                               Set<String> minimumVertexCut, Set<edge> minimumEdgeCut,
                               List<List<String>> vertexDisjointPaths,
                               List<List<String>> edgeDisjointPaths) {
            this.source = source;
            this.target = target;
            this.vertexConnectivity = vertexConnectivity;
            this.edgeConnectivity = edgeConnectivity;
            this.minimumVertexCut = Collections.unmodifiableSet(new LinkedHashSet<>(minimumVertexCut));
            this.minimumEdgeCut = Collections.unmodifiableSet(new LinkedHashSet<>(minimumEdgeCut));
            this.vertexDisjointPaths = Collections.unmodifiableList(vertexDisjointPaths);
            this.edgeDisjointPaths = Collections.unmodifiableList(edgeDisjointPaths);
        }

        public String getSource() { return source; }
        public String getTarget() { return target; }
        public int getVertexConnectivity() { return vertexConnectivity; }
        public int getEdgeConnectivity() { return edgeConnectivity; }
        public Set<String> getMinimumVertexCut() { return minimumVertexCut; }
        public Set<edge> getMinimumEdgeCut() { return minimumEdgeCut; }
        public List<List<String>> getVertexDisjointPaths() { return vertexDisjointPaths; }
        public List<List<String>> getEdgeDisjointPaths() { return edgeDisjointPaths; }
    }

    // ---- Core algorithms ----

    /**
     * Check if graph is connected via BFS.
     */
    public boolean isConnected() {
        Collection<String> vertices = graph.getVertices();
        if (vertices.size() <= 1) return true;
        String start = vertices.iterator().next();
        Set<String> visited = new HashSet<>();
        Queue<String> queue = new LinkedList<>();
        queue.add(start);
        visited.add(start);
        while (!queue.isEmpty()) {
            String v = queue.poll();
            for (String n : graph.getNeighbors(v)) {
                if (visited.add(n)) {
                    queue.add(n);
                }
            }
        }
        return visited.size() == vertices.size();
    }

    /**
     * Minimum degree δ(G).
     */
    public int minDegree() {
        if (graph.getVertexCount() == 0) return 0;
        int min = Integer.MAX_VALUE;
        for (String v : graph.getVertices()) {
            min = Math.min(min, graph.degree(v));
        }
        return min;
    }

    /**
     * Compute vertex connectivity κ(G).
     * For complete graphs, κ = n-1.
     * For disconnected graphs, κ = 0.
     * Otherwise, κ = min over all pairs (s,t) of non-adjacent vertices of κ(s,t).
     */
    public int vertexConnectivity() {
        int n = graph.getVertexCount();
        if (n <= 1) return 0;
        if (!isConnected()) return 0;

        // Check if complete graph
        int maxEdges = n * (n - 1) / 2;
        if (graph.getEdgeCount() == maxEdges) return n - 1;

        List<String> vList = new ArrayList<>(graph.getVertices());
        int minFlow = n;

        for (int i = 0; i < vList.size() && minFlow > 0; i++) {
            for (int j = i + 1; j < vList.size() && minFlow > 0; j++) {
                String s = vList.get(i);
                String t = vList.get(j);
                if (!graph.isNeighbor(s, t)) {
                    int flow = maxFlowVertexSplit(s, t);
                    minFlow = Math.min(minFlow, flow);
                }
            }
        }
        return minFlow;
    }

    /**
     * Compute edge connectivity λ(G).
     * λ = min over all s,t of λ(s,t).
     * Optimization: fix s, compute λ(s,t) for all t.
     */
    public int edgeConnectivity() {
        int n = graph.getVertexCount();
        if (n <= 1) return 0;
        if (!isConnected()) return 0;

        List<String> vList = new ArrayList<>(graph.getVertices());
        String s = vList.get(0);
        int minFlow = graph.getEdgeCount();

        for (int i = 1; i < vList.size() && minFlow > 0; i++) {
            int flow = maxFlowEdge(s, vList.get(i));
            minFlow = Math.min(minFlow, flow);
        }
        return minFlow;
    }

    /**
     * Find a minimum vertex cut set.
     */
    public Set<String> minimumVertexCut() {
        int n = graph.getVertexCount();
        if (n <= 1 || !isConnected()) return Collections.emptySet();

        int maxEdges = n * (n - 1) / 2;
        if (graph.getEdgeCount() == maxEdges) {
            Set<String> cut = new LinkedHashSet<>(graph.getVertices());
            String keep = cut.iterator().next();
            cut.remove(keep);
            return cut;
        }

        List<String> vList = new ArrayList<>(graph.getVertices());
        int bestFlow = n;
        String bestS = null, bestT = null;

        for (int i = 0; i < vList.size() && bestFlow > 0; i++) {
            for (int j = i + 1; j < vList.size() && bestFlow > 0; j++) {
                String s = vList.get(i);
                String t = vList.get(j);
                if (!graph.isNeighbor(s, t)) {
                    int flow = maxFlowVertexSplit(s, t);
                    if (flow < bestFlow) {
                        bestFlow = flow;
                        bestS = s;
                        bestT = t;
                    }
                }
            }
        }

        if (bestS == null) return Collections.emptySet();
        return extractVertexCut(bestS, bestT);
    }

    /**
     * Find a minimum edge cut set.
     */
    public Set<edge> minimumEdgeCut() {
        int n = graph.getVertexCount();
        if (n <= 1 || !isConnected()) return Collections.emptySet();

        List<String> vList = new ArrayList<>(graph.getVertices());
        String fixedS = vList.get(0);
        int bestFlow = graph.getEdgeCount();
        String bestT = null;

        for (int i = 1; i < vList.size() && bestFlow > 0; i++) {
            int flow = maxFlowEdge(fixedS, vList.get(i));
            if (flow < bestFlow) {
                bestFlow = flow;
                bestT = vList.get(i);
            }
        }

        if (bestT == null) return Collections.emptySet();
        return extractEdgeCut(fixedS, bestT);
    }

    /**
     * Test if graph is k-vertex-connected.
     */
    public boolean isKVertexConnected(int k) {
        if (k <= 0) return true;
        if (graph.getVertexCount() <= k) return false;
        return vertexConnectivity() >= k;
    }

    /**
     * Test if graph is k-edge-connected.
     */
    public boolean isKEdgeConnected(int k) {
        if (k <= 0) return true;
        return edgeConnectivity() >= k;
    }

    /**
     * Pairwise vertex and edge connectivity between s and t.
     * Includes vertex-disjoint and edge-disjoint paths (Menger's theorem).
     */
    public PairwiseResult pairwiseConnectivity(String s, String t) {
        if (s == null || t == null) throw new IllegalArgumentException("Vertices must not be null");
        if (!graph.containsVertex(s)) throw new IllegalArgumentException("Source vertex not in graph: " + s);
        if (!graph.containsVertex(t)) throw new IllegalArgumentException("Target vertex not in graph: " + t);
        if (s.equals(t)) throw new IllegalArgumentException("Source and target must differ");

        int vc = maxFlowVertexSplit(s, t);
        Set<String> vCut = extractVertexCut(s, t);
        int ec = maxFlowEdge(s, t);
        Set<edge> eCut = extractEdgeCut(s, t);
        List<List<String>> vPaths = findVertexDisjointPaths(s, t);
        List<List<String>> ePaths = findEdgeDisjointPaths(s, t);

        return new PairwiseResult(s, t, vc, ec, vCut, eCut, vPaths, ePaths);
    }

    /**
     * Enumerate all minimum-size vertex cuts (up to ALL_CUTS_LIMIT).
     */
    public List<Set<String>> allMinimumVertexCuts() {
        int kappa = vertexConnectivity();
        if (kappa == 0) return Collections.emptyList();

        List<String> vList = new ArrayList<>(graph.getVertices());
        List<Set<String>> allCuts = new ArrayList<>();
        Set<String> seen = new HashSet<>();

        for (int i = 0; i < vList.size() && allCuts.size() < ALL_CUTS_LIMIT; i++) {
            for (int j = i + 1; j < vList.size() && allCuts.size() < ALL_CUTS_LIMIT; j++) {
                String s = vList.get(i);
                String t = vList.get(j);
                if (!graph.isNeighbor(s, t)) {
                    int flow = maxFlowVertexSplit(s, t);
                    if (flow == kappa) {
                        Set<String> cut = extractVertexCut(s, t);
                        if (cut.size() == kappa) {
                            List<String> sorted = new ArrayList<>(cut);
                            Collections.sort(sorted);
                            String sig = sorted.toString();
                            if (seen.add(sig)) {
                                allCuts.add(cut);
                            }
                        }
                    }
                }
            }
        }
        return allCuts;
    }

    /**
     * Verify Whitney's inequality: κ(G) ≤ λ(G) ≤ δ(G).
     */
    public boolean verifyWhitney() {
        int kappa = vertexConnectivity();
        int lambda = edgeConnectivity();
        int delta = minDegree();
        return kappa <= lambda && lambda <= delta;
    }

    /**
     * Get vertex criticality — how much connectivity drops when each vertex is removed.
     */
    public Map<String, Integer> vertexCriticality() {
        int kappa = vertexConnectivity();
        Map<String, Integer> criticality = new LinkedHashMap<>();

        for (String v : graph.getVertices()) {
            Graph<String, edge> reduced = removeVertex(v);
            VertexConnectivityAnalyzer sub = new VertexConnectivityAnalyzer(reduced);
            int subKappa;
            if (reduced.getVertexCount() <= 1) {
                subKappa = 0;
            } else if (!sub.isConnected()) {
                subKappa = 0;
            } else {
                subKappa = sub.vertexConnectivity();
            }
            criticality.put(v, kappa - subKappa);
        }
        return criticality;
    }

    /**
     * Full connectivity analysis.
     */
    public ConnectivityResult analyze() {
        int kappa = vertexConnectivity();
        int lambda = edgeConnectivity();
        int delta = minDegree();
        Set<String> vCut = minimumVertexCut();
        Set<edge> eCut = minimumEdgeCut();
        boolean whitney = kappa <= lambda && lambda <= delta;

        return new ConnectivityResult(kappa, lambda, delta, vCut, eCut,
                whitney, graph.getVertexCount(), graph.getEdgeCount(), isConnected());
    }

    /**
     * Generate a text report.
     */
    public String generateReport() {
        StringBuilder sb = new StringBuilder();
        sb.append("=== Vertex Connectivity Analysis ===\n\n");

        int n = graph.getVertexCount();
        int m = graph.getEdgeCount();
        sb.append(String.format("Graph: %d vertices, %d edges\n", n, m));
        sb.append(String.format("Connected: %s\n\n", isConnected() ? "Yes" : "No"));

        if (!isConnected() || n <= 1) {
            sb.append("Graph is disconnected or trivial.\n");
            sb.append("Vertex connectivity κ = 0\n");
            sb.append("Edge connectivity λ = 0\n");
            return sb.toString();
        }

        int kappa = vertexConnectivity();
        int lambda = edgeConnectivity();
        int delta = minDegree();

        sb.append(String.format("Vertex connectivity κ(G) = %d\n", kappa));
        sb.append(String.format("Edge connectivity λ(G) = %d\n", lambda));
        sb.append(String.format("Minimum degree δ(G) = %d\n\n", delta));

        sb.append("Whitney's inequality: κ ≤ λ ≤ δ\n");
        sb.append(String.format("  %d ≤ %d ≤ %d  →  %s\n\n", kappa, lambda, delta,
                (kappa <= lambda && lambda <= delta) ? "✓ Holds" : "✗ Violated"));

        sb.append("Connectivity classification:\n");
        if (kappa == 0) {
            sb.append("  Not connected\n");
        } else if (kappa == 1) {
            sb.append("  1-connected (connected, has cut vertices)\n");
        } else if (kappa == 2) {
            sb.append("  2-connected (biconnected, no cut vertices)\n");
        } else if (kappa == 3) {
            sb.append("  3-connected (triconnected)\n");
        } else {
            sb.append(String.format("  %d-connected\n", kappa));
        }
        sb.append("\n");

        Set<String> vCut = minimumVertexCut();
        if (!vCut.isEmpty()) {
            sb.append("Minimum vertex cut:\n");
            sb.append(String.format("  {%s}\n", String.join(", ", vCut)));
            sb.append(String.format("  Size: %d\n\n", vCut.size()));
        }

        Set<edge> eCut = minimumEdgeCut();
        if (!eCut.isEmpty()) {
            sb.append("Minimum edge cut:\n");
            for (edge e : eCut) {
                sb.append(String.format("  %s\n", e));
            }
            sb.append(String.format("  Size: %d\n\n", eCut.size()));
        }

        sb.append("Vertex criticality (connectivity drop on removal):\n");
        Map<String, Integer> crit = vertexCriticality();
        List<Map.Entry<String, Integer>> sorted = new ArrayList<>(crit.entrySet());
        sorted.sort((a, b) -> b.getValue() - a.getValue());
        int shown = 0;
        for (Map.Entry<String, Integer> entry : sorted) {
            if (entry.getValue() > 0 || shown < 5) {
                sb.append(String.format("  %s: Δκ = %d\n", entry.getKey(), entry.getValue()));
                shown++;
            }
            if (shown >= 10) break;
        }

        return sb.toString();
    }

    // ---- Internal: max-flow with vertex splitting ----

    private int maxFlowVertexSplit(String s, String t) {
        Map<String, Map<String, Integer>> capacity = buildVertexSplitNetwork(s, t);
        String source = s + "_out";
        String sink = t + "_in";
        return edmondsKarp(capacity, source, sink);
    }

    private Map<String, Map<String, Integer>> buildVertexSplitNetwork(String s, String t) {
        Map<String, Map<String, Integer>> cap = new HashMap<>();
        int INF = graph.getVertexCount() + 1;

        for (String v : graph.getVertices()) {
            String vIn = v + "_in";
            String vOut = v + "_out";
            cap.putIfAbsent(vIn, new HashMap<>());
            cap.putIfAbsent(vOut, new HashMap<>());

            if (v.equals(s) || v.equals(t)) {
                setCapacity(cap, vIn, vOut, INF);
            } else {
                setCapacity(cap, vIn, vOut, 1);
            }
        }

        for (edge e : graph.getEdges()) {
            Collection<String> endpoints = graph.getEndpoints(e);
            Iterator<String> it = endpoints.iterator();
            String u = it.next();
            String v = it.next();
            setCapacity(cap, u + "_out", v + "_in", INF);
            setCapacity(cap, v + "_out", u + "_in", INF);
        }

        return cap;
    }

    private int maxFlowEdge(String s, String t) {
        Map<String, Map<String, Integer>> cap = new HashMap<>();
        for (String v : graph.getVertices()) {
            cap.putIfAbsent(v, new HashMap<>());
        }

        for (edge e : graph.getEdges()) {
            Collection<String> endpoints = graph.getEndpoints(e);
            Iterator<String> it = endpoints.iterator();
            String u = it.next();
            String v = it.next();
            addCapacity(cap, u, v, 1);
            addCapacity(cap, v, u, 1);
        }

        return edmondsKarp(cap, s, t);
    }

    private int edmondsKarp(Map<String, Map<String, Integer>> capacity, String source, String sink) {
        Map<String, Map<String, Integer>> residual = new HashMap<>();
        for (Map.Entry<String, Map<String, Integer>> entry : capacity.entrySet()) {
            residual.put(entry.getKey(), new HashMap<>(entry.getValue()));
        }
        for (String node : new ArrayList<>(residual.keySet())) {
            for (String neighbor : new ArrayList<>(residual.get(node).keySet())) {
                residual.putIfAbsent(neighbor, new HashMap<>());
            }
        }

        int totalFlow = 0;
        while (true) {
            Map<String, String> parent = new HashMap<>();
            Queue<String> queue = new LinkedList<>();
            queue.add(source);
            parent.put(source, null);

            while (!queue.isEmpty() && !parent.containsKey(sink)) {
                String u = queue.poll();
                Map<String, Integer> neighbors = residual.getOrDefault(u, Collections.emptyMap());
                for (Map.Entry<String, Integer> nb : neighbors.entrySet()) {
                    String v = nb.getKey();
                    if (nb.getValue() > 0 && !parent.containsKey(v)) {
                        parent.put(v, u);
                        queue.add(v);
                    }
                }
            }

            if (!parent.containsKey(sink)) break;

            int pathFlow = Integer.MAX_VALUE;
            String v = sink;
            while (!v.equals(source)) {
                String u = parent.get(v);
                pathFlow = Math.min(pathFlow, residual.get(u).getOrDefault(v, 0));
                v = u;
            }

            v = sink;
            while (!v.equals(source)) {
                String u = parent.get(v);
                residual.get(u).merge(v, -pathFlow, Integer::sum);
                residual.get(v).merge(u, pathFlow, Integer::sum);
                v = u;
            }

            totalFlow += pathFlow;
        }
        return totalFlow;
    }

    private Set<String> extractVertexCut(String s, String t) {
        Map<String, Map<String, Integer>> capacity = buildVertexSplitNetwork(s, t);
        String source = s + "_out";
        String sink = t + "_in";

        Map<String, Map<String, Integer>> residual = new HashMap<>();
        for (Map.Entry<String, Map<String, Integer>> entry : capacity.entrySet()) {
            residual.put(entry.getKey(), new HashMap<>(entry.getValue()));
        }
        for (String node : new ArrayList<>(residual.keySet())) {
            for (String neighbor : new ArrayList<>(residual.get(node).keySet())) {
                residual.putIfAbsent(neighbor, new HashMap<>());
            }
        }

        // Run max flow
        while (true) {
            Map<String, String> parent = new HashMap<>();
            Queue<String> queue = new LinkedList<>();
            queue.add(source);
            parent.put(source, null);
            while (!queue.isEmpty() && !parent.containsKey(sink)) {
                String u = queue.poll();
                for (Map.Entry<String, Integer> nb : residual.getOrDefault(u, Collections.emptyMap()).entrySet()) {
                    if (nb.getValue() > 0 && !parent.containsKey(nb.getKey())) {
                        parent.put(nb.getKey(), u);
                        queue.add(nb.getKey());
                    }
                }
            }
            if (!parent.containsKey(sink)) break;
            int pf = Integer.MAX_VALUE;
            String v = sink;
            while (!v.equals(source)) { String u = parent.get(v); pf = Math.min(pf, residual.get(u).getOrDefault(v, 0)); v = u; }
            v = sink;
            while (!v.equals(source)) { String u = parent.get(v); residual.get(u).merge(v, -pf, Integer::sum); residual.get(v).merge(u, pf, Integer::sum); v = u; }
        }

        // BFS from source in residual
        Set<String> reachable = new HashSet<>();
        Queue<String> queue = new LinkedList<>();
        queue.add(source);
        reachable.add(source);
        while (!queue.isEmpty()) {
            String u = queue.poll();
            for (Map.Entry<String, Integer> nb : residual.getOrDefault(u, Collections.emptyMap()).entrySet()) {
                if (nb.getValue() > 0 && reachable.add(nb.getKey())) {
                    queue.add(nb.getKey());
                }
            }
        }

        // Cut vertices: v_in reachable but v_out not
        Set<String> cut = new LinkedHashSet<>();
        for (String v : graph.getVertices()) {
            if (v.equals(s) || v.equals(t)) continue;
            if (reachable.contains(v + "_in") && !reachable.contains(v + "_out")) {
                cut.add(v);
            }
        }
        return cut;
    }

    private Set<edge> extractEdgeCut(String s, String t) {
        Map<String, Map<String, Integer>> cap = new HashMap<>();
        for (String v : graph.getVertices()) {
            cap.putIfAbsent(v, new HashMap<>());
        }
        for (edge e : graph.getEdges()) {
            Collection<String> endpoints = graph.getEndpoints(e);
            Iterator<String> it = endpoints.iterator();
            String u = it.next();
            String v = it.next();
            addCapacity(cap, u, v, 1);
            addCapacity(cap, v, u, 1);
        }

        Map<String, Map<String, Integer>> residual = new HashMap<>();
        for (Map.Entry<String, Map<String, Integer>> entry : cap.entrySet()) {
            residual.put(entry.getKey(), new HashMap<>(entry.getValue()));
        }
        for (String node : new ArrayList<>(residual.keySet())) {
            for (String neighbor : new ArrayList<>(residual.get(node).keySet())) {
                residual.putIfAbsent(neighbor, new HashMap<>());
            }
        }

        while (true) {
            Map<String, String> parent = new HashMap<>();
            Queue<String> queue = new LinkedList<>();
            queue.add(s);
            parent.put(s, null);
            while (!queue.isEmpty() && !parent.containsKey(t)) {
                String u = queue.poll();
                for (Map.Entry<String, Integer> nb : residual.getOrDefault(u, Collections.emptyMap()).entrySet()) {
                    if (nb.getValue() > 0 && !parent.containsKey(nb.getKey())) {
                        parent.put(nb.getKey(), u);
                        queue.add(nb.getKey());
                    }
                }
            }
            if (!parent.containsKey(t)) break;
            int pf = Integer.MAX_VALUE;
            String v = t;
            while (!v.equals(s)) { String u = parent.get(v); pf = Math.min(pf, residual.get(u).getOrDefault(v, 0)); v = u; }
            v = t;
            while (!v.equals(s)) { String u = parent.get(v); residual.get(u).merge(v, -pf, Integer::sum); residual.get(v).merge(u, pf, Integer::sum); v = u; }
        }

        Set<String> reachable = new HashSet<>();
        Queue<String> queue = new LinkedList<>();
        queue.add(s);
        reachable.add(s);
        while (!queue.isEmpty()) {
            String u = queue.poll();
            for (Map.Entry<String, Integer> nb : residual.getOrDefault(u, Collections.emptyMap()).entrySet()) {
                if (nb.getValue() > 0 && reachable.add(nb.getKey())) {
                    queue.add(nb.getKey());
                }
            }
        }

        Set<edge> cutEdges = new LinkedHashSet<>();
        for (edge e : graph.getEdges()) {
            Collection<String> endpoints = graph.getEndpoints(e);
            Iterator<String> it = endpoints.iterator();
            String u = it.next();
            String v = it.next();
            if ((reachable.contains(u) && !reachable.contains(v)) ||
                (reachable.contains(v) && !reachable.contains(u))) {
                cutEdges.add(e);
            }
        }
        return cutEdges;
    }

    /**
     * Find vertex-disjoint paths between s and t (Menger's theorem).
     */
    public List<List<String>> findVertexDisjointPaths(String s, String t) {
        Map<String, Map<String, Integer>> capacity = buildVertexSplitNetwork(s, t);
        String source = s + "_out";
        String sink = t + "_in";

        Map<String, Map<String, Integer>> residual = new HashMap<>();
        for (Map.Entry<String, Map<String, Integer>> entry : capacity.entrySet()) {
            residual.put(entry.getKey(), new HashMap<>(entry.getValue()));
        }
        for (String node : new ArrayList<>(residual.keySet())) {
            for (String neighbor : new ArrayList<>(residual.get(node).keySet())) {
                residual.putIfAbsent(neighbor, new HashMap<>());
            }
        }

        List<List<String>> paths = new ArrayList<>();

        while (true) {
            Map<String, String> parent = new HashMap<>();
            Queue<String> queue = new LinkedList<>();
            queue.add(source);
            parent.put(source, null);
            while (!queue.isEmpty() && !parent.containsKey(sink)) {
                String u = queue.poll();
                for (Map.Entry<String, Integer> nb : residual.getOrDefault(u, Collections.emptyMap()).entrySet()) {
                    if (nb.getValue() > 0 && !parent.containsKey(nb.getKey())) {
                        parent.put(nb.getKey(), u);
                        queue.add(nb.getKey());
                    }
                }
            }
            if (!parent.containsKey(sink)) break;

            List<String> rawPath = new ArrayList<>();
            String v = sink;
            while (v != null) { rawPath.add(v); v = parent.get(v); }
            Collections.reverse(rawPath);

            int pf = Integer.MAX_VALUE;
            for (int i = 0; i < rawPath.size() - 1; i++) {
                pf = Math.min(pf, residual.get(rawPath.get(i)).getOrDefault(rawPath.get(i + 1), 0));
            }
            for (int i = 0; i < rawPath.size() - 1; i++) {
                residual.get(rawPath.get(i)).merge(rawPath.get(i + 1), -pf, Integer::sum);
                residual.get(rawPath.get(i + 1)).merge(rawPath.get(i), pf, Integer::sum);
            }

            List<String> path = new ArrayList<>();
            for (String node : rawPath) {
                String orig = node.endsWith("_in") ? node.substring(0, node.length() - 3) :
                              node.endsWith("_out") ? node.substring(0, node.length() - 4) : node;
                if (path.isEmpty() || !path.get(path.size() - 1).equals(orig)) {
                    path.add(orig);
                }
            }
            paths.add(path);
        }
        return paths;
    }

    /**
     * Find edge-disjoint paths between s and t.
     */
    public List<List<String>> findEdgeDisjointPaths(String s, String t) {
        Map<String, Map<String, Integer>> cap = new HashMap<>();
        for (String v : graph.getVertices()) {
            cap.putIfAbsent(v, new HashMap<>());
        }
        for (edge e : graph.getEdges()) {
            Collection<String> endpoints = graph.getEndpoints(e);
            Iterator<String> it = endpoints.iterator();
            String u = it.next();
            String v = it.next();
            addCapacity(cap, u, v, 1);
            addCapacity(cap, v, u, 1);
        }

        Map<String, Map<String, Integer>> residual = new HashMap<>();
        for (Map.Entry<String, Map<String, Integer>> entry : cap.entrySet()) {
            residual.put(entry.getKey(), new HashMap<>(entry.getValue()));
        }
        for (String node : new ArrayList<>(residual.keySet())) {
            for (String neighbor : new ArrayList<>(residual.get(node).keySet())) {
                residual.putIfAbsent(neighbor, new HashMap<>());
            }
        }

        List<List<String>> paths = new ArrayList<>();

        while (true) {
            Map<String, String> parent = new HashMap<>();
            Queue<String> queue = new LinkedList<>();
            queue.add(s);
            parent.put(s, null);
            while (!queue.isEmpty() && !parent.containsKey(t)) {
                String u = queue.poll();
                for (Map.Entry<String, Integer> nb : residual.getOrDefault(u, Collections.emptyMap()).entrySet()) {
                    if (nb.getValue() > 0 && !parent.containsKey(nb.getKey())) {
                        parent.put(nb.getKey(), u);
                        queue.add(nb.getKey());
                    }
                }
            }
            if (!parent.containsKey(t)) break;

            List<String> path = new ArrayList<>();
            String v = t;
            while (v != null) { path.add(v); v = parent.get(v); }
            Collections.reverse(path);

            for (int i = 0; i < path.size() - 1; i++) {
                String u = path.get(i);
                String w = path.get(i + 1);
                residual.get(u).merge(w, -1, Integer::sum);
                residual.get(w).merge(u, 1, Integer::sum);
            }
            paths.add(path);
        }
        return paths;
    }

    // ---- Helpers ----

    private void setCapacity(Map<String, Map<String, Integer>> cap, String from, String to, int c) {
        cap.putIfAbsent(from, new HashMap<>());
        cap.get(from).put(to, c);
    }

    private void addCapacity(Map<String, Map<String, Integer>> cap, String from, String to, int c) {
        cap.putIfAbsent(from, new HashMap<>());
        cap.get(from).merge(to, c, Integer::sum);
    }

    private Graph<String, edge> removeVertex(String toRemove) {
        Graph<String, edge> g = new UndirectedSparseGraph<>();
        for (String v : graph.getVertices()) {
            if (!v.equals(toRemove)) g.addVertex(v);
        }
        int edgeId = 0;
        for (edge e : graph.getEdges()) {
            Collection<String> endpoints = graph.getEndpoints(e);
            Iterator<String> it = endpoints.iterator();
            String u = it.next();
            String v = it.next();
            if (!u.equals(toRemove) && !v.equals(toRemove)) {
                g.addEdge(new edge(edgeId++), u, v);
            }
        }
        return g;
    }
}
