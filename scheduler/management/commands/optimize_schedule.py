from django.core.management.base import BaseCommand
from scheduler.services.scheduler_service import run_automatic_scheduling
from core.models import MaintenanceRequest, RailwayBlock, OptimizationLog


class Command(BaseCommand):
    help = "Run the automatic Google OR-Tools CP-SAT scheduling engine for RailBlock AI"

    def handle(self, *args, **options):
        self.stdout.write(self.style.NOTICE("Starting RailBlock AI Automatic Scheduling Engine..."))

        result = run_automatic_scheduling(trigger_source="CLI Management Command (optimize_schedule)", force=True)

        self.stdout.write(self.style.SUCCESS(f"Engine Run Status: {result.get('status')}"))
        metrics = result.get('metrics', {})
        self.stdout.write(f"Solve Time: {metrics.get('solve_time_ms', 0):.2f} ms")
        self.stdout.write(f"Assignments Made: {metrics.get('total_assigned', 0)}")
        self.stdout.write(f"Bundled Tasks: {metrics.get('bundled_count', 0)}")
        self.stdout.write(f"Conflicts: {metrics.get('conflicts_count', 0)}")

        self.stdout.write("\n" + "=" * 60)
        self.stdout.write("CURRENT BLOCK ALLOCATIONS:")
        self.stdout.write("=" * 60)

        for block in RailwayBlock.objects.all():
            tasks = block.maintenance_tasks.all()
            self.stdout.write(
                f"[{block.block_code}] {block.location} ({block.start_time.strftime('%H:%M')} - {block.end_time.strftime('%H:%M')}) "
                f"| Util: {block.utilization_percent}% | Tasks: {tasks.count()}"
            )
            for t in tasks:
                self.stdout.write(
                    f"   --> [{t.team.code.upper()}] {t.request_code}: {t.maintenance_type} ({t.estimated_duration}m)"
                )
            if block.ai_summary:
                self.stdout.write(f"   AI Summary: {block.ai_summary[:90]}...")

        conflicts = MaintenanceRequest.objects.filter(status='CONFLICT')
        if conflicts.exists():
            self.stdout.write("\n" + self.style.WARNING("CONFLICTING / UNSCHEDULED REQUESTS:"))
            for c in conflicts:
                self.stdout.write(f"   [!] {c.request_code} ({c.team.code.upper()}): {c.conflict_reason}")

        self.stdout.write(self.style.SUCCESS("\nSCHEDULE UPDATED SUCCESSFULLY."))
