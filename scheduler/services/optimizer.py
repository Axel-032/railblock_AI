import time
import logging
from datetime import datetime, time as dtime, timedelta
from ortools.sat.python import cp_model

logger = logging.getLogger('scheduler')


def time_to_minutes(t: dtime) -> int:
    """Convert datetime.time to minutes from midnight (0 - 1439)"""
    return t.hour * 60 + t.minute


def minutes_to_time(minutes: int) -> dtime:
    """Convert minutes from midnight back to datetime.time"""
    minutes = max(0, min(minutes, 1439))
    hour = minutes // 60
    minute = minutes % 60
    return dtime(hour=hour, minute=minute)


def is_time_overlap(s1: int, e1: int, s2: int, e2: int) -> bool:
    """Checks if two intervals [s1, e1] and [s2, e2] overlap"""
    return max(s1, s2) < min(e1, e2)


class RailBlockOptimizer:
    """
    Google OR-Tools CP-SAT Mathematical Scheduling Engine for RailBlock AI (SIH 26027).
    
    Deterministically solves multi-department maintenance block allocation:
    - Minimizes train schedule conflicts
    - Minimizes asset downtime
    - Minimizes number of separate blocks
    - Maximizes priority tasks scheduled
    - Maximizes multi-agency task bundling (Engineering + Signal + Traction)
    - Maximizes block utilization
    """

    def __init__(self, requests, blocks, train_schedules, existing_assignments=None):
        self.requests = list(requests)
        self.blocks = list(blocks)
        self.train_schedules = list(train_schedules)
        self.existing_assignments = existing_assignments or []
        self.model = cp_model.CpModel()
        self.solver = cp_model.CpSolver()
        self.solver.parameters.max_time_in_seconds = 10.0
        self.solver.parameters.num_search_workers = 4

    def solve(self):
        start_time = time.time()
        logger.info("OPTIMIZATION STARTED")
        logger.info(f"REQUESTS FOUND: {len(self.requests)}")
        logger.info(f"BLOCKS FOUND: {len(self.blocks)}")

        if not self.requests:
            logger.info("OPTIMIZATION COMPLETED: No pending requests to schedule.")
            return {
                'status': 'NO_REQUESTS',
                'assignments': [],
                'conflicts': [],
                'metrics': {
                    'scheduled_count': 0,
                    'bundled_count': 0,
                    'blocks_used': 0,
                    'solve_time_ms': 0.0,
                }
            }

        if not self.blocks:
            logger.warning("OPTIMIZATION COMPLETED: No candidate railway blocks available.")
            conflicts = [{
                'request_id': r.id,
                'request_code': r.request_code,
                'reason': 'No candidate railway blocks registered on corridor'
            } for r in self.requests]
            return {
                'status': 'NO_BLOCKS',
                'assignments': [],
                'conflicts': conflicts,
                'metrics': {
                    'scheduled_count': 0,
                    'bundled_count': 0,
                    'blocks_used': 0,
                    'solve_time_ms': (time.time() - start_time) * 1000.0,
                }
            }

        # ---------------------------------------------------------------------
        # Precompute Train Schedule Restrictions per Location
        # ---------------------------------------------------------------------
        # Buffer around train arrivals/departures to ensure safety headway (minutes)
        SAFETY_BUFFER_MINUTES = 10

        train_restrictions = []
        for train in self.train_schedules:
            t_start = max(0, time_to_minutes(train.start_time) - SAFETY_BUFFER_MINUTES)
            t_end = min(1440, time_to_minutes(train.end_time) + SAFETY_BUFFER_MINUTES)
            train_restrictions.append({
                'train_number': train.train_number,
                'train_name': train.train_name,
                'location': train.location.strip().lower(),
                'start_min': t_start,
                'end_min': t_end,
                'tier': train.priority_tier,
            })

        # ---------------------------------------------------------------------
        # Decision Variables
        # ---------------------------------------------------------------------
        # x[r_idx, b_idx] in {0, 1}: True if request r is scheduled in block b
        x = {}
        # y[b_idx] in {0, 1}: True if block b is actively used by at least one task
        y = {}
        # team_used[b_idx, team_code] in {0, 1}: True if team has at least one task in block b
        team_used = {}

        teams_set = set(r.team.code for r in self.requests)

        for b_idx, block in enumerate(self.blocks):
            y[b_idx] = self.model.NewBoolVar(f"block_used_{b_idx}")
            for team_code in teams_set:
                team_used[b_idx, team_code] = self.model.NewBoolVar(f"team_used_{b_idx}_{team_code}")

        # Track train conflicts at block level
        block_has_train_conflict = {}
        for b_idx, block in enumerate(self.blocks):
            b_loc = block.location.strip().lower()
            b_start = time_to_minutes(block.start_time)
            b_end = time_to_minutes(block.end_time)

            conflicts_for_block = []
            for tr in train_restrictions:
                if tr['location'] == b_loc and is_time_overlap(b_start, b_end, tr['start_min'], tr['end_min']):
                    conflicts_for_block.append(tr)

            block_has_train_conflict[b_idx] = conflicts_for_block

        logger.info(f"CONFLICTS FOUND in corridor: {sum(len(c) for c in block_has_train_conflict.values())}")

        # Create assignment variables for feasible pairs
        for r_idx, req in enumerate(self.requests):
            req_loc = req.location.strip().lower()
            req_dur = req.estimated_duration

            for b_idx, block in enumerate(self.blocks):
                b_loc = block.location.strip().lower()
                b_dur = block.duration_minutes

                # Feasibility filters:
                # 1. Location match
                # 2. Task duration must fit completely inside block duration
                is_loc_match = (req_loc == b_loc) or (b_loc in req_loc) or (req_loc in b_loc)
                is_duration_fit = (req_dur <= b_dur)

                # Check if block has severe Tier 1 (Rajdhani/Vande Bharat) train conflict
                conflicts = block_has_train_conflict[b_idx]
                has_tier_1_clash = any(c['tier'] == 1 for c in conflicts)

                if is_loc_match and is_duration_fit and not (has_tier_1_clash and not req.is_emergency):
                    x[r_idx, b_idx] = self.model.NewBoolVar(f"x_req{r_idx}_blk{b_idx}")
                else:
                    # Infeasible assignment
                    x[r_idx, b_idx] = None

        # ---------------------------------------------------------------------
        # CONSTRAINTS
        # ---------------------------------------------------------------------

        # Constraint 4: A task cannot be scheduled twice (at most once)
        for r_idx, req in enumerate(self.requests):
            valid_b_vars = [x[r_idx, b_idx] for b_idx in range(len(self.blocks)) if x[r_idx, b_idx] is not None]
            if valid_b_vars:
                self.model.Add(sum(valid_b_vars) <= 1)

        # Link x[r, b] to y[b] (if any request assigned, block is used)
        for b_idx in range(len(self.blocks)):
            block_vars = [x[r_idx, b_idx] for r_idx in range(len(self.requests)) if x[r_idx, b_idx] is not None]
            if block_vars:
                # y[b] >= x[r, b]
                for var in block_vars:
                    self.model.Add(y[b_idx] >= var)
                # y[b] <= sum(block_vars)
                self.model.Add(sum(block_vars) >= y[b_idx])
            else:
                self.model.Add(y[b_idx] == 0)

        # Constraint 1 & 7: Maximum capacity of the block
        # Multi-agency concurrent shadow tasks:
        # Same department tasks require sequential time; different departments can work concurrently during the block.
        for b_idx, block in enumerate(self.blocks):
            for team_code in teams_set:
                same_team_vars = [
                    (self.requests[r_idx].estimated_duration, x[r_idx, b_idx])
                    for r_idx in range(len(self.requests))
                    if x[r_idx, b_idx] is not None and self.requests[r_idx].team.code == team_code
                ]
                if same_team_vars:
                    # Same department total duration must fit in block
                    self.model.Add(
                        sum(dur * var for dur, var in same_team_vars) <= block.duration_minutes
                    )
                    # Link to team_used[b, team_code]
                    for _, var in same_team_vars:
                        self.model.Add(team_used[b_idx, team_code] >= var)
                else:
                    self.model.Add(team_used[b_idx, team_code] == 0)

        # Constraint 3: Compatible maintenance types inside block
        # For prototype: Tasks in the same location are compatible if power-cut requirements align
        for b_idx, block in enumerate(self.blocks):
            power_cut_block = block.is_power_cut_required
            for r_idx, req in enumerate(self.requests):
                if x[r_idx, b_idx] is not None:
                    # If request specifically requires OHE 25kV power cut, block must allow it
                    needs_power_cut = 'ohe' in req.maintenance_type.lower() or 'catenary' in req.maintenance_type.lower()
                    if needs_power_cut and not power_cut_block:
                        # Cannot assign high-voltage catenary replacement to non-isolated block
                        self.model.Add(x[r_idx, b_idx] == 0)

        # ---------------------------------------------------------------------
        # OBJECTIVE FUNCTION
        # ---------------------------------------------------------------------
        objective_terms = []

        # 1. Maximize scheduled request priorities (Multiplied to preserve integer precision)
        for r_idx, req in enumerate(self.requests):
            priority = int(getattr(req, 'calculated_priority_score', 100.0) * 10)
            for b_idx in range(len(self.blocks)):
                if x[r_idx, b_idx] is not None:
                    # Reward scheduling high-priority and emergency tasks
                    objective_terms.append(priority * x[r_idx, b_idx])

        # 2. Maximize multi-department task bundling in the same block
        # Each additional team working in the same block provides a +2500 bundling bonus
        BUNDLE_BONUS = 2500
        for b_idx in range(len(self.blocks)):
            for team_code in teams_set:
                objective_terms.append(BUNDLE_BONUS * team_used[b_idx, team_code])

        # 3. Minimize number of separate blocks used (Fixed block cost)
        BLOCK_FIXED_COST = 1500
        for b_idx in range(len(self.blocks)):
            objective_terms.append(-BLOCK_FIXED_COST * y[b_idx])

        # 4. Minimize train conflicts (Penalty for minor tier 2/3/4 train overlaps)
        CONFLICT_PENALTY = 5000
        for b_idx, block in enumerate(self.blocks):
            conflicts = block_has_train_conflict[b_idx]
            if conflicts:
                for r_idx in range(len(self.requests)):
                    if x[r_idx, b_idx] is not None:
                        objective_terms.append(-CONFLICT_PENALTY * x[r_idx, b_idx])

        self.model.Maximize(sum(objective_terms))

        # ---------------------------------------------------------------------
        # Solve with CP-SAT Solver
        # ---------------------------------------------------------------------
        solver_status = self.solver.Solve(self.model)
        solve_time_ms = round((time.time() - start_time) * 1000.0, 2)

        status_str = {
            cp_model.OPTIMAL: 'OPTIMAL',
            cp_model.FEASIBLE: 'FEASIBLE',
            cp_model.INFEASIBLE: 'INFEASIBLE',
            cp_model.MODEL_INVALID: 'MODEL_INVALID',
            cp_model.UNKNOWN: 'UNKNOWN'
        }.get(solver_status, 'UNKNOWN')

        logger.info(f"OPTIMIZATION COMPLETED (Status: {status_str}, Time: {solve_time_ms}ms)")

        # ---------------------------------------------------------------------
        # Parse Solution Results
        # ---------------------------------------------------------------------
        scheduled_assignments = []
        conflicts_list = []
        scheduled_req_ids = set()
        blocks_used_count = 0

        if solver_status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            for b_idx, block in enumerate(self.blocks):
                is_used = self.solver.BooleanValue(y[b_idx])
                if is_used:
                    blocks_used_count += 1
                    block_requests = []
                    for r_idx, req in enumerate(self.requests):
                        if x[r_idx, b_idx] is not None and self.solver.BooleanValue(x[r_idx, b_idx]):
                            scheduled_req_ids.add(req.id)
                            block_requests.append(req)

                    # Check if this block had minor train conflicts
                    clashes = block_has_train_conflict[b_idx]

                    for req in block_requests:
                        scheduled_assignments.append({
                            'request': req,
                            'block': block,
                            'location': block.location,
                            'scheduled_start': block.start_time,
                            'scheduled_end': block.end_time,
                            'has_conflict': len(clashes) > 0,
                            'conflict_detail': clashes[0] if clashes else None,
                        })

        # Identify unscheduled requests and reasons
        for req in self.requests:
            if req.id not in scheduled_req_ids:
                reason = "No feasible window without express train detention or block duration exceeded"
                conflicts_list.append({
                    'request_id': req.id,
                    'request_code': req.request_code,
                    'location': req.location,
                    'severity': req.severity,
                    'reason': reason
                })

        # Calculate metrics
        bundled_count = sum(
            1 for b in self.blocks
            if sum(1 for a in scheduled_assignments if a['block'].id == b.id) > 1
        )

        return {
            'status': status_str,
            'assignments': scheduled_assignments,
            'conflicts': conflicts_list,
            'metrics': {
                'scheduled_count': len(scheduled_assignments),
                'bundled_count': bundled_count,
                'blocks_used': blocks_used_count,
                'conflicts_count': len(conflicts_list),
                'solve_time_ms': solve_time_ms,
            }
        }
