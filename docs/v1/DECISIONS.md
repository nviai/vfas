# DECISIONS – business / technical points decided on our own

Rule when the specification is unclear: choose the option that is **safer from an accounting standpoint** (no lost audit trail, no change to locked figures).

## Vouchers & numbering

| # | Decision | Rationale |
|---|---|---|
| D1 | **Voucher numbers are assigned at posting**, not when a draft is created. Drafts display "Nháp #id" (Draft #id). | Drafts can be deleted freely; assigning a number when the draft is created would leave gaps when drafts are deleted. Assigning at posting guarantees continuous, unique numbers. |
| D2 | The year in the voucher number and the renumbering period follow **the year of the posting date**. | The cash book is sorted by posting date. |
| D3 | Cancelled vouchers **keep their number** (never reused). | No lost trail; no gaps in numbering. |
| D4 | The voucher number format only allows `{prefix} {year} {yy} {seq} {seq:0Nd}`. If numbering restarts every year, the format must contain `{year}`/`{yy}`. | Avoids duplicate numbers across years. |
| D5 | If a generated number already exists (e.g. it clashes with an imported voucher number), it automatically moves on to the next number. | UNIQUE(fund, number) constraint. |
| D6 | If "Lưu và ghi sổ" (Save and post) is rejected (negative cash, locked period…), the voucher **is still saved as a draft** and a clear error is shown. | The data the user just entered is not lost. |
| D7 | The posting date cannot be in the future or before the book opening date; voucher date ≤ posting date (blocked in the form; the import only warns). | Voucher logic; old data can still be imported and is flagged again on the "Kiểm tra dữ liệu" (Data checks) page. |
| D8 | Posted vouchers: **only Accountant and above** can edit **the descriptive fields** (payer/payee, address, reason, contra account, source document, notes) while the period is open; with before/after audit. Amount, dates, type, voucher number and posting order never change. | Article 27 of the Accounting Law allows corrections in software as long as a trail is kept; the prompt forbids changing amount/date/type. |
| D9 | Two layers of blocking: service + **DB triggers** (SQLite & PostgreSQL) forbid deleting non-draft vouchers, changing figure fields, and reverting status (posted→draft, cancelled→anything else). | Protection even if someone edits the DB directly. |
| D10 | Recording an adjustment = a normal new voucher (receipt or payment, chosen by the user) with `adjusts_voucher_id`, recorded in the open period. The book prints "Điều chỉnh cho <số>" (Adjusts <number>) in the Notes column. | The correct way to fix errors once a period is locked. |
| D11 | Cashiers **neither see nor enter** the contra account and can still post; Accountants **must** choose an account. Posted vouchers missing an account appear in Data checks. | "Contra account mandatory for accountants"; cashiers only have permission to enter vouchers. |
| D12 | Attachments: only JPG/PNG/WEBP/PDF (checked by **magic bytes**, the file extension is not trusted), ≤ `MAX_UPLOAD_MB`; random storage name; deletable only while the voucher is a draft; can be added to posted vouchers. | Safe, and source documents are never lost. |
| D13 | Payers/payees are added to the catalog automatically when a voucher is saved with a new name; matching is **Unicode case-insensitive** (`ulower` function: SQLite calls Python's `str.lower()` because SQLite's `lower()` does not convert Đ→đ). | Avoids duplicates such as "Điện lực" / "điện lực". |

## Balances & negative cash

| # | Decision | Rationale |
|---|---|---|
| D14 | Book order: `post_date`, then `posting_seq` (assigned incrementally at posting, including back-dated postings). A back-dated voucher comes **after** all existing lines on the same date. | Per spec 3.4. |
| D15 | Balances computed with `SUM() OVER (ORDER BY post_date, posting_seq)`; no snapshot table yet. | Fast enough for small businesses; avoids the risk of drifting figures. |
| D16 | **Retroactive** negative-cash check when posting a payment and when **cancelling a receipt**; reports the date, line number (across the whole book since the opening date), voucher number and the negative balance. | Spec 3.4 + section 8 tests. |
| D17 | Posting/cancellation/import serialized with a process lock + `UPDATE funds SET lock_version = lock_version + 1` at the start of the transaction (row lock on PostgreSQL, write lock on SQLite). Run **1 uvicorn worker**. | Two people posting at the same time cannot both pass the negative-cash check. |
| D18 | The opening balance is locked as soon as **any voucher has ever been posted** (even if cancelled later). | Safer than "while a posted voucher exists". |

## Period locks

| # | Decision | Rationale |
|---|---|---|
| D19 | Locking is **sequential**: a period can only be locked when all previous periods are locked; a period can only be unlocked when no later period is locked. Unlocking a month requires unlocking the year first. | Otherwise, back-dated postings into an open earlier period would change the opening/closing balances of a locked later period. |
| D20 | Only periods that **have ended** can be locked. Locking a year requires all months before that year to be locked. | Avoids locking a period that is still active by mistake. |
| D21 | The `period_locks` table holds the current state; the lock/unlock history (who, when, reason) lives in the append-only audit log. | No lost trail. |
| D22 | "Kỳ chưa khóa quá 30 ngày" (periods left unlocked for over 30 days) = months whose last day is more than 30 days before today and that are not locked; shown grouped as month ranges. | Spec section 7. |

## Cash count

| # | Decision | Rationale |
|---|---|---|
| D23 | The book balance on the count report = all posted vouchers with posting date ≤ count date, frozen when the report is created. The count date cannot be in the future or before the book opening date. | The report is a document at a point in time. |
| D24 | Remaining difference = actual − (book balance at the count + posted vouchers linked by `cash_count_id`). Suggested settlements are only **links** opening a pre-filled voucher form (Surplus: PT Debit 1111/Credit 3381; Shortage: PC Debit 1381/Credit 1111); **never created automatically**. Accounts come from the accounting regime configuration. | Spec 3.7. |
| D25 | On 08a-TT, section III = I − II (book − actual) as in the form; a clear Surplus/Shortage line is printed in addition. Report number `KK{năm}-{seq:03d}` (năm = year). | Matches the form. |

## Amounts in words

| # | Decision | Rationale |
|---|---|---|
| D26 | "**chẵn**" ("exactly") is appended only when the amount is a whole multiple of 1,000,000 ("Mười triệu đồng chẵn"); not for 21,000, 15,000, 1,005,000. | Matches **exactly** all 4 expected results in section 8 (the spec would be inconsistent if "chẵn" were always appended). |
| D27 | Default "linh" (with a parameter for "lẻ") for a zero tens digit; "mốt" (one) after tens ≥ 2; "lăm" (five) after tens ≥ 1; "bốn" (four, not "tư"); non-leading groups are read in full, e.g. "không trăm" (zero hundred). Printed vouchers add a full stop at the end. | Style of accounting documents. |

## Permissions

| # | Decision | Rationale |
|---|---|---|
| D28 | Exporting books to PDF/Excel requires the "xuất báo cáo" (export reports) permission (Accountant and above). Cashiers view S04a on the web and can print **vouchers**. | Role table in section 4. |
| D29 | **Excel import** only for Chief accountant/Director. | Import writes into the books in bulk (including the opening balance) – high impact. |
| D30 | Audit log, Data checks: Accountant and above. Period locks: everyone can view, Chief accountant/Director act. Catalogs: everyone can view, Accountant and above edit. | Separates view and write permissions. |
| D31 | At least 1 active Chief accountant/Director must always remain; locking a user revokes all their sessions. | Nobody locks the company out of administration. |

## Excel import

| # | Decision | Rationale |
|---|---|---|
| D32 | Error rows **block** the import; it proceeds only when the user ticks "Bỏ qua N dòng lỗi và chỉ nhập các dòng hợp lệ" (Skip N error rows and import only the valid rows). | No silent partial imports. |
| D33 | Negative cash on import: merge the existing book + new rows in book order (new rows after existing rows on the same date); at the first negative point → flag the nearest preceding new payment as an error; repeat until nothing is negative. If the existing book is already negative, report it separately rather than blaming the file. | Also catches cases where later rows go negative. |
| D34 | Opening balance: if nothing has ever been posted → set `opening_balance` = H12 (audited). If data already exists → H12 must equal the book balance before the file's first date, otherwise block. H12 is an uncalculated formula → block; empty → 0 with a notice. | Balances never drift. |
| D35 | S04a has no payer/payee column → `partner_name` left empty. The contra account is taken from S04b by voucher number; codes not in the catalog → left empty + warning. | No unknown accounts created automatically. |
| D36 | Warnings (non-blocking): missing/invalid voucher date (posting date used), voucher date after posting date, the Balance column in the file differs from the recomputed balance. The import re-validates everything inside the transaction (the preview is not trusted). | Old data is often not clean. |

## Printing & export

| # | Decision | Rationale |
|---|---|---|
| D37 | Book PDFs are rendered in **2 passes** to print the exact "Sổ này có N trang, đánh số từ trang 01 đến trang N" (This book has N pages, numbered from page 01 to page N); column headers repeat on every page; "Trang x/y" (Page x/y) in the margin. Excel does not know the page count → dots are left. | Book template. |
| D38 | The "Ngày … tháng … năm …" (Day … month … year …) field for signing books is left blank for the signer to fill in; cash receipts/payments use the voucher date. | How books are signed in practice. |
| D39 | Vouchers are A5 landscape; drafts print the watermark "NHÁP – CHƯA GHI SỔ" (DRAFT – NOT POSTED) and leave the number blank; cancelled vouchers print "ĐÃ HỦY" (CANCELLED). "Người lập phiếu" (Prepared by) = the creator of the voucher (blank for imported vouchers). | Vouchers without a number are never issued as if they were real. |
| D40 | Excel: the Balance column is the formula `=H12+F13-G13…`, total rows use `SUM`, zero cells are left blank, dates are real date values, A4 landscape, header rows repeat. The layout matches the import (H12, row 13), so an exported file can be re-imported. | Spec section 6. |
| D41 | Print fonts: Times New Roman → Liberation Serif → DejaVu Serif (Docker installs `fonts-liberation2`, `fonts-dejavu-core`). | Full Vietnamese diacritics, Times-compatible metrics. |

## Chart of accounts

| # | Decision | Rationale |
|---|---|---|
| D42 | Seed exactly the account list from the spec with TT133 names. Note: in TT133, **3386 = "Nhận ký quỹ, ký cược"** (deposits received); unemployment insurance is **3385** (not in the list, can be added under Catalogs). Accounts are never deleted, only hidden. 1111 cannot be added as a contra account. | Matches TT133. |

## Technical & operations

| # | Decision | Rationale |
|---|---|---|
| D43 | Sessions stored in the DB (the cookie only holds a random token, the DB stores its SHA-256), HttpOnly, SameSite=Lax, `Secure` per `COOKIE_SECURE`. CSRF bound to the session; pages before sign-in use a double-submit cookie. Temporary 15-minute lockout after 8 failed sign-ins (per user+IP). | Secure by default. |
| D44 | Times stored in UTC, displayed in Asia/Ho_Chi_Minh. | Avoids time zone shifts. |
| D45 | i18n: the Vietnamese source string is the key; translations in each feature's `locales/en.json` + `core/locales/en.json` (core wins on duplicates). A test ensures every UI string has a translation. Dynamic messages (containing figures) stay in Vietnamese. | Simple, no gettext needed. |
| D46 | Backup = `.tar.gz` containing `manifest.json`, `data.json` (all tables, DB-independent), `soquy.db` (SQLite backup API, safe while running) and `attachments/`. SQLite restore uses the backup API (keeps the old copy as `.bak-…`); PostgreSQL loads `data.json` into a schema recreated by Alembic, then resets the sequences. | One command; SQLite → PostgreSQL cross-restore possible. |
| D47 | The dashboard does not repeat the "kỳ chưa khóa" (unlocked periods) warning (there is already a per-month count line); chart of month-end balances for the last 12 months, the last point = current balance. | Compact, no conflicting figures. |
