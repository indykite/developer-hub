# Copyright (c) 2026 IndyKite
from pathlib import Path

import pytest
from app.prompts import load_gallery

GALLERY = Path(__file__).resolve().parent.parent / "data" / "prompts.json"


def test_shipped_gallery_is_valid_and_names_known_personas():
    """Shipped gallery is valid and names known personas."""
    gallery = load_gallery(GALLERY)
    assert {c.kind for c in gallery.categories} == {"capability", "guardrail"}
    assert all(c.prompts for c in gallery.categories)
    assert any(p.auth_mode != "delegation" for c in gallery.categories for p in c.prompts)


def test_unknown_persona_is_rejected(tmp_path):
    """Unknown persona is rejected."""
    bad = tmp_path / "p.json"
    bad.write_text(
        '{"personas": [{"id": "a", "type": "User", "name": "A", "role": "r"}],'
        ' "categories": [{"id": "c", "title": "t", "blurb": "b",'
        ' "prompts": [{"persona": "nobody", "text": "x"}]}]}',
    )
    with pytest.raises(ValueError, match="nobody"):
        load_gallery(bad)
