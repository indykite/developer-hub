# Copyright (c) 2026 IndyKite
"""Graph Explorer: visualize the active dataset's bundled nodes and relationships.

File-backed on purpose: it renders ``data/<DATASET>/nodes.json`` and
``relationships.json`` exactly as the capture routes push them to the IKG, so
it works before anything is captured and needs no platform call.

Scale: the whole dataset is indexed once in memory (adjacency lists), then
served in views. Small datasets get everything in one payload. Above
NODE_BUDGET nodes the *overview* collapses the most numerous node types until
the rest fits the budget - a dataset-agnostic version of "hide the 14k Tracks" -
and those nodes are reached on demand: per-node expansion, a whole-type load,
or a server-side search that returns a node's neighborhood.
"""

import json
import logging
import threading

from api import _dataset
from flask import jsonify, render_template, request
from flask_openapi3 import APIBlueprint, Tag

tag = Tag(name="graph", description="Graph visualization of the active dataset")

logger = logging.getLogger(__name__)

api_graph = APIBlueprint(
    "graph",
    __name__,
    url_prefix="/graph",
    abp_tags=[tag],
    doc_ui=True,
)

NODES_FILE = _dataset.NODES_PATH
RELATIONSHIPS_FILE = _dataset.RELATIONSHIPS_PATH

# The overview shows at most this many nodes up front; the largest node types
# are collapsed (loaded on demand) until the remainder fits.
NODE_BUDGET = 1500
SEARCH_LIMIT = 20
# Shorter terms would scan and sort the whole index for near-useless results;
# the search box applies the same minimum before it calls the server.
SEARCH_MIN_CHARS = 2

# Label candidates tried in order; datasets differ in shape, so this is generic
# rather than a per-type map. A tuple joins several properties with a space.
_LABEL_CANDIDATES = (
    ("name",),
    ("first_name", "last_name"),
    ("firstname", "lastname"),
    ("title",),
    ("location",),
)
# Any property whose name ends with one of these reads as an identifier.
_ID_SUFFIXES = ("_number", "_id", "_plate")

# One dataset snapshot, rebuilt whenever either data file changes on disk.
_cache = {}
_cache_lock = threading.Lock()


def _node_label(props):
    for candidate in _LABEL_CANDIDATES:
        parts = [str(props[p]) for p in candidate if props.get(p) not in (None, "")]
        if parts:
            return " ".join(parts)
    for key in sorted(props):
        if key.endswith(_ID_SUFFIXES) and props[key] not in (None, ""):
            return str(props[key])
    return None


def _props_of(item):
    """Property values; a resolver-backed property (external_value, no value) is shown as such."""
    props = {}
    for p in item.get("properties", []) or []:
        if "value" in p:
            props[p["type"]] = p["value"]
        elif p.get("external_value"):
            props[p["type"]] = f"resolved at query time by external data resolver '{p['external_value']}'"
        else:
            props[p["type"]] = None
    return props


def _meta_of(item):
    """Per-property provenance (source, assurance_level, verified_time, ...) where present."""
    return {p["type"]: p["metadata"] for p in item.get("properties", []) or [] if p.get("metadata")}


