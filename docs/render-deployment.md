# Render deployment of the existing Meet-flow service

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

The local database is not part of Git or the Docker image. A fresh Render database needs its own administrator. In the Render Shell, run `python manage.py createsuperuser` once, then log in at `/login/` and create a company and its admin. Account passwords are not embedded in the code. Existing deployed records are upgraded in the connected database; if legacy products exist, an active superuser must exist before the import migration. Back up before upgrading a database containing existing business records.

### First administrator without Render Shell

For a fresh database, set `MEATFLOW_BOOTSTRAP_USERNAME` and `MEATFLOW_BOOTSTRAP_PASSWORD` as private environment variables on the existing Render service. Choose a unique username and a strong password. Save and deploy the latest commit with Docker Command blank. After migrations, startup creates the first superuser with a hashed password. No default password is supplied, and neither value is printed in logs. Log in at `/login/` using your chosen credentials, then remove both bootstrap variables and save again. Existing superusers cause bootstrap to skip; existing regular accounts are never promoted or reset. Do not commit these values or share them in chat. This is only initial setup, not password recovery.

### Forgotten platform administrator password

The Render account owner can set `MEATFLOW_RESET_PASSWORD` to a new strong password and `MEATFLOW_RESET_ID` to a fresh UUID, then deploy. If there is exactly one active superuser, that account is selected and its username is printed in the logs. With multiple admins, also set `MEATFLOW_RESET_USERNAME` to the exact target. Only an existing active superuser can be reset. Startup records the recovery ID in the database and changes the password once, invalidating old sessions through Django's password session hash. Reusing the same ID never reapplies the password, including after the user changes it. No public recovery endpoint is provided. Remove all `MEATFLOW_RESET_*` and old bootstrap variables immediately after successful login. Do not send or commit passwords. Keep the Docker Command blank so the startup recovery command runs.

The startup migration is appropriate for this single-service setup. For multiple instances, move migrations to one paid pre-deploy command and remove the startup migration from that environment's startup command so deployments do not run migrations concurrently.

Check `/login/`, `/static/inventory/workspace.css`, and `/healthz/` after deployment. Verify company login, a test stock entry and a CSV report in a test company before using live stock. Docker engine testing is also covered in the GitHub Actions workflow.
