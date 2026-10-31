"""Infrastructure discovery: docker-compose services, Kubernetes workloads, Dockerfiles."""
from __future__ import annotations

import logging
from pathlib import Path, PurePosixPath

import yaml

from app.ingestion.scanner import SourceFile
from app.parsing.extractors import DATABASE_TECHS, normalize_tech

log = logging.getLogger(__name__)

_INFRA_IMAGES = {
    "kafka": "kafka", "zookeeper": "zookeeper", "rabbitmq": "rabbitmq", "nats": "nats", "activemq": "activemq",
    "nginx": "nginx", "traefik": "traefik", "keycloak": "keycloak", "consul": "consul", "vault": "vault",
    "prometheus": "prometheus", "grafana": "grafana", "jaeger": "jaeger", "zipkin": "zipkin", "minio": "minio",
    "localstack": "localstack", "eureka": "eureka",
}
_DB_IMAGES = {"postgres": "postgresql", "mysql": "mysql", "mariadb": "mariadb", "mongo": "mongodb", "redis": "redis",
              "mssql": "sqlserver", "sqlserver": "sqlserver", "oracle": "oracle", "cassandra": "cassandra",
              "elasticsearch": "elasticsearch", "neo4j": "neo4j", "dynamodb": "dynamodb", "couchbase": "couchbase"}


def classify_image(image: str) -> tuple[str, str] | None:
    base = image.split("/")[-1].split(":")[0].lower()
    for key, tech in _DB_IMAGES.items():
        if key in base:
            return "database", normalize_tech(tech) if tech in DATABASE_TECHS else tech
    for key, name in _INFRA_IMAGES.items():
        if key in base:
            return "infrastructure", name
    return None


def discover_infrastructure(root: Path, files: list[SourceFile]) -> dict:
    compose_services: list[dict] = []
    k8s_workloads: list[dict] = []
    dockerfiles: list[str] = []
    for f in files:
        name = PurePosixPath(f.path).name.lower()
        if f.language == "Dockerfile":
            dockerfiles.append(f.path)
            continue
        if f.language != "YAML":
            continue
        try:
            text = (root / f.path).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if name.startswith("docker-compose") or name.startswith("compose."):
            try:
                doc = yaml.safe_load(text) or {}
            except yaml.YAMLError:
                continue
            base_dir = PurePosixPath(f.path).parent
            for svc_name, svc in (doc.get("services") or {}).items():
                if not isinstance(svc, dict):
                    continue
                build = svc.get("build")
                context = build.get("context") if isinstance(build, dict) else build
                depends = svc.get("depends_on") or []
                if isinstance(depends, dict):
                    depends = list(depends)
                compose_services.append({
                    "name": svc_name,
                    "image": svc.get("image"),
                    "build_context": str(PurePosixPath(base_dir, context)).replace("\\", "/").lstrip("./") if context else None,
                    "depends_on": [str(d) for d in depends],
                    "ports": [str(p) for p in svc.get("ports") or []],
                    "file": f.path,
                })
        elif f.category == "infra":
            try:
                for doc in yaml.safe_load_all(text):
                    if not isinstance(doc, dict) or "kind" not in doc:
                        continue
                    kind = doc.get("kind")
                    if kind in ("Deployment", "StatefulSet", "DaemonSet", "CronJob", "Job", "Service", "Ingress"):
                        meta = doc.get("metadata") or {}
                        images = []
                        spec = (doc.get("spec") or {}).get("template", {}).get("spec", {}) if kind != "CronJob" else {}
                        for c in (spec.get("containers") or []):
                            if isinstance(c, dict) and c.get("image"):
                                images.append(c["image"])
                        k8s_workloads.append({"kind": kind, "name": meta.get("name"), "images": images, "file": f.path})
            except yaml.YAMLError:
                continue
            except AttributeError:
                continue
    kinds = set()
    if k8s_workloads:
        kinds.add("kubernetes")
    if compose_services:
        kinds.add("compose")
    return {"compose_services": compose_services, "k8s_workloads": k8s_workloads, "dockerfiles": dockerfiles, "kinds": sorted(kinds)}
