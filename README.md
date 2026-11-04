# MeatFlow POS

A single-shop Django application for billing, stock, purchasing, returns, expenses and reports. The owner manages settings and staff through Django admin; cashiers use the dedicated billing screen.

## Start locally (Python 3.11+)

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install --upgrade pip
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python manage.py migrate
.venv\Scripts\python manage.py setup_roles
.venv\Scripts\python manage.py createsuperuser
.venv\Scripts\python manage.py runserver
```

Open http://127.0.0.1:8000/ and sign in. Cashiers go straight to billing; inventory staff go straight to purchases. Owner settings remain under `/admin/`. Open **Shop settings** to set the shop name, address, phone, GSTIN, state code, signatory and receipt footer. **Open billing screen** goes to `/pos/`; reports are at `/reports/`. Local commands read process environment variables. Django does not automatically load `.env`; Docker Compose does.

SQLite is suitable for local development and single-user trials. Use PostgreSQL for concurrent shop use: SQLite does not implement the row locks relied on by the posting services. Local development defaults to DEBUG enabled; production requires DEBUG disabled and a random secret of at least 50 characters.

## Daily operation

- **Products:** set kg/piece, **default buying price** and **selling price** once. Buying and selling prices stay separate; stock and weighted-average valuation are read-only. Low-stock and other options are under More details. For GST documents, configure the HSN and tax rate for each product. Piece quantities must be whole numbers. Admin prevents changing units after stock movements; create a new product for a different unit.
- **Purchases:** open **Purchases** (`/purchases/new/`), type a product, press Enter, enter quantity, adjust the prefilled buying price only if needed, then choose **Save purchase & update stock**. Supplier names are suggested from previous purchases. Buying prices fill from the last posted purchase, or the product default before its first purchase, and remain editable if the supplier price changes. Pressing Enter after quantity goes directly back to product search. Saving updates stock immediately; retries do not post twice. The admin draft/post workflow is still available for preparing drafts.
- **Billing:** type part of a product name. Exact/prefix matches appear first as you type; use arrow keys and Enter to choose. Enter quantity and press Enter to find the next item. Choosing an item already added focuses its existing quantity. **Save & next bill** immediately opens a fresh bill with a link to the last receipt. **Save & print** opens the thermal bill and requests printing. Payment is a one-click choice; customer/discount/tax details stay tucked away until needed. F2 focuses search; Ctrl+Enter saves. The server checks stock and prices; retry tokens prevent duplicate bills. Displayed totals are estimates until saved.
- **Printing:** choose a full invoice or an 80 mm receipt and use the browser print/PDF dialog. Configure your printer paper size to match.
- **Wastage:** add a negative stock adjustment with an explanation and post it. Count corrections may be positive or negative; adjustments cannot make stock negative.
- **Expenses:** record rent, utilities and other costs with dates and categories.
- **Reports:** start with Today, or click Yesterday, Last 7 days or This month. Sales, estimated profit, bill count and purchases appear first; payment totals, recent bills and low stock follow. More financial details and custom dates expand when needed. CSV download keeps the selected dates. Transactions/returns use posting dates, falling back to creation dates for old records. Stock valuation is always current.

Posted documents, transaction lines, returns and stock movements cannot be changed/deleted through supported application operations. Corrections must be recorded as new transactions. Bulk ORM updates/creation of documents and lines are disabled. Direct database access can bypass these application protections.

## Returns, refunds and purchase corrections

On a posted bill choose **Return / refund**. For a posted purchase choose **Return / correct** in the purchase list. Enter partial quantities, or all remaining quantities for a full reversal, explain the reason and choose the refund/settlement method.

Customer returns restore stock **only when marked fit for resale**. Spoiled goods stay out of stock. Refunds use the original prices, proportionally allocated discounts, original acquisition costs and original GST components. Cumulative rounding ensures all partial credits reconcile to the original amount. Quantities cannot exceed the remaining original quantity. Retry tokens protect submissions on the dedicated return screen. Print the resulting numbered credit document.

Supplier returns deduct available stock and record the original purchase value as the supplier credit. Inventory leaves at the current weighted-average cost; the difference appears as a supplier-return valuation variance in reports. A purchase correction can be entered by fully returning the original quantities and posting a corrected purchase. If those goods are no longer in stock, a full supplier return is blocked; reconcile the actual stock and accounting situation rather than forcing a reversal.

These screens record refunds and supplier credits; they do not send money through a payment gateway or settle supplier balances automatically. Purchase prices are acquisition costs; supplier input-GST accounting is outside this application's scope.

## Invoice configuration and GST

The owner can edit invoice details at any time in **Shop settings**. Each posted sale keeps a snapshot, so later shop/product changes do not rewrite issued bills. New documents receive financial-year sequence numbers, with separate series for sales, customer credits and supplier returns.

Available modes:

- **Simple receipt:** default; no GST is added.
- **Bill of supply:** prints supplier details and product HSN without charging GST.
- **GST tax invoice:** tax-exclusive prices, bill-level discounts allocated across lines, CGST/SGST for local supplies and IGST for interstate supplies. Customer GSTIN/address/state and higher-value customer details are validated. Customer tax and place-of-supply details can be entered on the billing screen.

The owner must configure the correct registration mode, state, HSN and applicable product rates. No meat-product tax rate is guessed. Invoice/credit fields follow the ordinary retail document structure described in [CBIC invoice rules](https://cbic-gst.gov.in/gst-invoice-rules.html); [current Rule 46](https://taxinformation.cbic.gov.in/content-page/explore-rules/1000136/1000001) and [credit-note particulars](https://taxinformation.cbic.gov.in/content-page/explore-rules/1000144/1000001) should be checked for the business's circumstances. The software does not perform tax filing, GSTIN verification against government records, e-invoice/IRN generation, e-way bills, reverse-charge transactions, exports or other special GST regimes. Businesses requiring those integrations must use the appropriate external workflow before treating the document as final for that purpose. Signatures and legal eligibility remain the shop operator's responsibility.

Old invoices retain their existing receipt identity. Migrations cannot recover shop/product details changed before snapshots existed. Money uses decimal arithmetic with half-up rounding to two places per line. Purchase/stock valuation uses weighted-average cost; this is management reporting rather than a complete general ledger.

## Staff roles and audit history

Run `setup_roles` after migration. It creates/updates three groups without assigning users or changing staff status:

| Group | Access |
|---|---|
| Cashier | Billing and viewing sales/invoices; no financial reports, refunds, acquisition costs or shop settings |
| Inventory Manager | Products, purchase drafts/posting, stock adjustments and stock movements |
| Owner | Shop operations, financial reports, returns, settings and audit history |

A superuser creates staff accounts, enables **Staff status** and assigns the appropriate group. Role memberships are explicit; existing users are not automatically promoted. Superusers administer user accounts.

**Audit events** record the actor, time, posting totals, reasons and original transaction references. Admin saves include before/after values; edits/deletions are logged. Events and stock movements are read-only through the supported application. Database administrators remain able to modify raw data. Audit history contains business/customer information and must be access-controlled.

## Backups and recovery drills

```powershell
.venv\Scripts\python manage.py backup_shop
.venv\Scripts\python manage.py verify_backup backups\YOUR-BACKUP.zip
.venv\Scripts\python manage.py restore_backup backups\YOUR-BACKUP.zip --destination D:\recovery\NEW-EMPTY-DIRECTORY
```

Backups include database, media, manifest and SHA-256 checksums; they exclude environment secrets and never overwrite an existing file. `verify_backup` checks every member and tests SQLite integrity or the PostgreSQL dump structure. The restore command supports SQLite and only creates a new recovery directory; it never replaces live database files. Pause shop writes when taking a backup if you need database/media to represent the same point in time.

For a supervised local backup worker:

```powershell
.venv\Scripts\python manage.py backup_loop --destination D:\local-backups --copy-to E:\existing-external-backups --interval-hours 24
# Add --once for one verified backup/copy and exit.
```

The external copy destination must already exist. A missing drive or failed verification stops the worker with an error. Run it under your service manager and monitor failures. No old backup is deleted automatically. Checksums detect corruption; they are not a signature or encryption. Protect backup access and retain copies outside this computer.

For PostgreSQL, a compatible `pg_dump` / `pg_restore` client is required and is included in the app image. Extract `database.dump` and restore with `pg_restore --no-owner --dbname=NEW_DATABASE database.dump` into a newly created recovery database, restore media into a separate directory and test an isolated app against it. Verification of dump structure does not replace a full PostgreSQL recovery drill. Never test recovery over the live database.

## Docker, static files and scheduled backups

Copy `.env.example` to `.env`, replace the secret and database password, and set your hostnames. The example is intentionally not usable as a production secret.

```powershell
docker compose up -d --build
docker compose exec app python manage.py migrate
docker compose exec app python manage.py setup_roles
docker compose exec app python manage.py createsuperuser
```

The app waits for PostgreSQL readiness. Nginx serves static/media volumes and proxies the application; the default web port binds to `127.0.0.1:8000`. Static files refresh when the app starts. `/healthz/` tests database connectivity without disclosing configuration. HTTPS is required for normal production login because DEBUG-disabled session cookies are secure.

For daily verified backups, set `BACKUP_HOST_DIR` in `.env` to an **existing external drive or synced host directory**, then enable the optional worker:

```powershell
docker compose --profile backups up -d --build
```

The worker creates a verified backup immediately and every 24 hours while running. Its default destination is a local backup directory; it becomes an off-device backup only when an appropriate host destination is configured. Monitor the worker with `docker compose logs backups`, configure storage/retention and perform recovery drills. Neither an external destination nor an active schedule is silently installed on the host by this checkout.

## HTTPS hosting

A ready-to-configure HTTPS override is included. Supply your own domain and valid certificate files `fullchain.pem` and `privkey.pem` in `TLS_CERT_DIR`, set `DJANGO_ALLOWED_HOSTS` and `DJANGO_CSRF_TRUSTED_ORIGINS=https://YOUR_DOMAIN`, then run:

