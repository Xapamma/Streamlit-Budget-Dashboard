import tempfile
import unittest
from pathlib import Path

import pandas as pd

from cleaning_logic import add_categories
from transaction_import import (
    ImportFormatError,
    STANDARD_COLUMNS,
    build_import_signature,
    load_transactions,
    remove_source_rows,
    replace_source_rows,
)
from final_export import (
    CATEGORY_LABEL_TO_PAIR,
    CATEGORY_PAIR_OPTIONS,
    FINAL_EXPORT_COLUMNS,
    complete_missing_categories,
    format_final_export,
    get_final_export_row_issues,
    get_exclusion_reason,
    normalize_account_type,
    normalize_export_filename,
    restored_rows_to_standardized,
    split_filtered_transactions,
    validate_final_export_rows,
)


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

    def test_file_rows_can_be_removed_and_replaced_by_filename(self):
        existing = pd.DataFrame(
            {"source_file": ["bank_a.csv", "bank_b.csv"], "amount": [1.0, 2.0]}
        )
        replacement = pd.DataFrame({"source_file": ["bank_a.csv"], "amount": [3.0]})

        replaced = replace_source_rows(
            existing,
            replacement,
            source_column="source_file",
            source_name="bank_a.csv",
        )
        removed = remove_source_rows(
            replaced,
            source_column="source_file",
            source_names={"bank_a.csv"},
        )

        self.assertEqual(replaced.to_dict("records"), [
            {"source_file": "bank_b.csv", "amount": 2.0},
            {"source_file": "bank_a.csv", "amount": 3.0},
        ])
        self.assertEqual(removed["source_file"].tolist(), ["bank_b.csv"])

    def test_import_signature_changes_when_file_settings_change(self):
        base = {
            "content_digest": "same-content",
            "bank": "New Bank",
            "account": "checking",
            "column_mapping": {"date": "Date", "description": "Description", "amount": "Amount"},
            "delimiter": ",",
            "decimal": ".",
            "thousands": ",",
            "positive_amounts_are_debits": False,
        }

        original = build_import_signature(**base)
        same = build_import_signature(**base)
        changed_mapping = build_import_signature(
            **{**base, "column_mapping": {**base["column_mapping"], "amount": "Debit"}}
        )
        changed_number_format = build_import_signature(**{**base, "decimal": ",", "thousands": "."})

        self.assertEqual(original, same)
        self.assertNotEqual(original, changed_mapping)
        self.assertNotEqual(original, changed_number_format)

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

    def test_reversed_signed_amounts_and_declined_rows(self):
        path = self.write_csv(
            "new_bank.csv",
            "Date,Details,Amount,Status\n"
            "2026-06-01,Purchase,24.50,Posted\n"
            "2026-06-02,Refund,-8.00,Posted\n"
            "not-a-date,Declined purchase,not-a-number,Declined\n",
        )
        mapping = {
            "date": "Date",
            "description": "Details",
            "amount": "Amount",
            "status": "Status",
        }

        result = load_transactions(
            path,
            bank="New Bank",
            account="checking",
            column_mapping=mapping,
            positive_amounts_are_debits=True,
        )

        self.assertEqual(result["amount"].tolist(), [-24.5, 8.0])
        self.assertEqual(result["transaction_type"].tolist(), ["debit", "credit"])
        self.assertEqual(result["status"].tolist(), ["Posted", "Posted"])
        self.assertEqual(result["source_row"].tolist(), [2, 3])
        self.assertEqual(result.attrs["declined_rows_dropped"], 1)
        declined = result.attrs["declined_transactions"]
        self.assertEqual(declined["status"].tolist(), ["Declined"])
        self.assertTrue(pd.isna(declined["date"].iloc[0]))
        self.assertTrue(pd.isna(declined["amount"].iloc[0]))
        declined_export = format_final_export(
            declined,
            merchant_cache_path=self.temp_path / "missing.json",
            apply_exclusions=False,
        )
        self.assertEqual(len(declined_export), 1)
        self.assertTrue(pd.isna(declined_export["date"].iloc[0]))
        self.assertTrue(pd.isna(declined_export["amount"].iloc[0]))

    def test_all_declined_rows_produce_an_empty_export_without_crashing(self):
        path = self.write_csv(
            "new_bank.csv",
            "Date,Details,Amount,Status\n"
            "not-a-date,Declined purchase,not-a-number,Declined\n",
        )
        mapping = {"date": "Date", "description": "Details", "amount": "Amount", "status": "Status"}

        imported = load_transactions(
            path,
            bank="New Bank",
            account="checking",
            column_mapping=mapping,
        )
        exported = format_final_export(imported, merchant_cache_path=self.temp_path / "missing.json")

        self.assertTrue(imported.empty)
        self.assertTrue(exported.empty)
        self.assertEqual(imported.attrs["declined_rows_dropped"], 1)

    def test_valid_dropped_row_can_be_converted_back_for_restore(self):
        dropped = pd.DataFrame(
            [["06/01/2026", "Refund from shop", "Shop", "credit", 20.0, "Income", "Refunds", "sofi", "savings"]],
            columns=FINAL_EXPORT_COLUMNS,
        )

        restored, error = restored_rows_to_standardized(
            dropped,
            statuses=pd.Series(["Declined"]),
            source_files=pd.Series(["sofi.csv"]),
            source_rows=pd.Series([4]),
        )

        self.assertIsNone(error)
        self.assertEqual(restored["transaction_type"].tolist(), ["credit"])
        self.assertEqual(restored["amount"].tolist(), [20.0])
        self.assertEqual(restored["status"].tolist(), ["Declined"])
        self.assertEqual(restored["source_row"].tolist(), [4])

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

    def test_payment_rules_report_reasons_and_preserve_known_merchant(self):
        transactions = pd.DataFrame(
            {
                "description": [
                    "CARD PAYMENT",
                    "Payment transfer",
                    "Weekly purchase",
                    "Weekly purchase",
                    "Grandma Becky Payment",
                ],
                "bank_category": ["", "", "", "Payment", ""],
                "status": ["Posted"] * 5,
            }
        )

        included, dropped = split_filtered_transactions(transactions)

        self.assertEqual(included["description"].tolist(), ["Weekly purchase", "Grandma Becky Payment"])
        self.assertEqual(len(dropped), 3)
        self.assertTrue(dropped["removal_reason"].str.contains("payment", case=False).all())
        self.assertIn("Declined", get_exclusion_reason(pd.Series({"status": "Declined"})))

    def test_positive_refund_descriptions_are_categorized_as_income_refunds(self):
        transactions = pd.DataFrame(
            {
                "merchant": ["Unknown Merchant", "Unknown Merchant"],
                "amount": [25.0, -25.0],
                "description": ["Refund from store", "Refund purchase"],
            }
        )

        result = add_categories(transactions)

        self.assertEqual(result.loc[0, "main_category"], "Income")
        self.assertEqual(result.loc[0, "sub_category"], "Refunds")
        self.assertEqual(result.loc[1, "main_category"], "General Spending")

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

    def test_export_filename_is_a_csv_basename(self):
        self.assertEqual(normalize_export_filename("monthly budget"), "monthly budget.csv")
        self.assertEqual(normalize_export_filename("report.csv"), "report.csv")
        self.assertEqual(normalize_export_filename("C:/exports/report.xlsx"), "report.csv")

    def test_editor_rows_validate_and_ignore_completely_blank_rows(self):
        transactions = pd.DataFrame(
            [
                ["6/1/2026", "Coffee", "Cafe", "debit", -4.5, "Food & Dining", "Fast Food", "sofi", "checking"],
                [None] * len(FINAL_EXPORT_COLUMNS),
            ],
            columns=FINAL_EXPORT_COLUMNS,
        )

        result, error = validate_final_export_rows(transactions)

        self.assertIsNone(error)
        self.assertEqual(len(result), 1)
        self.assertEqual(result.loc[0, "date"], "06/01/2026")
        self.assertEqual(result.loc[0, "amount"], -4.5)

    def test_editor_rejects_incomplete_rows(self):
        transactions = pd.DataFrame(
            [["not a date", "Coffee", "Cafe", "debit", -4.5, "", "", "sofi", "checking"]],
            columns=FINAL_EXPORT_COLUMNS,
        )

        result, error = validate_final_export_rows(transactions)

        self.assertEqual(len(result), 1)
        self.assertIn("MM/DD/YYYY", error)
        self.assertIn("Main Category is required", error)

    def test_row_issue_map_identifies_only_invalid_date_row(self):
        transactions = pd.DataFrame(
            [
                ["not-a-date", "Coffee", "Cafe", "debit", -4.5, "Food & Dining", "Fast Food", "sofi", "checking"],
                ["06/02/2026", "Fuel", "Costco Gas", "debit", -45.0, "Transportation", "Fuel", "capital one", "credit card"],
            ],
            columns=FINAL_EXPORT_COLUMNS,
        )

        issues = get_final_export_row_issues(transactions)

        self.assertEqual(list(issues), [0])
        self.assertIn("Date must use MM/DD/YYYY.", issues[0])

    def test_row_issue_disappears_after_date_is_fixed(self):
        transactions = pd.DataFrame(
            [["06/01/2026", "Coffee", "Cafe", "debit", -4.5, "Food & Dining", "Fast Food", "sofi", "checking"]],
            columns=FINAL_EXPORT_COLUMNS,
        )

        self.assertEqual(get_final_export_row_issues(transactions), {})

    def test_editor_rejects_subcategory_outside_selected_main_category(self):
        transactions = pd.DataFrame(
            [["06/01/2026", "Fuel", "Costco Gas", "debit", -45.0, "Food & Dining", "Fuel", "capital one", "credit card"]],
            columns=FINAL_EXPORT_COLUMNS,
        )

        _, error = validate_final_export_rows(transactions)

        self.assertIn("must belong to its selected main category", error)

    def test_subcategory_dropdown_labels_options_with_their_main_category(self):
        self.assertIn("Transportation :: Fuel", CATEGORY_PAIR_OPTIONS)
        self.assertEqual(CATEGORY_LABEL_TO_PAIR["Transportation :: Fuel"], ("Transportation", "Fuel"))
        self.assertNotIn("Food & Dining :: Fuel", CATEGORY_PAIR_OPTIONS)

    def test_missing_category_cells_use_existing_rules_without_overwriting_edits(self):
        transactions = pd.DataFrame(
            [
                ["06/01/2026", "Walmart purchase", "Walmart Supercenter", "debit", -25.0, None, None, "capital one", "credit card"],
                ["06/02/2026", "Custom merchant", "Custom merchant", "debit", -5.0, "Entertainment", "Recreation", "sofi", "checking"],
            ],
            columns=FINAL_EXPORT_COLUMNS,
        )

        result = complete_missing_categories(transactions)

        self.assertEqual(result.loc[0, "main_category"], "Food & Dining")
        self.assertEqual(result.loc[0, "sub_category"], "Groceries")
        self.assertEqual(result.loc[1, "main_category"], "Entertainment")
        self.assertEqual(result.loc[1, "sub_category"], "Recreation")


if __name__ == "__main__":
    unittest.main()