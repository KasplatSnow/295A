import logging
import os
import signal
import sys
import threading
import time

from django.core.management.base import BaseCommand
from api.services.worker_services import (
    EntityEmbeddingProcessor,
    OutboxStreamPublisherProcessor,
    RelayReconcilerProcessor,
    BaseWorkerService,
)
from api.management.commands._runtime_waits import (
    wait_for_ai,
    wait_for_mediamtx,
    wait_for_redis,
)

logger = logging.getLogger("run_worker_node")

class Command(BaseCommand):
    help = "[DEV-ONLY] Runs all background worker threads in one process for local development."

    def add_arguments(self, parser):
        parser.add_argument(
            "--poll-interval",
            type=float,
            default=1.0,
            help="Polling interval for all workers (except reconciler)",
        )
        parser.add_argument(
            "--reconciler-shadow",
            action="store_true",
            default=False,
            help="Run the relay reconciler in shadow (verify-only) mode.",
        )

    def handle(self, *args, **options):
        poll_interval = options["poll_interval"]
        reconciler_shadow = options["reconciler_shadow"]
        reconciler_interval = float(
            os.getenv("RECONCILER_POLL_INTERVAL_S", "10")
        )

        self.stdout.write(self.style.WARNING("!!! [DEV-ONLY] Starting Unified Worker Node !!!"))
        self.stdout.write("Use only for local development and testing. Deploy separate workers in cloud.")
        wait_for_redis(self.stdout, self.style)
        wait_for_ai(self.stdout, self.style)
        wait_for_mediamtx(self.stdout, self.style)

        # Instantiate processors
        processors = [
            EntityEmbeddingProcessor(limit=10),
            OutboxStreamPublisherProcessor(batch_size=100),
        ]
        reconciler_processor = RelayReconcilerProcessor(shadow_mode=reconciler_shadow)

        # Wrap in services (reconciler gets its own poll interval)
        services = [BaseWorkerService(p, poll_interval=poll_interval) for p in processors]
        services.append(
            BaseWorkerService(reconciler_processor, poll_interval=reconciler_interval)
        )
        threads = []

        def run_service(svc):
            try:
                svc.run_forever()
            except Exception as e:
                logger.error(f"Worker thread crashed: {e}", exc_info=True)

        for svc in services:
            t = threading.Thread(target=run_service, args=(svc,), daemon=True)
            t.start()
            threads.append(t)
            self.stdout.write(self.style.SUCCESS(f"Spawned thread for {svc.processor.get_name()}"))

        self.stdout.write(self.style.SUCCESS("All workers running. Press Ctrl+C to stop."))

        # Cross-platform graceful shutdown handling
        def shutdown_handler(signum, frame):
            self.stdout.write(self.style.WARNING("\nShutdown signal received. Stopping workers..."))
            for svc in services:
                svc.stop()
            # We don't join threads here to avoid blocking a signal handler
            # instead we just exit as they are daemon=True
            sys.exit(0)

        signal.signal(signal.SIGINT, shutdown_handler)
        signal.signal(signal.SIGTERM, shutdown_handler)

        try:
            # Keep main thread alive
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            shutdown_handler(None, None)
