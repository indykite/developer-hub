# Copyright (c) 2026 IndyKite
"""Verify a project's signed audit chain, read through the Audit Log API.

Same checks as the public ``audit-verify.py`` download (Audit Log API guide,
"How do I verify an export?"), applied to pages held in memory and reported per
batch so a page can draw them:

- every manifest and checkpoint names the requested project, and every batch
  the same project as its manifest
- manifests are contiguous from sequence 1 and each ``prev_hash`` equals the
  previous ``head_hash``
- ``head_hash`` recomputes as SHA-256 of
  ``<project_id>|<sequence>|<prev_hash>|<data_hash>|<batch_id>``
- the batch ``hash`` recomputes as SHA-256 of the ``data`` array exactly as
  served - the raw bytes, never re-serialized JSON - and equals ``data_hash``
- batch ``chain_hash``, ``batch_id``, ``manifest_id`` and ``project_id`` agree
  with the manifest
- every signature (``ES256-DER``: ECDSA P-256 over the SHA-256 digest,
  ASN.1 DER, standard Base64) verifies under the JWK named by ``kid``. This is
  not JWS ``ES256`` (raw R||S), so JWT libraries reject it as-is.
- every checkpoint signs
  ``project-checkpoint|<project_id>|<sequence>|<head_hash>|<created_at>`` and
  names the head the manifest at that sequence has

What it cannot tell: whether the chain is complete. A valid prefix stays valid
after its tail is removed; compare the newest head with one kept independently.

The tamper functions return a modified *copy* of a fetched chain, to show which
check catches which change. The platform's chain itself cannot be changed.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import re

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, utils

ALG = "ES256-DER"


# --- JSON scanning: the batch hash covers the data array exactly as served, so the
# --- raw bytes of each item's "data" value are taken from the page text.


def _skip_ws(s, i):
    while i < len(s) and s[i] in " \t\r\n":
        i += 1
    return i


def _scan_string(s, i):
    i += 1
    while True:
        c = s[i]
        if c == "\\":
            i += 2
        elif c == '"':
            return i + 1
        else:
            i += 1


def _scan_value(s, i):
    c = s[i]
    if c == '"':
        return _scan_string(s, i)
    if c in "[{":
        depth = 0
        while True:
            c = s[i]
            if c == '"':
                i = _scan_string(s, i)
                continue
            if c in "[{":
                depth += 1
            elif c in "]}":
                depth -= 1
                if depth == 0:
                    return i + 1
            i += 1
    j = i
    while j < len(s) and s[j] not in ",]} \t\r\n":
        j += 1
    return j


def _members(s, i):
    """Yield (key, value_start, value_end) for the object starting at s[i] == '{'."""
    i = _skip_ws(s, i + 1)
    while s[i] != "}":
        kend = _scan_string(s, i)
        key = json.loads(s[i:kend])
        i = _skip_ws(s, kend)
        i = _skip_ws(s, i + 1)  # past ':'
        vend = _scan_value(s, i)
        yield key, i, vend
        i = _skip_ws(s, vend)
        if s[i] == ",":
            i = _skip_ws(s, i + 1)


def _elements(s, i):
    """Yield (start, end) for each element of the array starting at s[i] == '['."""
    i = _skip_ws(s, i + 1)
    while s[i] != "]":
        end = _scan_value(s, i)
        yield i, end
        i = _skip_ws(s, end)
        if s[i] == ",":
            i = _skip_ws(s, i + 1)


def raw_items(page_text):
    """Return [(item_dict, raw_data_bytes_or_None)] for one response page."""
    out = []
    for key, vs, _ve in _members(page_text, _skip_ws(page_text, 0)):
        if key != "items":
            continue
        for es, ee in _elements(page_text, vs):
            item = json.loads(page_text[es:ee])
            raw = None
            for k, ds, de in _members(page_text, es):
                if k == "data":
                    raw = page_text[ds:de].encode("utf-8")
            out.append((item, raw))
    return out


def check_pages(pages):
    """Check one endpoint's saved pagination is complete; return a list of failures."""
    failures = []
    for n, text in enumerate(pages, 1):
        try:
            env = json.loads(text)
        except ValueError:
            failures.append(f"page {n}: not valid JSON")
            continue
        if not isinstance(env, dict) or not isinstance(env.get("items"), list):
            failures.append(f"page {n}: not a response envelope")
            continue
        expect_more = n < len(pages)
        if env.get("has_more") is not expect_more:
            failures.append(f"page {n}: has_more is {env.get('has_more')!r} on page {n} of {len(pages)}")
        cursor = env.get("next_cursor")
        if not isinstance(cursor, str) or bool(cursor) != expect_more:
            failures.append(f"page {n}: next_cursor does not match has_more")
    return failures