def _load_items(path, key, errors):
    """Return the list under *key*, accepting a bare list as well as ``{key: [...]}``.

    A missing or malformed file is logged, reported in *errors* and yields no
    items, so the page renders empty with a warning instead of failing with a 500.
    """
    try:
        with path.open(encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        logger.warning("Could not load %s: %s", path, e)
        # strerror keeps the page message short ("No such file or directory")
        # instead of echoing the full path a second time.
        errors.append(f"Could not load {path.name}: {getattr(e, 'strerror', None) or e}")
        return []
    items = data.get(key, []) if isinstance(data, dict) else data
    if not isinstance(items, list):
        errors.append(f"{path.name}: expected a list under '{key}'")
        return []
    return items


def _mtime_ns(path):
    """File mtime for the cache key, or None when the file is missing."""
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return None


def _node_key(ref):
    """Identify a node the way the IKG does: by (type, external_id).

    external_id alone is not unique - canbank has both a User and a Customer
    called ``rebecca`` - so the cytoscape element id carries the type too.
    """
    return f"{ref['type']}:{ref['external_id']}"


def _collapsed_types(type_counts, budget=NODE_BUDGET):
    """Pick the node types to hide from the overview so it fits the budget.

    Largest types go first: that is what makes the overview useful (the long
    tail of leaf entities disappears, the structure stays). Returns an empty
    set when the whole dataset already fits.
    """
    remaining = sum(type_counts.values())
    collapsed = set()
    for type_name, count in sorted(type_counts.items(), key=lambda kv: (-kv[1], kv[0])):
        if remaining <= budget:
            break
        collapsed.add(type_name)
        remaining -= count
    return collapsed


def _other_end(edge, key):
    return edge["target"] if edge["source"] == key else edge["source"]


def _build_dataset():
    errors = []
    nodes = {}
    for n in _load_items(NODES_FILE, "nodes", errors):
        props = _props_of(n)
        nodes[_node_key(n)] = {
            "external_id": n["external_id"],
            "type": n["type"],
            "label": _node_label(props) or n["external_id"],
            "labels": n.get("labels") or [],
            "is_identity": bool(n.get("is_identity")),
            "props": props,
            "meta": _meta_of(n),
        }

    edges = []
    degree = dict.fromkeys(nodes, 0)
    adj = {key: [] for key in nodes}  # key -> indexes into edges of every incident edge
    rel_counts = {}
    for r in _load_items(RELATIONSHIPS_FILE, "relationships", errors):
        src, tgt = _node_key(r["source"]), _node_key(r["target"])
        if src not in nodes or tgt not in nodes:
            logger.debug("Skipping relationship %s with unknown endpoint: %s -> %s", r.get("type"), src, tgt)
            continue
        idx = len(edges)
        edges.append({"source": src, "target": tgt, "type": r["type"], "props": _props_of(r), "meta": _meta_of(r)})
        degree[src] += 1
        degree[tgt] += 1
        adj[src].append(idx)
        adj[tgt].append(idx)
        rel_counts[r["type"]] = rel_counts.get(r["type"], 0) + 1

    type_counts = {}
    members = {}  # type -> keys of that type
    for key, n in nodes.items():
        type_counts[n["type"]] = type_counts.get(n["type"], 0) + 1
        members.setdefault(n["type"], []).append(key)

    collapsed = _collapsed_types(type_counts)
    # Per node: how many distinct neighbors live in collapsed types (the
    # "double-click to expand" count shown in the details panel). Distinct
    # nodes, not edges: parallel edges must not leave a phantom neighbor.
    hidden = {}
    for key, idxs in adj.items():
        hidden[key] = len(
            {_other_end(edges[idx], key) for idx in idxs if nodes[_other_end(edges[idx], key)]["type"] in collapsed},
        )

    search = [(n["label"].lower(), n["external_id"].lower(), key) for key, n in nodes.items()]

    return {
        "nodes": nodes,
        "edges": edges,
        "degree": degree,
        "adj": adj,
        "members": members,
        "hidden": hidden,
        "search": search,
        "collapsed": collapsed,
        "type_counts": dict(sorted(type_counts.items())),
        "rel_counts": dict(sorted(rel_counts.items())),
        "errors": errors,
    }


def _get_dataset():
    """Return the cached dataset, rebuilding when either data file changes on disk."""
    key = (_mtime_ns(NODES_FILE), _mtime_ns(RELATIONSHIPS_FILE))
    with _cache_lock:
        if _cache.get("key") != key:
            logger.info("Building graph dataset from %s and %s", NODES_FILE, RELATIONSHIPS_FILE)
            _cache["data"] = _build_dataset()
            _cache["key"] = key
            ds = _cache["data"]
            logger.info(
                "Graph dataset: %d nodes, %d relationships, %d types, collapsed in overview: %s",
                len(ds["nodes"]),
                len(ds["edges"]),
                len(ds["type_counts"]),
                sorted(ds["collapsed"]) or "none",
            )
        return _cache["data"]


def _node_element(key, ds):
    n = ds["nodes"][key]
    return {
        "data": {
            "id": key,
            "externalId": n["external_id"],
            "label": n["label"],
            "type": n["type"],
            "labels": n["labels"],
            "isIdentity": n["is_identity"],
            "props": n["props"],
            "meta": n["meta"],
            "degree": ds["degree"][key],
            "hiddenCount": ds["hidden"][key],
        },
    }


def _edge_element(idx, ds):
    e = ds["edges"][idx]
    return {
        "data": {
            "id": f"e{idx}",
            "source": e["source"],
            "target": e["target"],
            "label": e["type"],
            "props": e["props"],
            "meta": e["meta"],
        },
    }


def _elements(ds, keys, edge_idxs):
    return {
        "nodes": [_node_element(k, ds) for k in keys],
        "edges": [_edge_element(i, ds) for i in sorted(edge_idxs)],
    }


def _overview_elements(ds):
    """Every node outside the collapsed types, and the edges among them."""
    visible = [k for k, n in ds["nodes"].items() if n["type"] not in ds["collapsed"]]
    visible_set = set(visible)
    edge_idxs = [i for i, e in enumerate(ds["edges"]) if e["source"] in visible_set and e["target"] in visible_set]
    return _elements(ds, visible, edge_idxs)


def _expand_elements(ds, key):
    """One node's neighbors in collapsed types, plus every edge touching them.

    The client keeps only edges whose other endpoint is on the canvas, so the
    same payload works whether or not neighboring types were loaded already.
    """
    neighbors = sorted(
        {
            _other_end(ds["edges"][idx], key)
            for idx in ds["adj"][key]
            if ds["nodes"][_other_end(ds["edges"][idx], key)]["type"] in ds["collapsed"]
        },
    )
    edge_idxs = {idx for n in neighbors for idx in ds["adj"][n]}
    return _elements(ds, neighbors, edge_idxs)


def _neighborhood_elements(ds, key):
    """Return a node with all its neighbors and the edges among that set (search landing)."""
    keys = {key}
    keys.update(_other_end(ds["edges"][idx], key) for idx in ds["adj"][key])
    edge_idxs = {
        idx
        for k in keys
        for idx in ds["adj"][k]
        if ds["edges"][idx]["source"] in keys and ds["edges"][idx]["target"] in keys
    }
    return _elements(ds, sorted(keys), edge_idxs)


def _type_elements(ds, type_name):
    """Every node of one type plus all their incident edges (client filters by presence)."""
    keys = ds["members"][type_name]
    edge_idxs = {idx for k in keys for idx in ds["adj"][k]}
    return _elements(ds, keys, edge_idxs)


def _stats(ds):
    return {
        "type_counts": ds["type_counts"],
        "rel_counts": ds["rel_counts"],
        "collapsed": sorted(ds["collapsed"]),
        "nodes": len(ds["nodes"]),
        "edges": len(ds["edges"]),
        "budget": NODE_BUDGET,
        "errors": ds["errors"],
    }


@api_graph.get("/", tags=[tag])
def show_graph():
    """Render the graph explorer page for the active dataset's bundled files."""
    ds = _get_dataset()
    collapsed = sorted(ds["collapsed"], key=lambda t: -ds["type_counts"][t])
    return render_template(
        "graph/view.html",
        dataset=_dataset.DATASET,
        dataset_display_name=_dataset.DISPLAY_NAME,
        type_counts=ds["type_counts"],
        rel_counts=ds["rel_counts"],
        node_count=len(ds["nodes"]),
        edge_count=len(ds["edges"]),
        collapsed=collapsed,
        collapsed_nodes=sum(ds["type_counts"][t] for t in collapsed),
        budget=NODE_BUDGET,
        load_errors=ds["errors"],
    )


@api_graph.get("/data", tags=[tag])
def graph_data():
    """Return cytoscape.js elements for the active dataset.

    Query parameters (at most one):
      expand=<id>  neighbors of one node that live in collapsed types, with their edges
      node=<id>    one node with its whole neighborhood (search landing)
      type=<name>  every node of one type with its incident edges
    Without parameters: the overview (all nodes outside collapsed types) plus stats.
    Element ids are ``<type>:<external_id>``.
    """
    expand = request.args.get("expand")
    node = request.args.get("node")
    type_name = request.args.get("type")
    if sum(bool(x) for x in (expand, node, type_name)) > 1:
        return jsonify({"error": "Use at most one of: expand, node, type"}), 400
    ds = _get_dataset()
    if expand or node:
        key = expand or node
        if key not in ds["nodes"]:
            return jsonify({"error": f"Unknown node: {key}"}), 404
        elements = _expand_elements(ds, key) if expand else _neighborhood_elements(ds, key)
        return jsonify({"elements": elements})
    if type_name:
        if type_name not in ds["members"]:
            return jsonify({"error": f"Unknown node type: {type_name}"}), 404
        return jsonify({"elements": _type_elements(ds, type_name)})
    return jsonify({"dataset": _dataset.DATASET, "elements": _overview_elements(ds), "stats": _stats(ds)})


@api_graph.get("/search", tags=[tag])
def graph_search():
    """Find nodes by label or external_id substring (case-insensitive).

    Prefix matches rank first, then higher degree. Returns at most SEARCH_LIMIT
    matches, including nodes that are not in the overview. Terms shorter than
    SEARCH_MIN_CHARS return no matches without scanning the index.
    """
    q = (request.args.get("q") or "").strip().lower()
    if len(q) < SEARCH_MIN_CHARS:
        return jsonify({"matches": [], "total": 0})
    ds = _get_dataset()
    hits = []
    for label, ext_id, key in ds["search"]:
        if q in label or q in ext_id:
            rank = 0 if (label.startswith(q) or ext_id.startswith(q)) else 1
            hits.append((rank, -ds["degree"][key], label, key))
    hits.sort()
    matches = []
    for _, _, _, key in hits[:SEARCH_LIMIT]:
        n = ds["nodes"][key]
        matches.append(
            {
                "id": key,
                "label": n["label"],
                "type": n["type"],
                "externalId": n["external_id"],
                "degree": ds["degree"][key],
            },
        )
    return jsonify({"matches": matches, "total": len(hits)})