```powershell
docker compose -f docker-compose.yml -f docker-compose.production.yml --profile backups up -d --build
```

This exposes ports 80/443, redirects HTTP, enables secure forwarding and sets HSTS. The override requires a recent Docker Compose supporting `!override`. Configure certificate renewal separately. Enable HSTS subdomains/preload only after assessing all relevant domains. Do not publish the development server. Application containers use their internal health check with the expected forwarded HTTPS scheme.

## Updates and validation

Back up before upgrades, install dependencies from `requirements.txt`, apply migrations, update roles and run checks. `requirements.txt` describes supported dependency ranges. `requirements.lock` is a compatibility entry point pointing to those ranges, rather than a machine-specific exact-version snapshot. Docker and CI use the same supported ranges.

```powershell
.venv\Scripts\python manage.py check
.venv\Scripts\python manage.py makemigrations --check --dry-run
.venv\Scripts\python manage.py test
# With actual production environment variables:
.venv\Scripts\python manage.py check --deploy
```

The suite covers posting/rollback, duplicate submissions, partial and full returns, stock integrity, cost/tax/discount allocation, frozen invoices, roles, CSRF, report/payment reconciliation, migration backfill, backup tampering and separate SQLite recovery. The PostgreSQL overselling test runs only against a database with row-lock support. The included CI workflow runs tests on PostgreSQL and builds the Docker image when this project is placed in GitHub.

