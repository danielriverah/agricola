"""Tests de validacion de TaskCreate."""
import pytest
from pydantic import ValidationError

from app.schemas.task import TaskCreate


def base_daily(**kw):
    data = dict(
        task_name="t", service_name="servicio_a", endpoint="/jobs/full",
        method="POST", daily=True, hora_ejecucion="02:00:00",
    )
    data.update(kw)
    return data


def test_valid_daily_task():
    t = TaskCreate(**base_daily())
    assert t.daily and t.method == "POST"


def test_method_normalized_and_validated():
    t = TaskCreate(**base_daily(method="post"))
    assert t.method == "POST"
    with pytest.raises(ValidationError):
        TaskCreate(**base_daily(method="FETCH"))


def test_relative_endpoint_requires_service():
    with pytest.raises(ValidationError):
        TaskCreate(**base_daily(service_name=None, endpoint="/x"))


def test_absolute_endpoint_no_service_ok():
    t = TaskCreate(**base_daily(service_name=None, endpoint="http://a/x"))
    assert t.endpoint.startswith("http")


def test_job_id_requires_ruta():
    with pytest.raises(ValidationError):
        TaskCreate(**base_daily(trae_job_id=True))
    t = TaskCreate(**base_daily(trae_job_id=True, ruta_job_id="body.job_id"))
    assert t.ruta_job_id == "body.job_id"


def test_daily_requires_hora():
    with pytest.raises(ValidationError):
        TaskCreate(**base_daily(hora_ejecucion=None))


def test_daily_and_run_at_excluyentes():
    from datetime import datetime
    with pytest.raises(ValidationError):
        TaskCreate(**base_daily(run_at=datetime(2030, 1, 1, 2, 0, 0)))


def test_once_requires_run_at():
    with pytest.raises(ValidationError):
        TaskCreate(task_name="t", service_name="s", endpoint="/x", daily=False)


def test_auth_header_requires_name_and_token():
    with pytest.raises(ValidationError):
        TaskCreate(**base_daily(auth_type="header", auth_token="x"))
    with pytest.raises(ValidationError):
        TaskCreate(**base_daily(auth_type="bearer"))
    t = TaskCreate(**base_daily(auth_type="bearer", auth_token="tok"))
    assert t.auth_type == "bearer"


def test_invalid_visibility():
    with pytest.raises(ValidationError):
        TaskCreate(**base_daily(visibility="secret"))
