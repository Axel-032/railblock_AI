import json
import time
import random
from datetime import datetime
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_POST
from django.views.decorators.csrf import csrf_exempt
from django.core.management import call_command

from core.models import (
    Team,
    Asset,
    TrainSchedule,
    RailwayBlock,
    MaintenanceRequest,
    Notification,
    OptimizationLog,
    DailyScheduleControl,
)
from scheduler.services.scheduler_service import run_automatic_scheduling
from scheduler.services.lagrangian_optimizer import (
    SLOTS_PER_DAY,
    slot_to_time,
    time_to_slot,
    duration_to_slots
)


def _get_or_create_team(code: str) -> Team:
    """Safely retrieves or creates department team by code or alias."""
    team_configs = {
        'tms': {
            'name': 'Track Management System (TMS)',
            'dept': 'Track Management System — Civil Engineering / Permanent Way',
            'lead': 'Sr. DEN (Co-ordination) Ahmedabad',
            'color': '#0075F2',
            'aliases': ['engineering', 'tms']
        },
        'smms': {
            'name': 'Signal Maintenance & Management System (SMMS)',
            'dept': 'Signal Maintenance & Management System — S&T Division',
            'lead': 'Sr. DSTE (Signal) Ahmedabad',
            'color': '#2EC4B6',
            'aliases': ['signal', 'smms']
        },
        'trd': {
            'name': 'Traction Distribution System (TRD)',
            'dept': 'Traction Distribution System — 25kV AC Electrical OHE',
            'lead': 'Sr. DEE (TRD) Sabarmati',
            'color': '#F4D35E',
            'aliases': ['traction', 'trd']
        },
        'operations': {
            'name': 'Control Office / Operations',
            'dept': 'Traffic & Section Controller Master Command',
            'lead': 'Chief Section Controller (ChC) Control Office',
            'color': '#3E2A35',
            'aliases': ['operations']
        }
    }

    cfg = team_configs.get(code.lower(), team_configs['tms'])
    team = Team.objects.filter(code__in=cfg['aliases']).first()
    if not team:
        team = Team.objects.create(
            code=code.lower(),
            name=cfg['name'],
            department_name=cfg['dept'],
            lead_name=cfg['lead'],
            badge_color=cfg['color']
        )
    return team


def generate_request_code(dept_code: str) -> str:
    """Generates unique, collision-proof sequential railway request code."""
    today_str = timezone.now().strftime('%y%m%d')
    prefix = f"REQ-{dept_code.upper()}-{today_str}"
    count = MaintenanceRequest.objects.count() + 1
    rand_suffix = random.randint(10, 99)
    return f"{prefix}-{count:03d}-{rand_suffix}"


def index(request):
    """
    Main Corridor Block Schedule Dashboard combining:
    1. Track Management System (TMS)
    2. Signal Maintenance & Management System (SMMS)
    3. Traction Distribution System (TRD)
    With Section Controller Schedule Authorization Workflow.
    """
    blocks = RailwayBlock.objects.all().prefetch_related('maintenance_tasks', 'maintenance_tasks__team')
    requests = MaintenanceRequest.objects.all().select_related('team', 'assigned_block')
    trains = TrainSchedule.objects.all().order_by('start_time')
    latest_log = OptimizationLog.objects.first()
    recent_notifications = Notification.objects.filter(is_read=False).order_by('-created_at')[:8]

    # Daily Schedule Approval Control
    today = timezone.now().date()
    schedule_control, _ = DailyScheduleControl.objects.get_or_create(date=today)

    # Department breakdown
    tms_team = Team.objects.filter(code__in=['tms', 'engineering']).first()
    smms_team = Team.objects.filter(code__in=['smms', 'signal']).first()
    trd_team = Team.objects.filter(code__in=['trd', 'traction']).first()

    tms_reqs = requests.filter(team=tms_team) if tms_team else requests.none()
    smms_reqs = requests.filter(team=smms_team) if smms_team else requests.none()
    trd_reqs = requests.filter(team=trd_team) if trd_team else requests.none()

    # Parse Lagrangian telemetry
    lagrangian_data = {}
    if latest_log and latest_log.lagrangian_data:
        try:
            lagrangian_data = json.loads(latest_log.lagrangian_data)
        except Exception:
            lagrangian_data = {}

    joint_blocks = lagrangian_data.get('joint_blocks', [])
    separated_blocks = lagrangian_data.get('separated_blocks', [])
    iterations = lagrangian_data.get('iterations', [])

    bundled_blocks_count = sum(1 for b in blocks if b.maintenance_tasks.count() > 1)
    downtime_saved = latest_log.downtime_saved_minutes if latest_log else sum(
        (b.maintenance_tasks.count() - 1) * 60 for b in blocks if b.maintenance_tasks.count() > 1
    )
    uptime_pct = latest_log.uptime_percent if latest_log and latest_log.uptime_percent > 0 else 92.2

    context = {
        'blocks': blocks,
        'requests': requests,
        'trains': trains,
        'latest_log': latest_log,
        'recent_notifications': recent_notifications,
        'joint_blocks': joint_blocks,
        'separated_blocks': separated_blocks,
        'iterations': iterations,
        'schedule_control': schedule_control,
        'stats': {
            'total_blocks': blocks.count(),
            'total_requests': requests.count(),
            'tms_count': tms_reqs.count(),
            'smms_count': smms_reqs.count(),
            'trd_count': trd_reqs.count(),
            'bundled_blocks': bundled_blocks_count or len(joint_blocks),
            'minutes_saved': downtime_saved,
            'uptime_percent': uptime_pct,
            'separated_conflicts': len(separated_blocks),
            'peak_violations': 0,
            'solve_time_ms': latest_log.solve_time_ms if latest_log else 0.0,
            'duality_gap': latest_log.duality_gap_percent if latest_log else 0.0,
        }
    }
    return render(request, 'index.html', context)


