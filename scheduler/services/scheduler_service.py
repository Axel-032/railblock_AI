import json
import logging
import threading
import time
import random
from datetime import datetime, time as dtime
from django.db import transaction
from django.utils import timezone

from core.models import (
    MaintenanceRequest,
    RailwayBlock,
    TrainSchedule,
    Notification,
    OptimizationLog
)
from scheduler.services.priority import calculate_and_update_priorities
from scheduler.services.lagrangian_optimizer import (
    LagrangianBlockOptimizer,
    slot_to_time,
    time_to_slot,
    duration_to_slots
)

logger = logging.getLogger('scheduler')

_scheduler_lock = threading.Lock()
_last_run_timestamp = 0.0
DEBOUNCE_SECONDS = 0.8


def run_automatic_scheduling(trigger_source="Automatic Request Trigger", force=False):
    """
    Main orchestration service for AI Railway Block Scheduling.
    Powered by Google OR-Tools CP-SAT Solver with Lagrangian Relaxation Decomposition.

    1. Fetches maintenance requests from TMS, SMMS, TRD.
    2. Fetches train schedules & derives corridor traffic criticality profile.
    3. Solves CP-SAT subproblems iteratively updating Lagrangian multipliers λ_t.
    4. Combines compatible blocks (shadow windows) & separates incompatible blocks.
    5. Dispatches blocks to the least critical corridor time windows.
    6. Atomically updates RailwayBlock timetable, requests, and notifications.
    7. Stores Lagrangian convergence telemetry in OptimizationLog.
    """
    global _last_run_timestamp

    current_time = time.time()
    if not force and (current_time - _last_run_timestamp) < DEBOUNCE_SECONDS:
        logger.info("Scheduling request debounced.")
        return {"status": "DEBOUNCED"}

    with _scheduler_lock:
        _last_run_timestamp = time.time()
        start_clock = time.time()

        # 1. Fetch all non-completed requests
        requests_qs = MaintenanceRequest.objects.exclude(status='COMPLETED').select_related('team')
        all_requests = list(requests_qs)

        if not all_requests:
            logger.info("No maintenance requests found.")
            return {"status": "NO_REQUESTS"}

        # 2. Update priorities
        calculate_and_update_priorities(all_requests)

        # 3. Fetch train schedules
        train_schedules = list(TrainSchedule.objects.all())

        # 4. Run Lagrangian Block Optimizer
        optimizer = LagrangianBlockOptimizer(
            requests=all_requests,
            train_schedules=train_schedules,
            max_lagrangian_iterations=6,
            tolerance_per_slot=4,
        )
        opt_result = optimizer.solve()

        # 5. Persist Schedule and Synchronize Blocks
        from core.signals import set_in_scheduling
        set_in_scheduling(True)
        try:
            with transaction.atomic():
                # Map requests by ID
                req_map = {r.id: r for r in all_requests}

                # Clear old block assignments for fresh re-packing (both SCHEDULED and ACTIVE)
                RailwayBlock.objects.filter(status__in=['SCHEDULED', 'ACTIVE']).delete()

                # Group assignments by (location, start_slot, end_slot) to create synchronized RailwayBlocks
                # This guarantees that compatible co-scheduled tasks become a unified single RailwayBlock!
                block_groups = {}
                for asgn in opt_result.get('assignments', []):
                    key = (asgn['location'], asgn['start_slot'])
                    block_groups.setdefault(key, []).append(asgn)

                block_counter = 1
                today_str = timezone.now().strftime('%y%m%d')
                for (loc, s_slot), group in block_groups.items():
                    max_dur = max(item['duration_minutes'] for item in group)
                    end_slot = s_slot + duration_to_slots(max_dur)
                    st_time = slot_to_time(s_slot)
                    et_time = slot_to_time(end_slot)

                    # Check if any request in this block requires 25kV OHE power cut
                    req_objs = [req_map[item['request_id']] for item in group if item['request_id'] in req_map]
                    needs_power_cut = any(
                        'ohe' in r.maintenance_type.lower() or
                        'catenary' in r.maintenance_type.lower() or
                        'traction' in getattr(r.team, 'code', '').lower() or
                        'trd' in getattr(r.team, 'code', '').lower()
                        for r in req_objs
                    )

                    is_multi_agency = len(group) > 1
                    prefix = "JBLK" if is_multi_agency else "BLK"
                    rand_suffix = random.randint(10, 99)
                    block_code = f"{prefix}-{today_str}-{block_counter:02d}-{rand_suffix}"
                    while RailwayBlock.objects.filter(block_code=block_code).exists():
                        rand_suffix = random.randint(10, 99)
                        block_code = f"{prefix}-{today_str}-{block_counter:02d}-{rand_suffix}"
                    block_counter += 1

                    # Create unified RailwayBlock
                    block_obj = RailwayBlock.objects.create(
                        block_code=block_code,
                        location=loc,
                        date=timezone.now().date(),
                        start_time=st_time,
                        end_time=et_time,
                        duration_minutes=max_dur,
                        max_capacity_minutes=max_dur,
                        is_power_cut_required=needs_power_cut,
                        status='SCHEDULED',
                        ai_summary=(
                            f"AI Scheduled via CP-SAT & Lagrangian Relaxation. "
                            f"Placed in {group[0]['slot_period_desc']}. "
                            + (f"Combined {len(group)} compatible tasks ({', '.join(set(item['team_name'] for item in group))})." if is_multi_agency else "Dedicated department block.")
                        )
                    )

                    # Update each request in this block
                    for item in group:
                        req = req_map.get(item['request_id'])
                        if req:
                            req.assigned_block = block_obj
                            req.status = 'SCHEDULED'
                            req.conflict_reason = ''
                            req.scheduled_start_time = slot_to_time(item['start_slot'])
                            req.scheduled_end_time = slot_to_time(item['end_slot'])
                            req.is_joint = item['is_joint']
                            req.joined_with_codes = ', '.join(item['joined_with'])
                            req.slot_period_desc = item['slot_period_desc']
                            req.traffic_criticality = item['traffic_criticality']
                            req.save()

                            # Notify department
                            Notification.objects.create(
                                team=req.team,
                                title=f"Block Scheduled: {block_code} ({req.maintenance_type})",
                                message=(
                                    f"Task '{req.maintenance_type}' ({req.request_code}) at {loc} "
                                    f"scheduled for {st_time.strftime('%H:%M')} – {et_time.strftime('%H:%M')} "
                                    f"in {item['slot_period_desc']}."
                                    + (f" Combined in joint shadow block with {req.joined_with_codes}." if req.is_joint else "")
                                ),
                                notification_type='SCHEDULED',
                                is_read=False
                            )

            # Store Optimization Log with Lagrangian Telemetry
            solve_time = round((time.time() - start_clock) * 1000.0, 2)
            metrics = opt_result.get('metrics', {})
            OptimizationLog.objects.create(
                trigger_event=trigger_source,
                status=opt_result.get('status', 'OPTIMAL'),
                requests_count=len(all_requests),
                blocks_count=len(block_groups),
                conflicts_count=len(opt_result.get('separated_blocks', [])),
                solve_time_ms=solve_time,
                uptime_percent=metrics.get('track_uptime_percent', 92.5),
                downtime_saved_minutes=metrics.get('downtime_saved_minutes', 0),
                duality_gap_percent=metrics.get('final_duality_gap_percent', 0.0),
                details=(
                    f"Lagrangian CP-SAT solved in {metrics.get('lagrangian_iterations', 1)} iterations. "
                    f"Scheduled {len(opt_result.get('assignments', []))} tasks across "
                    f"TMS, SMMS, and TRD. Saved {metrics.get('downtime_saved_minutes', 0)} mins downtime."
                ),
                lagrangian_data=json.dumps({
                    'iterations': opt_result.get('lagrangian_logs', []),
                    'joint_blocks': opt_result.get('joint_blocks', []),
                    'separated_blocks': opt_result.get('separated_blocks', []),
                    'traffic_profile': opt_result.get('overall_traffic_profile', []),
                })
            )
        finally:
            set_in_scheduling(False)

        logger.info(f"Automatic scheduling finished: {opt_result.get('status')} in {solve_time}ms")
        return opt_result
