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

The startup migration is appropriate for this single-service setup. For multiple instances, move migrations to one paid pre-deploy command and remove the startup migration from that environment's startup command so deployments do not run migrations concurrently.

Check `/login/`, `/static/inventory/workspace.css`, and `/healthz/` after deployment. Verify company login, a test stock entry and a CSV report in a test company before using live stock. Docker engine testing is also covered in the GitHub Actions workflow.
