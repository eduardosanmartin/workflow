import pytest

from app.services.postulantes import (
    campos_pendientes,
    enmascarar,
    enmascarar_correo,
    enmascarar_nombre,
    enmascarar_telefono,
    validar_postulante,
)

CARRERAS = {"ENF", "KIN", "PSI", "DER"}


def datos_validos(**cambios):
    datos = {
        "rut": "12.345.678-5",
        "nombres": "Juan Andrés",
        "apellidos": "Pérez Soto",
        "telefono": "+56 9 1234-5678",
        "correo_personal": "Juan.Perez@Gmail.com",
        "carrera1": "enf",
        "carrera2": "",
        "carrera3": "",
    }
    datos.update(cambios)
    return datos


class TestValidarPostulante:
    def test_datos_validos_se_normalizan(self):
        limpios, errores = validar_postulante(datos_validos(), CARRERAS)
        assert errores == {}
        assert limpios == {
            "rut": "12345678-5",
            "nombres": "Juan Andrés",
            "apellidos": "Pérez Soto",
            "telefono": "+56912345678",
            "correo_personal": "juan.perez@gmail.com",
            "carrera1": "ENF",
            "carrera2": None,
            "carrera3": None,
        }

    def test_rut_invalido(self):
        _, errores = validar_postulante(datos_validos(rut="12.345.678-0"), CARRERAS)
        assert "rut" in errores

    @pytest.mark.parametrize(
        "campo", ["rut", "nombres", "apellidos", "telefono", "correo_personal", "carrera1"]
    )
    def test_campos_obligatorios(self, campo):
        _, errores = validar_postulante(datos_validos(**{campo: "  "}), CARRERAS)
        assert campo in errores

    def test_campo_ausente_es_obligatorio(self):
        datos = datos_validos()
        del datos["correo_personal"]
        _, errores = validar_postulante(datos, CARRERAS)
        assert "correo_personal" in errores

    def test_nombres_maximo_100_caracteres(self):
        _, errores = validar_postulante(datos_validos(nombres="a" * 101), CARRERAS)
        assert "nombres" in errores

    def test_espacios_internos_se_colapsan(self):
        limpios, _ = validar_postulante(datos_validos(nombres="  Juan   Andrés "), CARRERAS)
        assert limpios["nombres"] == "Juan Andrés"

    @pytest.mark.parametrize("telefono", ["1234567", "+5691234567890123", "+56 9 abcd 5678", "56-9-12"])
    def test_telefono_invalido(self, telefono):
        _, errores = validar_postulante(datos_validos(telefono=telefono), CARRERAS)
        assert "telefono" in errores

    @pytest.mark.parametrize("telefono", ["12345678", "912345678", "+56912345678", "56 9 1234 5678"])
    def test_telefono_valido(self, telefono):
        _, errores = validar_postulante(datos_validos(telefono=telefono), CARRERAS)
        assert "telefono" not in errores

    def test_un_solo_telefono(self):
        _, errores = validar_postulante(
            datos_validos(telefono="+56912345678, +56987654321"), CARRERAS
        )
        assert "telefono" in errores

    @pytest.mark.parametrize("correo", ["juan", "juan@", "@gmail.com", "juan@gmail", "ju an@gmail.com"])
    def test_correo_invalido(self, correo):
        _, errores = validar_postulante(datos_validos(correo_personal=correo), CARRERAS)
        assert "correo_personal" in errores

    def test_carrera_inexistente_o_inactiva(self):
        _, errores = validar_postulante(datos_validos(carrera1="XYZ"), CARRERAS)
        assert "carrera1" in errores

    def test_carrera3_requiere_carrera2(self):
        _, errores = validar_postulante(datos_validos(carrera3="KIN"), CARRERAS)
        assert "carrera3" in errores

    def test_carreras_distintas(self):
        _, errores = validar_postulante(datos_validos(carrera2="ENF"), CARRERAS)
        assert "carrera2" in errores

    def test_tres_carreras_validas(self):
        limpios, errores = validar_postulante(
            datos_validos(carrera2="kin", carrera3="PSI"), CARRERAS
        )
        assert errores == {}
        assert (limpios["carrera1"], limpios["carrera2"], limpios["carrera3"]) == ("ENF", "KIN", "PSI")


class TestEnmascarar:
    def test_nombre(self):
        assert enmascarar_nombre("Juan Pérez") == "Ju*** Pé***"

    def test_nombre_corto(self):
        assert enmascarar_nombre("Li") == "Li***"

    def test_nombre_vacio(self):
        assert enmascarar_nombre(None) is None

    def test_telefono_movil_chileno(self):
        assert enmascarar_telefono("+56912345678") == "+569 ****5678"

    def test_telefono_otro_formato(self):
        assert enmascarar_telefono("221234567") == "****4567"

    def test_correo(self):
        assert enmascarar_correo("juan.perez@gmail.com") == "j***@gmail.com"

    def test_enmascarar_postulante_completo_mantiene_carreras(self):
        datos = {
            "rut": "12345678-5",
            "nombres": "Juan Andrés",
            "apellidos": "Pérez Soto",
            "telefono": "+56912345678",
            "correo_personal": "juan@gmail.com",
            "carrera1": "ENF",
            "carrera2": "KIN",
            "carrera3": None,
        }
        resultado = enmascarar(datos)
        assert resultado == {
            "rut": "12345678-5",
            "nombres": "Ju*** An***",
            "apellidos": "Pé*** So***",
            "telefono": "+569 ****5678",
            "correo_personal": "j***@gmail.com",
            "carrera1": "ENF",
            "carrera2": "KIN",
            "carrera3": None,
        }
        # Ningún valor completo debe quedar en la respuesta.
        texto = str(resultado)
        for valor in ("Juan", "Pérez", "12345678 ", "juan@gmail.com", "+56912345678"):
            assert valor not in texto


class TestCamposPendientes:
    def test_sin_pendientes(self):
        datos = {
            "nombres": "Juan", "apellidos": "Pérez", "telefono": "+56912345678",
            "correo_personal": "j@x.cl", "carrera1": "ENF", "carrera2": "KIN", "carrera3": "PSI",
        }
        assert campos_pendientes(datos) == []

    def test_obligatorios_vacios_y_carreras_opcionales(self):
        datos = {
            "nombres": "Juan", "apellidos": "", "telefono": None,
            "correo_personal": "j@x.cl", "carrera1": "ENF", "carrera2": None, "carrera3": None,
        }
        assert campos_pendientes(datos) == ["apellidos", "telefono", "carrera2", "carrera3"]
