"""Embedded knowledge graph with adjacency indexes and JSON persistence.

This is the query engine used by impact analysis and the chat agent. When Neo4j is configured the same graph is
mirrored there (see neo4j_store.py) for ad-hoc Cypher exploration and cross-repository reasoning.
"""
from __future__ import annotations

import json
from collections import deque
from collections.abc import Iterable
from pathlib import Path


class KnowledgeGraph:
    def __init__(self) -> None:
        self.nodes: dict[str, dict] = {}
        self.out: dict[str, dict[str, set[str]]] = {}
        self.inc: dict[str, dict[str, set[str]]] = {}
        self.edge_props: dict[tuple[str, str, str], dict] = {}

    # ------------------------------------------------------------ mutation
    def add_node(self, node_id: str, label: str, name: str, **props) -> str:
        node = self.nodes.get(node_id)
        if node is None:
            self.nodes[node_id] = {"id": node_id, "label": label, "name": name, **props}
        else:
            node.update({k: v for k, v in props.items() if v is not None})
        return node_id

    def add_edge(self, src: str, dst: str, rel: str, **props) -> None:
        if src == dst or src not in self.nodes or dst not in self.nodes:
            return
        self.out.setdefault(src, {}).setdefault(rel, set()).add(dst)
        self.inc.setdefault(dst, {}).setdefault(rel, set()).add(src)
        key = (src, rel, dst)
        if props:
            existing = self.edge_props.setdefault(key, {})
            if "weight" in props:
                existing["weight"] = existing.get("weight", 0) + props["weight"]
            if "via" in props and existing.get("via"):
                props = {**props, "via": ",".join(sorted(set(existing["via"].split(",")) | {props["via"]}))}
            existing.update({k: v for k, v in props.items() if k != "weight"})

    # ------------------------------------------------------------ queries
    def edges(self) -> Iterable[tuple[str, str, str]]:
        for src, rels in self.out.items():
            for rel, dsts in rels.items():
                for dst in dsts:
                    yield src, rel, dst

    def edge_count(self) -> int:
        return sum(len(d) for rels in self.out.values() for d in rels.values())

    def successors(self, node_id: str, rels: Iterable[str] | None = None) -> set[str]:
        return self._adj(self.out, node_id, rels)

    def predecessors(self, node_id: str, rels: Iterable[str] | None = None) -> set[str]:
        return self._adj(self.inc, node_id, rels)

    @staticmethod
    def _adj(index: dict, node_id: str, rels: Iterable[str] | None) -> set[str]:
        by_rel = index.get(node_id, {})
        if rels is None:
            return set().union(*by_rel.values()) if by_rel else set()
        return set().union(*(by_rel.get(r, set()) for r in rels))

    def by_label(self, label: str) -> list[dict]:
        return [n for n in self.nodes.values() if n["label"] == label]

    def traverse(
        self, starts: Iterable[str], direction: str, rels: Iterable[str], max_depth: int = 4, limit: int = 5000
    ) -> dict[str, tuple[int, str | None]]:
        """BFS returning node -> (depth, predecessor-on-path)."""
        rels = tuple(rels)
        seen: dict[str, tuple[int, str | None]] = {s: (0, None) for s in starts if s in self.nodes}
        queue = deque(seen)
        while queue and len(seen) < limit:
            cur = queue.popleft()
            depth = seen[cur][0]
            if depth >= max_depth:
                continue
            nxt = self.predecessors(cur, rels) if direction == "in" else self.successors(cur, rels)
            for n in nxt:
                if n not in seen:
                    seen[n] = (depth + 1, cur)
                    queue.append(n)
        return seen

    @staticmethod
    def path_to(seen: dict[str, tuple[int, str | None]], node_id: str) -> list[str]:
        path = [node_id]
        while (prev := seen.get(path[-1], (0, None))[1]) is not None:
            path.append(prev)
        return path

    def search(self, text: str, labels: Iterable[str] | None = None, limit: int = 20) -> list[dict]:
        needle = text.lower()
        labels = set(labels) if labels else None
        exact, partial = [], []
        for n in self.nodes.values():
            if labels and n["label"] not in labels:
                continue
            name = n["name"].lower()
            if name == needle:
                exact.append(n)
            elif needle in name or needle in n["id"].lower():
                partial.append(n)
        partial.sort(key=lambda n: len(n["name"]))
        return (exact + partial)[:limit]

    def file_of(self, node_id: str) -> str | None:
        node = self.nodes.get(node_id)
        if not node:
            return None
        if node["label"] == "File":
            return node["path"]
        return node.get("file")

    def subgraph(self, node_ids: Iterable[str], rels: Iterable[str] | None = None) -> dict:
        ids = {i for i in node_ids if i in self.nodes}
        rels = set(rels) if rels else None
        edges = [
            {"source": s, "target": d, "type": r, **self.edge_props.get((s, r, d), {})}
            for s, r, d in self.edges()
            if s in ids and d in ids and (rels is None or r in rels)
        ]
        return {"nodes": [self.nodes[i] for i in ids], "edges": edges}

    def stats(self) -> dict:
        labels: dict[str, int] = {}
        for n in self.nodes.values():
            labels[n["label"]] = labels.get(n["label"], 0) + 1
        rels: dict[str, int] = {}
        for _, r, _ in self.edges():
            rels[r] = rels.get(r, 0) + 1
        return {"nodes": len(self.nodes), "edges": self.edge_count(), "labels": labels, "relationships": rels}

    # ------------------------------------------------------------ persistence
    def to_dict(self) -> dict:
        return {
            "nodes": list(self.nodes.values()),
            "edges": [[s, r, d, self.edge_props.get((s, r, d))] for s, r, d in self.edges()],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "KnowledgeGraph":
        g = cls()
        for n in data.get("nodes", []):
            g.nodes[n["id"]] = n
        for s, r, d, props in data.get("edges", []):
            g.out.setdefault(s, {}).setdefault(r, set()).add(d)
            g.inc.setdefault(d, {}).setdefault(r, set()).add(s)
            if props:
                g.edge_props[(s, r, d)] = props
        return g

    def save(self, path: Path) -> None:
        path.write_text(json.dumps(self.to_dict()), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "KnowledgeGraph":
        return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))
