import pytest

from app import config


def _set(monkeypatch, **values):
    for name, value in values.items():
        monkeypatch.setattr(config, name, value)


def test_missing_mongodb_uri_fails_fast(monkeypatch):
    _set(monkeypatch, MONGODB_URI="", USE_MOCK_DATA=True)
    with pytest.raises(config.ConfigError, match="MONGODB_URI"):
        config.validate()


def test_mock_mode_needs_no_nessie_keys(monkeypatch):
    _set(monkeypatch, MONGODB_URI="mongodb+srv://x", USE_MOCK_DATA=True, NESSIE_API_KEY="", NESSIE_CUSTOMER_ID="")
    config.validate()  # does not raise


def test_live_mode_requires_nessie_key_and_customer(monkeypatch):
    _set(monkeypatch, MONGODB_URI="mongodb+srv://x", USE_MOCK_DATA=False, NESSIE_API_KEY="", NESSIE_CUSTOMER_ID="")
    with pytest.raises(config.ConfigError) as exc:
        config.validate()
    assert "NESSIE_API_KEY" in str(exc.value)
    assert "NESSIE_CUSTOMER_ID" in str(exc.value)


def test_error_message_does_not_echo_the_connection_string(monkeypatch):
    _set(monkeypatch, MONGODB_URI="", USE_MOCK_DATA=False, NESSIE_API_KEY="k", NESSIE_CUSTOMER_ID="")
    with pytest.raises(config.ConfigError) as exc:
        config.validate()
    assert "mongodb+srv" not in str(exc.value)
