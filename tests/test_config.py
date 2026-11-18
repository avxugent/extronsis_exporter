"""Config loading and URL-parameter device construction."""

import pytest

from extronsis_exporter.__main__ import _device_from_query_params, _parse_bool, load_config


def write(tmp_path, text):
    p = tmp_path / "config.yaml"
    p.write_text(text)
    return str(p)


def test_empty_file_yields_defaults(tmp_path):
    cfg = load_config(write(tmp_path, ""))
    assert cfg["devices"] == []
    assert cfg["listen_port"] == 9877


def test_device_defaults_are_filled(tmp_path):
    cfg = load_config(write(tmp_path, "devices:\n  - {name: a, host: h}\n"))
    assert cfg["devices"][0] == {
        "name": "a", "host": "h", "port": 443, "path": "/www/index.html",
        "timeout": 10.0, "verify_tls": False,
    }


def test_device_overrides_are_kept(tmp_path):
    cfg = load_config(write(tmp_path, "devices:\n  - {name: a, host: h, port: 8443, verify_tls: true}\n"))
    assert cfg["devices"][0]["port"] == 8443
    assert cfg["devices"][0]["verify_tls"] is True


@pytest.mark.parametrize("dev", ["{host: h}", "{name: a}"])
def test_missing_required_device_field(tmp_path, dev):
    with pytest.raises(ValueError, match="missing the required"):
        load_config(write(tmp_path, f"devices:\n  - {dev}\n"))


def test_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_config(str(tmp_path / "nope.yaml"))


def test_example_config_is_valid():
    cfg = load_config("config.example.yaml")
    assert isinstance(cfg["devices"], list)


@pytest.mark.parametrize("raw, expected", [
    ("true", True), ("TRUE", True), ("1", True), ("yes", True), ("on", True),
    ("false", False), ("0", False), ("", False), ("nope", False),
])
def test_parse_bool(raw, expected):
    assert _parse_bool(raw) is expected


def test_query_params_minimal():
    dev = _device_from_query_params({"host": ["h"]})
    assert dev == {"host": "h", "name": "h", "port": 443, "path": "/www/index.html",
                   "timeout": 10.0, "verify_tls": False}


def test_query_params_full():
    dev = _device_from_query_params({
        "host": ["h"], "name": ["room"], "port": ["8443"], "path": ["/v"],
        "timeout": ["2.5"], "verify_tls": ["true"],
    })
    assert dev == {"host": "h", "name": "room", "port": 8443, "path": "/v",
                   "timeout": 2.5, "verify_tls": True}


@pytest.mark.parametrize("key, value", [("port", "abc"), ("timeout", "soon")])
def test_query_params_bad_numbers_raise(key, value):
    with pytest.raises(ValueError):
        _device_from_query_params({"host": ["h"], key: [value]})
