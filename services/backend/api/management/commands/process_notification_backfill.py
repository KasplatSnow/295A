from django.core.management.base import BaseCommand

from api.management.commands._runtime_waits import wait_for_redis
from api.services.worker_services import BaseWorkerService, NotificationBackfillProcessor


class Command(BaseCommand):
    help = "Process incident events into user notifications as a daemon."

    def add_arguments(self, parser):
        parser.add_argument(
            "--poll-interval",
            type=float,
            default=1.0,
            help="Seconds to sleep when the event stream is idle.",
        )

    def handle(self, *args, **options):
        poll_interval = options["poll_interval"]

        wait_for_redis(self.stdout, self.style)

        processor = NotificationBackfillProcessor()
        service = BaseWorkerService(processor, poll_interval=poll_interval)

        self.stdout.write(self.style.SUCCESS(f"Starting {processor.get_name()} daemon..."))
        service.run_forever()
