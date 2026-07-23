import unittest

from ap_control_tower.extraction.comparator import ACIERTO, compare_document
from ap_control_tower.extraction.schema import empty_document


class ExtractionComparatorTests(unittest.TestCase):
    def test_ids_ignore_spaces_and_hyphens_consistently(self) -> None:
        golden = empty_document()
        extracted = empty_document()
        golden.update({
            "proveedor_tax_id": "B-85902583",
            "numero_factura": "A-0001-00000001",
            "iban": "ES16 0081 5249 1100 0107 3717",
        })
        extracted.update({
            "proveedor_tax_id": "B85902583",
            "numero_factura": "A000100000001",
            "iban": "ES1600815249110001073717",
        })

        outcomes = {
            result.field: result.outcome
            for result in compare_document("doc", extracted, golden)
        }

        self.assertEqual(outcomes["proveedor_tax_id"], ACIERTO)
        self.assertEqual(outcomes["numero_factura"], ACIERTO)
        self.assertEqual(outcomes["iban"], ACIERTO)


if __name__ == "__main__":
    unittest.main()
