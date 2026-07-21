import os
import socket
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, wait

from django.core.management.base import BaseCommand
from django.db import close_old_connections, connection

from studio.publishing.service import claim_next_task, process_claimed_task, recover_expired_leases


class Command(BaseCommand):
    help = "Process queued publishing tasks."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")
        parser.add_argument("--interval", type=float, default=float(os.environ.get("PUBLISH_WORKER_INTERVAL_SECONDS", "3")))
        parser.add_argument("--concurrency", type=int, default=int(os.environ.get("PUBLISH_MAX_CONCURRENCY", "2")))

    def handle(self, *args, **options):
        worker_id = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
        concurrency = max(1, options["concurrency"])
        if connection.vendor == "sqlite":
            concurrency = 1
        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            futures = set()
            while True:
                recover_expired_leases()
                futures = {future for future in futures if not future.done()}
                while len(futures) < concurrency:
                    task = claim_next_task(worker_id)
                    if task is None:
                        break
                    futures.add(executor.submit(self._process, task.id, worker_id))
                if options["once"]:
                    wait(futures)
                    self.stdout.write(self.style.SUCCESS(f"Processed {len(futures)} publishing task(s)."))
                    return
                time.sleep(max(1.0, options["interval"]))

    @staticmethod
    def _process(task_id, worker_id):
        close_old_connections()
        try:
            return process_claimed_task(task_id, worker_id)
        finally:
            close_old_connections()
