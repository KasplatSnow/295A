from django.core.management.base import BaseCommand
from api.management.commands._runtime_waits import wait_for_ai
from api.services.worker_services import EntityEmbeddingProcessor, BaseWorkerService


class Command(BaseCommand):
    help = "Process queued entity enrollment/embedding jobs as a daemon."

    def add_arguments(self, parser):
        parser.add_argument(
            "--limit",
            type=int,
            default=10,
            help="Maximum number of queued jobs to process per batch.",
        )
        parser.add_argument(
            "--poll-interval",
            type=float,
            default=2.0,
            help="Seconds to sleep when the queue is empty.",
        )

    def handle(self, *args, **options):
        limit = options["limit"]
        poll_interval = options["poll_interval"]

        wait_for_ai(self.stdout, self.style)

        # Thin wrapper over SOLID service layer
        processor = EntityEmbeddingProcessor(limit=limit)
        service = BaseWorkerService(processor, poll_interval=poll_interval)
        
        self.stdout.write(self.style.SUCCESS(f"Starting {processor.get_name()} daemon..."))
        service.run_forever()
