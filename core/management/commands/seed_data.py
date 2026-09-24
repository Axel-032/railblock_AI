import datetime
from django.core.management.base import BaseCommand
from django.utils import timezone
from core.models import Team, Asset, TrainSchedule, RailwayBlock, MaintenanceRequest, Notification
from scheduler.services.scheduler_service import run_automatic_scheduling


class Command(BaseCommand):
    help = "Seed realistic Indian Railways Ahmedabad division corridor data for RailBlock AI prototype"

    def handle(self, *args, **options):
        self.stdout.write(self.style.NOTICE("Seeding RailBlock AI database with Indian Railways corridor data..."))

        # Clean old records
        Notification.objects.all().delete()
        MaintenanceRequest.objects.all().delete()
        RailwayBlock.objects.all().delete()
        TrainSchedule.objects.all().delete()
        Asset.objects.all().delete()
        Team.objects.all().delete()

        # 1. Teams
        eng = Team.objects.create(
            code='engineering',
            name='Civil Engineering',
            department_name='Civil Engineering (P-Way / Track Maintenance)',
            lead_name='Sr. DEN (Co-ord) Ahmedabad',
            badge_color='#0075F2'
        )
        sig = Team.objects.create(
            code='signal',
            name='Signal & Telecom',
            department_name='Signal & Telecommunication (S&T)',
            lead_name='Sr. DSTE Ahmedabad',
            badge_color='#2EC4B6'
        )
        trd = Team.objects.create(
            code='traction',
            name='Electrical TRD',
            department_name='Electrical Traction Distribution (25kV OHE)',
            lead_name='Sr. DEE (TRD) Sabarmati',
            badge_color='#F4D35E'
        )
        ops = Team.objects.create(
            code='operations',
            name='Operations / Section Control',
            department_name='Operating Department (Section Controller & Traffic)',
            lead_name='Sr. DOM Ahmedabad',
            badge_color='#3E2A35'
        )
        self.stdout.write(self.style.SUCCESS("[OK] Created 4 Railway Department Teams"))

        # 2. Corridor Assets
        assets_data = [
            ("AST-ADI-101", "Permanent Way Track Segment KM 492/10-20", "Ahmedabad", eng, "CRITICAL"),
            ("AST-MAN-204", "Point Machine 114-A Turnout", "Maninagar", sig, "HIGH"),
            ("AST-VAT-308", "25kV OHE Cantilever & Contact Wire", "Vatva", trd, "HIGH"),
            ("AST-SAB-402", "Axle Counter Track Circuit BPAC", "Sabarmati", sig, "MEDIUM"),
            ("AST-ADI-105", "Diamond Crossing Turnout 12B", "Ahmedabad", eng, "HIGH"),
            ("AST-MAN-209", "OHE Section Insulator 25kV", "Maninagar", trd, "MEDIUM"),
            ("AST-VAT-312", "Deep Screening Ballast Track Bed", "Vatva", eng, "LOW"),
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
        self.stdout.write(self.style.SUCCESS(f"[OK] Created {len(assets_data)} Corridor Assets"))

        # 3. Train Schedules (Restricted Windows)
        trains_data = [
            ("12932", "Ahmedabad - Mumbai Central Double Decker Express", "Ahmedabad", datetime.time(6, 0), datetime.time(6, 30), 1, "DN Main"),
            ("19015", "Saurashtra Express (Mumbai - Porbandar)", "Sabarmati", datetime.time(8, 30), datetime.time(9, 0), 2, "UP Main"),
            ("20901", "Mumbai - Gandhinagar Capital Vande Bharat Express", "Maninagar", datetime.time(11, 15), datetime.time(11, 40), 1, "DN Fast"),
            ("12009", "Mumbai Central - Ahmedabad Shatabdi Express", "Ahmedabad", datetime.time(13, 10), datetime.time(13, 40), 1, "DN Fast"),
            ("22953", "Gujarat Superfast Express", "Vatva", datetime.time(13, 45), datetime.time(14, 15), 2, "UP Main"),
            ("BOXN-781", "Container Freight Rake Concor Special", "Vatva", datetime.time(16, 35), datetime.time(17, 15), 4, "Goods Loop"),
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
        self.stdout.write(self.style.SUCCESS(f"[OK] Created {len(trains_data)} Train Schedules with Headway Boundaries"))

        # 4. Railway Corridor Possession Blocks
        blocks_data = [
            ("BLK-101", "Ahmedabad", datetime.time(7, 0), datetime.time(9, 0), 120, False),
            ("BLK-102", "Maninagar", datetime.time(12, 0), datetime.time(14, 0), 120, True),
            ("BLK-103", "Vatva", datetime.time(14, 35), datetime.time(16, 15), 100, False),
            ("BLK-104", "Sabarmati", datetime.time(16, 0), datetime.time(17, 30), 90, False),
        ]
        for bcode, bloc, bst, bet, bdur, bpw in blocks_data:
            RailwayBlock.objects.create(
                block_code=bcode,
                location=bloc,
                date=timezone.now().date(),
                start_time=bst,
                end_time=bet,
                duration_minutes=bdur,
                is_power_cut_required=bpw,
                max_capacity_minutes=bdur,
                status='SCHEDULED'
            )
        self.stdout.write(self.style.SUCCESS(f"[OK] Created {len(blocks_data)} Railway Corridor Blocks"))

        # 5. Initial Maintenance Requests
        reqs_data = [
            (
                "REQ-ENG-001",
                eng,
                "AST-ADI-101",
                "Permanent Way Track Segment",
                "Ahmedabad",
                "Emergency Rail Renewal KM 492/14",
                "Ultrasonic rail flaw detected near turnout tongue. Requires 6m rail piece renewal and flash butt weld.",
                "CRITICAL",
                True,
                60,
                "Track Gang (20 men) + Portable Rail Saw + Weld Kit"
            ),
            (
                "REQ-SIG-002",
                sig,
                "AST-MAN-204",
                "Point Machine 114-A",
                "Maninagar",
                "Point Machine Inspection & Throw Adjustment",
                "Quarterly obstacle deflection and rodding clearance inspection of motor point machine.",
                "HIGH",
                False,
                45,
                "S&T Point Technician Crew (4 staff) + Multimeter kit"
            ),
            (
                "REQ-TRD-003",
                trd,
                "AST-MAN-209",
                "25kV OHE Section Insulator",
                "Maninagar",
                "25kV OHE Dropper Replacement & Catenary Regulating",
                "Catenary sag adjustment and contact wire height verification at crossover junction.",
                "HIGH",
                False,
                60,
                "Tower Wagon (RU-800) + 25kV OHE Gang (8 crew)"
            ),
            (
                "REQ-ENG-004",
                eng,
                "AST-VAT-312",
                "Track Bed & Ballast",
                "Vatva",
                "Tie-Tamping on Goods Arrival Line",
                "Routine mechanised packing and tamping for track geometry retention.",
                "MEDIUM",
                False,
                90,
                "CSM 09-32 Continuous Tamping Machine"
            ),
        ]

        for rcode, rteam, raid, ratype, rloc, rmtype, rdesc, rsev, remerge, rdur, rres in reqs_data:
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
        self.stdout.write(self.style.SUCCESS(f"[OK] Created {len(reqs_data)} Maintenance Requests"))

        # 6. Run Initial Automatic Scheduling to bind baseline assignments
        self.stdout.write(self.style.NOTICE("Running automatic OR-Tools optimization to compute baseline block schedule..."))
        result = run_automatic_scheduling(trigger_source="Initial Data Seed", force=True)
        self.stdout.write(self.style.SUCCESS(f"[OK] Automatic scheduling complete: {result.get('status')}"))
        self.stdout.write(self.style.SUCCESS("Database seeding completed successfully."))
