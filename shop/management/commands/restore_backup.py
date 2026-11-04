from pathlib import Path, PurePosixPath
from zipfile import ZipFile
from django.core.management.base import BaseCommand, CommandError
from .verify_backup import verify


class Command(BaseCommand):
    help = "Restore a verified SQLite backup into a NEW directory for a recovery drill. Never replaces live files."

    def add_arguments(self, parser):
        parser.add_argument("archive")
        parser.add_argument("--destination", required=True)

    def handle(self, *args, **options):
        manifest = verify(options["archive"])
        if manifest["database"] != "db.sqlite3":
            raise CommandError(
                "PostgreSQL: restore database.dump into a new database with pg_restore; this command restores SQLite only."
            )
        destination = Path(options["destination"]).resolve()
        if destination.exists():
            raise CommandError(
                "Destination already exists. Choose a new recovery directory."
            )
        with ZipFile(options["archive"]) as archive:
            entries = []
            for name in archive.namelist():
                relative = PurePosixPath(name)
                target = (destination / Path(*relative.parts)).resolve()
                if (
                    relative.is_absolute()
                    or ".." in relative.parts
                    or "\\" in name
                    or not target.is_relative_to(destination)
                ):
                    raise CommandError("Unsafe path in backup archive.")
                entries.append((name, target))
            destination.mkdir(parents=True)
            for name, target in entries:
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(name) as source, target.open("xb") as output:
                    import shutil

                    shutil.copyfileobj(source, output)
        self.stdout.write(
            self.style.SUCCESS(f"Restored into separate directory: {destination}")
        )
