"""Tests del checksum de identificadores fiscales espanoles.

Los identificadores de estos tests son SINTETICOS a proposito: los casos que
motivaron el validador salieron de facturas reales de un cliente, y los CIF de
sus proveedores no se commitean. Se conserva la forma del error, no el dato.
"""

from __future__ import annotations

import unittest

from ap_control_tower.extraction.tax_id import (
    is_valid_cif,
    is_valid_nie,
    is_valid_nif,
    is_valid_spanish_tax_id,
    looks_spanish_tax_id,
    normalize_tax_id,
    tax_id_warning,
    tiene_forma_de_identificador,
)


class SpanishTaxIdTests(unittest.TestCase):
    def test_cif_validos(self):
        for cif in ("B12345674", "B00000000", "B11111119", "A22222228", "B65410011"):
            self.assertTrue(is_valid_cif(cif), cif)

    def test_caso_que_motivo_el_validador(self):
        """Un motor local duplico la letra inicial: 'BX...' donde iba 'B<digito>...'.

        Un solo caracter cambiado pasa cualquier revision visual y termina en un
        pago mal imputado, asi que tiene que saltar solo.
        """
        self.assertTrue(is_valid_cif("B12345674"))
        self.assertFalse(is_valid_spanish_tax_id("BB2345674"))
        warning = tax_id_warning("proveedor_tax_id", "BB2345674")
        self.assertIsNotNone(warning)
        self.assertIn("BB2345674", warning)
        self.assertIn("checksum", warning)

    def test_cif_con_digito_de_control_cambiado(self):
        self.assertTrue(is_valid_cif("B12345674"))
        for malo in ("B12345670", "B12345671", "B12345678"):
            self.assertFalse(is_valid_cif(malo), malo)

    def test_prefijo_es_intracomunitario(self):
        self.assertEqual(normalize_tax_id("ES B12345674"), "B12345674")
        self.assertTrue(is_valid_spanish_tax_id("ESB12345674"))
        self.assertFalse(is_valid_spanish_tax_id("ESB12345678"))

    def test_nif_persona_fisica(self):
        self.assertTrue(is_valid_nif("12345678Z"))
        self.assertFalse(is_valid_nif("12345678A"))

    def test_nie(self):
        self.assertTrue(is_valid_nie("X1234567L"))
        self.assertFalse(is_valid_nie("X1234567A"))

    def test_cif_con_letra_de_control(self):
        # P/Q/R/S/N/W/K exigen letra de control, no digito
        self.assertTrue(is_valid_cif("Q2826000H"))
        self.assertFalse(is_valid_cif("Q28260000"))

    def test_identificadores_extranjeros_no_generan_warning(self):
        """No se castiga lo que no puede ser espanol: distinta longitud."""
        for extranjero in ("NL123456789B01", "FR12345678901", "DE123456789"):
            self.assertFalse(looks_spanish_tax_id(extranjero), extranjero)
            self.assertIsNone(tax_id_warning("proveedor_tax_id", extranjero), extranjero)

    def test_vacios_no_generan_warning(self):
        for valor in (None, "", "   "):
            self.assertIsNone(tax_id_warning("proveedor_tax_id", valor), repr(valor))

    def test_valores_que_no_son_identificadores(self):
        """Forma de las salidas reales de un motor local: texto en vez de un ID."""
        for valor in ("NUNCA", "ALIMENTOS Y BEBIDAS S.L.U.", "S.L."):
            self.assertFalse(tiene_forma_de_identificador(valor), valor)
            warning = tax_id_warning("proveedor_tax_id", valor)
            self.assertIsNotNone(warning, repr(valor))
            self.assertIn("no tiene forma de identificador fiscal", warning)

    def test_prefijo_es_con_longitud_incorrecta(self):
        warning = tax_id_warning("proveedor_tax_id", "ESB2345674")
        self.assertIsNotNone(warning)
        self.assertIn("prefijo ES", warning)

    def test_id_valido_no_genera_warning(self):
        for valido in ("B12345674", "ESB12345674", "12345678Z", "X1234567L"):
            self.assertIsNone(tax_id_warning("proveedor_tax_id", valido), valido)


if __name__ == "__main__":
    unittest.main()
