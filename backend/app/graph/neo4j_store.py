"""Mirrors a repository knowledge graph into Neo4j for advanced Cypher reasoning."""
from __future__ import annotations

import logging
import re
from collections import defaultdict

from app.config import get_settings
from app.graph.store import KnowledgeGraph

log = logging.getLogger(__name__)
_SAFE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class Neo4jMirror:
    def __init__(self) -> None:
        settings = get_settings()
        self.enabled = bool(settings.neo4j_uri)
        self._driver = None
        if self.enabled:
            try:
                from neo4j import GraphDatabase

                self._driver = GraphDatabase.driver(settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password or ""))
            except Exception as exc:  # noqa: BLE001
                log.warning("Neo4j unavailable, using embedded graph only: %s", exc)
                self.enabled = False

    def ensure_schema(self) -> None:
        with self._driver.session() as s:
            s.run("CREATE CONSTRAINT node_uid IF NOT EXISTS FOR (n:Node) REQUIRE n.uid IS UNIQUE")
            s.run("CREATE INDEX node_repo IF NOT EXISTS FOR (n:Node) ON (n.repo_id)")

    def sync(self, repo_id: str, graph: KnowledgeGraph, batch: int = 500) -> bool:
        if not self.enabled or self._driver is None:
            return False
        try:
            self.ensure_schema()
            with self._driver.session() as s:
                s.run("MATCH (n:Node {repo_id: $rid}) DETACH DELETE n", rid=repo_id)
                by_label: dict[str, list[dict]] = defaultdict(list)
                for n in graph.nodes.values():
                    props = {k: v for k, v in n.items() if isinstance(v, (str, int, float, bool)) and k != "label"}
                    by_label[n["label"]].append({"uid": f"{repo_id}:{n['id']}", "props": {**props, "repo_id": repo_id}})
                for label, rows in by_label.items():
                    if not _SAFE.match(label):
                        continue
                    for i in range(0, len(rows), batch):
                        s.run(f"UNWIND $rows AS r MERGE (n:Node {{uid: r.uid}}) SET n += r.props, n:{label}", rows=rows[i:i + batch])
                by_rel: dict[str, list[dict]] = defaultdict(list)
                for src, rel, dst in graph.edges():
                    by_rel[rel].append({"s": f"{repo_id}:{src}", "d": f"{repo_id}:{dst}"})
                for rel, rows in by_rel.items():
                    if not _SAFE.match(rel):
                        continue
                    for i in range(0, len(rows), batch):
                        s.run(
                            f"UNWIND $rows AS r MATCH (a:Node {{uid: r.s}}) MATCH (b:Node {{uid: r.d}}) MERGE (a)-[:{rel}]->(b)",
                            rows=rows[i:i + batch],
                        )
            return True
        except Exception as exc:  # noqa: BLE001
            log.warning("Neo4j sync failed for %s: %s", repo_id, exc)
            return False

    def delete(self, repo_id: str) -> None:
        if self.enabled and self._driver is not None:
            try:
                with self._driver.session() as s:
                    s.run("MATCH (n:Node {repo_id: $rid}) DETACH DELETE n", rid=repo_id)
            except Exception as exc:  # noqa: BLE001
                log.warning("Neo4j delete failed: %s", exc)


_mirror: Neo4jMirror | None = None


def get_mirror() -> Neo4jMirror:
    global _mirror
    if _mirror is None:
        _mirror = Neo4jMirror()
    return _mirror
