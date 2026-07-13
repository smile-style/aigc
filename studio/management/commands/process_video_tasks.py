import os
import time

from django.core.management.base import BaseCommand

from studio.models import GenerationTask, VideoAsset
from studio.services.video import process_export_task, process_video_asset


class Command(BaseCommand):
    help = "Process queued and running video generation/export tasks."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="Process one pass and exit.")
        parser.add_argument("--interval", type=float, default=float(os.environ.get("VIDEO_WORKER_INTERVAL_SECONDS", "5")))

    def handle(self, *args, **options):
        while True:
            processed = self._process_pass()
            if options["once"]:
                self.stdout.write(self.style.SUCCESS(f"Processed {processed} task(s)."))
                return
            time.sleep(max(1.0, options["interval"]))

    def _process_pass(self):
        processed = 0
        assets = list(
            VideoAsset.objects.filter(
                status__in=[VideoAsset.STATUS_QUEUED, VideoAsset.STATUS_SUBMITTING, VideoAsset.STATUS_RUNNING]
            ).order_by("created_at", "id")[:20]
        )
        for asset in assets:
            process_video_asset(asset.id)
            processed += 1
        export_tasks = list(
            GenerationTask.objects.filter(
                task_type=GenerationTask.TYPE_VIDEO_EXPORT,
                status=GenerationTask.STATUS_PENDING,
            ).order_by("created_at", "id")[:2]
        )
        for task in export_tasks:
            process_export_task(task.id)
            processed += 1
        return processed
