# MeatFlow Stock

Stock-only, multi-company inventory management. Prices, buying costs, taxes and payment fields are absent from the active application. Printable stock documents show amounts and totals as `00`.

## Upgrade an existing installation

Stop all running MeatFlow servers before upgrading. Back up before migrations:

```powershell
cd D:\kkkk\meatflow-pos
.\.venv\Scripts\python.exe manage.py backup_shop
.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe manage.py runserver 127.0.0.1:8010 --noreload
```

The one-time upgrade creates **My shop**, imports all former products and their current stock as opening quantities dated on the upgrade day, and assigns the first active superuser as its company admin. Historical financial tables remain preserved, with no routes exposing the former billing UI or Django admin. They are not recalculated or changed. Earlier transactions are not represented as historical stock activity in the new reports. An existing database with products requires an active superuser before migration. Opening balances retain their original quantities; no prices are imported.

Existing other users have no company access until an admin creates or assigns their accounts. Do not run the former role setup command for the new app.

## New installation

Use Python 3.11 or newer, create a virtual environment, install `requirements.txt`, then run `migrate` and `createsuperuser`. Sign in at `/login/`. A platform superuser without a company sees `/companies/`, where they create companies and an initial admin account. An upgraded superuser can also open `/companies/` from the navigation.

## Users and permissions

* Platform superusers create, rename and deactivate companies.
* Company admins manage their own items, users, stock entries and reports.
* Stock operators receive selected opening, purchase, sale, deletion and report features.
* Report viewers receive report/export access only.

Company admins create globally unique usernames and strong temporary passwords. Passwords are hashed using Django's password hashing; passwords are never displayed after saving. A new or reset user must change their temporary password before company access. Editing a user without entering a password retains their current password. Uncheck Active to disable an account. Users cannot disable or demote themselves. An ordinary company admin cannot edit a platform superuser. Never share the platform superuser login with staff.

Every request scopes items, entries, users, receipts and reports to the authenticated membership. Company ID is never accepted from stock/user forms. Inactive companies cannot access inventory. The old `/admin/` and financial document routes are closed.

## Stock workflow

Create items with name, category and unit (kg or pieces). Add opening stock as the first active entry per item; later stock receipts use Purchases. Search by item name, enter quantity, and press Enter to move to the next row. Pieces must be whole numbers. Entries support up to 100 items and three quantity decimal places. Future entries and negative historical balances are blocked.

Purchases increase stock, sales decrease stock. Entries are immutable through the UI. To correct an entry, delete it with a reason and enter the corrected document. Deletion is a recorded reversal; the original actor, date, lines, deleting user and reason remain available. A reversal recalculates the original day's stock and following days. It is not a new stock movement dated on the deletion day. A purchase/opening deletion is blocked when it would make a later balance negative. Each form has a session-bound idempotency token to prevent double posting.

Reports show one row per date and item: opening balance, opening added, purchases, sales and closing balance. Filter by date range (up to 367 days) and category. Closing = opening + opening added + purchases - sales. Each item's unit remains separate. Download CSV opens in spreadsheet software; print uses the same filtered rows. CSV text is escaped against spreadsheet formula injection. Amounts, revenue and profit are not reported.

## Testing

```powershell
.\.venv\Scripts\python.exe manage.py test inventory
.\.venv\Scripts\python.exe manage.py test shop --settings=config.legacy_test_settings
```

The second command tests the preserved former billing code in an isolated URL configuration; those routes are never mounted by the live app.

## Temporary tunnel

Run MeatFlow locally and, in another terminal, `cloudflared tunnel --url http://127.0.0.1:8010`. Restart Django in the same terminal where you set `DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1,<exact tunnel hostname>` and `DJANGO_CSRF_TRUSTED_ORIGINS=https://<exact tunnel hostname>`. Quick Tunnel addresses change after restart. This is temporary testing, not a production deployment. Do not use wildcard allowed hosts.

## Deployment and backups

Existing Docker/PostgreSQL and backup commands are preserved. Use PostgreSQL for concurrent production stock operations (company row locks serialize posting/reversal); SQLite is for local use and does not supply PostgreSQL-style row locks. Configure HTTPS, DEBUG=0, a strong secret, exact hosts, trusted origins and durable backups before production. Uploaded legacy media and the database are included by `backup_shop`; test recovery with `verify_backup` and `restore_backup`. See `docs/legacy-billing.md` for the former system's infrastructure reference; its billing instructions do not apply to the active stock-only UI.
