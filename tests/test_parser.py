import io
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from parser import Parser

class TestParser(unittest.TestCase):

    def setUp(self):
        self.parser = Parser()

    @patch('parser.pd')
    def test_parse_bank_statement_failed_load(self, mock_pd):
        # Mock read_csv to always raise Exception
        mock_pd.read_csv.side_effect = Exception("Failed")
        file_input = MagicMock()

        with self.assertLogs("parser", level="WARNING"):
            result = self.parser.parse_bank_statement(file_input)
        self.assertEqual(result, [])

    @patch('parser.pd')
    def test_parse_bank_statement_missing_columns(self, mock_pd):
        mock_df = MagicMock()
        mock_df.columns = ['Unknown1', 'Unknown2']
        mock_pd.read_csv.return_value = mock_df

        file_input = MagicMock()
        with self.assertLogs("parser", level="WARNING"):
            result = self.parser.parse_bank_statement(file_input)
        self.assertEqual(result, [])

    @patch('parser.pd')
    def test_parse_bank_statement_success(self, mock_pd):
        mock_df = MagicMock()
        # Ensure it passes the amount column check in the heuristic
        mock_columns = MagicMock()
        mock_columns.__iter__.return_value = ['Datum', 'Name', 'Betrag']
        mock_columns.__contains__.side_effect = lambda col: col in ['Datum', 'Name', 'Betrag']

        def mock_get_loc(col):
            return ['Datum', 'Name', 'Betrag'].index(col)
        mock_columns.get_loc = mock_get_loc

        mock_df.columns = mock_columns

        # Mock itertuples
        # row: (Datum, Name, Betrag)
        row1 = ('2023-01-01', 'Test Transaction 1', '1.234,56')
        row2 = ('2023-01-02', 'Test Transaction 2', '-50,00')
        row3 = ('2023-01-03', 'General Currency Conversion', '10.0') # Should be excluded
        row4 = ('2023-01-04', float('nan'), '100.0') # nan description

        mock_df.itertuples.return_value = [row1, row2, row3, row4]

        # Mock isna
        def mock_isna(val):
            if isinstance(val, float) and str(val) == 'nan':
                return True
            return False
        mock_pd.isna.side_effect = mock_isna

        mock_pd.read_csv.return_value = mock_df

        file_input = MagicMock()
        result = self.parser.parse_bank_statement(file_input)

        self.assertEqual(len(result), 1) # Positive amounts and excluded rows are ignored

        tx2 = result[0]
        self.assertEqual(tx2['date'], '2023-01-02')
        self.assertEqual(tx2['description'], 'Test Transaction 2')
        self.assertEqual(tx2['amount'], -50.00)

    def test_parse_amount(self):
        test_cases = [
            ("1.234,56", 1234.56),
            ("1,234.56", 1234.56),
            ("1234,56", 1234.56),
            ("1234.56", 1234.56),
            ("1,234", 1234.0),
            ("123,45", 123.45),
            ("-1.234,56", -1234.56),
            ("  10.0  ", 10.0),
            (100, 100.0),
            (float('nan'), 0.0),
            ("", 0.0),
        ]
        for val, expected in test_cases:
            with self.subTest(val=val):
                self.assertEqual(self.parser._parse_amount(val), expected)

    def test_import_report_counts_imported_and_skipped_rows(self):
        csv_data = io.StringIO(
            "Datum;Name;Betrag\n"
            "2023-01-01;Groceries;-12,50\n"
            "2023-01-02;Salary;100,00\n"
            "2023-01-03;General Authorization;-5,00\n"
            ";Missing date;-3,00\n"
        )

        transactions, report = self.parser.parse_bank_statement_with_report(csv_data)

        self.assertEqual(len(transactions), 1)
        self.assertEqual(report['rows_read'], 4)
        self.assertEqual(report['imported_expenses'], 1)
        self.assertEqual(report['skipped_non_expenses'], 1)
        self.assertEqual(report['skipped_excluded'], 1)
        self.assertEqual(report['skipped_missing_data'], 1)

    def test_unreadable_csv_sets_report_details_and_warns(self):
        csv_data = io.StringIO("not a bank statement\njust text\n")

        with self.assertLogs("parser", level="WARNING") as captured:
            transactions, report = self.parser.parse_bank_statement_with_report(csv_data)

        self.assertEqual(transactions, [])
        self.assertEqual(report["status"], "Not imported")
        self.assertIn("Could not read a supported CSV format", report["details"])
        self.assertTrue(any("Could not parse CSV" in line for line in captured.output))

    def test_missing_columns_listed_in_report_details(self):
        csv_data = io.StringIO("Foo;Bar;Betrag\nshop;-; -12,50\n")

        with self.assertLogs("parser", level="WARNING") as captured:
            transactions, report = self.parser.parse_bank_statement_with_report(csv_data)

        self.assertEqual(transactions, [])
        self.assertEqual(report["status"], "Not imported")
        self.assertIn("Required columns are missing", report["details"])
        self.assertIn("Foo", report["details"])
        self.assertTrue(any("Required columns are missing" in line for line in captured.output))

    def test_successful_load_logs_separator_at_debug(self):
        csv_data = io.StringIO("Datum;Name;Betrag\n2023-01-01;Shop;-1,00\n")

        with self.assertLogs("parser", level="DEBUG") as captured:
            transactions, report = self.parser.parse_bank_statement_with_report(csv_data)

        self.assertEqual(len(transactions), 1)
        self.assertEqual(report["status"], "Imported")
        self.assertTrue(
            any("separator=';'" in line and "encoding=utf-8" in line for line in captured.output)
        )

    def test_bank_headerless_csv_uses_fixed_headers(self):
        csv_data = io.StringIO(
            '123456;Supermarket;2023-01-01;2023-01-01;-12,50;EUR\n'
            '123456;Salary;2023-01-02;2023-01-02;100,00;EUR\n'
        )
        csv_data.name = 'bank_Umsatzliste_20260720_1920.csv'

        transactions, report = self.parser.parse_bank_statement_with_report(csv_data)

        self.assertEqual(report['File'], 'bank_Umsatzliste_20260720_1920.csv')
        self.assertEqual(report['rows_read'], 2)
        self.assertEqual(report['imported_expenses'], 1)
        self.assertEqual(transactions[0]['date'], '2023-01-01')
        self.assertEqual(transactions[0]['description'], 'Supermarket')
        self.assertEqual(transactions[0]['amount'], -12.50)

if __name__ == '__main__':
    unittest.main()
