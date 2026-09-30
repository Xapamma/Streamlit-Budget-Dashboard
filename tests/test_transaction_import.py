import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

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
    FINAL_MAIN_CATEGORY_OPTIONS,
    complete_missing_categories,
    format_final_export,
    get_final_export_row_issues,
    get_exclusion_reason,
    make_transfer_review_rows,
    merchant_needs_review,
    normalize_account_type,
    normalize_export_filename,
    restored_rows_to_standardized,
    split_filtered_transactions,
    validate_final_export_rows,
)
from merchant_assistance import (
    approve_merchant_match,
    ask_ollama_app_help,
    load_merchant_cache,
    merge_merchant_cache_edits,
    save_merchant_cache,
    suggest_merchant_and_category_with_ollama,
)
from bank_profiles import load_bank_profiles, save_bank_profile
from fuzzy_search import fuzzy_match_indices
from dashboard_data import (
    prepare_analytics_transactions,
    spending_for_budget_category,
    summarize_transactions,
    trend_totals,
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

    def test_approved_merchant_is_saved_and_no_longer_needs_ai_review(self):
        cache_path = self.temp_path / "merchant_cache.json"
        description = "ACME MARKET 1234"
        merchant = "Acme Market"

        cache_key = approve_merchant_match(description, merchant, cache_path=cache_path)

        import json
        cache = json.loads(cache_path.read_text(encoding="utf-8"))
        self.assertEqual(cache[cache_key], merchant)
        self.assertFalse(merchant_needs_review(description, merchant, cache))

    def test_cache_manager_updates_and_deletes_entries(self):
        cache_path = self.temp_path / "merchant_cache.json"
        save_merchant_cache({"coffee shop": "Coffee Shop", "old store": "Old Store"}, cache_path)
        save_merchant_cache({"coffee shop": "New Coffee Name"}, cache_path)

        self.assertEqual(load_merchant_cache(cache_path), {"coffee shop": "New Coffee Name"})

    def test_cache_manager_rejects_blank_entries(self):
        with self.assertRaisesRegex(ValueError, "cannot be blank"):
            save_merchant_cache({"  ": "Merchant"}, self.temp_path / "merchant_cache.json")

    def test_bank_profile_is_saved_and_updated(self):
        profile_path = self.temp_path / "bank_profiles.json"
        initial_profile = {
            "delimiter": "Semicolon (;)",
            "mapping": {"date": "Activity Date", "amount": "Net"},
        }

        save_bank_profile("New Bank", initial_profile, profile_path)
        save_bank_profile(
            "New Bank",
            {**initial_profile, "mapping": {"date": "Date", "amount": "Amount"}},
            profile_path,
        )

        profiles = load_bank_profiles(profile_path)
        self.assertEqual(profiles["New Bank"]["mapping"], {"date": "Date", "amount": "Amount"})
        self.assertEqual(profiles["New Bank"]["delimiter"], "Semicolon (;)")

    def test_fuzzy_search_ignores_case_and_finds_typos(self):
        values = ["Coffee Place", "KRAZY Plant Shop 34987"]

        self.assertEqual(fuzzy_match_indices("krazy plnt", values), [1])
        self.assertEqual(fuzzy_match_indices("COFFEE", values), [0])
        self.assertEqual(fuzzy_match_indices("", values), [0, 1])

    def test_cache_search_edits_preserve_hidden_entries(self):
        current_cache = {"coffee shop": "Coffee Shop", "electric bill": "Power Utility"}

        revised = merge_merchant_cache_edits(
            current_cache,
            {"coffee shop"},
            [("coffee shop", "New Coffee Name")],
        )
        deleted = merge_merchant_cache_edits(current_cache, {"coffee shop"}, [])

        self.assertEqual(revised, {"electric bill": "Power Utility", "coffee shop": "New Coffee Name"})
        self.assertEqual(deleted, {"electric bill": "Power Utility"})

    def test_ollama_category_suggestion_uses_only_allowed_category_pairs(self):
        response = {
            "message": {
                "content": json.dumps(
                    {
                        "merchant": "Krazy Plant Shop",
                        "main_category": "Shopping & Supplies",
                        "sub_category": "General Retail",
                    }
                )
            }
        }
        fake_ollama = SimpleNamespace(chat=lambda **_: response)
        allowed = {"Shopping & Supplies": ["General Retail"]}

        with patch.dict("sys.modules", {"ollama": fake_ollama}):
            suggestion = suggest_merchant_and_category_with_ollama(
                "UNRECOGNIZED XYZ", -19.0, allowed
            )

        self.assertEqual(
            suggestion,
            {
                "merchant": "Krazy Plant Shop",
                "main_category": "Shopping & Supplies",
                "sub_category": "General Retail",
            },
        )

    def test_ollama_category_suggestion_rejects_unknown_pairs(self):
        response = {
            "message": {
                "content": json.dumps(
                    {
                        "merchant": "Krazy Plant Shop",
                        "main_category": "Shopping & Supplies",
                        "sub_category": "Unlisted Category",
                    }
                )
            }
        }
        calls = []

        def mock_chat(**_):
            calls.append(True)
            return response

        fake_ollama = SimpleNamespace(chat=mock_chat)

        with patch.dict("sys.modules", {"ollama": fake_ollama}):
            suggestion = suggest_merchant_and_category_with_ollama(
                "UNRECOGNIZED XYZ", -19.0, {"Shopping & Supplies": ["General Retail"]}
            )

        self.assertEqual(len(calls), 5)
        self.assertEqual(suggestion["merchant"], "Krazy Plant Shop")
        self.assertEqual(suggestion["main_category"], "General Spending")
        self.assertEqual(suggestion["sub_category"], "Other")
        self.assertIn("invalid category after 5 attempts", suggestion["category_warning"])

    def test_ollama_category_suggestion_recovers_on_fifth_attempt(self):
        valid_response = {
            "message": {
                "content": json.dumps(
                    {
                        "merchant": "Krazy Plant Shop",
                        "main_category": "Shopping & Supplies",
                        "sub_category": "General Retail",
                    }
                )
            }
        }
        responses = iter(
            [
                {"message": {"content": "not json"}},
                {"message": {"content": "not json"}},
                {"message": {"content": "not json"}},
                {"message": {"content": "not json"}},
                valid_response,
            ]
        )
        calls = []

        def mock_chat(**_):
            calls.append(True)
            return next(responses)

        with patch.dict("sys.modules", {"ollama": SimpleNamespace(chat=mock_chat)}):
            suggestion = suggest_merchant_and_category_with_ollama(
                "UNRECOGNIZED XYZ", -19.0, {"Shopping & Supplies": ["General Retail"]}
            )

        self.assertEqual(len(calls), 5)
        self.assertEqual(suggestion["sub_category"], "General Retail")
        self.assertNotIn("category_warning", suggestion)

    def test_ollama_category_suggestion_explains_missing_merchant(self):
        response = {"message": {"content": json.dumps({"merchant": ""})}}
        calls = []

        def mock_chat(**_):
            calls.append(True)
            return response

        with patch.dict("sys.modules", {"ollama": SimpleNamespace(chat=mock_chat)}):
            with self.assertRaisesRegex(ValueError, "after 5 attempts"):
                suggest_merchant_and_category_with_ollama(
                    "UNRECOGNIZED XYZ", -19.0, {"Shopping & Supplies": ["General Retail"]}
                )
        self.assertEqual(len(calls), 5)

    def test_app_help_chat_uses_only_bounded_help_history(self):
        captured = {}
        fake_ollama = SimpleNamespace(
            chat=lambda **kwargs: (
                captured.update(kwargs)
                or {"message": {"content": "Choose your bank and map the columns."}}
            )
        )
        history = [
            {"role": "user" if index % 2 == 0 else "assistant", "content": f"help turn {index}"}
            for index in range(10)
        ]

        with patch.dict("sys.modules", {"ollama": fake_ollama}):
            answer = ask_ollama_app_help("How do I import a file?", history)

        sent_messages = captured["messages"]
        self.assertEqual(answer, "Choose your bank and map the columns.")
        self.assertEqual(len(sent_messages), 10)
        self.assertEqual(sent_messages[0]["role"], "system")
        self.assertEqual(sent_messages[1]["content"], "help turn 2")
        self.assertEqual(sent_messages[-1]["content"], "How do I import a file?")

    def test_analytics_data_handles_dates_signed_amounts_and_budget_categories(self):
        export_rows = pd.DataFrame(
            {
                "date": ["01/15/2026", "02/02/2026", "bad date"],
                "merchant": ["Market", "Paycheck", "Invalid"],
                "amount": [-50.0, 100.0, -10.0],
                "main_category": ["Food & Dining", "Income", "Food & Dining"],
                "sub_category": ["Groceries", "Paychecks", "Groceries"],
            }
        )

        transactions = prepare_analytics_transactions(export_rows)
        totals = summarize_transactions(transactions)
        monthly = trend_totals(transactions, "M")

        self.assertEqual(len(transactions), 2)
        self.assertEqual(totals["income"], 100.0)
        self.assertEqual(totals["spending"], 50.0)
        self.assertEqual(totals["net_savings"], 50.0)
        self.assertEqual(monthly.loc[pd.Timestamp("2026-01-01"), "spending"], 50.0)
        self.assertEqual(
            spending_for_budget_category(transactions, "Food & Dining :: Groceries"), 50.0
        )

    def test_known_rule_matches_do_not_need_ai_review(self):
        self.assertFalse(merchant_needs_review("WALMART SUPERCENTER #123", "Walmart Supercenter", {}))
        self.assertTrue(merchant_needs_review("ACME MARKET 1234", "Acme Market 1234", {}))

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

    def test_ai_merchant_alias_uses_canonical_merchant_category(self):
        transactions = pd.DataFrame(
            [{"merchant": "Costco", "amount": -20.0, "description": "Unmatched transaction"}]
        )

        categorized = add_categories(transactions)

        self.assertEqual(categorized.loc[0, "main_category"], "Food & Dining")
        self.assertEqual(categorized.loc[0, "sub_category"], "Groceries")

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
        self.assertNotIn("Other Income", FINAL_MAIN_CATEGORY_OPTIONS)
        self.assertIn("Income :: Other Income", CATEGORY_PAIR_OPTIONS)
        self.assertIn("Transfer", FINAL_MAIN_CATEGORY_OPTIONS)
        self.assertEqual(CATEGORY_LABEL_TO_PAIR["Transfer :: N/A"], ("Transfer", "N/A"))

    def test_transfer_with_na_subcategory_is_a_valid_export_category(self):
        transaction = pd.DataFrame(
            [["06/01/2026", "Transfer", "Internal Transfer", "debit", -20.0, "Transfer", "N/A", "sofi", "checking"]],
            columns=FINAL_EXPORT_COLUMNS,
        )

        _, error = validate_final_export_rows(transaction)

        self.assertIsNone(error)

    def test_transfer_category_sets_na_and_builds_excluded_review_row(self):
        transactions = pd.DataFrame(
            [["06/01/2026", "Moved money", "Bank Transfer", "debit", -50.0, "Transfer", "Fuel", "sofi", "checking"]],
            columns=FINAL_EXPORT_COLUMNS,
        )

        completed = complete_missing_categories(
            transactions.assign(sub_category=None)
        )
        review = make_transfer_review_rows(
            completed,
            source_files=pd.Series(["statement.csv"]),
            source_rows=pd.Series([12]),
        )

        self.assertEqual(completed.loc[0, "sub_category"], "N/A")
        self.assertEqual(review.loc[0, "main_category"], "Transfer")
        self.assertEqual(review.loc[0, "sub_category"], "N/A")
        self.assertEqual(review.loc[0, "removal_reason"], "Manually categorized as Transfer.")
        self.assertEqual(review.loc[0, "source_file"], "statement.csv")
        self.assertEqual(review.loc[0, "source_row"], 12)

    def test_unclassified_positive_income_uses_income_main_category(self):
        categorized = add_categories(
            pd.DataFrame(
                [{"merchant": "Unknown Merchant", "amount": 25.0, "description": "Deposit"}]
            )
        )

        self.assertEqual(categorized.loc[0, "main_category"], "Income")
        self.assertEqual(categorized.loc[0, "sub_category"], "Other Income")

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