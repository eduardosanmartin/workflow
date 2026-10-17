"""Actualización automática de la vista de seguimiento cada 10 segundos (RF-20, RNF-04)."""

from app import models as m
from app.services import bandeja
from app.services import configuracion as cfg
from app.services import seguimiento
from tests.test_admin import texto
from tests.test_bandeja import version
from tests.test_inicio_pantallas import admin, app, confirmar, proceso, publico, staff  # noqa: F401


def preparar(staff):
    confirmar(staff)
    expediente = m.Expediente.get()
    return expediente, f"/seguimiento/{expediente.token_seguimiento}"


def test_la_pagina_trae_el_sondeo_y_su_firma(staff, publico, proceso):
    expediente, url = preparar(staff)
    html = texto(publico.get(url))
    firma = seguimiento.firma(expediente.token_seguimiento)
    assert f'data-sondeo="{url}/estado"' in html and f'data-firma="{firma}"' in html
    assert "js/seguimiento.js" in html and 'aria-live="polite"' in html


def test_estado_parcial_sin_cambios_responde_304(staff, publico, proceso):
    expediente, url = preparar(staff)
    respuesta = publico.get(f"{url}/estado")
    assert respuesta.status_code == 200
    html = texto(respuesta)
    assert "<html" not in html and "M-0001" in html and "Dirígete a: MNP" in html
    etiqueta = respuesta.headers["ETag"]
    assert etiqueta == f'"{seguimiento.firma(expediente.token_seguimiento)}"'
    assert respuesta.headers["Cache-Control"] == "no-store"

    sin_cambios = publico.get(f"{url}/estado", headers={"If-None-Match": etiqueta})
    assert sin_cambios.status_code == 304 and sin_cambios.get_data() == b""


def test_un_cambio_del_recorrido_cambia_la_firma(app, staff, publico, admin, proceso):
    expediente, url = preparar(staff)
    etiqueta = publico.get(f"{url}/estado").headers["ETag"]
    with app.app_context():
        bandeja.atender(expediente.id, version(expediente), admin)
    respuesta = publico.get(f"{url}/estado", headers={"If-None-Match": etiqueta})
    assert respuesta.status_code == 200 and respuesta.headers["ETag"] != etiqueta
    assert "Estás siendo atendido en: MNP" in texto(respuesta)


def test_firma_cambia_al_editar_datos_y_al_cerrar_el_proceso(app, staff, publico, admin, proceso):
    expediente, _ = preparar(staff)
    token = expediente.token_seguimiento
    inicial = seguimiento.firma(token)
    m.Postulante.update(actualizado_en=m.ahora()).execute()
    editada = seguimiento.firma(token)
    assert editada != inicial
    with app.app_context():
        cfg.cerrar_proceso(proceso.id, admin)
    assert seguimiento.firma(token) != editada


def test_proceso_cerrado_en_el_sondeo(app, staff, publico, admin, proceso):
    _, url = preparar(staff)
    with app.app_context():
        cfg.cerrar_proceso(proceso.id, admin)
    respuesta = publico.get(f"{url}/estado")
    assert respuesta.status_code == 200
    html = texto(respuesta)
    assert "Este proceso ha finalizado" in html and 'data-finalizado' in html


def test_token_inexistente_en_el_sondeo(publico, proceso):
    assert publico.get("/seguimiento/no-existe/estado").status_code == 404
    assert seguimiento.firma("no-existe") is None
    assert seguimiento.firma("x" * 65) is None
