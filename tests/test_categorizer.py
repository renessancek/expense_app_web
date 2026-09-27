import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from categorizer import Categorizer


class TestCategorizer(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.rules_path = os.path.join(self.test_dir, "rules.json")
        self.categorizer = Categorizer(rules_path=self.rules_path)

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    def test_unknown_description_uses_fallback(self):
        self.assertEqual(self.categorizer.suggest_category("unknown vendor xyz"), "Sonstiges")

    def test_rule_matches_case_insensitively(self):
        self.categorizer.add_rule(["REWE"], "Supermarkt")
        self.assertEqual(self.categorizer.suggest_category("rewe sagt danke"), "Supermarkt")

    def test_rule_uses_word_boundaries(self):
        self.categorizer.add_rule(["net"], "Internet")
        self.assertEqual(self.categorizer.suggest_category("NETTO DISCOUNT"), "Sonstiges")
        self.assertEqual(self.categorizer.suggest_category("pay via net transfer"), "Internet")

    def test_update_and_delete_rule_take_effect_immediately(self):
        self.categorizer.add_rule(["rewe"], "Supermarkt")
        self.assertTrue(self.categorizer.update_rule_keywords("Supermarkt", ["aldi"]))
        self.assertEqual(self.categorizer.suggest_category("REWE"), "Sonstiges")
        self.assertEqual(self.categorizer.suggest_category("ALDI SUED"), "Supermarkt")
        self.categorizer.delete_rule("Supermarkt")
        self.assertEqual(self.categorizer.suggest_category("ALDI SUED"), "Sonstiges")

    def test_rename_category_updates_rule(self):
        self.categorizer.add_rule(["rewe"], "Supermarkt")
        self.assertTrue(self.categorizer.rename_category("Supermarkt", "Groceries"))
        self.assertEqual(self.categorizer.suggest_category("REWE"), "Groceries")

    def test_rename_category_rejects_empty_and_same_name(self):
        self.categorizer.add_rule(["rewe"], "Supermarkt")
        with self.assertRaises(ValueError):
            self.categorizer.rename_category("Supermarkt", "   ")
        self.assertFalse(self.categorizer.rename_category("Supermarkt", "Supermarkt"))
        self.assertFalse(self.categorizer.rename_category("Missing", "Other"))
        self.assertEqual(self.categorizer.suggest_category("REWE"), "Supermarkt")

    def test_rename_category_merges_into_existing(self):
        self.categorizer.add_rule(["rewe"], "Supermarkt")
        self.categorizer.add_rule(["aldi"], "Lebensmittel")
        self.assertTrue(self.categorizer.rename_category("Supermarkt", "Lebensmittel"))
        categories = [rule["category"] for rule in self.categorizer.rules]
        self.assertEqual(categories.count("Lebensmittel"), 1)
        self.assertNotIn("Supermarkt", categories)
        self.assertEqual(self.categorizer.suggest_category("REWE"), "Lebensmittel")
        self.assertEqual(self.categorizer.suggest_category("ALDI SUED"), "Lebensmittel")
        food = next(rule for rule in self.categorizer.rules if rule["category"] == "Lebensmittel")
        self.assertEqual(sorted(food["keywords"]), ["aldi", "rewe"])

    def test_import_uses_rules_and_discards_legacy_mappings(self):
        self.categorizer.import_rules({
            "mappings": {"amazon": "Shopping"},
            "rules": [{"category": "Groceries", "keywords": ["rewe"]}],
        })
        self.assertEqual(self.categorizer.suggest_category("REWE"), "Groceries")
        self.assertEqual(self.categorizer.suggest_category("AMAZON PRIME"), "Sonstiges")
        with open(self.rules_path, encoding="utf-8") as rules_file:
            self.assertEqual(json.load(rules_file), {"rules": [{"category": "Groceries", "keywords": ["rewe"]}]})

    def _write_import(self, name, content, encoding="utf-8"):
        path = os.path.join(self.test_dir, name)
        with open(path, "w", encoding=encoding, newline="") as handle:
            handle.write(content)
        return path

    def test_import_csv_quoted_keywords_from_sheets(self):
        path = self._write_import(
            "rules.csv",
            "category,keywords\nSupermarkt,\"rewe, aldi, lidl\"\nAmazon,amazon\n",
        )
        self.categorizer.import_rules_from_path(path)
        self.assertEqual(self.categorizer.suggest_category("REWE sagt danke"), "Supermarkt")
        self.assertEqual(self.categorizer.suggest_category("ALDI SUED"), "Supermarkt")
        self.assertEqual(self.categorizer.suggest_category("AMAZON PRIME"), "Amazon")

    def test_import_csv_wide_columns_and_long_format(self):
        path = self._write_import(
            "rules.csv",
            "category,keyword,extra\nSupermarkt,rewe,aldi\nSupermarkt,lidl,\nAmazon,amzn,\n",
        )
        self.categorizer.import_rules_from_path(path)
        self.assertEqual(
            self.categorizer.rules,
            [
                {"category": "Supermarkt", "keywords": ["rewe", "aldi", "lidl"]},
                {"category": "Amazon", "keywords": ["amzn"]},
            ],
        )

    def test_import_german_semicolon_csv_with_kategorie_header(self):
        path = self._write_import(
            "regeln.csv",
            "Kategorie;Stichwörter\nSupermarkt;rewe, aldi\nVersicherung;allianz\n",
        )
        self.categorizer.import_rules_from_path(path)
        self.assertEqual(self.categorizer.suggest_category("rewe markt"), "Supermarkt")
        self.assertEqual(self.categorizer.suggest_category("ALLIANZ VERS"), "Versicherung")

    def test_import_headerless_csv_and_json_file(self):
        csv_path = self._write_import("rules.csv", "Supermarkt,rewe,aldi\n")
        self.categorizer.import_rules_from_path(csv_path)
        self.assertEqual(self.categorizer.suggest_category("ALDI"), "Supermarkt")

        json_path = self._write_import(
            "rules.json",
            json.dumps({"rules": [{"category": "Internet", "keywords": ["net"]}]}),
        )
        self.categorizer.import_rules_from_path(json_path)
        self.assertEqual(self.categorizer.suggest_category("pay via net transfer"), "Internet")
        self.assertEqual(self.categorizer.suggest_category("REWE"), "Sonstiges")

    def test_import_csv_rejects_empty_keyword_table(self):
        path = self._write_import("empty.csv", "category,keywords\n,\n")
        with self.assertRaises(ValueError):
            self.categorizer.import_rules_from_path(path)


if __name__ == "__main__":
    unittest.main()