def build_chain(pages, jwks_sets, project_id):
    """Parse fetched pages into a chain: {"project_id", "manifests", "batches", "checkpoints", "jwks"}.

    *pages* maps "manifests" / "logs" / "checkpoints" to raw page texts.
    *jwks_sets* is a list of JWK Set documents (current plus any kept snapshots).
    *project_id* is the project the chain was requested for; every record must
    name it, so a validly signed chain of another project is not accepted.
    """
    return {
        "project_id": project_id,
        "manifests": [m for text in pages["manifests"] for m, _ in raw_items(text)],
        "batches": [[b, raw] for text in pages["logs"] for b, raw in raw_items(text)],
        "checkpoints": [c for text in pages["checkpoints"] for c, _ in raw_items(text)],
        "jwks": jwks_sets,
    }


# --- keys and signatures


def _b64url_int(s):
    return int.from_bytes(base64.urlsafe_b64decode(s + "=" * (-len(s) % 4)), "big")


def load_keys(jwks_sets):
    """Return {kid: EC public key} for every P-256 key in the given JWK Sets."""
    keys = {}
    for jwks in jwks_sets:
        for k in jwks.get("keys", []):
            if k.get("kty") != "EC" or k.get("crv") != "P-256":
                continue
            keys[k["kid"]] = ec.EllipticCurvePublicNumbers(
                _b64url_int(k["x"]),
                _b64url_int(k["y"]),
                ec.SECP256R1(),
            ).public_key()
    return keys


def _signature_error(keys, kid, alg, hex_digest, signature_b64):
    """Return None when the signature verifies, else why it does not."""
    if alg != ALG:
        return f"alg is {alg!r}, expected {ALG!r}"
    key = keys.get(kid)
    if key is None:
        return f"kid {kid!r} is in none of the JWK Sets"
    try:
        key.verify(
            base64.b64decode(signature_b64),
            bytes.fromhex(hex_digest),
            ec.ECDSA(utils.Prehashed(hashes.SHA256())),
        )
    except (InvalidSignature, ValueError) as e:
        return f"signature does not verify under kid {kid!r} ({e.__class__.__name__})"
    return None


def sha256_hex(data):
    """Return the lower-case hex SHA-256 of *data*."""
    return hashlib.sha256(data).hexdigest()


def _head_hash(m):
    return sha256_hex(f"{m['project_id']}|{m['sequence']}|{m['prev_hash']}|{m['data_hash']}|{m['batch_id']}".encode())


def _checkpoint_digest(c):
    return sha256_hex(
        f"project-checkpoint|{c['project_id']}|{c['sequence']}|{c['head_hash']}|{c['created_at']}".encode(),
    )


def _event_summary(raw):
    try:
        events = json.loads(raw)
    except (TypeError, ValueError):
        return 0, []
    if not isinstance(events, list):
        return 0, []
    types = sorted({e.get("eventType", "?") for e in events if isinstance(e, dict)})
    return len(events), types


# --- the chain


def _check_manifest(m, keys, prev_head, expected_seq, project_id):
    """Check one manifest: its project, its place in the sequence, its link, its head and its signature."""
    seq = m["sequence"]
    return {
        # The batch's project is compared with the manifest's in _check_batch.
        "project": None if m["project_id"] == project_id else f"belongs to project {m['project_id']}",
        "sequence": None if seq == expected_seq else f"expected sequence {expected_seq} (gap or duplicate)",
        "link": None if m["prev_hash"] == prev_head else f"prev_hash does not match head_hash of sequence {seq - 1}",
        "head_hash": None if _head_hash(m) == m["head_hash"] else "head_hash does not recompute",
        "manifest_signature": _signature_error(keys, m["kid"], m["alg"], m["head_hash"], m["signature"]),
    }


