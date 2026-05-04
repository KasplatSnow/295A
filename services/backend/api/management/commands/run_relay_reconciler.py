"""
Django management command to run the relay reconciler as a long-lived worker.

Usage:
    python manage.py run_relay_reconciler              # active mode, infinite loop
    python manage.py run_relay_reconciler --shadow      # shadow/verify-only mode
    python manage.py run_relay_reconciler --once        # single sweep then exit
    python manage.py run_relay_reconciler --interval 15 # custom poll interval
"""
import logging
import os
import signal
import sys
import time

from django.core.management.base import BaseCommand

from api.management.commands._runtime_waits import wait_for_mediamtx
from api.services.relay_reconciler import RelayReconciler

logger = logging.getLogger("run_relay_reconciler")


class Command(BaseCommand):
    help = (
        "Runs the MediaMTX relay reconciler worker. "
        "Reads desired state from Postgres and applies to MediaMTX."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--once",
            action="store_true",
            default=False,
            help="Run a single reconciliation sweep and exit.",
        )
        parser.add_argument(
            "--shadow",
            action="store_true",
            default=False,
            help="Shadow mode: verify and log drift without applying changes.",
        )
        parser.add_argument(
            "--interval",
            type=float,
            default=None,
            help=(
                "Poll interval in seconds between reconcile sweeps. "
                "Defaults to RECONCILER_POLL_INTERVAL_S env var or 10s."
            ),
        )

    def handle(self, *args, **options):
        once = options["once"]
        shadow = options["shadow"]
        interval = options["interval"] or float(
            os.getenv("RECONCILER_POLL_INTERVAL_S", "10")
        )

        mode_label = "SHADOW" if shadow else "ACTIVE"
        self.stdout.write(
            self.style.WARNING(
                f"Starting relay reconciler [{mode_label}] "
                f"(interval={interval}s, once={once})"
            )
        )

        reconciler = RelayReconciler(shadow_mode=shadow)

        if once:
            result = reconciler.reconcile_all()
            self._print_result(result)
            return

        # Graceful shutdown handling
        stop = False

        def shutdown_handler(signum, frame):
            nonlocal stop
            self.stdout.write(
                self.style.WARNING("\nShutdown signal received. Stopping reconciler...")
            )
            stop = True

        signal.signal(signal.SIGINT, shutdown_handler)
        signal.signal(signal.SIGTERM, shutdown_handler)

        # Wait for MediaMTX to become reachable before starting loop
        wait_for_mediamtx(self.stdout, self.style)

        while not stop:
            try:
                result = reconciler.reconcile_all()
                if result.failed > 0:
                    logger.warning(
                        "Reconcile sweep: %d applied, %d removed, %d verified, %d failed",
                        result.applied,
                        result.removed,
                        result.verified,
                        result.failed,
                    )
                elif result.total > 0:
                    logger.info(
                        "Reconcile sweep: %d applied, %d removed, %d verified",
                        result.applied,
                        result.removed,
                        result.verified,
                    )
            except Exception as exc:
                logger.error("Reconcile sweep failed: %s", exc, exc_info=True)

            # Sleep in small increments so shutdown is responsive
            waited = 0.0
            while waited < interval and not stop:
                time.sleep(min(1.0, interval - waited))
                waited += 1.0

        self.stdout.write(self.style.SUCCESS("Relay reconciler stopped cleanly."))
    def _print_result(self, result):
        """Pretty-print a reconcile result."""
        import json

        self.stdout.write(json.dumps(result.as_dict(), indent=2))
        if result.failed > 0:
            self.stderr.write(
                self.style.WARNING(
                    f"\nReconciliation completed with {result.failed} failures."
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    f"\nReconciliation completed. "
                    f"{result.applied} applied, {result.removed} removed, "
                    f"{result.verified} verified."
                )
            )
