import logging
import os
import time

from django.core.management.base import BaseCommand
from django.utils import timezone

from studio.models import GenerationTask, ShotSubtitleSetting, SubtitleTrack, VideoAsset, VideoComposition
from studio.services.subtitles import process_subtitle_task
from studio.services.video import process_export_task, process_video_asset


logger = logging.getLogger(__name__)


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
        processed += self._process_items(
            assets,
            process_video_asset,
            self._record_asset_failure,
            "video asset",
        )
        subtitle_tasks = list(
            GenerationTask.objects.filter(
                task_type=GenerationTask.TYPE_SUBTITLE_ALIGN,
                status=GenerationTask.STATUS_PENDING,
            ).order_by("created_at", "id")[:2]
        )
        processed += self._process_items(
            subtitle_tasks,
            process_subtitle_task,
            self._record_subtitle_failure,
            "subtitle alignment task",
        )
        export_tasks = list(
            GenerationTask.objects.filter(
                task_type=GenerationTask.TYPE_VIDEO_EXPORT,
                status=GenerationTask.STATUS_PENDING,
            ).order_by("created_at", "id")[:2]
        )
        processed += self._process_items(
            export_tasks,
            process_export_task,
            self._record_export_failure,
            "video export task",
        )
        return processed

    def _process_items(self, items, processor, failure_recorder, label):
        processed = 0
        for item in items:
            try:
                processor(item.id)
            except Exception as exc:
                logger.exception("Unhandled failure while processing %s %s", label, item.id)
                self.stderr.write(self.style.ERROR(f"Failed {label} {item.id}: {exc}"))
                try:
                    failure_recorder(item, exc)
                except Exception:
                    logger.exception("Could not record failure for %s %s", label, item.id)
            processed += 1
        return processed

    @staticmethod
    def _record_asset_failure(asset, error):
        now = timezone.now()
        VideoAsset.objects.filter(pk=asset.pk).update(
            status=VideoAsset.STATUS_FAILED,
            error_message=str(error),
            finished_at=now,
            updated_at=now,
        )
        if asset.generation_task_id:
            GenerationTask.objects.filter(pk=asset.generation_task_id).update(
                status=GenerationTask.STATUS_FAILED,
                error_message=str(error),
                finished_at=now,
            )

    @staticmethod
    def _record_subtitle_failure(task, error):
        now = timezone.now()
        GenerationTask.objects.filter(pk=task.pk).update(
            status=GenerationTask.STATUS_FAILED,
            error_message=str(error),
            finished_at=now,
        )
        SubtitleTrack.objects.filter(pk=task.target_id).update(
            status=SubtitleTrack.STATUS_FAILED,
            error_message=str(error),
            updated_at=now,
        )
        ShotSubtitleSetting.objects.filter(
            shot_id__in=(task.input_snapshot or {}).get("shot_ids", [])
        ).update(
            status=ShotSubtitleSetting.STATUS_FAILED,
            error_message=str(error),
            updated_at=now,
        )

    @staticmethod
    def _record_export_failure(task, error):
        now = timezone.now()
        GenerationTask.objects.filter(pk=task.pk).update(
            status=GenerationTask.STATUS_FAILED,
            error_message=str(error),
            finished_at=now,
        )
        composition = VideoComposition.objects.filter(pk=task.target_id).first()
        if composition is None:
            snapshot = task.input_snapshot or {}
            composition = VideoComposition.objects.filter(
                episode_id=snapshot.get("episode_id"),
                version=snapshot.get("composition_version"),
            ).first()
        if composition is not None:
            VideoComposition.objects.filter(pk=composition.pk).update(
                status=VideoComposition.STATUS_FAILED,
                error_message=str(error),
                updated_at=now,
            )
