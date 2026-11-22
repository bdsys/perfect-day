from app.core.security import hash_token
from app.services.sharing import effective_role, mask_email, new_invite_token, normalize_email


def test_mask_email():
    assert mask_email("mom@example.com") == "m•••@example.com"
    assert mask_email("a@x.io") == "a•••@x.io"


def test_normalize_email():
    assert normalize_email("  Mom@Example.COM ") == "mom@example.com"


def test_effective_role():
    assert effective_role("editor", "plus") == "viewer"
    assert effective_role("editor", "free") == "viewer"
    assert effective_role("editor", "family") == "editor"
    assert effective_role("viewer", "family") == "viewer"


def test_new_invite_token_hash_matches():
    raw, hashed = new_invite_token()
    assert hashed == hash_token(raw)
    assert raw != hashed
    assert new_invite_token()[0] != raw
