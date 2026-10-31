# Graphs and trees in plain Python

Most graph and tree work in applications (dependency ordering, shortest routes, folder hierarchies, permission inheritance, reachability) needs no library: dicts, sets, `deque` and `heapq` are enough and fast. This reference gives tested implementations and the representation choices behind them. All traversals are iterative, because recursion in Python stops at about 1000 levels by default.

## Representing a graph

An adjacency mapping, a dict from each node to its neighbours, is the default representation. Use a set of neighbours for unweighted graphs and a dict of neighbour to weight for weighted ones.

| Representation | Shape | Good for |
|---|---|---|
| adjacency sets | `{"a": {"b", "c"}, ...}` | unweighted graphs, fast "is there an edge" |
| adjacency dict of weights | `{"a": {"b": 4.0}, ...}` | shortest paths |
| edge list | `[("a", "b"), ...]` | input and output, sorting edges (Kruskal) |
| adjacency matrix (NumPy) | `n × n` array | dense graphs, linear algebra |

Building adjacency sets from an edge list with `defaultdict(set)`:

```python
from collections import defaultdict

edges = [("a", "b"), ("a", "c"), ("b", "d"), ("c", "d"), ("d", "e")]
graph: defaultdict[str, set[str]] = defaultdict(set)
for u, v in edges:
    graph[u].add(v)
    graph[v].add(u)            # leave out for a directed graph
```

Nodes can be any hashable value: strings, ints, tuples (grid coordinates), frozen dataclasses. Nodes that only appear as targets are not keys of a directed graph; read neighbours with `graph.get(node, ())` so that lookup never inserts.

## Breadth-first search and shortest paths by hops

Breadth-first search (BFS) visits nodes in order of distance from the start, so the first time it reaches a node it has found a shortest path in number of edges. It uses a `deque` as a FIFO queue and a dict of parents to rebuild the path.

```python
from collections import deque
from collections.abc import Iterable, Mapping


def bfs_path(graph: Mapping[str, Iterable[str]], start: str, goal: str) -> list[str] | None:
    parent: dict[str, str | None] = {start: None}
    queue = deque([start])
    while queue:
        node = queue.popleft()
        if node == goal:
            path: list[str] = []
            while node is not None:
                path.append(node)
                node = parent[node]
            return path[::-1]
        for nxt in graph.get(node, ()):
            if nxt not in parent:          # parent doubles as the "seen" set
                parent[nxt] = node
                queue.append(nxt)
    return None
```

Mark nodes as seen when they are queued, not when they are dequeued; otherwise a node can be queued many times and the search slows down sharply on dense graphs.

## Depth-first search without recursion

Depth-first search (DFS) with an explicit stack visits the same nodes as the recursive version without hitting the recursion limit. Push neighbours in reverse order if the visiting order must match the recursive one.

```python
from collections.abc import Iterable, Iterator, Mapping


def dfs(graph: Mapping[str, Iterable[str]], start: str) -> Iterator[str]:
    seen = {start}
    stack = [start]
    while stack:
        node = stack.pop()
        yield node
        for nxt in sorted(graph.get(node, ()), reverse=True):    # sorted for a stable order
            if nxt not in seen:
                seen.add(nxt)
                stack.append(nxt)
```

Connected components come from running the search from each unseen node; reachability ("can a reach e?") is one search.

## Dijkstra's shortest paths with heapq

Dijkstra's algorithm finds the cheapest path from one source when all edge weights are non-negative. A heap holds `(distance, node)` pairs; stale entries are skipped instead of updated, because a heap has no fast decrease-key.

```python
import heapq
from collections.abc import Mapping


def dijkstra(graph: Mapping[str, Mapping[str, float]], source: str) -> dict[str, float]:
    dist = {source: 0.0}
    heap = [(0.0, source)]
    while heap:
        d, node = heapq.heappop(heap)
        if d > dist.get(node, float("inf")):
            continue                                   # a shorter path was already found
        for nxt, weight in graph.get(node, {}).items():
            nd = d + weight
            if nd < dist.get(nxt, float("inf")):
                dist[nxt] = nd
                heapq.heappush(heap, (nd, nxt))
    return dist


roads = {"a": {"b": 4, "c": 1}, "c": {"b": 1, "d": 5}, "b": {"d": 1}}
dijkstra(roads, "a")      # {'a': 0.0, 'c': 1.0, 'b': 2.0, 'd': 3.0}
```

Keep a `parent` dict, as in BFS, to rebuild the route. Nodes must be comparable for ties in the heap (strings and ints are); for other node types push `(distance, counter, node)`. Negative weights need Bellman-Ford instead.

## Topological order with graphlib

`graphlib.TopologicalSorter` (3.9) orders nodes so that every node comes after its dependencies: build steps, migrations, plugin start-up. The input maps each node to the set of nodes it depends on.

