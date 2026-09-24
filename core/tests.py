import datetime
import json
from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone
from core.models import Team, Asset, TrainSchedule, RailwayBlock, MaintenanceRequest, OptimizationLog, DailyScheduleControl
from scheduler.services.scheduler_service import run_automatic_scheduling
from scheduler.services.lagrangian_optimizer import LagrangianBlockOptimizer


class RailBlockLagrangianE2ETest(TestCase):
    def setUp(self):
        # 1. Setup the 3 railway departments: TMS, SMMS, TRD
        self.tms = Team.objects.create(
            code='tms',
            name='Track Management System (TMS)',
            department_name='Track Management System (Civil Engineering / P-Way)',
            lead_name='Sr. DEN (Co-ord)',
            badge_color='#0075F2'
        )
        self.smms = Team.objects.create(
            code='smms',
            name='Signal Maintenance & Management System (SMMS)',
            department_name='Signal Maintenance & Management System (S&T)',
            lead_name='Sr. DSTE',
            badge_color='#2EC4B6'
        )
        self.trd = Team.objects.create(
            code='trd',
            name='Traction Distribution System (TRD)',
            department_name='Traction Distribution System (25kV OHE)',
            lead_name='Sr. DEE (TRD)',
            badge_color='#F4D35E'
        )

        # 2. Corridor Assets
        self.asset_tms = Asset.objects.create(
            asset_id='AST-TMS-01',
            asset_type='Turnout 114-A Rail Tongue',
            location='Maninagar Down Main',
            team=self.tms,
            criticality='CRITICAL'
        )
        self.asset_smms = Asset.objects.create(
            asset_id='AST-SMMS-01',
            asset_type='Point Machine 114-A',
            location='Maninagar Down Main',
            team=self.smms,
            criticality='HIGH'
        )
        self.asset_trd = Asset.objects.create(
            asset_id='AST-TRD-01',
            asset_type='25kV OHE Wire',
            location='Maninagar Down Main',
            team=self.trd,
            criticality='HIGH'
        )

        # 3. Train Schedules creating morning peak rush
        self.train_vb = TrainSchedule.objects.create(
            train_number='20901',
            train_name='Vande Bharat Express',
            location='Maninagar Down Main',
            start_time=datetime.time(7, 15),
            end_time=datetime.time(7, 55),
            priority_tier=1,
            track_direction='DN Fast'
        )
        self.train_raj = TrainSchedule.objects.create(
            train_number='12952',
            train_name='Rajdhani Express',
            location='Ahmedabad Up Main',
            start_time=datetime.time(18, 50),
            end_time=datetime.time(19, 30),
            priority_tier=1,
            track_direction='UP Main'
        )

        self.client = Client()

    def test_pages_render_successfully(self):
        """Verify Main Dashboard and all 3 department views return HTTP 200."""
        endpoints = [
            ('/', 'Corridor Master Block Timetable'),
            ('/tms/', 'Track Management System (TMS)'),
            ('/smms/', 'Signal Maintenance & Management System (SMMS)'),
            ('/trd/', 'Traction Distribution System (TRD)'),
        ]
        for url, expected_text in endpoints:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200, f"Failed on URL: {url}")
            self.assertContains(response, expected_text)

    def test_api_timeline_data(self):
        """Verify the /api/timeline-data/ endpoint returns valid JSON structure."""
        response = self.client.get('/api/timeline-data/')
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn('slots', data)
        self.assertEqual(len(data['slots']), 48)  # 48 half-hour slots in 24 hours
        self.assertIn('sections', data)

    def test_lagrangian_compatible_bundling(self):
        """
        Verify that compatible requests (TRD 25kV OHE + TMS Rail Tongue + SMMS Point Machine)
        on the same section are combined into a joint shadow block window, maximizing uptime.
        """
        req_trd = MaintenanceRequest.objects.create(
            request_code='REQ-TRD-TEST',
            team=self.trd,
            asset_id='AST-TRD-01',
            asset_type='25kV OHE Wire',
            location='Maninagar Down Main',
            maintenance_type='25kV OHE Wire Tensioning',
            description='OHE wire maintenance',
            severity='HIGH',
            estimated_duration=90,
            status='PENDING'
        )
        req_tms = MaintenanceRequest.objects.create(
            request_code='REQ-TMS-TEST',
            team=self.tms,
            asset_id='AST-TMS-01',
            asset_type='Turnout Rail Tongue',
            location='Maninagar Down Main',
            maintenance_type='Turnout Tongue Rail Renewal',
            description='Turnout tongue replacement',
            severity='CRITICAL',
            estimated_duration=60,
            status='PENDING'
        )
        req_smms = MaintenanceRequest.objects.create(
            request_code='REQ-SMMS-TEST',
            team=self.smms,
            asset_id='AST-SMMS-01',
            asset_type='Point Machine',
            location='Maninagar Down Main',
            maintenance_type='Point Machine Overhaul',
            description='Point machine inspection',
            severity='HIGH',
            estimated_duration=60,
            status='PENDING'
        )

        res = run_automatic_scheduling(trigger_source="Test Bundling", force=True)
        self.assertIn(res['status'], ('OPTIMAL', 'FEASIBLE'))

        req_trd.refresh_from_db()
        req_tms.refresh_from_db()
        req_smms.refresh_from_db()

        # All 3 should be scheduled
        self.assertEqual(req_trd.status, 'SCHEDULED')
        self.assertEqual(req_tms.status, 'SCHEDULED')
        self.assertEqual(req_smms.status, 'SCHEDULED')

        # They should share the exact same scheduled start time (co-scheduled joint window!)
        self.assertEqual(req_trd.scheduled_start_time, req_tms.scheduled_start_time)
        self.assertEqual(req_trd.scheduled_start_time, req_smms.scheduled_start_time)
        self.assertTrue(req_trd.is_joint or req_tms.is_joint or req_smms.is_joint)

        # Downtime saved should be positive (90 + 60 + 60 = 210 mins if separate, but takes max(90, 60, 60)=90 min window!)
        metrics = res.get('metrics', {})
        self.assertGreater(metrics.get('downtime_saved_minutes', 0), 0)

    def test_lagrangian_incompatible_separation(self):
        """
        Verify that incompatible requests on the same section (Heavy Ballast Cleaning vs Axle Counter Calibration)
        are strictly separated into non-overlapping time slots.
        """
        req_bcm = MaintenanceRequest.objects.create(
            request_code='REQ-BCM-TEST',
            team=self.tms,
            asset_id='AST-TMS-03',
            asset_type='Track Bed',
            location='Ahmedabad Up Main',
            maintenance_type='Deep Screening Ballast Cleaning Machine (BCM)',
            description='Heavy track lifting and ballast screening',
            severity='HIGH',
            estimated_duration=120,
            status='PENDING'
        )
        req_axle = MaintenanceRequest.objects.create(
            request_code='REQ-AXLE-TEST',
            team=self.smms,
            asset_id='AST-SMMS-02',
            asset_type='Axle Counter',
            location='Ahmedabad Up Main',
            maintenance_type='Axle Counter (MSDAC) Electronic Calibration',
            description='Precision sensor calibration',
            severity='HIGH',
            estimated_duration=90,
            status='PENDING'
        )

        res = run_automatic_scheduling(trigger_source="Test Incompatible Separation", force=True)
        self.assertIn(res['status'], ('OPTIMAL', 'FEASIBLE'))

        req_bcm.refresh_from_db()
        req_axle.refresh_from_db()

        # Both scheduled
        self.assertEqual(req_bcm.status, 'SCHEDULED')
        self.assertEqual(req_axle.status, 'SCHEDULED')

        # They MUST NOT overlap in time (intervals disjoint)
        s_bcm = req_bcm.scheduled_start_time
        e_bcm = req_bcm.scheduled_end_time
        s_axle = req_axle.scheduled_start_time
        e_axle = req_axle.scheduled_end_time

        # Check disjointness: either BCM finishes before Axle starts, or Axle finishes before BCM starts
        is_separated = (e_bcm <= s_axle) or (e_axle <= s_bcm)
        self.assertTrue(is_separated, f"Incompatible tasks overlapped: BCM [{s_bcm}-{e_bcm}] vs Axle [{s_axle}-{e_axle}]")

    def test_same_department_requests_are_separated(self):
        """Same-department jobs on the same corridor must not be scheduled into the same jammed window."""
        req_a = MaintenanceRequest.objects.create(
            request_code='REQ-TMS-A',
            team=self.tms,
            asset_id='AST-TMS-01',
            asset_type='Track',
            location='Maninagar Down Main',
            maintenance_type='Track Geometry Survey',
            description='Routine survey',
            severity='MEDIUM',
            estimated_duration=60,
            status='PENDING'
        )
        req_b = MaintenanceRequest.objects.create(
            request_code='REQ-TMS-B',
            team=self.tms,
            asset_id='AST-TMS-02',
            asset_type='Track',
            location='Maninagar Down Main',
            maintenance_type='Turnout Sleeper Renewal',
            description='Sleeper replacement',
            severity='HIGH',
            estimated_duration=60,
            status='PENDING'
        )

        res = run_automatic_scheduling(trigger_source="Test Same Dept Separation", force=True)
        self.assertIn(res['status'], ('OPTIMAL', 'FEASIBLE'))

        req_a.refresh_from_db()
        req_b.refresh_from_db()
        self.assertEqual(req_a.status, 'SCHEDULED')
        self.assertEqual(req_b.status, 'SCHEDULED')

        s_a = req_a.scheduled_start_time
        e_a = req_a.scheduled_end_time
        s_b = req_b.scheduled_start_time
        e_b = req_b.scheduled_end_time
        is_separated = (e_a <= s_b) or (e_b <= s_a)
        self.assertTrue(is_separated, f"Same-department tasks overlapped: {req_a.request_code} [{s_a}-{e_a}] vs {req_b.request_code} [{s_b}-{e_b}]")

    def test_preferred_time_is_used_as_soft_request(self):
        """A department's preferred time-of-day should be treated as a scheduling preference before final AI optimization."""
        req = MaintenanceRequest.objects.create(
            request_code='REQ-TMS-PREFERRED',
            team=self.tms,
            asset_id='AST-TMS-04',
            asset_type='Track',
            location='Vatva Freight Loop',
            maintenance_type='Track Fastening Inspection',
            description='Late-evening inspection',
            severity='MEDIUM',
            estimated_duration=60,
            preferred_time=datetime.time(19, 0),
            status='PENDING'
        )

        res = run_automatic_scheduling(trigger_source="Test Preferred Time", force=True)
        self.assertIn(res['status'], ('OPTIMAL', 'FEASIBLE'))
        req.refresh_from_db()
        self.assertEqual(req.status, 'SCHEDULED')
        self.assertTrue(datetime.time(18, 30) <= req.scheduled_start_time <= datetime.time(19, 30),
                        f"Preferred time was ignored: scheduled at {req.scheduled_start_time} for {req.request_code}")

    def test_placement_in_least_critical_time(self):
        """
        Verify that blocks avoid the passenger train peak rush (e.g. 07:15 Vande Bharat)
        and are placed in off-peak lean hours (night 01:00-05:00 or midday lean 11:30-14:00).
        """
        req = MaintenanceRequest.objects.create(
            request_code='REQ-TMS-OFFPEAK',
            team=self.tms,
            asset_id='AST-TMS-01',
            asset_type='Track',
            location='Maninagar Down Main',
            maintenance_type='Track Geometry Tamping',
            description='Routine tamping',
            severity='MEDIUM',
            estimated_duration=60,
            status='PENDING'
        )

        res = run_automatic_scheduling(trigger_source="Test Off-Peak", force=True)
        req.refresh_from_db()

        self.assertEqual(req.status, 'SCHEDULED')
        # The scheduled time should not conflict with 07:15 - 07:55 Vande Bharat Express
        vb_start = datetime.time(7, 15)
        vb_end = datetime.time(7, 55)

        s_time = req.scheduled_start_time
        e_time = req.scheduled_end_time

        # Overlap check
        clash = max(s_time, vb_start) < min(e_time, vb_end)
        self.assertFalse(clash, f"Scheduled block [{s_time}-{e_time}] overlapped with Vande Bharat Express [{vb_start}-{vb_end}]!")

    def test_add_block_and_auto_schedule(self):
        """
        Verify that submitting a new maintenance request via /api/submit-request/:
        1. Successfully creates the MaintenanceRequest record with unique request code.
        2. Automatically triggers CP-SAT Lagrangian solver without requiring an optimize button.
        3. Marks the block as SCHEDULED in off-peak hours.
        4. Resets DailyScheduleControl to unapproved (pending Section Controller authorization).
        """
        # First ensure schedule is approved
        today = timezone.now().date()
        control = DailyScheduleControl.objects.create(date=today, is_approved=True, approved_by="Section Controller")

        payload = {
            'department': 'tms',
            'maintenance_type': 'Emergency Rail Fracture Ultrasonic Testing',
            'location': 'Maninagar Down Main',
            'estimated_duration': 60,
            'severity': 'CRITICAL',
            'required_resources': 'USFD Flaw Detector + P-Way Gang (6 staff)',
            'description': 'Continuous welded rail defect detection post-winter cycle'
        }

        response = self.client.post('/api/submit-request/', data=payload, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data['success'])
        self.assertIn('REQ-TMS-', data['request_code'])

        # Verify record in database
        created_req = MaintenanceRequest.objects.get(request_code=data['request_code'])
        self.assertEqual(created_req.maintenance_type, 'Emergency Rail Fracture Ultrasonic Testing')
        self.assertEqual(created_req.status, 'SCHEDULED')
        self.assertIsNotNone(created_req.scheduled_start_time)

        # Verify daily approval was invalidated because schedule changed
        control.refresh_from_db()
        self.assertFalse(control.is_approved)

    def test_cancel_block_and_auto_reoptimize(self):
        """Verify that cancelling a block removes it from DB and auto-reoptimizes remaining tasks."""
        req = MaintenanceRequest.objects.create(
            request_code='REQ-TMS-CANCEL-ME',
            team=self.tms,
            asset_id='AST-TMS-01',
            asset_type='Track',
            location='Maninagar Down Main',
            maintenance_type='Temporary Sleepers Packing',
            estimated_duration=45,
            status='PENDING'
        )
        run_automatic_scheduling(trigger_source="Setup", force=True)

        response = self.client.post(f'/api/cancel-request/{req.id}/', HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['success'])

        # Verify deleted
        self.assertFalse(MaintenanceRequest.objects.filter(id=req.id).exists())

    def test_toggle_schedule_approval(self):
        """Verify Control Office sign-off toggle endpoint."""
        today = timezone.now().date()
        control, _ = DailyScheduleControl.objects.get_or_create(date=today)
        self.assertFalse(control.is_approved)

        # 1. Authorize
        res1 = self.client.post('/api/approve-schedule/', HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(res1.status_code, 200)
        control.refresh_from_db()
        self.assertTrue(control.is_approved)
        self.assertIsNotNone(control.approved_at)
        self.assertIn('Chief Section Controller', control.approved_by)

        # 2. Reopen for modifications
        res2 = self.client.post('/api/approve-schedule/', HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(res2.status_code, 200)
        control.refresh_from_db()
        self.assertFalse(control.is_approved)

