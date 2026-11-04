import hashlib
import json
import sqlite3
import subprocess
import tempfile
from contextlib import closing
from pathlib import Path
from zipfile import ZipFile, BadZipFile
from django.core.management.base import BaseCommand, CommandError


def verify(path):
    try:
        with ZipFile(path) as archive:
            names = archive.namelist()
            if len(names) != len(set(names)) or archive.testzip():
                raise CommandError("Backup contains duplicate or corrupted entries.")
            manifest = json.loads(archive.read("manifest.json"))
            database = manifest["database"]
            if database not in ("db.sqlite3", "database.dump"):
                raise CommandError("Unsupported backup database entry.")
            checksums = manifest.get("sha256")
            if not checksums:
                raise CommandError(
                    "This legacy archive has no checksums. Create a new backup."
                )
            if set(checksums) != set(names) - {"manifest.json"}:
                raise CommandError("Backup entries do not match the manifest.")
            for name, expected in checksums.items():
                with archive.open(name) as source:
                    digest = hashlib.sha256()
                    for chunk in iter(lambda: source.read(1024 * 1024), b""):
                        digest.update(chunk)
                if digest.hexdigest() != expected:
                    raise CommandError(f"Checksum failed: {name}")
            with tempfile.TemporaryDirectory() as directory:
                database_path = Path(directory) / database
                with (
                    archive.open(database) as source,
                    database_path.open("wb") as target,
                ):
                    import shutil

                    shutil.copyfileobj(source, target)
                if database == "db.sqlite3":
                    with closing(sqlite3.connect(database_path)) as conn:
                        if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                            raise CommandError("SQLite integrity check failed.")
                else:
                    try:
                        subprocess.run(
                            ["pg_restore", "--list", str(database_path)],
                            check=True,
                            capture_output=True,
                        )
                    except (OSError, subprocess.CalledProcessError) as exc:
                        raise CommandError(
                            "pg_restore could not validate this dump. Install a compatible PostgreSQL client."
                        ) from exc
            return manifest
    except (OSError, BadZipFile, KeyError, ValueError, sqlite3.DatabaseError) as exc:
        raise CommandError("Invalid or unreadable backup archive.") from exc


class Command(BaseCommand):
    help = "Verify backup checksums and SQLite integrity or PostgreSQL dump structure."

    def add_arguments(self, parser):
        parser.add_argument("archive")

    def handle(self, *args, **options):
        verify(options["archive"])
        self.stdout.write(self.style.SUCCESS("Backup verification passed."))
