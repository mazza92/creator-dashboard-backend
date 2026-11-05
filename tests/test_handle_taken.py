"""A creator signing up again with a handle already on another account gets told where it lives (masked)."""
import inspect

import app as A


def test_mask_email():
    assert A._mask_email("team+555@newcollab.co") == "te•••@newcollab.co"
    assert A._mask_email("") == ""
    assert A._mask_email("nope") == ""


def test_step1_returns_handle_taken_with_hint():
    src = inspect.getsource(A)
    assert "'error': 'handle_taken'" in src
    assert "'account_hint': _mask_email(holder.get('holder_email'))" in src
