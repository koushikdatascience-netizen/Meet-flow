# Chatterjee Flow Stock

Stock-only inventory management for one company and its internal staff. There is no SaaS signup, company creation or company switching. Prices, buying costs, taxes and payment fields are absent from the active application. Printable stock documents show amounts and totals as `00`.

## Upgrade an existing installation

Stop all running Chatterjee Flow servers before upgrading. Back up before migrations:

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

Create items with name and category; the unit is fixed to Pieces. Add opening stock as the first active entry per item; later stock receipts use Purchases. Search by item name, enter quantity, and press Enter to move to the next row. Pieces must be whole numbers. Entries support up to 100 items and whole-number piece quantities. New entries always use today’s server-assigned date; negative historical balances are blocked.

Purchases increase stock, sales decrease stock. Purchases, sales and opening entries have Edit and Delete actions. Edit prefills the original items and quantities, retains the original date and saves an audit snapshot of the previous version. Stock is validated atomically after the replacement; negative balances are blocked, stale forms cannot overwrite newer edits, and repeated submissions are idempotent. Staff need their entry feature plus Edit entries permission; admins have edit access automatically. To delete an entry, confirm Delete. No notes or typed deletion reason are required. Deletion is a recorded reversal; the original actor, date, lines and deleting user remain recorded; a standard confirmation reason is stored automatically. A reversal recalculates the original day's stock and following days. It is not a new stock movement dated on the deletion day. A purchase/opening deletion is blocked when it would make a later balance negative. Each form has a session-bound idempotency token to prevent double posting.

Reports show Date, Item, Opening stock, Purchase, Total, Sale and Closing stock. Category and unit columns are omitted; the category filter is retained. Initial opening entries are included in Opening; no separate Opening added column is shown. Reports automatically show today in Asia/Kolkata time, with a category filter. Entry dates are also assigned by the server; date inputs and report date-range controls are removed. Opening = previous closing + initial opening entries. Total = Opening + Purchase. Closing stock left = Total - Sale. Purchases are counted exactly once. The totals row and current-stock figure reflect the category filter and include all displayed items. Saved purchases, sales and deletions recalculate balances on the next report request. Historical dates on existing records are preserved. All items use Pieces. Existing numeric quantities were relabelled as Pieces without conversion or rounding at the owner’s request; new entries require whole numbers. Download CSV opens in spreadsheet software; print uses the same filtered rows. CSV text is escaped against spreadsheet formula injection. Amounts, revenue and profit are not reported.

## Testing

```powershell
.\.venv\Scripts\python.exe manage.py test inventory
.\.venv\Scripts\python.exe manage.py test shop --settings=config.legacy_test_settings
```

The second command tests the preserved former billing code in an isolated URL configuration; those routes are never mounted by the live app.

## Temporary tunnel

Run Chatterjee Flow locally and, in another terminal, `cloudflared tunnel --url http://127.0.0.1:8010`. Restart Django in the same terminal where you set `DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1,<exact tunnel hostname>` and `DJANGO_CSRF_TRUSTED_ORIGINS=https://<exact tunnel hostname>`. Quick Tunnel addresses change after restart. This is temporary testing, not a production deployment. Do not use wildcard allowed hosts.

## Deployment and backups

Existing Docker/PostgreSQL and backup commands are preserved. Use PostgreSQL for concurrent production stock operations (company row locks serialize posting/reversal); SQLite is for local use and does not supply PostgreSQL-style row locks. Configure HTTPS, DEBUG=0, a strong secret, exact hosts, trusted origins and durable backups before production. Uploaded legacy media and the database are included by `backup_shop`; test recovery with `verify_backup` and `restore_backup`. See `docs/legacy-billing.md` for the former system's infrastructure reference; its billing instructions do not apply to the active stock-only UI.

## Single-company upgrade

Migration 0006 selects the existing owner workspace, or the workspace with the most stock activity when the owner has none, and assigns existing staff to that one workspace. No item, entry, user or audit history is deleted. Any extra companies created during earlier testing remain archived in the database; their stock is not silently merged. All normal users work in the same selected workspace. Back up before migration.

## Item categories

Administrators can add, edit and delete categories from Home → Item Categories. Chicken, Mutton, Fish and Others are seeded on upgrade as the starting list, not a restriction. New categories appear in the item dropdown and report filter. Renaming updates every linked item in the same transaction. Category names are unique without regard to case. Deletion is blocked while any item, including a removed item, still uses the category; move those items to another category first. This keeps stock history intact.
