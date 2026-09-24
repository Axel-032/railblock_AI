import time
import math
import logging
from datetime import datetime, time as dtime, timedelta
from typing import List, Dict, Any, Optional, Tuple, Set
from ortools.sat.python import cp_model

logger = logging.getLogger('scheduler')

# Total slots in 24 hours with 30-minute granularity
SLOTS_PER_DAY = 48
MINUTES_PER_SLOT = 30


def time_to_slot(t: dtime) -> int:
    """Convert datetime.time to 30-min slot index (0 - 47)."""
    minutes = t.hour * 60 + t.minute
    return min(SLOTS_PER_DAY - 1, max(0, minutes // MINUTES_PER_SLOT))


def slot_to_time(slot: int) -> dtime:
    """Convert slot index (0 - 47) back to datetime.time."""
    slot = max(0, min(SLOTS_PER_DAY - 1, slot))
    total_min = slot * MINUTES_PER_SLOT
    hour = min(23, total_min // 60)
    minute = total_min % 60
    return dtime(hour=hour, minute=minute)


def slot_to_str(slot: int) -> str:
    """Format slot index as HH:MM string."""
    t = slot_to_time(slot)
    return t.strftime("%H:%M")


def duration_to_slots(duration_minutes: int) -> int:
    """Convert duration in minutes to number of 30-min slots (ceiling)."""
    return max(1, math.ceil(duration_minutes / MINUTES_PER_SLOT))


class LagrangianBlockOptimizer:
    """
    Railway Maintenance Block Scheduler using Google OR-Tools CP-SAT
    with Lagrangian Relaxation Decomposition.

    Core Objectives:
    1. Maximizes Corridor Uptime by combining compatible blocks (TMS + SMMS + TRD)
       into shared 'Shadow / Joint' possession windows.
    2. Enforces strict safety separation for incompatible maintenance types.
    3. Places blocks during least critical train traffic times (avoiding passenger peaks).
    4. Dualizes corridor capacity linking constraints using Lagrangian multipliers λ_t.
    """

    def __init__(
        self,
        requests: List[Any],
        train_schedules: List[Any],
        sections: Optional[List[str]] = None,
        max_lagrangian_iterations: int = 6,
        tolerance_per_slot: int = 5,
    ):
        self.requests = list(requests)
        self.train_schedules = list(train_schedules)
        self.sections = sections or list(set(r.location for r in self.requests))
        self.max_iterations = max_lagrangian_iterations
        self.tolerance_per_slot = tolerance_per_slot

        # Precompute train traffic criticality profile C(section, slot)
        self.criticality_profile, self.overall_traffic_profile = self._build_criticality_profile()

    def _build_criticality_profile(self) -> Tuple[Dict[str, List[int]], List[int]]:
        """
        Calculates train traffic criticality for each section and each 30-min slot.
        Tier 1 (Vande Bharat / Rajdhani / Shatabdi): weight 12
        Tier 2 (Mail / Express): weight 7
        Tier 3 (Passenger / Local): weight 4
        Tier 4 (Freight): weight 2
        """
        profile: Dict[str, List[int]] = {sec: [0] * SLOTS_PER_DAY for sec in self.sections}
        overall = [0] * SLOTS_PER_DAY

        tier_weights = {1: 12, 2: 7, 3: 4, 4: 2}

        for train in self.train_schedules:
            weight = tier_weights.get(getattr(train, 'priority_tier', 2), 5)
            s_slot = time_to_slot(train.start_time)
            e_slot = time_to_slot(train.end_time)
            # Add safety buffer: 1 slot before and after
            buf_start = max(0, s_slot - 1)
            buf_end = min(SLOTS_PER_DAY - 1, e_slot + 1)

            t_loc = train.location.strip()
            # Match location or apply to matching sections
            matched_sections = [sec for sec in self.sections if t_loc.lower() in sec.lower() or sec.lower() in t_loc.lower()]
            if not matched_sections:
                matched_sections = self.sections

            for sec in matched_sections:
                for slot in range(buf_start, buf_end + 1):
                    profile[sec][slot] += weight
                    overall[slot] += weight

        return profile, overall

    def _check_compatibility(self, req_a: Any, req_b: Any) -> Tuple[bool, str]:
        """
        Determines if two maintenance requests are compatible, incompatible, or neutral.
        Returns: (is_compatible, reason)
        """
        # Must be in the same corridor location/section to be combined
        loc_a = req_a.location.strip().lower()
        loc_b = req_b.location.strip().lower()
        if loc_a != loc_b and loc_a not in loc_b and loc_b not in loc_a:
            return False, "Different corridor sections"

        type_a = req_a.maintenance_type.lower()
        type_b = req_b.maintenance_type.lower()
        dept_a = getattr(req_a.team, 'code', '').lower()
        dept_b = getattr(req_b.team, 'code', '').lower()

        # Incompatibility Rules (Safety Conflicts)
        # 1. Heavy Ballast Cleaning Machine (BCM) / Track Lifting vs S&T Axle Counter / Point Machine calibration
        if ("ballast" in type_a or "screening" in type_a or "lifting" in type_a) and ("axle" in type_b or "point" in type_b or "circuit" in type_b):
            return False, "INCOMPATIBLE: Heavy track vibration/lifting corrupts S&T electronic sensor calibration."
        if ("ballast" in type_b or "screening" in type_b or "lifting" in type_b) and ("axle" in type_a or "point" in type_a or "circuit" in type_a):
            return False, "INCOMPATIBLE: Heavy track vibration/lifting corrupts S&T electronic sensor calibration."

        # 2. Live High-Voltage 25kV OHE Energization/Testing vs Track Gang Manual Welding
        if ("energiz" in type_a or "live" in type_a) and ("weld" in type_b or "gang" in type_b):
            return False, "INCOMPATIBLE: High-voltage electrical safety hazard for track welding gang."
        if ("energiz" in type_b or "live" in type_b) and ("weld" in type_a or "gang" in type_a):
            return False, "INCOMPATIBLE: High-voltage electrical safety hazard for track welding gang."

        # 3. Two heavy mechanized track machines on same segment (resource clash)
        if ("tamping" in type_a or "bcm" in type_a) and ("tamping" in type_b or "bcm" in type_b):
            return False, "INCOMPATIBLE: Track machine headway violation (two heavy machines cannot occupy single block)."

        # Compatibility Rules (Bundling Opportunities)
        # 1. TRD 25kV OHE Power Cut + TMS Track Inspection/Renewal: Excellent compatibility!
        # When OHE power is isolated, track gang can work completely safely without electrical hazards.
        is_trd_a = dept_a in ('traction', 'trd')
        is_trd_b = dept_b in ('traction', 'trd')
        is_tms_a = dept_a in ('engineering', 'tms')
        is_tms_b = dept_b in ('engineering', 'tms')
        is_smms_a = dept_a in ('signal', 'smms')
        is_smms_b = dept_b in ('signal', 'smms')

        if (is_trd_a and (is_tms_b or is_smms_b)) or (is_trd_b and (is_tms_a or is_smms_a)):
            return True, "COMPATIBLE: TRD 25kV Power Block enables safe zero-voltage corridor for Track & Signal works."

        # 2. TMS Track Inspection / Turnout Renewal + SMMS Point Machine Maintenance
        if (is_tms_a and is_smms_b) or (is_tms_b and is_smms_a):
            if "point" in type_a or "point" in type_b or "turnout" in type_a or "turnout" in type_b:
                return True, "COMPATIBLE: Joint Turnout & Point Machine overhaul (P-Way + S&T integrated window)."
            return True, "COMPATIBLE: Track possession co-utilization for Signal gear inspection."

        # 3. Same department routine non-conflicting tasks must still be sequenced
        # to prevent corridor crowding and resource pile-up at the same critical time.
        if dept_a == dept_b:
            return False, "SAME_DEPT: Same department tasks require sequential execution on the same corridor."

        return True, "COMPATIBLE: Multi-agency concurrent block possession."

    def solve(self) -> Dict[str, Any]:
        """
        Executes the Lagrangian Relaxation iterative loop with CP-SAT subproblem solver.
        """
        start_clock = time.time()
        n_req = len(self.requests)

        if n_req == 0:
            return {
                'status': 'NO_REQUESTS',
                'assignments': [],
                'joint_blocks': [],
                'separated_blocks': [],
                'lagrangian_logs': [],
                'metrics': {
                    'scheduled_count': 0,
                    'joint_bundles_count': 0,
                    'downtime_saved_minutes': 0,
                    'track_uptime_percent': 100.0,
                    'solve_time_ms': 0.0,
                },
                'criticality_profile': self.criticality_profile,
                'overall_traffic_profile': self.overall_traffic_profile,
            }

        # Classify all request pairs into compatible vs incompatible
        compatible_pairs: List[Tuple[int, int, str]] = []
        incompatible_pairs: List[Tuple[int, int, str]] = []

        for i in range(n_req):
            for j in range(i + 1, n_req):
                is_comp, reason = self._check_compatibility(self.requests[i], self.requests[j])
                if is_comp:
                    compatible_pairs.append((i, j, reason))
                elif "INCOMPATIBLE" in reason or "SAME_DEPT" in reason:
                    incompatible_pairs.append((i, j, reason))

        logger.info(f"Requests: {n_req} | Compatible pairs: {len(compatible_pairs)} | Incompatible pairs: {len(incompatible_pairs)}")

        # Initialize Lagrangian Multipliers λ_t for each slot t in 0..47
        # λ_t represents the shadow cost of scheduling during congested / high-traffic slot t
        multipliers = [0.0] * SLOTS_PER_DAY

        best_primal_solution: Optional[Dict[str, Any]] = None
        best_primal_cost = float('inf')
        best_primal_uptime = 0.0

        lagrangian_history = []
        step_size = 2.0

        # ---------------------------------------------------------------------
        # Lagrangian Relaxation Loop
        # ---------------------------------------------------------------------
        for iteration in range(1, self.max_iterations + 1):
            iter_start = time.time()

            # 1. Formulate & Solve CP-SAT Subproblem with current multipliers λ
            sub_res = self._solve_cpsat_subproblem(
                multipliers=multipliers,
                compatible_pairs=compatible_pairs,
                incompatible_pairs=incompatible_pairs,
            )

            if sub_res['status'] not in ('OPTIMAL', 'FEASIBLE'):
                logger.warning(f"Lagrangian iteration {iteration}: Subproblem status {sub_res['status']}")
                break

            # 2. Compute subgradient: g_t = (Slot Occupancy * Criticality) - Capacity_Tolerance
            subgradient = [0.0] * SLOTS_PER_DAY
            slot_occupancy_count = [0] * SLOTS_PER_DAY

            for asgn in sub_res['assignments']:
                s_slot = asgn['start_slot']
                e_slot = asgn['end_slot']
                loc = asgn['location']
                for slot in range(s_slot, e_slot):
                    slot_occupancy_count[slot] += 1
                    crit = self.criticality_profile.get(loc, [2] * SLOTS_PER_DAY)[slot]
                    subgradient[slot] += crit

            for slot in range(SLOTS_PER_DAY):
                subgradient[slot] -= self.tolerance_per_slot

            # Dual bound (Lower Bound for minimization)
            dual_bound = sub_res['objective_value'] - sum(multipliers[t] * self.tolerance_per_slot for t in range(SLOTS_PER_DAY))

            # Primal true objective evaluation (Actual disruption + downtime - joint bundling savings)
            primal_metrics = self._evaluate_primal_solution(sub_res['assignments'], compatible_pairs)
            primal_cost = primal_metrics['total_disruption_cost']
            uptime_pct = primal_metrics['track_uptime_percent']

            # Track best primal feasible solution
            if primal_cost < best_primal_cost:
                best_primal_cost = primal_cost
                best_primal_uptime = uptime_pct
                best_primal_solution = sub_res
                best_primal_solution['primal_metrics'] = primal_metrics

            # Calculate Duality Gap %
            duality_gap = 0.0
            if abs(primal_cost) > 1e-4:
                duality_gap = round(max(0.0, (primal_cost - dual_bound) / abs(primal_cost)) * 100.0, 2)

            iter_duration_ms = round((time.time() - iter_start) * 1000.0, 2)

            # Record iteration log
            lagrangian_history.append({
                'iteration': iteration,
                'dual_bound': round(dual_bound, 2),
                'primal_cost': round(primal_cost, 2),
                'uptime_percent': round(uptime_pct, 1),
                'duality_gap_percent': duality_gap,
                'max_multiplier': round(max(multipliers), 2),
                'multipliers_sample': [round(multipliers[t], 1) for t in range(0, SLOTS_PER_DAY, 4)],  # sample every 2 hours
                'solve_time_ms': iter_duration_ms,
            })

            # 3. Update Multipliers via Subgradient Ascent: λ_{t}^{k+1} = max(0, λ_{t}^k + step * g_t)
            norm_sq = sum(g * g for g in subgradient)
            if norm_sq > 1e-6:
                effective_step = step_size / math.sqrt(iteration)
                for t in range(SLOTS_PER_DAY):
                    multipliers[t] = max(0.0, multipliers[t] + effective_step * (subgradient[t] / math.sqrt(norm_sq)))

        # Fallback if no solution found
        if best_primal_solution is None:
            best_primal_solution = sub_res

        total_solve_time_ms = round((time.time() - start_clock) * 1000.0, 2)

        # Build detailed joint blocks and separated blocks report
        joint_blocks, separated_blocks = self._build_joint_and_separated_blocks(
            best_primal_solution['assignments'],
            compatible_pairs,
            incompatible_pairs
        )

        return {
            'status': best_primal_solution.get('status', 'OPTIMAL'),
            'assignments': best_primal_solution['assignments'],
            'joint_blocks': joint_blocks,
            'separated_blocks': separated_blocks,
            'lagrangian_logs': lagrangian_history,
            'metrics': {
                'scheduled_count': len(best_primal_solution['assignments']),
                'joint_bundles_count': len(joint_blocks),
                'downtime_saved_minutes': best_primal_solution.get('primal_metrics', {}).get('downtime_saved_minutes', 0),
                'track_uptime_percent': best_primal_uptime,
                'total_disruption_cost': round(best_primal_cost, 1),
                'solve_time_ms': total_solve_time_ms,
                'lagrangian_iterations': len(lagrangian_history),
                'final_duality_gap_percent': lagrangian_history[-1]['duality_gap_percent'] if lagrangian_history else 0.0,
            },
            'criticality_profile': self.criticality_profile,
            'overall_traffic_profile': self.overall_traffic_profile,
        }

    def _solve_cpsat_subproblem(
        self,
        multipliers: List[float],
        compatible_pairs: List[Tuple[int, int, str]],
        incompatible_pairs: List[Tuple[int, int, str]],
    ) -> Dict[str, Any]:
        """
        Solves the CP-SAT subproblem for the given Lagrangian multiplier vector λ.
        """
        model = cp_model.CpModel()
        n_req = len(self.requests)

        # Decision variables
        start_vars = {}
        end_vars = {}
        interval_vars = {}
        dur_slots_map = {}

        # 1. Create Interval Variables for each request
        for i, req in enumerate(self.requests):
            dur_slots = duration_to_slots(req.estimated_duration)
            dur_slots_map[i] = dur_slots

            earliest = 0
            latest = SLOTS_PER_DAY

            s_var = model.NewIntVar(earliest, latest - dur_slots, f"start_{i}")
            e_var = model.NewIntVar(dur_slots, latest, f"end_{i}")
            interval_var = model.NewIntervalVar(s_var, dur_slots, e_var, f"interval_{i}")

            start_vars[i] = s_var
            end_vars[i] = e_var
            interval_vars[i] = interval_var

        # 2. INCOMPATIBILITY CONSTRAINTS: Separate incompatible blocks!
        # Incompatible requests on the same section cannot overlap in time.
        for i, j, reason in incompatible_pairs:
            model.AddNoOverlap([interval_vars[i], interval_vars[j]])

        # 3. COMPATIBILITY & COMBINING BLOCKS:
        # For compatible pairs on the same section, we introduce a boolean variable
        # joint[i, j] = 1 if start_vars[i] == start_vars[j].
        # We reward joint[i, j] strongly in the objective to coalesce compatible works!
        joint_vars = {}
        for idx, (i, j, reason) in enumerate(compatible_pairs):
            joint_var = model.NewBoolVar(f"joint_{i}_{j}")
            model.Add(start_vars[i] == start_vars[j]).OnlyEnforceIf(joint_var)
            joint_vars[(i, j)] = joint_var

        # 4. OBJECTIVE FUNCTION:
        objective_terms = []
        SCALE = 100

        for i, req in enumerate(self.requests):
            loc = req.location.strip()
            loc_criticality = self.criticality_profile.get(loc, self.overall_traffic_profile)
            dur = dur_slots_map[i]
            preferred_slot = None
            if getattr(req, 'preferred_time', None):
                preferred_slot = time_to_slot(req.preferred_time)

            for s in range(SLOTS_PER_DAY - dur + 1):
                slot_cost = 0.0
                for t in range(s, s + dur):
                    traffic_crit = loc_criticality[t]
                    multiplier_penalty = multipliers[t]
                    slot_cost += (traffic_crit * 1.5 + multiplier_penalty * 3.0)

                # Preferred night/lean window bonus (encourage 01:00-05:00 and 11:30-14:00)
                if (2 <= s <= 8) or (23 <= s <= 26):
                    slot_cost -= 15.0

                # Department-specific time-of-day preference is a soft preference:
                # it should guide the schedule toward a preferred window when safe,
                # but never override traffic safety or incompatibility constraints.
                if preferred_slot is not None:
                    center_slot = s + (dur / 2.0)
                    distance = abs(center_slot - preferred_slot)
                    if distance <= 0.5:
                        slot_cost -= 180.0
                    elif distance <= 1.5:
                        slot_cost -= 120.0
                    elif distance <= 3.0:
                        slot_cost -= 60.0
                    elif distance <= 5.0:
                        slot_cost -= 20.0
                    else:
                        slot_cost += distance * 12.0

                cost_int = int(slot_cost * SCALE)

                is_at_s = model.NewBoolVar(f"req_{i}_at_{s}")
                model.Add(start_vars[i] == s).OnlyEnforceIf(is_at_s)
                model.Add(start_vars[i] != s).OnlyEnforceIf(is_at_s.Not())

                objective_terms.append(cost_int * is_at_s)

        # Bundling Reward: Combining compatible blocks saves corridor track downtime!
        JOINT_BUNDLE_REWARD = int(35.0 * SCALE)
        for (i, j), joint_var in joint_vars.items():
            objective_terms.append(-JOINT_BUNDLE_REWARD * joint_var)

        model.Minimize(sum(objective_terms))

        # Solve CP-SAT
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = 4.0
        solver.parameters.num_search_workers = 4

        status = solver.Solve(model)

        status_str = {
            cp_model.OPTIMAL: 'OPTIMAL',
            cp_model.FEASIBLE: 'FEASIBLE',
            cp_model.INFEASIBLE: 'INFEASIBLE',
            cp_model.MODEL_INVALID: 'MODEL_INVALID',
            cp_model.UNKNOWN: 'UNKNOWN',
        }.get(status, 'UNKNOWN')

        assignments = []
        if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            for i, req in enumerate(self.requests):
                s_slot = solver.Value(start_vars[i])
                dur = dur_slots_map[i]
                e_slot = s_slot + dur
                st_time = slot_to_time(s_slot)
                et_time = slot_to_time(e_slot)

                loc = req.location.strip()
                crit_slice = self.criticality_profile.get(loc, self.overall_traffic_profile)[s_slot:e_slot]
                avg_crit = sum(crit_slice) / max(1, len(crit_slice))

                joined_with = []
                for (a, b), j_var in joint_vars.items():
                    if solver.BooleanValue(j_var):
                        if a == i:
                            joined_with.append(self.requests[b].request_code)
                        elif b == i:
                            joined_with.append(self.requests[a].request_code)

                is_joint = len(joined_with) > 0

                assignments.append({
                    'request_id': req.id,
                    'request_code': req.request_code,
                    'team_code': getattr(req.team, 'code', 'tms'),
                    'team_name': getattr(req.team, 'name', 'TMS'),
                    'maintenance_type': req.maintenance_type,
                    'location': req.location,
                    'start_slot': s_slot,
                    'end_slot': e_slot,
                    'start_time_str': st_time.strftime("%H:%M"),
                    'end_time_str': et_time.strftime("%H:%M"),
                    'duration_minutes': req.estimated_duration,
                    'duration_slots': dur,
                    'is_joint': is_joint,
                    'joined_with': joined_with,
                    'traffic_criticality': round(avg_crit, 1),
                    'is_emergency': getattr(req, 'is_emergency', False),
                    'severity': getattr(req, 'severity', 'MEDIUM'),
                    'slot_period_desc': self._get_period_description(s_slot),
                })

        return {
            'status': status_str,
            'assignments': assignments,
            'objective_value': solver.ObjectiveValue() / SCALE if status in (cp_model.OPTIMAL, cp_model.FEASIBLE) else 99999.0,
        }

    def _get_period_description(self, slot: int) -> str:
        """Helper to label the operational time slot period."""
        if 2 <= slot <= 8:
            return "Deep Night Lean Window (01:00 - 05:00) [Least Critical]"
        elif 9 <= slot <= 13:
            return "Early Morning Shoulder (05:00 - 07:00)"
        elif 14 <= slot <= 20:
            return "Morning Peak Passenger Rush (07:00 - 10:30) [Critical]"
        elif 21 <= slot <= 28:
            return "Midday Lean Window (10:30 - 14:30) [Off-Peak Corridor]"
        elif 29 <= slot <= 33:
            return "Afternoon Freight Window (14:30 - 17:00)"
        elif 34 <= slot <= 41:
            return "Evening Peak Passenger Rush (17:00 - 21:00) [Critical]"
        else:
            return "Late Evening Lean (21:00 - 01:00)"

    def _evaluate_primal_solution(
        self,
        assignments: List[Dict[str, Any]],
        compatible_pairs: List[Tuple[int, int, str]]
    ) -> Dict[str, Any]:
        """Calculates true engineering metrics for a feasible schedule."""
        total_disruption = 0.0
        raw_downtime = sum(a['duration_minutes'] for a in assignments)

        joint_groups: Dict[Tuple[str, int], List[Dict[str, Any]]] = {}
        for a in assignments:
            key = (a['location'], a['start_slot'])
            joint_groups.setdefault(key, []).append(a)

        actual_downtime = 0
        minutes_saved = 0
        for key, group in joint_groups.items():
            max_dur = max(item['duration_minutes'] for item in group)
            actual_downtime += max_dur
            if len(group) > 1:
                sum_dur = sum(item['duration_minutes'] for item in group)
                minutes_saved += (sum_dur - max_dur)

        for a in assignments:
            loc = a['location']
            s = a['start_slot']
            e = a['end_slot']
            crit_slice = self.criticality_profile.get(loc, self.overall_traffic_profile)[s:e]
            total_disruption += sum(crit_slice)

        total_disruption_cost = total_disruption * 10 + (actual_downtime * 0.5) - (minutes_saved * 2.0)

        total_available_mins = 1440 * max(1, len(self.sections))
        track_uptime_pct = max(0.0, min(100.0, ((total_available_mins - actual_downtime) / total_available_mins) * 100.0))

        return {
            'total_disruption_cost': total_disruption_cost,
            'raw_downtime_minutes': raw_downtime,
            'actual_downtime_minutes': actual_downtime,
            'downtime_saved_minutes': minutes_saved,
            'track_uptime_percent': round(track_uptime_pct, 1),
        }

    def _build_joint_and_separated_blocks(
        self,
        assignments: List[Dict[str, Any]],
        compatible_pairs: List[Tuple[int, int, str]],
        incompatible_pairs: List[Tuple[int, int, str]],
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        Builds structured summaries of combined joint blocks and separated incompatible blocks.
        """
        grouped: Dict[Tuple[str, int], List[Dict[str, Any]]] = {}
        for a in assignments:
            key = (a['location'], a['start_slot'])
            grouped.setdefault(key, []).append(a)

        joint_blocks = []
        joint_block_idx = 1
        for (loc, start_slot), group in grouped.items():
            if len(group) > 1:
                max_dur = max(item['duration_minutes'] for item in group)
                end_slot = start_slot + duration_to_slots(max_dur)
                teams_involved = list(set(item['team_name'] for item in group))
                req_codes = [item['request_code'] for item in group]
                tasks = [item['maintenance_type'] for item in group]
                saved = sum(item['duration_minutes'] for item in group) - max_dur

                joint_blocks.append({
                    'joint_id': f"JB-{joint_block_idx:02d}",
                    'location': loc,
                    'start_time': slot_to_time(start_slot).strftime("%H:%M"),
                    'end_time': slot_to_time(end_slot).strftime("%H:%M"),
                    'duration_minutes': max_dur,
                    'teams': teams_involved,
                    'tasks': tasks,
                    'request_codes': req_codes,
                    'minutes_saved': saved,
                    'explanation': (
                        f"Combined {len(group)} compatible tasks across "
                        f"{' + '.join(teams_involved)} into a single {max_dur}-min possession window. "
                        f"Saved {saved} minutes of corridor closure."
                    ),
                })
                joint_block_idx += 1

        asgn_by_code = {a['request_code']: a for a in assignments}
        separated_blocks = []
        for i, j, reason in incompatible_pairs:
            req_a = self.requests[i]
            req_b = self.requests[j]
            asgn_a = asgn_by_code.get(req_a.request_code)
            asgn_b = asgn_by_code.get(req_b.request_code)
            if asgn_a and asgn_b:
                overlap = max(asgn_a['start_slot'], asgn_b['start_slot']) < min(asgn_a['end_slot'], asgn_b['end_slot'])
                separated_blocks.append({
                    'req_a': {
                        'code': req_a.request_code,
                        'team': getattr(req_a.team, 'name', 'TMS'),
                        'type': req_a.maintenance_type,
                        'time': f"{asgn_a['start_time_str']} - {asgn_a['end_time_str']}",
                    },
                    'req_b': {
                        'code': req_b.request_code,
                        'team': getattr(req_b.team, 'name', 'SMMS'),
                        'type': req_b.maintenance_type,
                        'time': f"{asgn_b['start_time_str']} - {asgn_b['end_time_str']}",
                    },
                    'location': req_a.location,
                    'is_safely_separated': not overlap,
                    'separation_reason': reason,
                })

        return joint_blocks, separated_blocks
