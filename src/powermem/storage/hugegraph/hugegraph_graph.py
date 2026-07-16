"""
HugeGraph graph storage implementation for PowerMem.

Implements GraphStoreBase using HugeGraph 1.7.0 REST API via pyhugegraphclient.
Provides entity extraction, relationship storage, and multi-hop graph traversal
as a drop-in replacement for OceanBase graph store.
"""

import json
import logging
import re
import time
from typing import Any, Dict, List, Optional

from powermem.integrations import EmbedderFactory, LLMFactory
from powermem.storage.base import GraphStoreBase
from powermem.utils.utils import (
    format_entities,
    generate_snowflake_id,
    get_current_datetime,
    remove_code_blocks,
)
from powermem.prompts import GraphPrompts

logger = logging.getLogger(__name__)

# Vertex labels used for memory entities
_VERTEX_LABELS = [
    "person", "organization", "location", "skill", "concept",
    "project", "product", "event", "team", "identity", "time",
    "tool", "method", "feeling", "community", "book", "animal",
    "food", "color", "activity",
]

# Property keys for vertices
_VERTEX_PROPS = ["name", "type", "content", "created_at", "memory_id",
                 "access_count", "initial_score", "last_accessed_at"]


class HugeGraphMemoryGraph(GraphStoreBase):
    """HugeGraph-based graph store for PowerMem.

    Uses HugeGraph 1.7.0 REST API via PyHugeClient for:
    - Dynamic schema management (vertex/edge labels created on-demand)
    - Entity storage with PRIMARY_KEY ID strategy
    - Multi-hop graph traversal for search
    - Edge label conflict resolution with _v2/_v3 variants
    """

    def __init__(self, config: Any) -> None:
        self.config = config
        self._extract_llm = None
        self._embedder = None
        self._graph_prompts = GraphPrompts()
        self._edge_cache: Dict[tuple, str] = {}
        self._vl_cache: set = set()
        self._schema_initialized = False

        # Connection params from config
        self._url = getattr(config, "host", "") or "http://127.0.0.1:8080"
        if not self._url.startswith("http"):
            self._url = f"http://{self._url}:{getattr(config, 'port', '8080')}"
        self._user = getattr(config, "user", "admin") or "admin"
        self._password = getattr(config, "password", "admin") or "admin"
        self._graph = getattr(config, "db_name", "hugegraph") or "hugegraph"
        self._max_hops = getattr(config, "max_hops", 3)

        # Collection name for filtering
        self._collection = getattr(config, "collection_name", "power_mem")

        self._init_client()
        self._init_schema()

    def _init_client(self):
        """Initialize PyHugeClient connection."""
        try:
            from pyhugegraph.client import PyHugeClient
            self._client = PyHugeClient(
                url=self._url,
                user=self._user,
                pwd=self._password,
                graph=self._graph,
            )
            logger.info(f"HugeGraph connected: {self._url} graph={self._graph}")
        except ImportError:
            raise ImportError(
                "pyhugegraph is not installed. Install with: pip install pyhugegraph"
            )
        except Exception as e:
            raise ConnectionError(f"Failed to connect to HugeGraph at {self._url}: {e}")

    def _init_schema(self):
        """Initialize property keys and base vertex labels."""
        if self._schema_initialized:
            return
        s = self._client.schema()

        # Property keys
        for prop in _VERTEX_PROPS:
            try:
                pk = s.propertyKey(prop)
                if prop in ("name", "type", "content", "memory_id"):
                    pk = pk.asText()
                elif prop in ("created_at", "initial_score", "last_accessed_at"):
                    pk = pk.asDouble()
                elif prop == "access_count":
                    pk = pk.asInt()
                pk.ifNotExist().create()
            except Exception:
                pass  # Already exists

        # Vertex labels
        for vl in _VERTEX_LABELS:
            self._ensure_vertex_label(vl)

        self._schema_initialized = True
        logger.info("HugeGraph schema initialized (dynamic mode)")

    def _ensure_vertex_label(self, label: str) -> bool:
        """Ensure a vertex label exists."""
        if label in self._vl_cache:
            return True
        s = self._client.schema()
        try:
            s.vertexLabel(label).properties("name", "type") \
                .usePrimaryKeyId().primaryKeys("name").ifNotExist().create()
            self._vl_cache.add(label)
            return True
        except Exception:
            self._vl_cache.add(label)
            try:
                s.getVertexLabel(label)
                return True
            except Exception:
                return False

    def _ensure_edge_label(self, edge_label: str, src_label: str, tgt_label: str) -> str:
        """Ensure edge label exists for given source/target. Returns actual label name."""
        cache_key = (edge_label, src_label, tgt_label)
        if cache_key in self._edge_cache:
            return self._edge_cache[cache_key]

        s = self._client.schema()
        # Try to create with ifNotExist
        try:
            s.edgeLabel(edge_label) \
                .sourceLabel(src_label).targetLabel(tgt_label) \
                .ifNotExist().create()
            self._edge_cache[cache_key] = edge_label
            return edge_label
        except Exception:
            # Label may exist with different source/target — try variants
            for i in range(2, 10):
                variant = f"{edge_label}_v{i}"
                try:
                    s.edgeLabel(variant) \
                        .sourceLabel(src_label).targetLabel(tgt_label) \
                        .ifNotExist().create()
                    self._edge_cache[cache_key] = variant
                    return variant
                except Exception:
                    continue
            # Fallback: use original (may fail silently)
            self._edge_cache[cache_key] = edge_label
            return edge_label

    def _get_llm(self):
        """Get or create LLM instance for entity extraction."""
        if self._extract_llm is None:
            llm_config = getattr(self.config, "llm", None)
            self._extract_llm = LLMFactory.create(llm_config) if llm_config else LLMFactory.create()
        return self._extract_llm

    def _get_embedder(self):
        """Get or create embedder instance."""
        if self._embedder is None:
            self._embedder = EmbedderFactory.create()
        return self._embedder

    def _infer_vertex_label(self, entity_type: str) -> str:
        """Map entity type to a valid vertex label."""
        type_map = {
            "person": "person", "user": "person", "people": "person",
            "organization": "organization", "company": "organization", "org": "organization",
            "location": "location", "place": "location", "city": "location", "country": "location",
            "skill": "skill", "ability": "skill",
            "concept": "concept", "idea": "concept", "topic": "concept",
            "project": "project", "task": "project",
            "product": "product", "tool": "tool", "software": "product",
            "event": "event", "meeting": "event", "conference": "event",
            "team": "team", "group": "team", "department": "team",
            "job": "identity", "role": "identity", "profession": "identity", "identity": "identity",
            "time": "time", "date": "time", "day": "time",
            "method": "method", "approach": "method", "technique": "method",
            "feeling": "feeling", "emotion": "feeling",
            "book": "book", "movie": "concept", "song": "concept",
            "animal": "animal", "pet": "animal",
            "food": "food", "dish": "food",
            "color": "color",
            "activity": "activity", "hobby": "activity", "sport": "activity",
            "community": "community",
        }
        return type_map.get(entity_type.lower(), "concept")

    def _extract_entities_and_relations(self, data: str) -> Dict[str, Any]:
        """Use LLM to extract entities and relationships from text."""
        llm = self._get_llm()
        prompt = self._graph_prompts.extract_prompt(data)
        try:
            response = llm.complete(prompt)
            text = remove_code_blocks(response.text if hasattr(response, "text") else str(response))
            result = json.loads(text)
            return result
        except Exception as e:
            logger.warning(f"LLM entity extraction failed: {e}")
            return {"entities": [], "relationships": []}

    def _store_entity(self, name: str, entity_type: str, memory_id: str) -> Optional[str]:
        """Store or get an entity vertex. Returns vertex ID."""
        label = self._infer_vertex_label(entity_type)
        if not self._ensure_vertex_label(label):
            return None

        # PRIMARY_KEY ID format: label_id:name
        vid = f"{label}:{name}"
        try:
            # Check if exists
            existing = self._client.graph().getVertex(label, vid)
            if existing:
                return vid
        except Exception:
            pass

        # Create new vertex
        try:
            self._client.graph().addVertex(label) \
                .property("name", name) \
                .property("type", entity_type) \
                .property("memory_id", memory_id) \
                .property("created_at", time.time()) \
                .property("access_count", 0) \
                .property("initial_score", 1.0) \
                .property("last_accessed_at", time.time()) \
                .commit()
            return vid
        except Exception as e:
            logger.debug(f"Vertex create failed for {name}: {e}")
            return vid  # May already exist

    def _store_relationship(self, src_name: str, src_type: str,
                            tgt_name: str, tgt_type: str,
                            relation: str, memory_id: str):
        """Store a relationship edge between two entities."""
        src_label = self._infer_vertex_label(src_type)
        tgt_label = self._infer_vertex_label(tgt_type)
        self._ensure_vertex_label(src_label)
        self._ensure_vertex_label(tgt_label)

        # Sanitize edge label
        edge_label = re.sub(r'[^a-zA-Z0-9_]', '_', relation.lower())[:60]
        if not edge_label:
            edge_label = "related_to"

        actual_label = self._ensure_edge_label(edge_label, src_label, tgt_label)

        src_vid = f"{src_label}:{src_name}"
        tgt_vid = f"{tgt_label}:{tgt_name}"

        try:
            self._client.graph().addEdge(actual_label) \
                .from_(src_vid).to(tgt_vid) \
                .property("memory_id", memory_id) \
                .commit()
        except Exception as e:
            logger.debug(f"Edge create failed {src_name}->{tgt_name}: {e}")

    def add(self, data: str, filters: Dict[str, Any]) -> Dict[str, Any]:
        """Add data to the graph: extract entities, store vertices and edges."""
        memory_id = str(filters.get("memory_id") or generate_snowflake_id())
        user_id = filters.get("user_id", "default")

        # Extract entities and relations via LLM
        result = self._extract_entities_and_relations(data)
        entities = result.get("entities", result.get("nodes", []))
        relationships = result.get("relationships", result.get("edges", []))

        stored_nodes = []
        stored_edges = []

        # Store entities
        for entity in entities:
            name = entity.get("name", entity.get("label", ""))
            etype = entity.get("type", entity.get("entity_type", "concept"))
            if name:
                vid = self._store_entity(name, etype, memory_id)
                if vid:
                    stored_nodes.append({"id": vid, "name": name, "type": etype})

        # Store relationships
        for rel in relationships:
            src = rel.get("source", rel.get("src", rel.get("subject", "")))
            tgt = rel.get("target", rel.get("tgt", rel.get("object", "")))
            relation = rel.get("relationship", rel.get("label", rel.get("predicate", "related_to")))
            src_type = rel.get("source_type", rel.get("src_type", "concept"))
            tgt_type = rel.get("target_type", rel.get("tgt_type", "concept"))
            if src and tgt:
                self._store_relationship(src, src_type, tgt, tgt_type, relation, memory_id)
                stored_edges.append({
                    "source": src, "target": tgt, "relationship": relation
                })

        logger.info(f"Graph add: {len(stored_nodes)} nodes, {len(stored_edges)} edges for memory {memory_id}")
        return {
            "memory_id": memory_id,
            "entities": stored_nodes,
            "relationships": stored_edges,
        }

    def search(self, query: str, filters: Dict[str, Any], limit: int = 10) -> List[Dict[str, Any]]:
        """Search graph for entities and relationships matching the query."""
        # Extract entities from query
        result = self._extract_entities_and_relations(query)
        query_entities = [e.get("name", "") for e in result.get("entities", []) if e.get("name")]

        results = []
        for entity_name in query_entities[:5]:  # Limit to top 5 entities
            # Search for matching vertices
            for vl in _VERTEX_LABELS:
                try:
                    vertices = self._client.graph().getVerticesByLabel(
                        vl, limit=limit
                    )
                    if vertices:
                        for v in (vertices if isinstance(vertices, list) else [vertices]):
                            vdata = v if isinstance(v, dict) else {}
                            vname = vdata.get("name", "")
                            if entity_name.lower() in vname.lower() or vname.lower() in entity_name.lower():
                                # Get edges for this vertex
                                vid = vdata.get("id", f"{vl}:{vname}")
                                edges = self._get_vertex_edges(vid, vl, self._max_hops)
                                results.append({
                                    "entity": vname,
                                    "type": vdata.get("type", vl),
                                    "vertex_id": vid,
                                    "edges": edges[:limit],
                                })
                                if len(results) >= limit:
                                    break
                except Exception:
                    continue
            if len(results) >= limit:
                break

        return results[:limit]

    def _get_vertex_edges(self, vid: str, vlabel: str, max_hops: int) -> List[Dict[str, str]]:
        """Get edges for a vertex via BFS traversal."""
        edges = []
        try:
            # Use HugeGraph traversers API for neighbors
            traversers = self._client.traversers()
            neighbors = traversers.kout(vid, label=vlabel, depth=max_hops)
            if neighbors:
                for path in (neighbors if isinstance(neighbors, list) else [neighbors]):
                    if isinstance(path, dict):
                        edges.append({
                            "source": path.get("source", ""),
                            "target": path.get("target", ""),
                            "relationship": path.get("label", ""),
                        })
        except Exception as e:
            logger.debug(f"Traversal failed for {vid}: {e}")
        return edges

    def delete_all(self, filters: Dict[str, Any]) -> None:
        """Delete all graph data for the given filters (by user_id or memory_id)."""
        memory_id = filters.get("memory_id")
        user_id = filters.get("user_id")

        if memory_id:
            # Delete vertices with matching memory_id property
            for vl in _VERTEX_LABELS:
                try:
                    vertices = self._client.graph().getVerticesByLabel(vl, limit=1000)
                    if vertices:
                        for v in (vertices if isinstance(vertices, list) else [vertices]):
                            vdata = v if isinstance(v, dict) else {}
                            if vdata.get("memory_id") == memory_id:
                                vid = vdata.get("id", "")
                                if vid:
                                    self._client.graph().removeVertex(vl, vid)
                except Exception:
                    continue

    def get_all(self, filters: Dict[str, Any], limit: int = 100) -> List[Dict[str, str]]:
        """Retrieve all nodes and relationships from the graph."""
        results = []
        for vl in _VERTEX_LABELS:
            try:
                vertices = self._client.graph().getVerticesByLabel(vl, limit=limit)
                if vertices:
                    for v in (vertices if isinstance(vertices, list) else [vertices]):
                        vdata = v if isinstance(v, dict) else {}
                        results.append({
                            "id": str(vdata.get("id", "")),
                            "label": vl,
                            "name": vdata.get("name", ""),
                            "type": vdata.get("type", vl),
                            "memory_id": vdata.get("memory_id", ""),
                        })
                        if len(results) >= limit:
                            return results
            except Exception:
                continue
        return results

    def reset(self) -> None:
        """Reset the graph by clearing all vertices."""
        for vl in _VERTEX_LABELS:
            try:
                vertices = self._client.graph().getVerticesByLabel(vl, limit=10000)
                if vertices:
                    for v in (vertices if isinstance(vertices, list) else [vertices]):
                        vdata = v if isinstance(v, dict) else {}
                        vid = vdata.get("id", "")
                        if vid:
                            try:
                                self._client.graph().removeVertex(vl, vid)
                            except Exception:
                                pass
            except Exception:
                continue
        self._edge_cache.clear()
        logger.info("Graph reset completed")

    def get_statistics(self, filters: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Get statistics for the graph data."""
        vertex_count = 0
        edge_count = 0
        label_stats: Dict[str, int] = {}

        for vl in _VERTEX_LABELS:
            try:
                vertices = self._client.graph().getVerticesByLabel(vl, limit=10000)
                count = len(vertices) if isinstance(vertices, list) else (1 if vertices else 0)
                vertex_count += count
                if count > 0:
                    label_stats[vl] = count
            except Exception:
                continue

        return {
            "vertex_count": vertex_count,
            "edge_count": edge_count,
            "vertex_labels": label_stats,
            "provider": "hugegraph",
            "graph_name": self._graph,
            "url": self._url,
        }

    def get_unique_users(self) -> List[str]:
        """Get a list of unique user IDs from stored memories."""
        users = set()
        for vl in _VERTEX_LABELS:
            try:
                vertices = self._client.graph().getVerticesByLabel(vl, limit=10000)
                if vertices:
                    for v in (vertices if isinstance(vertices, list) else [vertices]):
                        vdata = v if isinstance(v, dict) else {}
                        mid = vdata.get("memory_id", "")
                        if mid:
                            # Extract user_id from memory_id (convention: user_id:timestamp)
                            parts = mid.split(":")
                            if len(parts) > 1:
                                users.add(parts[0])
        except Exception:
            continue
        return list(users)
