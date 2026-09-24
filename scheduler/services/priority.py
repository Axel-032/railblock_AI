import logging
from django.utils import timezone

logger = logging.getLogger('scheduler')


SEVERITY_WEIGHTS = {
    'CRITICAL': 500.0,
    'HIGH': 300.0,
    'MEDIUM': 150.0,
    'LOW': 50.0,
}

ASSET_CRITICALITY_WEIGHTS = {
    'CRITICAL': 200.0,
    'HIGH': 100.0,
    'MEDIUM': 50.0,
    'LOW': 10.0,
}

EMERGENCY_BOOST = 1000.0


def calculate_priority_score(request) -> float:
    """
    Computes a deterministic multi-factor priority score for a maintenance request.
    
    Factors considered:
    1. Emergency status (Emergency requests receive immediate precedence)
    2. Maintenance severity (Critical > High > Medium > Low)
    3. Asset criticality (Vital mainline tracks, point machines, and 25kV OHE have higher weight)
    4. Duration factor (Shorter urgent interventions can be scheduled faster)
    5. Age / waiting time (Prevents request starvation)
    """
    score = 0.0

    # 1. Emergency Preemption Boost
    if getattr(request, 'is_emergency', False):
        score += EMERGENCY_BOOST

    # 2. Severity Weight
    severity = getattr(request, 'severity', 'MEDIUM').upper()
    score += SEVERITY_WEIGHTS.get(severity, 150.0)

    # 3. Asset Criticality
    # Check related asset if present, or infer from asset_type
    asset_crit = 'MEDIUM'
    if hasattr(request, 'asset') and request.asset:
        asset_crit = request.asset.criticality
    else:
        # Heuristic inference from asset_type keywords
        asset_type_lower = str(getattr(request, 'asset_type', '')).lower()
        if any(w in asset_type_lower for w in ['track', 'p-way', 'point', 'interlocking', 'catenary']):
            asset_crit = 'HIGH' if severity in ['CRITICAL', 'HIGH'] else 'MEDIUM'
        elif 'bridge' in asset_type_lower or 'fracture' in asset_type_lower:
            asset_crit = 'CRITICAL'
            
    score += ASSET_CRITICALITY_WEIGHTS.get(asset_crit, 50.0)

    # 4. Age factor (Pending age in hours)
    if hasattr(request, 'created_at') and request.created_at:
        hours_pending = (timezone.now() - request.created_at).total_seconds() / 3600.0
        score += min(hours_pending * 5.0, 100.0)  # Capped at 100 age points

    # 5. Maintenance type urgency modifier
    m_type = str(getattr(request, 'maintenance_type', '')).lower()
    if any(w in m_type for w in ['fracture', 'break', 'snap', 'jam', 'emergency']):
        score += 250.0
    elif any(w in m_type for w in ['tamping', 'screening', 'renewal', 'overhaul']):
        score += 80.0

    return round(score, 2)


def update_request_priority(request, save=True) -> float:
    """Calculates and assigns priority to a single MaintenanceRequest"""
    score = calculate_priority_score(request)
    request.calculated_priority_score = score
    if save:
        request.save(update_fields=['calculated_priority_score'])
    return score


def calculate_and_update_priorities(requests):
    """Batch calculates priority scores for a queryset or list of MaintenanceRequests"""
    for req in requests:
        req.calculated_priority_score = calculate_priority_score(req)
        req.save(update_fields=['calculated_priority_score'])