Local automated tests and browser checks do not establish deployment readiness. Actual PostgreSQL concurrency/recovery, Docker runtime, certificate renewal, off-device storage and printer behavior must be verified in the target environment. The current local database is migrated and role groups are prepared; create an owner login before use. Demonstration preview data is isolated from the shop database.


### Product search checks

`node shop/js_tests/product-search.test.cjs` checks matching order, partial/multi-word queries, accents and stock availability without installing JavaScript packages. The billing and purchase screens share the same lookup and keyboard controls. Browser verification uses demonstration data in a separate preview database; it does not create shop transactions.


### Automatic buying prices

Product setup has two distinct fields: **Default buying price** and **Selling price**. Purchases suggest the most recent posted purchase-line price, ordered by posting time (creation time for legacy records) and line ID. Drafts do not change the suggestion. A zero buying price is retained when explicitly posted. Before the first posted purchase, the product default is used. Existing products receive a zero default during migration; set their actual default once if no purchase history exists. No historical cost is inferred from the selling price.

A purchase saves the actual accepted buying price on each line, even when left blank and filled automatically. Price overrides affect future purchase suggestions after posting. The product default and selling price are not overwritten. Weighted-average acquisition cost continues to drive inventory valuation and cost at sale. Bills always fill the selling price independently.


