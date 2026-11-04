import os
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Run verified backups on an interval. Run as a supervised service. No files are pruned automatically."

    def add_arguments(self, parser):
        parser.add_argument(
            "--destination", default=os.environ.get("BACKUP_DIR", "backups")
        )
        parser.add_argument("--copy-to", default=os.environ.get("BACKUP_COPY_DIR", ""))
        parser.add_argument("--interval-hours", type=float, default=24)
        parser.add_argument("--once", action="store_true")

    def handle(self, *args, **options):
        if options["interval_hours"] <= 0:
            raise CommandError("Interval must be positive.")
        while True:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            target = Path(options["destination"]) / f"meatflow-{stamp}.zip"
            call_command("backup_shop", output=str(target))
            call_command("verify_backup", str(target))
            if options["copy_to"]:
                external = Path(options["copy_to"])
                # A missing external drive must fail rather than silently write locally.
                if not external.is_dir():
                    raise CommandError("External backup directory is unavailable.")
                destination = external / target.name
                with target.open("rb") as source, destination.open("xb") as output:
                    shutil.copyfileobj(source, output)
                call_command("verify_backup", str(destination))
            if options["once"]:
                return
            time.sleep(options["interval_hours"] * 3600)