def _check_batch(m, pair, keys):
    """Check the batch at the manifest's sequence; return (checks, events, event_types)."""
    if pair is None:
        return {"batch": "no batch for this manifest"}, 0, []
    b, raw = pair
    mismatched = [f for f in ("batch_id", "manifest_id", "project_id") if b.get(f) != m.get(f)]
    checks = {"batch": f"{', '.join(mismatched)} differ from the manifest" if mismatched else None}
    events, event_types = 0, []
    if raw is None:
        checks["data_hash"] = "no data array"
    else:
        data_hash = sha256_hex(raw)
        checks["data_hash"] = (
            None
            if data_hash == b["hash"] == m["data_hash"]
            else "SHA-256 of data as served differs from hash / data_hash"
        )
        events, event_types = _event_summary(raw)
    checks["chain_hash"] = (
        None if b["chain_hash"] == m["head_hash"] else "chain_hash differs from the manifest head_hash"
    )
    checks["batch_signature"] = _signature_error(keys, b["kid"], b["alg"], b["hash"], b["signature"])
    return checks, events, event_types


def _check_checkpoint(c, keys, heads, project_id):
    """Return None when the checkpoint is this project's, verifies, and names the manifest's head, else why not."""
    if c["project_id"] != project_id:
        return f"belongs to project {c['project_id']}"
    problem = _signature_error(keys, c["kid"], c["alg"], _checkpoint_digest(c), c["signature"])
    if problem:
        return problem
    if c["sequence"] not in heads:
        return "no manifest at that sequence"
    if heads[c["sequence"]] != c["head_hash"]:
        return "head_hash differs from the manifest at that sequence"
    return None


def verify(chain):
    """Verify a chain built by build_chain; return a report the page can render.

    Each entry of report["entries"] is one sequence with a ``checks`` map of
    check name -> None (passed) or the failure text.
    """
    keys = load_keys(chain["jwks"])
    manifests = sorted(chain["manifests"], key=lambda m: m["sequence"])
    batches = {}
    failures = []
    for b, raw in chain["batches"]:
        if b["sequence"] in batches:
            failures.append(f"batch {b['sequence']}: sequence appears twice")
        batches[b["sequence"]] = (b, raw)

    entries = []
    heads = {}
    prev_head = ""
    expected_seq = 1
    for m in manifests:
        seq = m["sequence"]
        checks = _check_manifest(m, keys, prev_head, expected_seq, chain["project_id"])
        batch_checks, events, event_types = _check_batch(m, batches.get(seq), keys)
        checks.update(batch_checks)
        failures.extend(
            f"sequence {seq} {name.replace('_', ' ')}: {problem}" for name, problem in checks.items() if problem
        )
        entries.append(
            {
                "sequence": seq,
                "batch_id": m["batch_id"],
                "prev_hash": m["prev_hash"],
                "data_hash": m["data_hash"],
                "head_hash": m["head_hash"],
                "kid": m["kid"],
                "created_at": m.get("created_at", ""),
                "events": events,
                "event_types": event_types,
                "checks": checks,
                "ok": not any(checks.values()),
            },
        )
        heads[seq] = m["head_hash"]
        prev_head = m["head_hash"]
        expected_seq = seq + 1

    failures.extend(f"batch {seq}: no manifest for this batch" for seq in sorted(set(batches) - set(heads)))

    checkpoints = []
    for c in chain["checkpoints"]:
        problem = _check_checkpoint(c, keys, heads, chain["project_id"])
        if problem:
            failures.append(f"checkpoint {c.get('checkpoint_id', '')} (sequence {c['sequence']}): {problem}")
        checkpoints.append(
            {
                "checkpoint_id": c.get("checkpoint_id", ""),
                "sequence": c["sequence"],
                "head_hash": c["head_hash"],
                "created_at": c["created_at"],
                "kid": c["kid"],
                "error": problem,
            },
        )

    newest = manifests[-1] if manifests else None
    return {
        "ok": not failures,
        "failures": failures,
        "entries": entries,
        "checkpoints": checkpoints,
        "kids": sorted(keys),
        "events": sum(e["events"] for e in entries),
        "newest_sequence": newest["sequence"] if newest else 0,
        "newest_head": newest["head_hash"] if newest else "",
    }


