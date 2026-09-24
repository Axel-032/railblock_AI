from django.db import models
from django.utils import timezone
import datetime


class Team(models.Model):
    """
    Main user roles: Engineering, Signal, Traction, Operations.
    """
    TEAM_CHOICES = [
        ('tms', 'Track Management System (TMS)'),
        ('smms', 'Signal Maintenance & Management System (SMMS)'),
        ('trd', 'Traction Distribution System (TRD)'),
        ('engineering', 'Track Management System (TMS)'),
        ('signal', 'Signal Maintenance & Management System (SMMS)'),
        ('traction', 'Traction Distribution System (TRD)'),
        ('operations', 'Joint Operations / Master Control'),
    ]

    code = models.CharField(max_length=20, unique=True, choices=TEAM_CHOICES)
    name = models.CharField(max_length=100)
    department_name = models.CharField(max_length=150, help_text="e.g. Civil Engineering (P-Way)")
    lead_name = models.CharField(max_length=100, default="Section Controller")
    badge_color = models.CharField(max_length=20, default="#0075F2")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.name} ({self.code})"


class Asset(models.Model):
    """
    Track and corridor assets (P-Way track, Point Machines, 25kV OHE, etc.)
    """
    CRITICALITY_CHOICES = [
        ('CRITICAL', 'Critical'),
        ('HIGH', 'High'),
        ('MEDIUM', 'Medium'),
        ('LOW', 'Low'),
    ]

    asset_id = models.CharField(max_length=50, unique=True)
    asset_type = models.CharField(max_length=100)
    location = models.CharField(max_length=100)
    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name='assets')
    criticality = models.CharField(max_length=20, choices=CRITICALITY_CHOICES, default='MEDIUM')
    last_inspection_date = models.DateField(default=timezone.now)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.asset_id} - {self.asset_type} ({self.location})"


class TrainSchedule(models.Model):
    """
    Train timetable on the corridor. Represents restricted windows where maintenance cannot conflict.
    """
    PRIORITY_TIERS = [
        (1, 'Tier 1 - Premium (Vande Bharat / Shatabdi / Rajdhani)'),
        (2, 'Tier 2 - Mail / Express'),
        (3, 'Tier 3 - Passenger / Suburban'),
        (4, 'Tier 4 - Freight'),
    ]

    train_number = models.CharField(max_length=20)
    train_name = models.CharField(max_length=120)
    location = models.CharField(max_length=100)
    start_time = models.TimeField(help_text="Arrival / start of train occupancy")
    end_time = models.TimeField(help_text="Departure / clearance of track segment")
    priority_tier = models.IntegerField(choices=PRIORITY_TIERS, default=2)
    track_direction = models.CharField(max_length=20, default="UP Main")

    class Meta:
        ordering = ['start_time']

    def __str__(self):
        return f"{self.train_number} {self.train_name} @ {self.location} ({self.start_time.strftime('%H:%M')}-{self.end_time.strftime('%H:%M')})"


class RailwayBlock(models.Model):
    """
    Corridor possession blocks (e.g. BLK-101, BLK-102, BLK-103, BLK-104).
    """
    STATUS_CHOICES = [
        ('SCHEDULED', 'Scheduled'),
        ('ACTIVE', 'Active'),
        ('COMPLETED', 'Completed'),
        ('CONFLICT', 'Conflict'),
        ('EMERGENCY', 'Emergency'),
    ]

    block_code = models.CharField(max_length=30, unique=True)
    location = models.CharField(max_length=100)
    date = models.DateField(default=timezone.now)
    start_time = models.TimeField()
    end_time = models.TimeField()
    duration_minutes = models.IntegerField(help_text="Window length in minutes")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='SCHEDULED')
    is_power_cut_required = models.BooleanField(default=False, help_text="25kV OHE isolation")
    max_capacity_minutes = models.IntegerField(default=120)
    ai_summary = models.TextField(blank=True, default="", help_text="Llama 3.2 generated operational justification")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['start_time']

    def __str__(self):
        return f"{self.block_code} ({self.location} {self.start_time.strftime('%H:%M')}-{self.end_time.strftime('%H:%M')} [{self.status}])"

    @property
    def primary_team_name(self):
        """Lead team on this block or Multi-Agency if multiple tasks combined"""
        teams = list(self.maintenance_tasks.values_list('team__name', flat=True).distinct())
        if len(teams) > 1:
            return "Multi-Agency (" + " + ".join([t.replace(' Team', '') for t in teams]) + ")"
        elif len(teams) == 1:
            return teams[0]
        return "Operations"

    @property
    def primary_task_summary(self):
        """Summary of tasks assigned to this block"""
        tasks = list(self.maintenance_tasks.values_list('maintenance_type', flat=True))
        if tasks:
            return ", ".join(tasks[:2]) + (f" (+{len(tasks)-2} more)" if len(tasks) > 2 else "")
        return "General Line Possession"

    @property
    def utilized_minutes(self):
        """Sum of scheduled task durations in this block"""
        return sum(t.estimated_duration for t in self.maintenance_tasks.all())

    @property
    def utilization_percent(self):
        if self.duration_minutes > 0:
            pct = round((self.utilized_minutes / self.duration_minutes) * 100)
            return min(pct, 100)
        return 0


