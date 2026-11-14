# Render deployment of the existing Meet-flow service

## One-time live stock cleanup

Without environment variables: sign in to the live Render app as the owner (superuser), open Users → Clear stock data, review the scope, tick the confirmation and submit. A session-bound one-use token, CSRF protection and owner-only authorization protect the action. The page is disabled outside a configured Render host. It uses the same atomic persistent backup and preserves accounts, roles and categories. Opening the page alone does not delete anything.

Only when the owner explicitly requests a full stock reset: set `MEATFLOW_CLEAR_STOCK_ONCE` to a new UUID on the live Render service, then deploy with Docker Command blank. The command only runs when `RENDER=true`. It locks the workspaces, serializes the active and legacy item/stock/transaction tables, verifies a SHA-256 checked compressed backup, stores that backup persistently in `inventory_stockclearbackup`, and clears those tables in the same transaction. Backup failure rolls back the operation. Users, memberships, permissions, categories, settings and previous backups remain. Existing numeric IDs are not reset. The same UUID never clears stock again, so subsequent restarts cannot erase newly entered records. Remove the variable immediately after the successful cleanup message. A new UUID requests another full cleanup; do not rotate it casually. Backups contain business records and have no public download route. Local data is not changed by deployment. This command does not transfer or clear the developer's local database.

The Docker image now serves static files using WhiteNoise, reads Render's exact assigned hostname, trusts Render's HTTPS proxy, accepts `DATABASE_URL`, runs migrations on startup and binds Gunicorn to `$PORT`. It does not create accounts or publish local data automatically.

In the existing Render **Web Service**:

- Repository: `koushikdatascience-netizen/Meet-flow`, branch `main`.
- Runtime: Docker. Dockerfile path `./Dockerfile`.
- Docker Command: leave blank to use the image's new startup command. An old override such as `gunicorn ...` bypasses migrations.
- Health check: `/healthz/`.
- Auto-deploy: enable if desired, or Manual Deploy > Deploy latest commit.
- `DJANGO_DEBUG=0`.
- `DJANGO_SECRET_KEY`: a private random value of at least 50 characters; keep the same key across deployments.
- `DATABASE_URL`: PostgreSQL internal connection URL from a database in the same Render region. Keep it private. Existing separate `POSTGRES_*` credentials also work.
- Render supplies `RENDER_EXTERNAL_HOSTNAME` and `PORT`; the code reads them automatically.
- Custom domains, if any: include exact names in `DJANGO_ALLOWED_HOSTS` and HTTPS origins in `DJANGO_CSRF_TRUSTED_ORIGINS`.

Do not deploy business records using SQLite inside the ephemeral container. Startup fails explicitly instead of silently losing stock and users after a restart. `SQLITE_PATH` is supported only when you have deliberately attached a persistent disk and point it there; PostgreSQL is preferred.

The local database is not part of Git or the Docker image. A fresh Render database needs its own administrator. In the Render Shell, run `python manage.py createsuperuser` once, then log in at `/login/` to open the internal stock workspace. Create staff accounts under Users. Account passwords are not embedded in the code. Existing deployed records are upgraded in the connected database; if legacy products exist, an active superuser must exist before the import migration. Back up before upgrading a database containing existing business records.

### Administrator credentials without Render Shell

Set only `MEATFLOW_BOOTSTRAP_USERNAME` and `MEATFLOW_BOOTSTRAP_PASSWORD` in the private Render environment. These are the authoritative owner administrator credentials while they remain set. Startup creates the first admin or updates the matching existing superuser. When only one superuser exists, changing the configured username renames that account while retaining its identity and history. With multiple superusers, the username must match the exact target. Regular users are never promoted or overwritten. Passwords must pass Django validation and are hashed; unchanged passwords keep their hash so ordinary redeploys retain sessions. A changed password invalidates old sessions. If you change the admin password within the app, also update the Render environment, otherwise the configured password will be restored on the next deployment. Remove both variables only if you want to manage the account password exclusively inside the app.

The separate `MEATFLOW_RESET_*` recovery variables are no longer used. Remove them if you added any. Do not publish or share password values. Keep Docker Command blank and deploy the latest commit. Logs confirm credential synchronization without printing the password.

The startup migration is appropriate for this single-service setup. For multiple instances, move migrations to one paid pre-deploy command and remove the startup migration from that environment's startup command so deployments do not run migrations concurrently.

Check `/login/`, `/static/inventory/workspace.css`, and `/healthz/` after deployment. Verify login, role access and the stock report after deployment. Use a separate test database for test transactions. Docker engine testing is also covered in the GitHub Actions workflow.