# --- tampering with a copy


def _digit_flip(raw):
    """Change one digit in the first event's timestamp (or the first digit at all)."""
    text = raw.decode("utf-8")
    match = re.search(r'"time"\s*:\s*"\d', text) or re.search(r"\d", text)
    if not match:
        return raw, False
    pos = match.end() - 1
    flipped = str((int(text[pos]) + 1) % 10)
    return (text[:pos] + flipped + text[pos + 1 :]).encode("utf-8"), True


def _batch_at(chain, sequence):
    return next((pair for pair in chain["batches"] if pair[0]["sequence"] == sequence), None)


def _manifest_at(chain, sequence):
    return next((m for m in chain["manifests"] if m["sequence"] == sequence), None)


# Each tamper function changes *forged* (a deep copy) in place and returns its description.


def _tamper_edit_event(forged, sequence):
    pair = _batch_at(forged, sequence)
    if pair is None or pair[1] is None:
        return f"no batch at sequence {sequence}"
    pair[1], changed = _digit_flip(pair[1])
    return (
        f"changed one digit in an event of batch {sequence}" if changed else f"batch {sequence} has no digit to change"
    )


def _tamper_edit_and_rehash(forged, sequence):
    # The attacker edits an event and recomputes every hash after it - all the
    # SHA-256 checks pass again, but nobody without the private key can re-sign.
    pair = _batch_at(forged, sequence)
    if pair is None or pair[1] is None:
        return f"no batch at sequence {sequence}"
    pair[1], _ = _digit_flip(pair[1])
    prev_head = ""
    for m in sorted(forged["manifests"], key=lambda x: x["sequence"]):
        if m["sequence"] >= sequence:
            m["prev_hash"] = prev_head
            b_pair = _batch_at(forged, m["sequence"])
            if b_pair is not None and b_pair[1] is not None:
                m["data_hash"] = b_pair[0]["hash"] = sha256_hex(b_pair[1])
            m["head_hash"] = _head_hash(m)
            if b_pair is not None:
                b_pair[0]["chain_hash"] = m["head_hash"]
        prev_head = m["head_hash"]
    return f"changed batch {sequence} and recomputed every hash from there on"


def _tamper_delete_batch(forged, sequence):
    forged["manifests"] = [m for m in forged["manifests"] if m["sequence"] != sequence]
    forged["batches"] = [p for p in forged["batches"] if p[0]["sequence"] != sequence]
    return f"removed batch and manifest {sequence}"


def _tamper_truncate_tail(forged, sequence):
    forged["manifests"] = [m for m in forged["manifests"] if m["sequence"] < sequence]
    forged["batches"] = [p for p in forged["batches"] if p[0]["sequence"] < sequence]
    forged["checkpoints"] = [c for c in forged["checkpoints"] if c["sequence"] < sequence]
    return f"cut the chain off before sequence {sequence} (checkpoints after it removed too)"


def _tamper_forge_checkpoint(forged, sequence):
    if not forged["checkpoints"]:
        return "no checkpoint to forge"
    target = forged["checkpoints"][-1]
    if target["sequence"] == sequence:
        # Already at that sequence: forge the head it names instead, so the
        # change is never a no-op.
        head = target["head_hash"]
        target["head_hash"] = ("0" if head[0] != "0" else "1") + head[1:]
        return f"changed the head the newest checkpoint names at sequence {sequence}"
    m = _manifest_at(forged, sequence)
    target["sequence"] = sequence
    target["head_hash"] = m["head_hash"] if m else target["head_hash"]
    return f"moved the newest checkpoint to sequence {sequence}"


_TAMPERS = {
    "edit-event": _tamper_edit_event,
    "edit-and-rehash": _tamper_edit_and_rehash,
    "delete-batch": _tamper_delete_batch,
    "truncate-tail": _tamper_truncate_tail,
    "forge-checkpoint": _tamper_forge_checkpoint,
}


def tamper(chain, action, sequence):
    """Return (tampered_copy, description) for one tamper action on one sequence."""
    forged = copy.deepcopy(chain)
    tamper_fn = _TAMPERS.get(action)
    if tamper_fn is None:
        return forged, f"unknown action {action!r}"
    return forged, tamper_fn(forged, sequence)
