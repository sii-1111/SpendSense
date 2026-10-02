import unittest

from narration import load_csv


class ExcelExportHeaderTests(unittest.TestCase):
    def test_finds_header_after_report_rows_and_parses_bank_aliases(self):
        statement = """Account statement
Generated for account ending 1234
Transaction Date,Transaction Details,Withdrawal (Dr),Deposit (Cr)
2026-09-01,UPI/DR/123/SHOP/YESB/shop@okhdfcbank/order,125.50,
2026-09-02,NEFT CR-HDFC0000123/ACME TECHNOLOGIES,,4000.00
"""

        transactions = load_csv(statement)

        self.assertEqual(len(transactions), 2)
        self.assertEqual(transactions[0]["date"], "2026-09-01")
        self.assertEqual(transactions[0]["amount"], 125.5)
        self.assertEqual(transactions[0]["direction"], "debit")
        self.assertEqual(transactions[1]["direction"], "credit")

    def test_parses_excel_date_serials(self):
        statement = "Date,Description,Amount,Type\n46204,POS UBER INDIA,125.50,DR\n"

        transactions = load_csv(statement)

        self.assertEqual(transactions[0]["date"], "2026-07-01")


if __name__ == "__main__":
    unittest.main()
