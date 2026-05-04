from django.core.management.base import BaseCommand
from api.management.commands._runtime_waits import wait_for_redis
from api.services.worker_services import OutboxStreamPublisherProcessor, BaseWorkerService


class Command(BaseCommand):
    help = "Long-running daemon to publish transactional outbox events to Redis Streams"

    def add_arguments(self, parser):
        parser.add_argument(
            "--poll-interval",
            type=float,
            default=0.5,
            help="Interval in seconds to poll the outbox when empty",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=100,
            help="Maximum number of events to publish per batch",
        )
        parser.add_argument(
            "--stream-name",
            type=str,
            default="vigilzone:stream:events",
            help="The Redis Stream name to continuously append to",
        )

    def handle(self, *args, **options):
        poll_interval = options["poll_interval"]
        batch_size = options["batch_size"]
        stream_name = options["stream_name"]

        wait_for_redis(self.stdout, self.style)

        # Thin wrapper over SOLID service layer
        processor = OutboxStreamPublisherProcessor(batch_size=batch_size, stream_name=stream_name)
        service = BaseWorkerService(processor, poll_interval=poll_interval)

        self.stdout.write(self.style.SUCCESS(f"Starting {processor.get_name()} daemon [stream={stream_name}]..."))
        service.run_forever()
