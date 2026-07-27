import os
import time
from concurrent.futures import ThreadPoolExecutor, wait

from django.core.management.base import BaseCommand
from django.db import close_old_connections, connection

from studio.services.episode_workflow import advance_active_workflows
from studio.generation_queue import (
    claim_next_task,
    process_claimed_task,
    recover_expired_leases,
    worker_id,
)


class Command(BaseCommand):
    help = "Process persistent script, character, storyboard, and cover tasks."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")
        parser.add_argument(
            "--interval",
            type=float,
            default=float(os.environ.get("GENERATION_WORKER_INTERVAL_SECONDS", "2")),
        )
        parser.add_argument(
            "--concurrency",
            type=int,
            default=int(os.environ.get("GENERATION_MAX_CONCURRENCY", "2")),
        )

    def handle(self, *args, **options):
        owner = worker_id()
        concurrency = max(1, options["concurrency"])
        if connection.vendor == "sqlite":
            self._handle_serial(owner, options)
            return
        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            futures = set()
            while True:
                advance_active_workflows()
                recover_expired_leases()
                futures = {future for future in futures if not future.done()}
                claimed_count = 0
                while len(futures) < concurrency:
                    task = claim_next_task(owner)
                    if task is None:
                        break
                    futures.add(executor.submit(self._process, task.id, owner))
                    claimed_count += 1
                if options["once"]:
                    wait(futures)
                    self.stdout.write(
                        self.style.SUCCESS(f"Processed {claimed_count} generation task(s).")
                    )
                    return
                time.sleep(max(0.5, options["interval"]))

    def _handle_serial(self, owner, options):
        while True:
            advance_active_workflows()
            recover_expired_leases()
            task = claim_next_task(owner)
            claimed_count = 0
            if task is not None:
                process_claimed_task(task.id, owner)
                claimed_count = 1
            if options["once"]:
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Processed {claimed_count} generation task(s)."
                    )
                )
                return
            time.sleep(max(0.5, options["interval"]))

    @staticmethod
    def _process(task_id, owner):
        close_old_connections()
        try:
            return process_claimed_task(task_id, owner)
        finally:
            close_old_connections()