### Items in the shop interface

Open **Items** in the main menu (`/items/`) to add, search and edit products without entering Django admin. The main form contains item name, kg/piece unit, default buying price and selling price. Category, low-stock threshold and GST fields are under optional details. Stock is read-only; record receipts through Purchases.

**Delete** first asks for confirmation, then removes the item from active use by marking it inactive. Existing bills, purchases, stock balances and audit records are preserved. It no longer appears in new bill/purchase pickers. Open **Removed items** and choose **Restore** to bring it back. This is intentionally a recoverable removal, not permanent erasure of business history.

Owners and inventory managers can manage items. Cashiers do not receive item-management or buying-price access. Item creation, edits, removal and restoration are audited. Editing uses a row lock so a stale item form cannot overwrite stock changed by a purchase or sale.


### Customer state during billing

Simple receipt mode hides customer GSTIN and state fields; normal checkout does not require them. GST document modes use a state-name dropdown rather than asking staff to type a numeric code. A customer GSTIN can supply its state when no state is selected. Older one-digit codes and common state abbreviations are normalized; invalid GST states remain field errors and cannot change stock. State choices follow the official GST e-way-bill master linked in `shop/states.py`.


### Sales, purchase and item reports

Reports now have five tabs: **Overview**, **Sales & bills**, **Purchases**, **Item performance** and **Returns**. Sales and purchase registers include all matching posted documents, with 25 records per page and direct links to their original details. Search bills/customers or purchases/suppliers, filter sales by payment method and purchases by supplier, and choose dates with the quick buttons or custom range. Date ranges stay selected when switching tabs; tab-specific search filters are cleared on a tab switch.

The sales register shows original bill values, discounts, GST and gross profit before credits. The purchase register shows original acquisition costs and quantities, plus supplier purchase/credit totals. Customer and supplier credits have their own register on their posting dates, including returns against transactions outside the selected period. Overview net totals include those credits. Item performance combines sales, purchase and return activity by item and unit, with historical selling/acquisition costs rather than current product prices. kg and pieces are never added together as one number. Negative net quantities can occur when an older transaction is returned in the selected period.

CSV downloads include every matching record and preserve the current filters. Printable registers also include every matching record rather than only the visible page. The Overview CSV contains payment collections, daily activity and supplier totals. CSV text fields are escaped against spreadsheet formula interpretation. Reports are read-only and do not change stock or transactions. Purchase details show line amounts and posted state; opening an old purchase no longer claims it was just saved.


### Installation on another PC

Use Python 3.11 or newer and create a new virtual environment on that PC; do not copy `.venv`. Check `python --version`, upgrade pip, and install with `python -m pip install -r requirements.txt`. If a configured package mirror is missing releases, use `python -m pip install --index-url https://pypi.org/simple -r requirements.txt`. Keep certificate verification enabled.

Older copies of `requirements.lock` pinned packages from the original environment that may not exist on the recipient's index. Install from `requirements.txt` or replace the old lock file with the updated compatibility entry point. Upgrading pip alone does not make an unavailable pinned version appear. If Python is below 3.11, install a supported Python and recreate the virtual environment before installing.
