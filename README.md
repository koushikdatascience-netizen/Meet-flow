# MeatFlow Stock

Stock-only inventory management for one company and its internal staff. There is no SaaS signup, company creation or company switching. Prices, buying costs, taxes and payment fields are absent from the active application. Printable stock documents show amounts and totals as `00`.

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

Use Python 3.11 or newer, create a virtual environment, install `requirements.txt`, then run `migrate` and `createsuperuser`. Sign in at `/login/`. Administrators open Home directly and manage staff under Users. The internal workspace is created automatically by migrations. On Render, the existing `MEATFLOW_BOOTSTRAP_USERNAME` and `MEATFLOW_BOOTSTRAP_PASSWORD` environment variables control the administrator login; see `docs/render-deployment.md`.

## Users and permissions

* Administrators manage items, staff users, stock entries and reports for the single internal workspace.
* Hotel staff roles: Manager, Worker, Support, Cook and Server / serving staff. Selecting a role in Users suggests permissions; admins can customize every feature before saving. Managers default to stock operations and reports, workers to purchase/sale entries, support to reports, and cooks/servers to stock-out entries. Only Administrators manage users and item setup. Existing Stock operator and Report viewer accounts remain supported.
* Report viewers receive report/export access only.

Admins create unique usernames and strong temporary passwords. Passwords are hashed using Django's password hashing; passwords are never displayed after saving. A new or reset user must change their temporary password before workspace access. Editing a user without entering a password retains their current password. Uncheck Active to disable an account. Users cannot disable or demote themselves. Ordinary staff admins cannot edit the environment-managed superuser. Never share the owner login with staff.

Every request checks the user’s role and feature access in the single internal workspace. No company selection is accepted from forms. Historical company references remain internal database details to preserve records; the company-management route is removed. The old `/admin/` and financial document routes are closed.

## Stock workflow

Create items with name, category and unit (kg or pieces). Add opening stock as the first active entry per item; later stock receipts use Purchases. Search by item name, enter quantity, and press Enter to move to the next row. Pieces must be whole numbers. Entries support up to 100 items and three quantity decimal places. Future entries and negative historical balances are blocked.

Purchases increase stock, sales decrease stock. Entries are immutable through the UI. To correct an entry, delete it with a reason and enter the corrected document. Deletion is a recorded reversal; the original actor, date, lines, deleting user and reason remain available. A reversal recalculates the original day's stock and following days. It is not a new stock movement dated on the deletion day. A purchase/opening deletion is blocked when it would make a later balance negative. Each form has a session-bound idempotency token to prevent double posting.

Reports show one row per date and item: opening stock, purchases, sales and closing stock. Purchases and initial opening entries are included in the displayed opening stock; no separate Opening added column is shown. Filter by date range (up to 367 days) and category. Opening = previous closing + today’s purchases + initial opening entries. Closing = opening - sales. The Purchase column is informational and is not added a second time. Saved purchases, sales and deletions recalculate that day and subsequent days on the next report request. Each item's unit remains separate. Download CSV opens in spreadsheet software; print uses the same filtered rows. CSV text is escaped against spreadsheet formula injection. Amounts, revenue and profit are not reported.

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

## Single-company upgrade

Migration 0006 selects the existing owner workspace, or the workspace with the most stock activity when the owner has none, and assigns existing staff to that one workspace. No item, entry, user or audit history is deleted. Any extra companies created during earlier testing remain archived in the database; their stock is not silently merged. All normal users work in the same selected workspace. Back up before migration.