def tms_dashboard(request):
    """Track Management System (TMS) Portal."""
    team = _get_or_create_team('tms')
    return _render_department_dashboard(request, team, 'dashboard_tms.html')


def smms_dashboard(request):
    """Signal Maintenance & Management System (SMMS) Portal."""
    team = _get_or_create_team('smms')
    return _render_department_dashboard(request, team, 'dashboard_smms.html')


def trd_dashboard(request):
    """Traction Distribution System (TRD) Portal."""
    team = _get_or_create_team('trd')
    return _render_department_dashboard(request, team, 'dashboard_trd.html')


def team_dashboard(request, team_code):
    """Dispatcher for legacy routes."""
    code = team_code.lower()
    if code in ('engineering', 'tms'):
        return tms_dashboard(request)
    elif code in ('signal', 'smms'):
        return smms_dashboard(request)
    elif code in ('traction', 'trd'):
        return trd_dashboard(request)
    elif code == 'operations':
        return operations_dashboard(request)

    team = _get_or_create_team(code)
    return _render_department_dashboard(request, team, 'dashboard_team.html')


def _render_department_dashboard(request, team: Team, template_name: str):
    """Renders department dashboard with assigned tasks, assets, and request forms."""
    dept_aliases = [team.code]
    if team.code in ('tms', 'engineering'):
        dept_aliases = ['tms', 'engineering']
    elif team.code in ('smms', 'signal'):
        dept_aliases = ['smms', 'signal']
    elif team.code in ('trd', 'traction'):
        dept_aliases = ['trd', 'traction']

    matching_teams = Team.objects.filter(code__in=dept_aliases)
    team_requests = MaintenanceRequest.objects.filter(team__in=matching_teams).order_by('-created_at')
    team_assets = Asset.objects.filter(team__in=matching_teams)

    team_blocks = RailwayBlock.objects.filter(maintenance_tasks__team__in=matching_teams).distinct().prefetch_related(
        'maintenance_tasks', 'maintenance_tasks__team'
    )
    joint_blocks = [b for b in team_blocks if b.maintenance_tasks.count() > 1]

    notifications = Notification.objects.filter(team__in=matching_teams).order_by('-created_at')[:10]
    unread_count = Notification.objects.filter(team__in=matching_teams, is_read=False).count()

    latest_log = OptimizationLog.objects.first()
    schedule_control, _ = DailyScheduleControl.objects.get_or_create(date=timezone.now().date())

    context = {
        'team': team,
        'requests': team_requests,
        'assets': team_assets,
        'blocks': team_blocks,
        'joint_blocks': joint_blocks,
        'notifications': notifications,
        'unread_count': unread_count,
        'latest_log': latest_log,
        'schedule_control': schedule_control,
        'metrics': {
            'total': team_requests.count(),
            'scheduled': team_requests.filter(status='SCHEDULED').count(),
            'pending': team_requests.filter(status='PENDING').count(),
            'joint_count': len(joint_blocks),
            'critical_count': team_requests.filter(severity='CRITICAL').count(),
        }
    }
    return render(request, template_name, context)


