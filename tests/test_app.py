from app import create_app
from app.config import TestConfig


def test_app_factory_responde_salud():
    app = create_app(TestConfig)
    respuesta = app.test_client().get("/salud")
    assert respuesta.status_code == 200
    assert respuesta.get_json() == {"estado": "ok"}