```python
from graphlib import CycleError, TopologicalSorter

deps = {"app": {"db", "cache"}, "db": {"config"}, "cache": {"config"}, "config": set()}
list(TopologicalSorter(deps).static_order())     # ['config', 'db', 'cache', 'app']

try:
    list(TopologicalSorter({"a": {"b"}, "b": {"a"}}).static_order())
except CycleError as e:
    cycle = e.args[1]                            # ['a', 'b', 'a']: report it to the person
```

The incremental API gives batches of nodes whose dependencies are all done, which is how to run independent steps in parallel.

```python
from graphlib import TopologicalSorter

ts = TopologicalSorter(deps)
ts.prepare()                                     # raises CycleError here if there is a cycle
while ts.is_active():
    ready = ts.get_ready()                       # ('config',), then ('db', 'cache'), then ('app',)
    run_in_parallel(ready)                       # e.g. a ThreadPoolExecutor; wait for the batch
    ts.done(*ready)                              # or call ts.done(node) as each one finishes
```

## Union-find for grouping connected items

Union-find (disjoint set) answers "are these two in the same group?" as groups merge: duplicate detection across matching keys, connected components from a stream of edges, Kruskal's minimum spanning tree. With path compression, each operation is close to constant time.

```python
from collections.abc import Hashable


class UnionFind:
    def __init__(self) -> None:
        self.parent: dict[Hashable, Hashable] = {}

    def find(self, x: Hashable) -> Hashable:
        self.parent.setdefault(x, x)
        root = x
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[x] != root:                 # path compression
            self.parent[x], x = root, self.parent[x]
        return root

    def union(self, a: Hashable, b: Hashable) -> None:
        self.parent[self.find(a)] = self.find(b)


uf = UnionFind()
uf.union("alice@example.com", "acct-1")
uf.union("acct-1", "alice@example.org")
uf.find("alice@example.com") == uf.find("alice@example.org")      # True: same person
```

## Trees as node objects

A tree node is a small class with a list of children; a dataclass keeps it short. Walk it with an explicit stack to handle any depth.

```python
from collections.abc import Iterator
from dataclasses import dataclass, field


@dataclass
class Node:
    name: str
    children: list["Node"] = field(default_factory=list)


def walk(root: Node) -> Iterator[tuple[int, Node]]:
    """Pre-order traversal with depth, without recursion."""
    stack = [(0, root)]
    while stack:
        depth, node = stack.pop()
        yield depth, node
        stack.extend((depth + 1, child) for child in reversed(node.children))


tree = Node("root", [Node("a", [Node("a1")]), Node("b")])
[n.name for _, n in walk(tree)]       # ['root', 'a', 'a1', 'b']
```

A recursive generator (`yield from walk(child)`) reads more naturally but fails with `RecursionError: maximum recursion depth exceeded` on a chain 5000 nodes deep. Raising the limit with `sys.setrecursionlimit` risks crashing the interpreter on deep C stacks; the iterative form is the fix.

- Keep a `parent` reference only if you need upward navigation; it creates reference cycles, which the garbage collector handles but which delay freeing.
- For trees loaded from JSON, nested dicts and lists are often enough; convert to node objects when methods and types help.
- A flat table of `(id, parent_id)` rows becomes a tree with one dict from id to node and one pass that appends each node to its parent's children.

## Tries for prefix search

A trie stores strings by characters along a path of nested dicts; all words with a given prefix sit under one node. It suits autocomplete and prefix matching over a fixed vocabulary.

```python
from collections.abc import Iterator

END = object()                                   # marks the end of a word; never a character


def trie_insert(trie: dict, word: str) -> None:
    node = trie
    for ch in word:
        node = node.setdefault(ch, {})
    node[END] = True


def trie_prefix(trie: dict, prefix: str) -> Iterator[str]:
    node = trie
    for ch in prefix:
        if ch not in node:
            return
        node = node[ch]
    stack = [(node, prefix)]
    while stack:
        node, word = stack.pop()
        for key, child in node.items():
            if key is END:
                yield word
            else:
                stack.append((child, word + key))
```

For a few thousand words, `[w for w in words if w.startswith(prefix)]` or `bisect` on a sorted list is simpler and fast enough; a trie pays off for large vocabularies and many lookups.

## When to use a graph library

Plain Python fits graphs up to a few hundred thousand edges and the algorithms above. Reach for a library when you need many algorithms or much larger graphs.

| Library | Pick when |
|---|---|
| NetworkX | many algorithms (centrality, flows, isomorphism), readable code, moderate size |
| rustworkx | NetworkX-like API with a Rust core, for large graphs |
| SciPy `scipy.sparse.csgraph` | shortest paths and components on sparse matrices |
| a graph database | the graph is the application's main data and must persist |