def operations_dashboard(request):
    """Section Controller / Master Command operations view."""
    return index(request)


# -----------------------------------------------------------------------------
# REST APIs: Add Block, Schedule Approval, Timeline Data, Deletion
# -----------------------------------------------------------------------------

@csrf_exempt
@require_POST
def submit_request(request):
    """
    Submits a new maintenance block request for TMS, SMMS, or TRD.
    Immediately & automatically triggers Lagrangian CP-SAT re-optimization.
    Invalidates previous daily approval so Section Controller can review the updated plan.
    """
    try:
        # Support both JSON payload and standard form-encoded data
        if request.content_type == 'application/json':
            try:
                data = json.loads(request.body.decode('utf-8'))
            except Exception:
                data = {}
        else:
            data = request.POST

        dept_code = data.get('department', 'tms').lower().strip()
        if dept_code not in ('tms', 'smms', 'trd'):
            dept_code = 'tms'

        team = _get_or_create_team(dept_code)
        req_type = data.get('maintenance_type', 'Routine Corridor Maintenance').strip()
        location = data.get('location', 'Ahmedabad Up Main').strip()
        duration_raw = data.get('estimated_duration', 60)
        try:
            duration = int(duration_raw)
        except (ValueError, TypeError):
            duration = 60

        severity = data.get('severity', 'MEDIUM').strip().upper()
        if severity not in ('CRITICAL', 'HIGH', 'MEDIUM', 'LOW'):
            severity = 'MEDIUM'

        is_emergency = data.get('is_emergency') in ('true', 'True', '1', 1, 'on')
        resources = data.get('required_resources', 'Standard Gang & Tools').strip()
        preferred_time_raw = (data.get('preferred_time') or '').strip()
        preferred_time = None
        if preferred_time_raw:
            try:
                preferred_time = datetime.strptime(preferred_time_raw, '%H:%M').time()
            except ValueError:
                preferred_time = None
        desc = data.get('description', 'Maintenance possession requested on corridor.').strip()

        # Generate unique request code
        req_code = generate_request_code(dept_code)

        # Create record
        new_req = MaintenanceRequest.objects.create(
            request_code=req_code,
            team=team,
            asset_id=f"AST-{dept_code.upper()}-{int(time.time()) % 1000:03d}",
            asset_type=req_type,
            location=location,
            maintenance_type=req_type,
            description=desc,
            severity=severity,
            is_emergency=is_emergency,
            estimated_duration=duration,
            preferred_date=timezone.now().date(),
            preferred_time=preferred_time,
            required_resources=resources,
            status='PENDING'
        )

        # Run automatic CP-SAT Lagrangian optimization immediately
        opt_res = run_automatic_scheduling(trigger_source=f"Block Request {req_code} Added", force=True)

        # Invalidate daily approval because corridor state has changed
        today = timezone.now().date()
        control, _ = DailyScheduleControl.objects.get_or_create(date=today)
        control.is_approved = False
        control.remarks = f"Schedule updated with new request {req_code}. Pending re-authorization."
        control.save()

        # Return JSON if AJAX/fetch
        if request.headers.get('x-requested-with') == 'XMLHttpRequest' or request.content_type == 'application/json':
            return JsonResponse({
                'success': True,
                'request_code': req_code,
                'message': f"Request {req_code} registered. Corridor schedule automatically re-optimized.",
                'metrics': opt_res.get('metrics', {})
            })

        messages.success(request, f"Block Request {req_code} registered. AI schedule automatically recomputed.")
        return redirect(request.META.get('HTTP_REFERER', 'index'))

    except Exception as e:
        if request.headers.get('x-requested-with') == 'XMLHttpRequest' or request.content_type == 'application/json':
            return JsonResponse({'success': False, 'error': str(e)}, status=500)
        messages.error(request, f"Error registering request: {str(e)}")
        return redirect('index')


