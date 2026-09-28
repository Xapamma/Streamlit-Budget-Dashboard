import tempfile
import unittest
from pathlib import Path

import pandas as pd

from transaction_import import ImportFormatError, STANDARD_COLUMNS, load_transactions
from final_export import FINAL_EXPORT_COLUMNS, format_final_export, normalize_account_type


class LoadTransactionsTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.temp_path = Path(self.temp_dir.name)

    def write_csv(self, filename, content):
        path = self.temp_path / filename
        path.write_text(content, encoding="utf-8")
        return path

    def test_capital_one_split_debits_and_credits(self):
        path = self.write_csv(
            "capital_one_cc.csv",
            "Transaction Date,Posted Date,Description,Debit,Credit,Category\n"
            "2026-01-02,2026-01-03,Coffee,$12.50,,Dining\n"
            "2026-01-04,2026-01-04,Payment,,25.00,Payment\n",
        )

        result = load_transactions(path, bank="capital one")

        self.assertEqual(result.columns.tolist(), STANDARD_COLUMNS)
        self.assertEqual(result["amount"].tolist(), [-12.5, 25.0])
        self.assertEqual(result["transaction_type"].tolist(), ["debit", "credit"])
        self.assertEqual(result["account"].tolist(), ["credit card", "credit card"])
        self.assertEqual(result["bank_category"].tolist(), ["Dining", "Payment"])
        self.assertEqual(result["date"].iloc[0], pd.Timestamp("2026-01-02"))
        self.assertEqual(result["posted_date"].iloc[0], pd.Timestamp("2026-01-03"))

    def test_gwcu_signed_amount_and_optional_transaction_id(self):
        path = self.write_csv(
            "gwcu_checking.csv",
            '"Transaction ID","Effective Date","Posting Date","Amount",'
            '"Description","Type"\n'
            'abc-1,2026-02-01,2026-02-02,"(1,234.56)",Groceries,Card\n'
            'abc-2,2026-02-03,2026-02-03,15.25,Dividend,Dividends\n',
        )

        result = load_transactions(path, bank="gwcu")

        self.assertEqual(result["amount"].tolist(), [-1234.56, 15.25])
        self.assertEqual(result["transaction_type"].tolist(), ["debit", "credit"])
        self.assertEqual(result["transaction_id"].tolist(), ["abc-1", "abc-2"])
        self.assertEqual(result["bank"].unique().tolist(), ["Goldenwest Credit Union"])
        self.assertEqual(result["account"].unique().tolist(), ["checking"])
        self.assertEqual(result["date"].iloc[0], pd.Timestamp("2026-02-02"))
        self.assertEqual(result["posted_date"].iloc[0], pd.Timestamp("2026-02-01"))

    def test_sofi_signed_amount_and_bank_category(self):
        path = self.write_csv(
            "sofi_savings.csv",
            "Date,Description,Type,Amount,Current balance,Status\n"
            "2026-03-01,Interest,INTEREST_EARNED,2.10,50.00,Posted\n"
            "2026-03-02,Transfer,OTHER,-10.00,40.00,Posted\n",
        )

        result = load_transactions(path, bank="sofi")

        self.assertEqual(result["amount"].tolist(), [2.1, -10.0])
        self.assertEqual(result["bank_category"].tolist(), ["INTEREST_EARNED", "OTHER"])
        self.assertEqual(result["account"].unique().tolist(), ["savings"])
        self.assertEqual(result["currency"].tolist(), [None, None])

    def test_new_bank_can_be_added_with_explicit_column_mapping(self):
        path = self.write_csv(
            "new_bank.csv",
            "Activity Date,Details,Outflow,Inflow,Reference\n"
            "2026-04-01,Bookstore,18.75,,ref-1\n"
            "2026-04-02,Paycheck,,900.00,ref-2\n",
        )
        mapping = {
            "date": "Activity Date",
            "description": "Details",
            "debit": "Outflow",
            "credit": "Inflow",
            "transaction_id": "Reference",
        }

        result = load_transactions(
            path,
            bank="New Bank",
            account="checking",
            column_mapping=mapping,
        )

        self.assertEqual(result["bank"].unique().tolist(), ["New Bank"])
        self.assertEqual(result["amount"].tolist(), [-18.75, 900.0])
        self.assertEqual(result["transaction_id"].tolist(), ["ref-1", "ref-2"])
        self.assertEqual(result["source_row"].tolist(), [2, 3])

    def test_custom_delimiter_is_supported(self):
        path = self.write_csv(
            "new_bank.csv",
            "Date;Description;Amount\n2026-05-01;Market;-21,50\n",
        )
        mapping = {"date": "Date", "description": "Description", "amount": "Amount"}

        result = load_transactions(
            path,
            bank="New Bank",
            column_mapping=mapping,
            delimiter=";",
            decimal=",",
            thousands=None,
        )

        self.assertEqual(result["amount"].tolist(), [-21.5])

    def test_unknown_bank_without_mapping_fails_instead_of_guessing(self):
        path = self.write_csv("unknown.csv", "Activity,Details,Value\n2026-01-01,x,2\n")

        with self.assertRaisesRegex(ImportFormatError, "Provide column_mapping"):
            load_transactions(path, bank="New Bank")

    def test_invalid_amount_is_reported_with_csv_row(self):
        path = self.write_csv(
            "sofi_checking.csv",
            "Date,Description,Type,Amount\n2026-01-01,Coffee,OTHER,not-a-number\n",
        )

        with self.assertRaisesRegex(ImportFormatError, r"row\(s\): \[2\]"):
            load_transactions(path, bank="sofi")

    def test_final_export_matches_example_schema_and_labels(self):
        transactions = pd.DataFrame(
            {
                "date": [pd.Timestamp("2026-06-01"), pd.Timestamp("2026-06-02")],
                "description": ["WALMART SUPERCENTER", "PAYROLL DEPOSIT"],
                "amount": [-24.5, 800.0],
                "transaction_type": ["debit", "credit"],
                "bank": ["Capital One", "Goldenwest Credit Union"],
                "account": ["CC", "money market"],
            }
        )

        result = format_final_export(transactions, merchant_cache_path=self.temp_path / "missing.json")

        self.assertEqual(result.columns.tolist(), FINAL_EXPORT_COLUMNS)
        self.assertEqual(result["merchant"].tolist(), ["Walmart Supercenter", "Payroll Deposit"])
        self.assertEqual(result["type"].tolist(), ["debit", "credit"])
        self.assertEqual(result["bank"].tolist(), ["capital one", "goldenwest"])
        self.assertEqual(result["account"].tolist(), ["credit card", "money market"])
        self.assertEqual(result["main_category"].tolist(), ["Food & Dining", "Income"])
        self.assertEqual(result["date"].tolist(), ["06/01/2026", "06/02/2026"])
        self.assertEqual(normalize_account_type("CC"), "credit card")
        self.assertEqual(normalize_account_type("Money market"), "money market")

    def test_final_export_excludes_the_legacy_noise_descriptions(self):
        descriptions = [
            "Transfer to SoFi checking",
            "Payment to Capital One",
            "Coffee Shop",
        ]
        transactions = pd.DataFrame(
            {
                "date": pd.to_datetime(["2026-06-01", "2026-06-02", "2026-06-03"]),
                "description": descriptions,
                "amount": [-10.0, -20.0, -4.5],
                "transaction_type": ["debit", "debit", "debit"],
                "bank": ["SoFi"] * 3,
                "account": ["checking"] * 3,
            }
        )

        result = format_final_export(transactions, merchant_cache_path=self.temp_path / "missing.json")

        self.assertEqual(result["description"].tolist(), ["Coffee Shop"])
        self.assertEqual(result["date"].tolist(), ["06/03/2026"])

    def test_final_export_sorts_dates_ascending(self):
        transactions = pd.DataFrame(
            {
                "date": pd.to_datetime(["2026-06-03", "2026-06-01"]),
                "description": ["Later purchase", "Earlier purchase"],
                "amount": [-3.0, -1.0],
                "transaction_type": ["debit", "debit"],
                "bank": ["SoFi", "SoFi"],
                "account": ["checking", "checking"],
            }
        )

        result = format_final_export(transactions, merchant_cache_path=self.temp_path / "missing.json")

        self.assertEqual(result["date"].tolist(), ["06/01/2026", "06/03/2026"])

    def test_costco_gas_and_wholesale_keep_separate_categories(self):
        transactions = pd.DataFrame(
            {
                "date": pd.to_datetime(["2026-06-01", "2026-06-02"]),
                "description": ["COSTCO GAS #1234", "COSTCO WHSE #5678"],
                "amount": [-45.0, -80.0],
                "transaction_type": ["debit", "debit"],
                "bank": ["Capital One", "Capital One"],
                "account": ["credit card", "credit card"],
            }
        )

        result = format_final_export(transactions, merchant_cache_path=self.temp_path / "missing.json")

        self.assertEqual(result["merchant"].tolist(), ["Costco Gas", "Costco Wholesale"])
        self.assertEqual(result["sub_category"].tolist(), ["Fuel", "Groceries"])


if __name__ == "__main__":
    unittest.main()