class MaintenanceRequest(models.Model):
    """
    Submitted maintenance requests from Engineering, Signal, Traction teams.
    """
    SEVERITY_CHOICES = [
        ('CRITICAL', 'Critical'),
        ('HIGH', 'High'),
        ('MEDIUM', 'Medium'),
        ('LOW', 'Low'),
    ]

    STATUS_CHOICES = [
        ('PENDING', 'Pending'),
        ('SCHEDULED', 'Scheduled'),
        ('ACTIVE', 'Active'),
        ('COMPLETED', 'Completed'),
        ('CONFLICT', 'Conflict'),
    ]

    request_code = models.CharField(max_length=30, unique=True)
    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name='requests')
    asset_id = models.CharField(max_length=50)
    asset_type = models.CharField(max_length=100)
    location = models.CharField(max_length=100)
    maintenance_type = models.CharField(max_length=120)
    description = models.TextField()
    severity = models.CharField(max_length=20, choices=SEVERITY_CHOICES, default='MEDIUM')
    is_emergency = models.BooleanField(default=False)
    estimated_duration = models.IntegerField(help_text="Duration in minutes (e.g. 45, 60, 90)")
    preferred_date = models.DateField(default=timezone.now)
    preferred_time = models.TimeField(null=True, blank=True)
    required_resources = models.CharField(max_length=200, help_text="e.g. Tamping Machine + Gang, Tower Wagon")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='PENDING')
    assigned_block = models.ForeignKey(
        RailwayBlock,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='maintenance_tasks'
    )
    calculated_priority_score = models.FloatField(default=0.0)
    conflict_reason = models.TextField(blank=True, default='')
    ai_justification = models.TextField(blank=True, default='')
    # Fields determined by Lagrangian CP-SAT optimization
    scheduled_start_time = models.TimeField(null=True, blank=True)
    scheduled_end_time = models.TimeField(null=True, blank=True)
    is_joint = models.BooleanField(default=False, help_text="Co-scheduled in joint shadow block")
    joined_with_codes = models.CharField(max_length=200, blank=True, default='')
    slot_period_desc = models.CharField(max_length=150, blank=True, default='')
    traffic_criticality = models.FloatField(default=0.0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-calculated_priority_score', '-created_at']

    def __str__(self):
        return f"{self.request_code} [{self.team.code.upper()}]: {self.maintenance_type} @ {self.location} ({self.status})"


class Notification(models.Model):
    """
    Alerts for teams and operations controllers.
    """
    NOTIFICATION_TYPES = [
        ('SCHEDULED', 'Scheduled Block'),
        ('CONFLICT', 'Conflict Alert'),
        ('EMERGENCY', 'Emergency Request'),
        ('AI_RECOMMENDATION', 'AI Recommendation'),
        ('SYSTEM', 'System Notice'),
    ]

    team = models.ForeignKey(Team, null=True, blank=True, on_delete=models.CASCADE, related_name='notifications')
    title = models.CharField(max_length=200)
    message = models.TextField()
    notification_type = models.CharField(max_length=30, choices=NOTIFICATION_TYPES, default='SYSTEM')
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"[{self.notification_type}] {self.title}"


class OptimizationLog(models.Model):
    """
    Logs every execution of the automatic OR-Tools scheduler.
    """
    trigger_event = models.CharField(max_length=200)
    status = models.CharField(max_length=50, default='OPTIMAL')
    requests_count = models.IntegerField(default=0)
    blocks_count = models.IntegerField(default=0)
    conflicts_count = models.IntegerField(default=0)
    solve_time_ms = models.FloatField(default=0.0)
    details = models.TextField(blank=True, default='')
    uptime_percent = models.FloatField(default=0.0)
    downtime_saved_minutes = models.IntegerField(default=0)
    duality_gap_percent = models.FloatField(default=0.0)
    lagrangian_data = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"Optimization ({self.status}) at {self.created_at.strftime('%H:%M:%S')} - {self.solve_time_ms:.1f}ms"


class DailyScheduleControl(models.Model):
    """
    Control Office authorization and sign-off state for the day's corridor maintenance schedule.
    """
    date = models.DateField(default=timezone.now, unique=True)
    is_approved = models.BooleanField(default=False)
    approved_by = models.CharField(max_length=120, default="Chief Section Controller (ChC)")
    approved_at = models.DateTimeField(null=True, blank=True)
    remarks = models.TextField(blank=True, default="Schedule optimized via CP-SAT Lagrangian relaxation.")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        status = "APPROVED" if self.is_approved else "PENDING_APPROVAL"
        return f"Daily Schedule {self.date} [{status}]"
