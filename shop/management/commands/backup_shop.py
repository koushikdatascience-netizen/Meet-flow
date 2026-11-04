"""Create a database and uploaded-media backup without including secrets."""

from contextlib import closing
import json
import hashlib
import os
import sqlite3
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection


class Command(BaseCommand):
    help = "Back up the database and media into a ZIP. PostgreSQL requires pg_dump."

    def add_arguments(self, parser):
        parser.add_argument("--output", help="New ZIP file path (never overwrites).")

    def handle(self, *args, **options):
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        target = Path(
            options["output"] or settings.BASE_DIR / "backups" / f"meatflow-{stamp}.zip"
        )
        if target.exists():
            raise CommandError("Backup path already exists; choose a new file.")
        target.parent.mkdir(parents=True, exist_ok=True)
        engine = connection.settings_dict["ENGINE"]
        with tempfile.TemporaryDirectory() as temporary:
            temporary = Path(temporary)
            if engine.endswith("sqlite3"):
                source_path = Path(connection.settings_dict["NAME"])
                if not source_path.is_file():
                    raise CommandError("Database does not exist. Run migrate first.")
                database = temporary / "db.sqlite3"
                with closing(
                    sqlite3.connect(
                        f"{source_path.resolve().as_uri()}?mode=ro", uri=True
                    )
                ) as source:
                    with closing(sqlite3.connect(database)) as destination:
                        source.backup(destination)
            elif engine.endswith("postgresql"):
                database = temporary / "database.dump"
                config = connection.settings_dict
                environment = os.environ.copy()
                environment["PGPASSWORD"] = config.get("PASSWORD", "")
                command = [
                    "pg_dump",
                    "--format=custom",
                    "--no-owner",
                    "--file",
                    str(database),
                    "--dbname",
                    config["NAME"],
                    "--username",
                    config.get("USER", ""),
                    "--host",
                    config.get("HOST") or "localhost",
                    "--port",
                    str(config.get("PORT") or 5432),
                ]
                try:
                    subprocess.run(
                        command, env=environment, check=True, capture_output=True
                    )
                except (OSError, subprocess.CalledProcessError) as exc:
                    raise CommandError(
                        "Database backup failed. Check pg_dump installation and database connectivity."
                    ) from exc
            else:
                raise CommandError("Only SQLite and PostgreSQL are supported.")
            archive_path = temporary / "backup.zip"
            checksums = {}
            with ZipFile(archive_path, "w", ZIP_DEFLATED) as archive:

                def add_file(path, name):
                    digest = hashlib.sha256()
                    with path.open("rb") as source, archive.open(name, "w") as output:
                        for chunk in iter(lambda: source.read(1024 * 1024), b""):
                            digest.update(chunk)
                            output.write(chunk)
                    checksums[name] = digest.hexdigest()

                add_file(database, database.name)
                media = Path(settings.MEDIA_ROOT)
                if media.exists():
                    for item in sorted(media.rglob("*")):
                        if (
                            item.is_file()
                            and not item.is_symlink()
                            and item.resolve().is_relative_to(media.resolve())
                        ):
                            add_file(
                                item, "media/" + item.relative_to(media).as_posix()
                            )
                archive.writestr(
                    "manifest.json",
                    json.dumps(
                        {
                            "version": 2,
                            "created_at_utc": stamp,
                            "engine": engine,
                            "database": database.name,
                            "sha256": checksums,
                        },
                        indent=2,
                    ),
                )
            # Exclusive creation prevents accidentally replacing an existing backup.
            with target.open("xb") as destination, archive_path.open("rb") as source:
                import shutil

                shutil.copyfileobj(source, destination)
        self.stdout.write(self.style.SUCCESS(f"Backup created: {target.resolve()}"))
