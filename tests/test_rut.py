import pytest

from app.services.rut import RutInvalido, calcular_dv, es_valido, normalizar


class TestCalcularDv:
    @pytest.mark.parametrize(
        "cuerpo, dv",
        [
            ("12345678", "5"),
            ("11111111", "1"),
            ("76086428", "5"),
            ("10000013", "K"),
            ("1000005", "K"),
            ("6", "K"),
            ("14", "0"),
        ],
    )
    def test_calcula_digito_verificador_modulo_11(self, cuerpo, dv):
        assert calcular_dv(cuerpo) == dv


class TestNormalizar:
    @pytest.mark.parametrize(
        "entrada",
        ["12.345.678-5", "12345678-5", "123456785", " 12.345.678-5 ", "12 345 678-5"],
    )
    def test_acepta_con_o_sin_puntos_y_guion(self, entrada):
        assert normalizar(entrada) == "12345678-5"

    def test_k_minuscula_se_normaliza_a_mayuscula(self):
        assert normalizar("10.000.013-k") == "10000013-K"

    def test_quita_ceros_a_la_izquierda(self):
        assert normalizar("010000013-K") == "10000013-K"

    @pytest.mark.parametrize(
        "entrada",
        ["", "   ", None, "12345678-4", "abc", "12.345.678-X", "1-", "-5", "1234567890-1", "0-0"],
    )
    def test_rechaza_rut_invalido(self, entrada):
        with pytest.raises(RutInvalido):
            normalizar(entrada)

    def test_acepta_entero_como_en_excel(self):
        # Excel puede entregar el RUT sin DV como número; con DV llega como texto.
        assert normalizar(123456785) == "12345678-5"


class TestEsValido:
    def test_true_para_rut_valido(self):
        assert es_valido("12.345.678-5")

    def test_false_para_rut_invalido(self):
        assert not es_valido("12.345.678-0")
