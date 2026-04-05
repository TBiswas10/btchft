from __future__ import annotations

import pytest

from btc_hft.config import load_and_validate_settings


def test_validate_rejects_live_mode(monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY", "key")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "secret")
    monkeypatch.setenv("ALPACA_PAPER", "false")
    monkeypatch.setenv("DRY_RUN", "false")

    with pytest.raises(ValueError, match="paper trading only"):
        load_and_validate_settings()
