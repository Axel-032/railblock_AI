import threading
from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from core.models import MaintenanceRequest

_signal_local = threading.local()


def set_in_scheduling(value: bool):
    _signal_local.in_scheduling = value


def is_in_scheduling() -> bool:
    return getattr(_signal_local, 'in_scheduling', False)


@receiver(post_save, sender=MaintenanceRequest)
def on_maintenance_request_saved(sender, instance, created, **kwargs):
    """
    Automatic trigger when a maintenance request is submitted or updated.
    The system NEVER requires the user to click an Optimize button.
    """
    if is_in_scheduling():
        return

    # Skip if update_fields only contains priority/status/assigned_block/conflict_reason to prevent re-triggering loops
    update_fields = kwargs.get('update_fields')
    if update_fields and set(update_fields).issubset({'assigned_block', 'status', 'conflict_reason', 'calculated_priority_score', 'ai_justification'}):
        return

    from scheduler.services.scheduler_service import run_automatic_scheduling
    try:
        set_in_scheduling(True)
        run_automatic_scheduling(trigger_source=f"MaintenanceRequest {instance.request_code} {'Created' if created else 'Updated'}")
    finally:
        set_in_scheduling(False)


@receiver(post_delete, sender=MaintenanceRequest)
def on_maintenance_request_deleted(sender, instance, **kwargs):
    """
    Automatic re-optimization if a maintenance request is cancelled/deleted.
    """
    if is_in_scheduling():
        return

    from scheduler.services.scheduler_service import run_automatic_scheduling
    try:
        set_in_scheduling(True)
        run_automatic_scheduling(trigger_source=f"MaintenanceRequest {instance.request_code} Deleted")
    finally:
        set_in_scheduling(False)
