import datetime
from django.core.management.base import BaseCommand
from django.utils import timezone
from core.models import Team, Asset, TrainSchedule, RailwayBlock, MaintenanceRequest, Notification
from scheduler.services.scheduler_service import run_automatic_scheduling


class Command(BaseCommand):
    help = "Seed realistic Indian Railways corridor data for TMS, SMMS, TRD block scheduling"

    def handle(self, *args, **options):
        self.stdout.write(self.style.NOTICE("Initializing Railway Block Scheduling Database..."))

        # Clean existing records
        Notification.objects.all().delete()
        MaintenanceRequest.objects.all().delete()
        RailwayBlock.objects.all().delete()
        TrainSchedule.objects.all().delete()
        Asset.objects.all().delete()
        Team.objects.all().delete()

        # 1. Departments / Teams
        tms = Team.objects.create(
            code='tms',
            name='Track Management System (TMS)',
            department_name='Track Management System — Civil Engineering / Permanent Way',
            lead_name='Sr. DEN (Co-ordination) Ahmedabad',
            badge_color='#0075F2'  # Blue (primary)
        )
        smms = Team.objects.create(
            code='smms',
            name='Signal Maintenance & Management System (SMMS)',
            department_name='Signal Maintenance & Management System — S&T Division',
            lead_name='Sr. DSTE (Signal) Ahmedabad',
            badge_color='#2EC4B6'  # Signal S&T
        )
        trd = Team.objects.create(
            code='trd',
            name='Traction Distribution System (TRD)',
            department_name='Traction Distribution System — 25kV AC Electrical OHE',
            lead_name='Sr. DEE (TRD) Sabarmati',
            badge_color='#F4D35E'  # Gold (accent)
        )
        ops = Team.objects.create(
            code='operations',
            name='Joint Operations / Section Control',
            department_name='Traffic & Section Controller Master Command',
            lead_name='Chief Section Controller (ChC) Control Office',
            badge_color='#3E2A35'  # Violet (accent)
        )
        self.stdout.write(self.style.SUCCESS("[OK] Created 3 Primary Railway Departments (TMS, SMMS, TRD) + Operations"))

        # 2. Corridor Assets
        assets_data = [
            # TMS Assets
            ("AST-TMS-01", "Turnout 114-A 1:12 Curved Switch", "Maninagar Down Main", tms, "CRITICAL"),
            ("AST-TMS-02", "Continuous Welded Rail (CWR) KM 492/10-20", "Sabarmati Fast Line", tms, "HIGH"),
            ("AST-TMS-03", "Deep Screening Ballast Bed Segment B-4", "Ahmedabad Up Main", tms, "HIGH"),
            ("AST-TMS-04", "Goods Arrival Track Line 3", "Vatva Freight Loop", tms, "MEDIUM"),
            # SMMS Assets
            ("AST-SMMS-01", "Dual-Control Point Machine 114-A", "Maninagar Down Main", smms, "CRITICAL"),
            ("AST-SMMS-02", "Multi-Section Digital Axle Counter (MSDAC)", "Ahmedabad Up Main", smms, "HIGH"),
            ("AST-SMMS-03", "Audio Frequency Track Circuit (AFTC)", "Vatva Freight Loop", smms, "MEDIUM"),
            ("AST-SMMS-04", "Colour Light Home Signal Aspect S-12", "Sabarmati Fast Line", smms, "LOW"),
            # TRD Assets
            ("AST-TRD-01", "25kV OHE Catenary & Contact Wire Mast 492/14", "Maninagar Down Main", trd, "HIGH"),
            ("AST-TRD-02", "Motorized Section Isolator Switch SS-04", "Maninagar Down Main", trd, "HIGH"),
            ("AST-TRD-03", "Cantilever Assembly & Dropper System", "Sabarmati Fast Line", trd, "MEDIUM"),
            ("AST-TRD-04", "25kV Sub-Sector Switching Post SP-02", "Ahmedabad Up Main", trd, "CRITICAL"),
        ]
        for aid, atype, loc, team, crit in assets_data:
            Asset.objects.create(
                asset_id=aid,
                asset_type=atype,
                location=loc,
                team=team,
                criticality=crit,
                last_inspection_date=timezone.now().date()
            )
        self.stdout.write(self.style.SUCCESS(f"[OK] Registered {len(assets_data)} Department Corridor Assets"))

        # 3. Train Timetable (Defining Corridor Traffic Criticality Profile)
        trains_data = [
            # Morning Peak (06:00 - 10:30) - Highly Critical
            ("12932", "Ahmedabad - Mumbai Central Double Decker", "Ahmedabad Up Main", datetime.time(6, 0), datetime.time(6, 40), 1, "UP Main"),
            ("20901", "Mumbai - Gandhinagar Vande Bharat Express", "Maninagar Down Main", datetime.time(7, 15), datetime.time(7, 55), 1, "DN Fast"),
            ("12009", "Mumbai Central - Ahmedabad Shatabdi Express", "Ahmedabad Up Main", datetime.time(8, 30), datetime.time(9, 10), 1, "DN Fast"),
            ("19015", "Saurashtra Express (Mumbai - Porbandar)", "Sabarmati Fast Line", datetime.time(9, 30), datetime.time(10, 15), 2, "UP Main"),
            # Midday Lean Window (11:30 - 14:00) - Off-Peak Lean Corridor
            ("BOXN-412", "Container Freight Rake Special", "Vatva Freight Loop", datetime.time(12, 30), datetime.time(13, 0), 4, "Goods Loop"),
            # Afternoon Transition (14:30 - 16:30)
            ("22953", "Gujarat Superfast Express", "Maninagar Down Main", datetime.time(15, 0), datetime.time(15, 40), 2, "UP Main"),
            ("19216", "Saurashtra Janta Express", "Sabarmati Fast Line", datetime.time(16, 10), datetime.time(16, 50), 2, "DN Main"),
            # Evening Peak (17:30 - 21:00) - Highly Critical
            ("20902", "Gandhinagar - Mumbai Vande Bharat Express", "Maninagar Down Main", datetime.time(17, 45), datetime.time(18, 25), 1, "UP Fast"),
            ("12952", "New Delhi - Mumbai Central Rajdhani Express", "Ahmedabad Up Main", datetime.time(18, 50), datetime.time(19, 30), 1, "UP Main"),
            ("12971", "Bhavnagar Superfast Express", "Sabarmati Fast Line", datetime.time(20, 10), datetime.time(20, 50), 2, "DN Main"),
        ]
        for num, name, loc, st, et, prio, track in trains_data:
            TrainSchedule.objects.create(
                train_number=num,
                train_name=name,
                location=loc,
                start_time=st,
                end_time=et,
                priority_tier=prio,
                track_direction=track
            )
        self.stdout.write(self.style.SUCCESS(f"[OK] Configured {len(trains_data)} Timetable Services establishing Traffic Heatmap"))

        # 4. Realistic Maintenance Requests across TMS, SMMS, TRD
        # Explicitly designed with compatible bundling opportunities and safety incompatibilities!
        requests_data = [
            # -------------------------------------------------------------
            # SET 1: Maninagar Down Main -> COMPATIBLE BUNDLING OPPORTUNITY
            # TRD OHE Power Cut + TMS Turnout Tongue Renewal + SMMS Point Machine Overhaul
            # -------------------------------------------------------------
            (
                "REQ-TRD-101",
                trd,
                "AST-TRD-01",
                "25kV OHE Contact Wire",
                "Maninagar Down Main",
                "25kV OHE Wire Tensioning & Insulator Washing",
                "OHE annual catenary height regulation and high-pressure chemical washing of section insulators. Requires 25kV power cut.",
                "HIGH",
                False,
                90,
                "Tower Wagon (RU-800) + TRD OHE Crew (8 staff)"
            ),
            (
                "REQ-TMS-102",
                tms,
                "AST-TMS-01",
                "Turnout 114-A Switch",
                "Maninagar Down Main",
                "Turnout Rail Tongue Replacement & Welding",
                "Renew worn LH switch tongue rail and flash butt weld with stock rail. Safely co-workable under OHE power cut.",
                "CRITICAL",
                True,
                60,
                "P-Way Welding Gang (15 men) + Portable Rail Cutter"
            ),
            (
                "REQ-SMMS-103",
                smms,
                "AST-SMMS-01",
                "Point Machine 114-A",
                "Maninagar Down Main",
                "Point Machine Overhaul & Throw Calibration",
                "Quarterly obstacle deflection check, friction clutch testing, and switch rodding alignment.",
                "HIGH",
                False,
                60,
                "S&T Point Technician Gang (4 staff) + Test Rig"
            ),

            # -------------------------------------------------------------
            # SET 2: Sabarmati Fast Line -> COMPATIBLE BUNDLING OPPORTUNITY
            # TRD Catenary Alignment + TMS Ultrasonic Flaw Testing
            # -------------------------------------------------------------
            (
                "REQ-TRD-201",
                trd,
                "AST-TRD-03",
                "Cantilever & Dropper",
                "Sabarmati Fast Line",
                "25kV Catenary Bracket Alignment & Dropper Adjustment",
                "Fine alignment of stagger and contact wire profile along crossover curve.",
                "MEDIUM",
                False,
                60,
                "TRD Ladder Trolley Gang (6 staff)"
            ),
            (
                "REQ-TMS-202",
                tms,
                "AST-TMS-02",
                "Continuous Welded Rail",
                "Sabarmati Fast Line",
                "Ultrasonic Flaw Detection (USFD) Rail Testing",
                "Continuous non-destructive ultrasonic testing for internal rail transverse fissures.",
                "HIGH",
                False,
                60,
                "USFD Digital Machine Crew (3 technicians)"
            ),

            # -------------------------------------------------------------
            # SET 3: Ahmedabad Up Main -> INCOMPATIBLE SAFETY CONFLICT
            # TMS Heavy Ballast Cleaning (Lifting track) vs SMMS Axle Counter Calibration
            # Must be strictly separated by AI!
            # -------------------------------------------------------------
            (
                "REQ-TMS-301",
                tms,
                "AST-TMS-03",
                "Track Bed & Ballast",
                "Ahmedabad Up Main",
                "Deep Screening Ballast Cleaning Machine (BCM)",
                "Heavy mechanized ballast excavation and track lifting. Causes severe ground vibration and track realignment.",
                "HIGH",
                False,
                120,
                "Plasser RM-80 BCM Machine + Consist"
            ),
            (
                "REQ-SMMS-302",
                smms,
                "AST-SMMS-02",
                "Digital Axle Counter",
                "Ahmedabad Up Main",
                "Axle Counter (MSDAC) Electronic Calibration",
                "High-precision electromagnetic sensor sensitivity alignment. INCOMPATIBLE with track vibration and lifting.",
                "HIGH",
                False,
                90,
                "Senior Section Engineer (Signal) + Precision Millivoltmeter"
            ),
            (
                "REQ-TRD-303",
                trd,
                "AST-TRD-04",
                "25kV Switching Post",
                "Ahmedabad Up Main",
                "25kV Sub-Sector Isolator Overhaul",
                "Vacuum circuit breaker contact replacement and auxiliary contact calibration.",
                "MEDIUM",
                False,
                60,
                "TRD Substation Electrical Crew (5 staff)"
            ),

            # -------------------------------------------------------------
            # SET 4: Vatva Freight Loop -> LEAST CRITICAL TIME DEMONSTRATION
            # Routine track packing and S&T bonding shifted to night lean hours
            # -------------------------------------------------------------
            (
                "REQ-TMS-401",
                tms,
                "AST-TMS-04",
                "Goods Line Track",
                "Vatva Freight Loop",
                "Mechanized Tie-Tamping on Goods Line",
                "Routine track geometry retention tamping along siding arrival loop.",
                "MEDIUM",
                False,
                90,
                "CSM 09-32 Continuous Tamping Machine"
            ),
            (
                "REQ-SMMS-402",
                smms,
                "AST-SMMS-03",
                "Track Circuit AFTC",
                "Vatva Freight Loop",
                "Track Circuit Bonding & Impedance Bond Renewal",
                "Renew copper bond wires and tuning unit resistance check.",
                "LOW",
                False,
                60,
                "S&T Track Circuit Technicians (3 crew)"
            ),
        ]

        for rcode, rteam, raid, ratype, rloc, rmtype, rdesc, rsev, remerge, rdur, rres in requests_data:
            MaintenanceRequest.objects.create(
                request_code=rcode,
                team=rteam,
                asset_id=raid,
                asset_type=ratype,
                location=rloc,
                maintenance_type=rmtype,
                description=rdesc,
                severity=rsev,
                is_emergency=remerge,
                estimated_duration=rdur,
                preferred_date=timezone.now().date(),
                required_resources=rres,
                status='PENDING'
            )
        self.stdout.write(self.style.SUCCESS(f"[OK] Created {len(requests_data)} Multi-Department Maintenance Requests"))

        # 5. Run Initial Optimization
        self.stdout.write(self.style.NOTICE("Executing Google OR-Tools CP-SAT with Lagrangian Relaxation..."))
        opt_res = run_automatic_scheduling(trigger_source="Database Seeder Initial Run", force=True)

        metrics = opt_res.get('metrics', {})
        self.stdout.write(self.style.SUCCESS(
            f"[OPTIMIZATION COMPLETE]\n"
            f"  - Status: {opt_res.get('status')}\n"
            f"  - Tasks Scheduled: {metrics.get('scheduled_count')}\n"
            f"  - Joint Blocks Combined: {metrics.get('joint_bundles_count')}\n"
            f"  - Downtime Saved: {metrics.get('downtime_saved_minutes')} mins\n"
            f"  - Track Uptime: {metrics.get('track_uptime_percent')}%\n"
            f"  - Solve Time: {metrics.get('solve_time_ms')} ms\n"
            f"  - Lagrangian Iterations: {metrics.get('lagrangian_iterations')}"
        ))
