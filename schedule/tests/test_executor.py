"""Tests de logica pura del executor (sin red ni DB)."""
from app.core.app_config import AppConfig, MySQLConfig
from app.services.executor import build_headers, extract_job_id, resolve_final_url


def make_cfg():
    return AppConfig(
        config_id="microservicio-schedule", enabled=True,
        timezone="America/Mexico_City",
        targets={"servicio_a": "http://servicio-a"},
        mysql=MySQLConfig("h", 3306, "db", "u", "p"),
        request_timeout_seconds=30,
    )


def test_resolve_relative():
    cfg = make_cfg()
    url, err = resolve_final_url(
        {"endpoint": "/internal/jobs/full", "service_name": "servicio_a"}, cfg
    )
    assert err is None
    assert url == "http://servicio-a/internal/jobs/full"


def test_resolve_absolute():
    cfg = make_cfg()
    url, err = resolve_final_url(
        {"endpoint": "http://other/x", "service_name": None}, cfg
    )
    assert err is None and url == "http://other/x"


def test_resolve_unknown_service():
    cfg = make_cfg()
    url, err = resolve_final_url({"endpoint": "/x", "service_name": "nope"}, cfg)
    assert url is None and "targets" in err


def test_extract_job_id_simple():
    jid, obs = extract_job_id({"job_id": "abc"}, "body.job_id")
    assert jid == "abc" and obs is None


def test_extract_job_id_nested():
    jid, obs = extract_job_id({"task": {"job_id": 42}}, "body.task.job_id")
    assert jid == "42" and obs is None


def test_extract_job_id_deep():
    jid, _ = extract_job_id({"data": {"execution": {"id": "x9"}}}, "body.data.execution.id")
    assert jid == "x9"


def test_extract_job_id_missing():
    jid, obs = extract_job_id({"other": 1}, "body.job_id")
    assert jid is None and "no se pudo extraer" in obs


def test_build_headers_bearer():
    h = build_headers({"auth_type": "bearer", "auth_token": "T"})
    assert h["Authorization"] == "Bearer T"


def test_build_headers_custom():
    h = build_headers({"auth_type": "header", "auth_header_name": "X-Key", "auth_token": "T"})
    assert h["X-Key"] == "T"


def test_build_headers_none():
    h = build_headers({"auth_type": "none"})
    assert "Authorization" not in h