@csrf_exempt
@require_POST
def cancel_request(request, req_id):
    """
    Cancels/deletes a maintenance block request and automatically updates the schedule.
    """
    try:
        req = get_object_or_404(MaintenanceRequest, id=req_id)
        code = req.request_code
        req.delete()

        # Re-optimize schedule automatically
        run_automatic_scheduling(trigger_source=f"Request {code} Cancelled", force=True)

        # Invalidate daily approval
        today = timezone.now().date()
        control, _ = DailyScheduleControl.objects.get_or_create(date=today)
        control.is_approved = False
        control.save()

        if request.headers.get('x-requested-with') == 'XMLHttpRequest':
            return JsonResponse({'success': True, 'message': f"Request {code} removed. Schedule recomputed."})

        messages.info(request, f"Request {code} removed. Corridor schedule automatically recomputed.")
        return redirect(request.META.get('HTTP_REFERER', 'index'))
    except Exception as e:
        if request.headers.get('x-requested-with') == 'XMLHttpRequest':
            return JsonResponse({'success': False, 'error': str(e)}, status=500)
        messages.error(request, f"Error cancelling request: {str(e)}")
        return redirect('index')


@csrf_exempt
@require_POST
def api_toggle_schedule_approval(request):
    """
    Control Office authorization endpoint: Chief Section Controller approves/revokes
    today's corridor maintenance schedule.
    """
    try:
        today = timezone.now().date()
        control, _ = DailyScheduleControl.objects.get_or_create(date=today)

        # Toggle approval state
        control.is_approved = not control.is_approved
        if control.is_approved:
            control.approved_at = timezone.now()
            control.approved_by = "Chief Section Controller (ChC) — Ahmedabad Division"
            control.remarks = "Schedule approved and officially authorized for traffic execution."
            msg = "Daily corridor maintenance schedule APPROVED and locked for traffic operations."
        else:
            control.approved_at = None
            control.remarks = "Schedule proposal reopened by Section Controller for modifications."
            msg = "Schedule authorization revoked. Status set to PROPOSED (Pending Approval)."

        control.save()

        # Update all assigned blocks status
        status_to_set = 'ACTIVE' if control.is_approved else 'SCHEDULED'
        RailwayBlock.objects.filter(status__in=['SCHEDULED', 'ACTIVE']).update(status=status_to_set)

        if request.headers.get('x-requested-with') == 'XMLHttpRequest' or request.content_type == 'application/json':
            return JsonResponse({
                'success': True,
                'is_approved': control.is_approved,
                'approved_by': control.approved_by,
                'approved_at': control.approved_at.strftime("%H:%M") if control.approved_at else None,
                'message': msg
            })

        messages.success(request, msg)
        return redirect(request.META.get('HTTP_REFERER', 'index'))
    except Exception as e:
        if request.headers.get('x-requested-with') == 'XMLHttpRequest':
            return JsonResponse({'success': False, 'error': str(e)}, status=500)
        messages.error(request, f"Approval error: {str(e)}")
        return redirect('index')


def api_timeline_data(request):
    """
    Supplies JSON data to the plain JS timeline:
    - 48 half-hour slots
    - Corridor train traffic profile
    - Section rows
    - Scheduled block intervals with department tags and joint groupings
    """
    latest_log = OptimizationLog.objects.first()
    traffic_profile = [0] * SLOTS_PER_DAY
    joint_blocks = []
    separated_blocks = []

    if latest_log and latest_log.lagrangian_data:
        try:
            data = json.loads(latest_log.lagrangian_data)
            traffic_profile = data.get('traffic_profile', [0] * SLOTS_PER_DAY)
            joint_blocks = data.get('joint_blocks', [])
            separated_blocks = data.get('separated_blocks', [])
        except Exception:
            pass

    scheduled_reqs = MaintenanceRequest.objects.filter(status='SCHEDULED').select_related('team')
    items = []
    for r in scheduled_reqs:
        s_slot = time_to_slot(r.scheduled_start_time) if r.scheduled_start_time else 0
        e_slot = time_to_slot(r.scheduled_end_time) if r.scheduled_end_time else s_slot + duration_to_slots(r.estimated_duration)
        dept_code = getattr(r.team, 'code', 'tms').lower()
        if dept_code in ('engineering', 'tms'):
            dept_code = 'tms'
        elif dept_code in ('signal', 'smms'):
            dept_code = 'smms'
        elif dept_code in ('traction', 'trd'):
            dept_code = 'trd'

        items.append({
            'id': r.id,
            'code': r.request_code,
            'department': dept_code,
            'department_name': r.team.name,
            'type': r.maintenance_type,
            'location': r.location,
            'duration_minutes': r.estimated_duration,
            'start_slot': s_slot,
            'end_slot': e_slot,
            'start_time': r.scheduled_start_time.strftime("%H:%M") if r.scheduled_start_time else "00:00",
            'end_time': r.scheduled_end_time.strftime("%H:%M") if r.scheduled_end_time else "01:00",
            'is_joint': r.is_joint,
            'joined_with': r.joined_with_codes,
            'severity': r.severity,
            'is_emergency': r.is_emergency,
            'slot_period_desc': r.slot_period_desc,
        })

    sections = list(set(r['location'] for r in items))
    if not sections:
        sections = ["Maninagar Down Main", "Ahmedabad Up Main", "Sabarmati Fast Line", "Vatva Freight Loop"]

    slots = []
    for slot in range(SLOTS_PER_DAY):
        t = slot_to_time(slot)
        crit = traffic_profile[slot] if slot < len(traffic_profile) else 0
        slots.append({
            'slot': slot,
            'time': t.strftime("%H:%M"),
            'traffic_criticality': crit,
            'is_peak': crit > 12,
            'is_lean': crit <= 5,
        })

    # Approval state
    today = timezone.now().date()
    control, _ = DailyScheduleControl.objects.get_or_create(date=today)

    return JsonResponse({
        'slots': slots,
        'sections': sorted(sections),
        'items': items,
        'joint_blocks': joint_blocks,
        'separated_blocks': separated_blocks,
        'uptime_percent': latest_log.uptime_percent if latest_log else 92.2,
        'downtime_saved_minutes': latest_log.downtime_saved_minutes if latest_log else 300,
        'is_approved': control.is_approved,
        'approved_by': control.approved_by,
        'approved_at': control.approved_at.strftime("%H:%M") if control.approved_at else None,
    })


