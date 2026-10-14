"""Layout of the cash book Excel file.

Shared by export and import; kept identical to v1.

Sheet "S04a-DNN": opening balance in cell H12, data from row 13,
columns A-I.
Sheet "S04b-DNN": same rows, with an extra column F (offsetting
account), so the amount columns shift one column to the right.
"""

S04A_SHEET = "S04a-DNN"
S04B_SHEET = "S04b-DNN"
S05_SHEET = "S05-DNN"

OPENING_ROW = 12  # the "Số tồn đầu kỳ" (opening balance) row
FIRST_DATA_ROW = 13

S04A = {
    "post_date": 1,  # A posting date
    "doc_date": 2,  # B document date
    "receipt_no": 3,  # C receipt number
    "payment_no": 4,  # D payment number
    "description": 5,  # E description
    "receipt": 6,  # F (1) receipts
    "payment": 7,  # G (2) payments
    "balance": 8,  # H (3) balance
    "note": 9,  # I (G) note
}

S04B = {
    "post_date": 1,
    "doc_date": 2,
    "receipt_no": 3,
    "payment_no": 4,
    "description": 5,
    "contra": 6,  # F offsetting account
    "receipt": 7,  # G (1) debit movements
    "payment": 8,  # H (2) credit movements
    "balance": 9,  # I (3) balance
    "note": 10,  # J (G) note
}

# S05-DNN bank book: A posting date, B document number, C document
# date, D description, E offsetting account, 1 receipts (deposited),
# 2 payments (withdrawn), 3 balance, F note.
S05 = {
    "post_date": 1,
    "number": 2,
    "doc_date": 3,
    "description": 4,
    "contra": 5,
    "receipt": 6,
    "payment": 7,
    "balance": 8,
    "note": 9,
}

# Rows whose note is this marker are skipped on import
EXAMPLE_ROW_MARKER = "Dòng ví dụ"
