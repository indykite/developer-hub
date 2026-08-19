# Copyright (c) 2026 IndyKite
import logging
import threading
from pathlib import Path

import ijson
from flask import jsonify, render_template, request
from flask_openapi3 import APIBlueprint, Tag

tag = Tag(name="graph", description="Graph visualization of the captured music dataset")

logger = logging.getLogger(__name__)

api_graph = APIBlueprint(
    "graph",
    __name__,
    url_prefix="/graph",
    abp_tags=[tag],
    doc_ui=True,
)

NODES_FILE = Path(__file__).parent.parent / "data" / "nodes" / "nodes_music.json"
RELATIONSHIPS_FILE = Path(__file__).parent.parent / "data" / "relationships" / "relationships_music.json"

# The label property to prefer per node type (falls back to the first property, then external_id).
_LABEL_PROPS = {
    "Artist": ("name",),
    "Album": ("title",),
    "Track": ("title",),
    "Venue": ("name",),
    "Playlist": ("name",),
    "Person": ("firstname", "lastname"),
}

# One dataset snapshot built by streaming the same files capture pushes to the IKG.
# This is the "bundled" source: it shows the graph as captured, before anything is
# modified in the live IKG. A future "live" source (CIQ read) can plug in beside it.
_cache = {}
_cache_lock = threading.Lock()


def _node_label(node_type, props):
    parts = [str(props[p]) for p in _LABEL_PROPS.get(node_type, ()) if p in props]
    if parts:
        return " ".join(parts)
    return None


def _build_dataset():
    """Stream both data files once and index them for the graph endpoints.

    ~16k nodes / ~31k relationships compact to a few MB of dicts, so unlike the
    capture path (which must assume multi-GB files) holding one snapshot in
    memory is fine - it is what makes expand/filter requests instant.
    """
    nodes = {}
    with NODES_FILE.open("rb") as f:
        for n in ijson.items(f, "nodes.item", use_float=True):
            props = {p["type"]: p.get("value") for p in n.get("properties", [])}
            nodes[n["external_id"]] = {
                "type": n["type"],
                "label": _node_label(n["type"], props) or n["external_id"],
                "props": props,
            }

    edges = []
    degree = dict.fromkeys(nodes, 0)
    track_count = dict.fromkeys(nodes, 0)
    adj = {}  # external_id -> indexes into edges of every incident edge
    track_adj = {}  # non-Track external_id -> indexes into edges touching a Track
    with RELATIONSHIPS_FILE.open("rb") as f:
        for r in ijson.items(f, "relationships.item", use_float=True):
            src, tgt = r["source"]["external_id"], r["target"]["external_id"]
            if src not in nodes or tgt not in nodes:
                continue
            idx = len(edges)
            edges.append({"source": src, "target": tgt, "type": r["type"]})
            degree[src] += 1
            degree[tgt] += 1
            adj.setdefault(src, []).append(idx)
            adj.setdefault(tgt, []).append(idx)
            src_is_track = nodes[src]["type"] == "Track"
            tgt_is_track = nodes[tgt]["type"] == "Track"
            if src_is_track != tgt_is_track:
                plain = tgt if src_is_track else src
                track_count[plain] += 1
                track_adj.setdefault(plain, []).append(idx)

    type_counts = {}
    for n in nodes.values():
        type_counts[n["type"]] = type_counts.get(n["type"], 0) + 1

    return {
        "nodes": nodes,
        "edges": edges,
        "degree": degree,
        "adj": adj,
        "track_count": track_count,
        "track_adj": track_adj,
        "type_counts": type_counts,
    }


def _get_dataset():
    """Return the cached dataset, rebuilding when either data file changes on disk."""
    key = (NODES_FILE.stat().st_mtime_ns, RELATIONSHIPS_FILE.stat().st_mtime_ns)
    with _cache_lock:
        if _cache.get("key") != key:
            logger.info("Building graph dataset from %s and %s", NODES_FILE.name, RELATIONSHIPS_FILE.name)
            _cache["data"] = _build_dataset()
            _cache["key"] = key
        return _cache["data"]


def _node_element(ext_id, ds):
    n = ds["nodes"][ext_id]
    return {
        "data": {
            "id": ext_id,
            "label": n["label"],
            "type": n["type"],
            "props": n["props"],
            "degree": ds["degree"][ext_id],
            "trackCount": ds["track_count"][ext_id],
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
        },
    }


@api_graph.get("/", tags=[tag])
def show_graph():
    """Render the graph explorer page for the bundled music dataset."""
    ds = _get_dataset()
    return render_template(
        "graph/view.html",
        type_counts=ds["type_counts"],
        edge_count=len(ds["edges"]),
    )


def _expand_elements(ds, expand):
    """Return the cytoscape elements for one node's Track neighbors.

    Includes every edge touching those tracks whose other endpoint is visible
    in the default view (any non-Track node) or another expanded track.
    """
    track_ids = set()
    edge_idxs = set()
    for idx in ds["track_adj"].get(expand, []):
        e = ds["edges"][idx]
        track = e["source"] if ds["nodes"][e["source"]]["type"] == "Track" else e["target"]
        track_ids.add(track)
    for track in track_ids:
        for idx in ds["adj"].get(track, []):
            e = ds["edges"][idx]
            other = e["target"] if e["source"] == track else e["source"]
            if ds["nodes"][other]["type"] != "Track" or other in track_ids:
                edge_idxs.add(idx)
    return {
        "nodes": [_node_element(i, ds) for i in sorted(track_ids)],
        "edges": [_edge_element(i, ds) for i in sorted(edge_idxs)],
    }


def _default_elements(ds, include_tracks):
    """Return the cytoscape elements and stats for the full listing."""
    node_ids = [i for i, n in ds["nodes"].items() if include_tracks or n["type"] != "Track"]
    visible = set(node_ids)
    edge_idxs = [i for i, e in enumerate(ds["edges"]) if e["source"] in visible and e["target"] in visible]
    return {
        "elements": {
            "nodes": [_node_element(i, ds) for i in node_ids],
            "edges": [_edge_element(i, ds) for i in edge_idxs],
        },
        "stats": {"type_counts": ds["type_counts"], "edges": len(edge_idxs)},
    }


@api_graph.get("/data", tags=[tag])
def graph_data():
    """Return cytoscape.js elements for the bundled dataset.

    Query parameters:
      include_tracks=1  include the ~14k Track nodes (heavy; default excludes them)
      expand=<id>       only the Track neighbors of one node plus their edges
                        (edges are limited to endpoints the default view already shows)
    """
    ds = _get_dataset()
    expand = request.args.get("expand")
    if expand:
        if expand not in ds["nodes"]:
            return jsonify({"error": f"Unknown node: {expand}"}), 404
        return jsonify({"elements": _expand_elements(ds, expand)})
    return jsonify(_default_elements(ds, request.args.get("include_tracks") == "1"))