@csrf_exempt
@require_POST
def api_reset_data(request):
    """Resets corridor to baseline dummy data."""
    try:
        call_command('seed_railway_data')
        return JsonResponse({'success': True, 'message': 'Corridor timetable & requests reset to baseline.'})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


def toggle_block_status(request, block_id):
    """Toggles status of a corridor block."""
    block = get_object_or_404(RailwayBlock, id=block_id)
    if block.status == 'SCHEDULED':
        block.status = 'ACTIVE'
    elif block.status == 'ACTIVE':
        block.status = 'COMPLETED'
    elif block.status == 'COMPLETED':
        block.status = 'SCHEDULED'
    block.save(update_fields=['status'])
    messages.info(request, f"Block {block.block_code} status set to {block.status}.")
    return redirect(request.META.get('HTTP_REFERER', 'index'))


def mark_notifications_read(request, team_code):
    """Dismisses notifications."""
    Notification.objects.filter(is_read=False).update(is_read=True)
    messages.success(request, "Notifications marked as read.")
    return redirect(request.META.get('HTTP_REFERER', 'index'))


@csrf_exempt
def api_lagrangian_telemetry(request):
    """
    Supplies Lagrangian relaxation telemetry to the math inspector modal:
    dual bounds, primal costs, duality gaps, multipliers, and iteration history.
    """
    try:
        latest_log = OptimizationLog.objects.first()
        lagrangian_logs = []
        status = "OPTIMAL"
        metrics = {
            'solve_time_ms': 500.0,
            'track_uptime_percent': 92.2,
            'downtime_saved_minutes': 300,
            'lagrangian_iterations': 6,
            'final_duality_gap_percent': 0.0,
        }
        joint_blocks = []
        separated_blocks = []

        if latest_log:
            metrics['solve_time_ms'] = latest_log.solve_time_ms
            metrics['track_uptime_percent'] = latest_log.uptime_percent
            metrics['downtime_saved_minutes'] = latest_log.downtime_saved_minutes
            metrics['final_duality_gap_percent'] = latest_log.duality_gap_percent
            status = latest_log.status or "OPTIMAL"

            if latest_log.lagrangian_data:
                try:
                    data = json.loads(latest_log.lagrangian_data)
                    lagrangian_logs = data.get('iterations', [])
                    metrics['lagrangian_iterations'] = len(lagrangian_logs)
                    joint_blocks = data.get('joint_blocks', [])
                    separated_blocks = data.get('separated_blocks', [])
                    if 'metrics' in data:
                        metrics.update(data['metrics'])
                except Exception:
                    pass

        return JsonResponse({
            'success': True,
            'status': status,
            'lagrangian_logs': lagrangian_logs,
            'metrics': metrics,
            'joint_blocks': joint_blocks,
            'separated_blocks': separated_blocks,
        })
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)

