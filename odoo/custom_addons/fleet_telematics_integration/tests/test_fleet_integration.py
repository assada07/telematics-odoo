# ==============================================================================
# tests/test_fleet_integration.py
# Odoo TestCase รวม UC-01, UC-02, UC-04 ในไฟล์เดียว
#
# วิธีรัน:
#   odoo-bin -c odoo.conf -d <db> --test-enable --stop-after-init \
#            --test-tags /fleet_telematics_integration
# ==============================================================================

from unittest.mock import patch, MagicMock
from datetime import datetime, timezone, timedelta

from odoo.tests.common import TransactionCase
from odoo.exceptions import ValidationError


# ══════════════════════════════════════════════════════════════════════════════
# Shared Setup — ข้อมูลพื้นฐานที่ใช้ร่วมกันทุก UC
# ══════════════════════════════════════════════════════════════════════════════

class FleetTelematicsBase(TransactionCase):
    """Base class: สร้าง brand/model/vehicle/driver ที่ใช้ร่วมกัน"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        cls.brand  = cls.env['fleet.vehicle.model.brand'].create(
            {'name': 'TEST_BRAND'})
        cls.fmodel = cls.env['fleet.vehicle.model'].create(
            {'name': 'TEST_MODEL', 'brand_id': cls.brand.id})
        cls.partner = cls.env['res.partner'].create({'name': 'Test Driver'})
        cls.employee = cls.env['hr.employee'].create({'name': 'Test Employee'})

        cls.v1 = cls.env['fleet.vehicle'].create({
            'model_id':             cls.fmodel.id,
            'license_plate':        'BASE-001',
            'telematics_device_id': 'KTC-BASE-01',
        })

        ICP = cls.env['ir.config_parameter'].sudo()
        ICP.set_param('fleet_telematics.mtd_api_url', 'http://test-backend:8001')
        ICP.set_param('fleet_telematics.mtd_api_key', 'TEST-KEY')

    def _make_vehicle(self, plate, device=None):
        vals = {
            'model_id':      self.fmodel.id,
            'license_plate': plate,
            'driver_id':     self.partner.id,
        }
        if device is not None:
            vals['telematics_device_id'] = device
        return self.env['fleet.vehicle'].create(vals)

    def _make_scoring(self, name='Cfg', active=True, **kw):
        # deactivate ที่มีอยู่ก่อน (ถ้า active=True)
        if active:
            self.env['fleet.telematics.scoring.config'].search(
                [('active', '=', True)]).write({'active': False})
        vals = dict(
            name=name, active=active, effective_date='2025-01-01',
            score_base=100.0, max_deduct_per_trip=50.0,
            harsh_brake_deduct=5.0, harsh_accel_deduct=3.0,
            harsh_corner_deduct=3.0, speeding_deduct=10.0,
            idling_deduct=2.0, bump_deduct=4.0,
            harsh_brake_g=0.40, harsh_accel_g=0.40, harsh_corner_g=0.40,
            speeding_kmh_over=20.0, idle_min_threshold=5.0,
            tier_a_min_score=90.0, tier_a_bonus_pct=10.0,
            tier_b_min_score=75.0, tier_b_bonus_pct=5.0,
            tier_c_min_score=60.0, tier_c_bonus_pct=0.0,
        )
        vals.update(kw)
        return self.env['fleet.telematics.scoring.config'].create(vals)

    def _make_trip_dict(self, trip_id, device='KTC-BASE-01', **kw):
        data = {
            'trip_id':           trip_id,
            'device_id':         device,
            'driver_name':       'Test Employee',
            'start_time':        '2025-06-10T08:00:00',
            'end_time':          '2025-06-10T09:00:00',
            'distance_km':       50.0,
            'avg_speed':         60.0,
            'max_speed':         90.0,
            'idle_min':           5.0,
            'fuel_used_est':      4.5,
            'driver_score':      85.0,
            'harsh_brake_count':  1,
            'harsh_accel_count':  0,
            'harsh_corner_count': 2,
            'speeding_count':     1,
            'gps_track_json':    '[]',
        }
        data.update(kw)
        return data

    def _mock_api(self, trips):
        r = MagicMock()
        r.json.return_value = {'trips': trips}
        r.raise_for_status.return_value = None
        return r


# ══════════════════════════════════════════════════════════════════════════════
# UC-01 — สร้าง / จัดการรถและ Device  (fleet_vehicle_ext.py)
# ══════════════════════════════════════════════════════════════════════════════

class TestUC01VehicleDevice(FleetTelematicsBase):
    """
    ครอบคลุม:
      - บันทึกรถ + Device ID สำเร็จ
      - ดักจับ Device ID ซ้ำ → ValidationError
      - ดักจับทะเบียนซ้ำ → ValidationError
      - เปลี่ยน Device ID → previous_device_id บันทึกอัตโนมัติ
      - Device ว่าง/None → ไม่นับซ้ำ
      - เคลียร์ Device → รถคันอื่นใช้ได้
      - write Device เดิม → ไม่ error
      - ตรวจ default fields
    """

    def test_01_create_vehicle_with_device_success(self):
        """สร้างรถพร้อม Device ID — บันทึกได้ ค่าตรง"""
        v = self._make_vehicle('กข-T01', 'KTC-T01')
        self.assertTrue(v.id)
        self.assertEqual(v.telematics_device_id, 'KTC-T01')
        self.assertEqual(v.online_status, 'unknown')
        self.assertEqual(v.sync_status,   'idle')
        self.assertFalse(v.ignition)

    def test_02_duplicate_device_id_raises(self):
        """Device ID ซ้ำ → ValidationError พร้อมระบุ Device ID"""
        self._make_vehicle('กข-T02', 'KTC-DUP')
        with self.assertRaises(ValidationError) as ctx:
            self._make_vehicle('กข-T03', 'KTC-DUP')
        self.assertIn('KTC-DUP', str(ctx.exception))

    def test_03_duplicate_license_plate_raises(self):
        """ทะเบียนรถซ้ำ → ValidationError พร้อมระบุทะเบียน"""
        self._make_vehicle('กข-SAME', 'KTC-P01')
        with self.assertRaises(ValidationError) as ctx:
            self._make_vehicle('กข-SAME', 'KTC-P02')
        self.assertIn('กข-SAME', str(ctx.exception))

    def test_04_change_device_saves_previous(self):
        """เปลี่ยน Device ID → previous_device_id เก็บค่าเก่าอัตโนมัติ"""
        v = self._make_vehicle('กข-T04', 'KTC-OLD')
        v.write({'telematics_device_id': 'KTC-NEW'})
        self.assertEqual(v.telematics_device_id, 'KTC-NEW')
        self.assertEqual(v.previous_device_id,   'KTC-OLD')

    def test_05_empty_device_allowed_multiple_vehicles(self):
        """รถหลายคันที่ไม่มี Device → บันทึกได้ทั้งหมด ไม่ถือว่าซ้ำ"""
        v1 = self._make_vehicle('กข-T05A')
        v2 = self._make_vehicle('กข-T05B')
        self.assertTrue(v1.id)
        self.assertTrue(v2.id)
        self.assertFalse(v1.telematics_device_id)

    def test_06_clear_device_allows_reassign(self):
        """เคลียร์ Device ออกจากรถคันเดิม → รถคันใหม่ใช้ Device นั้นได้"""
        v1 = self._make_vehicle('กข-T06A', 'KTC-XFER')
        v1.write({'telematics_device_id': False})
        v2 = self._make_vehicle('กข-T06B', 'KTC-XFER')
        self.assertEqual(v2.telematics_device_id, 'KTC-XFER')

    def test_07_write_same_device_no_error(self):
        """write Device เดิมของรถตัวเอง → ไม่ถือว่าซ้ำ ไม่ error"""
        v = self._make_vehicle('กข-T07', 'KTC-STABLE')
        try:
            v.write({'telematics_device_id': 'KTC-STABLE'})
        except ValidationError:
            self.fail("write Device ID เดิมต้องไม่ raise ValidationError")

    def test_08_default_registered_status_fields(self):
        """ค่า default ของ online_status / sync_status / ignition ถูกต้อง"""
        v = self._make_vehicle('กข-T08', 'KTC-T08')
        self.assertEqual(v.online_status, 'unknown')
        self.assertEqual(v.sync_status,   'idle')
        self.assertFalse(v.ignition)
        self.assertEqual(v.current_speed, 0.0)


# ══════════════════════════════════════════════════════════════════════════════
# UC-02 — ตั้งค่า Scoring Config  (telematics_scoring.py)
# ══════════════════════════════════════════════════════════════════════════════

class TestUC02ScoringConfig(FleetTelematicsBase):
    """
    ครอบคลุม:
      - บันทึก ScoringConfig สำเร็จ
      - Active ได้เพียง 1 config
      - Tier order A > B > C > 0
      - Boundary: deduct ติดลบ, score_base = 0
      - Boundary: G-force / threshold = 0
      - Boundary: max_deduct > score_base
      - _build_config_payload() ครบทุก key
      - Deactivate แล้ว Active ใหม่ได้
    """

    def test_01_create_scoring_config_success(self):
        """สร้าง ScoringConfig สำเร็จ — ค่าทุก field บันทึกถูกต้อง"""
        cfg = self._make_scoring('UC02-01')
        self.assertTrue(cfg.id)
        self.assertEqual(cfg.score_base,         100.0)
        self.assertEqual(cfg.harsh_brake_deduct,   5.0)
        self.assertEqual(cfg.tier_a_min_score,    90.0)
        self.assertTrue(cfg.active)

    def test_02_only_one_active_config_allowed(self):
        """Active ScoringConfig ได้เพียง 1 รายการ → รายการที่ 2 ต้อง raise"""
        self._make_scoring('UC02-02A', active=True)
        with self.assertRaises(ValidationError) as ctx:
            # ไม่ผ่าน _make_scoring เพราะมัน deactivate ให้อัตโนมัติ
            self.env['fleet.telematics.scoring.config'].create({
                'name': 'UC02-02B', 'active': True,
                'effective_date': '2025-01-01',
                'score_base': 100.0, 'max_deduct_per_trip': 50.0,
                'harsh_brake_deduct': 5.0, 'harsh_accel_deduct': 3.0,
                'harsh_corner_deduct': 3.0, 'speeding_deduct': 10.0,
                'idling_deduct': 2.0, 'bump_deduct': 4.0,
                'harsh_brake_g': 0.40, 'harsh_accel_g': 0.40, 'harsh_corner_g': 0.40,
                'speeding_kmh_over': 20.0, 'idle_min_threshold': 5.0,
                'tier_a_min_score': 90.0, 'tier_a_bonus_pct': 10.0,
                'tier_b_min_score': 75.0, 'tier_b_bonus_pct': 5.0,
                'tier_c_min_score': 60.0, 'tier_c_bonus_pct': 0.0,
            })
        self.assertIn('Active', str(ctx.exception))

    def test_03a_tier_order_a_less_than_b_raises(self):
        """Tier A < Tier B → ValidationError"""
        with self.assertRaises(ValidationError):
            self._make_scoring(active=False,
                               tier_a_min_score=70.0,
                               tier_b_min_score=80.0,
                               tier_c_min_score=60.0)

    def test_03b_tier_c_equal_zero_raises(self):
        """Tier C = 0 → ValidationError (ต้อง > 0)"""
        with self.assertRaises(ValidationError):
            self._make_scoring(active=False,
                               tier_a_min_score=90.0,
                               tier_b_min_score=75.0,
                               tier_c_min_score=0.0)

    def test_03c_tier_b_equal_c_raises(self):
        """Tier B == Tier C → ValidationError"""
        with self.assertRaises(ValidationError):
            self._make_scoring(active=False,
                               tier_a_min_score=90.0,
                               tier_b_min_score=60.0,
                               tier_c_min_score=60.0)

    def test_04_negative_deduct_raises(self):
        """ค่าหักคะแนนติดลบ → ValidationError (_check_positive_deducts)"""
        with self.assertRaises(ValidationError):
            self._make_scoring(active=False, harsh_brake_deduct=-5.0)

    def test_05_zero_score_base_raises(self):
        """score_base = 0 → ValidationError"""
        with self.assertRaises(ValidationError):
            self._make_scoring(active=False, score_base=0.0)

    def test_06_zero_g_threshold_raises(self):
        """G-force threshold = 0 → ValidationError (_check_positive_thresholds)"""
        with self.assertRaises(ValidationError):
            self._make_scoring(active=False, harsh_brake_g=0.0)

    def test_07_max_deduct_exceeds_base_raises(self):
        """max_deduct_per_trip > score_base → ValidationError"""
        with self.assertRaises(ValidationError):
            self._make_scoring(active=False,
                               score_base=100.0,
                               max_deduct_per_trip=150.0)

    def test_08_build_config_payload_has_all_keys(self):
        """_build_config_payload() ต้องครบทุก key ที่ Backend ต้องการ"""
        cfg     = self._make_scoring('UC02-08')
        payload = cfg._build_config_payload()
        required = [
            'score_base', 'max_deduct_per_trip',
            'harsh_brake_deduct', 'harsh_accel_deduct', 'harsh_corner_deduct',
            'speeding_deduct', 'idling_deduct', 'bump_deduct',
            'harsh_brake_g', 'harsh_accel_g', 'harsh_corner_g',
            'speeding_kmh_over', 'idle_min_threshold',
            'tier_a_min_score', 'tier_a_bonus_pct',
            'tier_b_min_score', 'tier_b_bonus_pct',
            'tier_c_min_score', 'tier_c_bonus_pct',
        ]
        for key in required:
            self.assertIn(key, payload, f"payload ต้องมี key '{key}'")
        self.assertEqual(payload['score_base'], 100.0)

    def test_09_deactivate_then_activate_new(self):
        """Deactivate config เดิม แล้ว active ใหม่ → ต้องสำเร็จ"""
        c1 = self._make_scoring('UC02-09A', active=True)
        c1.write({'active': False})
        c2 = self._make_scoring('UC02-09B', active=True)
        self.assertTrue(c2.active)
        self.assertFalse(c1.active)


# ══════════════════════════════════════════════════════════════════════════════
# UC-04 — GPS Poll + Dedup + Batch  (telematics_log.py section [I]–[N])
# ══════════════════════════════════════════════════════════════════════════════

class TestUC04TripSync(FleetTelematicsBase):
    """
    ครอบคลุม:
      - _get_poll_window() Cold Start และ Warm Start
      - _filter_new_trips() กรอง existing IDs ออก (Dedup ชั้น 2)
      - _build_trip_vals() แปลง dict → vals ถูกต้อง
      - _build_trip_vals() device ไม่มีในระบบ → คืน {}
      - _cron_sync_trips() สร้าง trip ใหม่
      - _cron_sync_trips() ไม่ duplicate เมื่อรัน 2 รอบ (Dedup ชั้น 1+2)
      - _cron_sync_trips() write existing trip (ไม่ create ใหม่)
      - _cron_sync_trips() บันทึก last_poll_ts หลัง sync
      - _sql_constraints UNIQUE(external_trip_id) มีอยู่ (Dedup ชั้น 3)
      - _cron_sync_trips() เมื่อไม่มี API URL → ไม่ raise
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        ICP = cls.env['ir.config_parameter'].sudo()
        ICP.set_param('fleet_telematics.trip_last_poll_ts', '')

    def setUp(self):
        super().setUp()
        # รีเซ็ต last_poll_ts ก่อนแต่ละ test
        self.env['ir.config_parameter'].sudo().set_param(
            'fleet_telematics.trip_last_poll_ts', '')

    def test_01_get_poll_window_cold_start(self):
        """Cold Start (ไม่มี last_poll_ts) → since ≈ now-5min, until ≈ now"""
        Log      = self.env['fleet.telematics.log']
        before   = datetime.now(timezone.utc)
        since, until = Log._get_poll_window()
        after    = datetime.now(timezone.utc)

        # until อยู่ระหว่าง before และ after
        self.assertGreaterEqual(until, before)
        self.assertLessEqual(until, after + timedelta(seconds=1))

        # since ≈ until - 5 นาที (tolerance 10 วินาที)
        delta = abs((until - since).total_seconds() - 300)
        self.assertLess(delta, 10, f"since ต้องห่าง until ~5 นาที (delta={delta}s)")

    def test_02_get_poll_window_warm_start(self):
        """Warm Start → since ตรงกับ last_poll_ts ที่บันทึกไว้"""
        ICP      = self.env['ir.config_parameter'].sudo()
        fixed_ts = datetime(2025, 6, 10, 8, 0, 0, tzinfo=timezone.utc)
        ICP.set_param('fleet_telematics.trip_last_poll_ts', fixed_ts.isoformat())

        Log  = self.env['fleet.telematics.log']
        since, until = Log._get_poll_window()

        self.assertEqual(
            since.replace(tzinfo=timezone.utc), fixed_ts,
            "since ต้องตรงกับ last_poll_ts ที่บันทึกไว้"
        )
        self.assertGreater(until, since)

    def test_03_filter_new_trips_dedup(self):
        """_filter_new_trips() กรอง trip ที่มีใน DB แล้ว — คืนเฉพาะรายการใหม่"""
        Log = self.env['fleet.telematics.log']

        # สร้าง trip เดิมใน DB ก่อน
        Log.create({
            'external_trip_id': 'FILTER-OLD',
            'vehicle_id':  self.v1.id,
            'driver_id':   self.employee.id,
            'trip_start':  '2025-06-10 08:00:00',
            'state':       'synced',
        })

        trips = [
            self._make_trip_dict('FILTER-OLD'),   # มีอยู่แล้ว
            self._make_trip_dict('FILTER-NEW-1'), # ใหม่
            self._make_trip_dict('FILTER-NEW-2'), # ใหม่
        ]
        new_trips, existing_map = Log._filter_new_trips(trips)

        self.assertEqual(len(new_trips), 2)
        self.assertIn('FILTER-OLD', existing_map)
        new_ids = [t['trip_id'] for t in new_trips]
        self.assertNotIn('FILTER-OLD',   new_ids)
        self.assertIn('FILTER-NEW-1', new_ids)
        self.assertIn('FILTER-NEW-2', new_ids)

    def test_04_build_trip_vals_ok(self):
        """_build_trip_vals() แปลง dict → vals ครบถูกต้อง"""
        Log  = self.env['fleet.telematics.log']
        vals = Log._build_trip_vals(self._make_trip_dict('VALS-001'))

        self.assertEqual(vals['external_trip_id'], 'VALS-001')
        self.assertEqual(vals['vehicle_id'],        self.v1.id)
        self.assertEqual(vals['distance_km'],       50.0)
        self.assertEqual(vals['state'],             'synced')

    def test_05_build_trip_vals_unknown_device_empty(self):
        """_build_trip_vals() device ไม่มีในระบบ → คืน {} (caller จะ skip)"""
        Log  = self.env['fleet.telematics.log']
        vals = Log._build_trip_vals(
            self._make_trip_dict('VALS-X', device='DEVICE-NOTEXIST'))
        self.assertEqual(vals, {})

    def test_06_cron_creates_new_trips(self):
        """_cron_sync_trips() สร้าง trip ใหม่จาก API response"""
        Log   = self.env['fleet.telematics.log']
        trips = [
            self._make_trip_dict('CRON-C01'),
            self._make_trip_dict('CRON-C02'),
        ]
        before = Log.search_count(
            [('external_trip_id', 'in', ['CRON-C01', 'CRON-C02'])])

        with patch('requests.get', return_value=self._mock_api(trips)):
            with patch('time.sleep'):
                Log._cron_sync_trips()

        after = Log.search_count(
            [('external_trip_id', 'in', ['CRON-C01', 'CRON-C02'])])
        self.assertEqual(after - before, 2, "ต้องสร้าง 2 trips ใหม่")

    def test_07_cron_no_duplicate_on_second_run(self):
        """Dedup ชั้น 1+2: รัน Cron 2 รอบ → บันทึกแค่ครั้งเดียว"""
        Log   = self.env['fleet.telematics.log']
        trips = [self._make_trip_dict('CRON-D01')]

        with patch('requests.get', return_value=self._mock_api(trips)):
            with patch('time.sleep'):
                Log._cron_sync_trips()   # รอบ 1
                Log._cron_sync_trips()   # รอบ 2 (since ≥ until รอบ 1 → API คืน [])

        count = Log.search_count([('external_trip_id', '=', 'CRON-D01')])
        self.assertEqual(count, 1, "ต้องมีแค่ 1 record ไม่ซ้ำ")

    def test_08_cron_updates_existing_trip(self):
        """_cron_sync_trips() trip ที่มีอยู่แล้ว → write() แทน create()"""
        Log = self.env['fleet.telematics.log']
        Log.create({
            'external_trip_id': 'CRON-U01',
            'vehicle_id': self.v1.id,
            'driver_id':  self.employee.id,
            'trip_start': '2025-06-10 08:00:00',
            'state':      'synced',
            'distance_km': 10.0,
        })
        trip = self._make_trip_dict('CRON-U01', distance_km=99.0)

        with patch('requests.get', return_value=self._mock_api([trip])):
            with patch('time.sleep'):
                Log._cron_sync_trips()

        rec = Log.search([('external_trip_id', '=', 'CRON-U01')], limit=1)
        self.assertEqual(len(rec), 1,    "ต้องไม่สร้างซ้ำ")
        self.assertAlmostEqual(rec.distance_km, 99.0,
                               msg="ต้อง update distance_km เป็นค่าใหม่")

    def test_09_cron_saves_last_poll_ts(self):
        """หลัง _cron_sync_trips() ต้องบันทึก last_poll_ts ใน ir.config_parameter"""
        Log = self.env['fleet.telematics.log']
        ICP = self.env['ir.config_parameter'].sudo()

        with patch('requests.get', return_value=self._mock_api([])):
            with patch('time.sleep'):
                Log._cron_sync_trips()

        ts = ICP.get_param('fleet_telematics.trip_last_poll_ts', '')
        self.assertTrue(ts, "ต้องบันทึก last_poll_ts หลัง Cron ทำงาน")
        # ตรวจว่า parse ได้ (format ถูก)
        try:
            datetime.fromisoformat(ts)
        except ValueError:
            self.fail(f"last_poll_ts ต้องเป็น ISO format (ได้: {ts})")

    def test_10_sql_unique_constraint_on_external_trip_id(self):
        """Dedup ชั้น 3: _sql_constraints ต้องมี UNIQUE(external_trip_id)"""
        Log         = self.env['fleet.telematics.log']
        constraints = [c[1].upper() for c in Log._sql_constraints]
        self.assertTrue(
            any('EXTERNAL_TRIP_ID' in c for c in constraints),
            "_sql_constraints ต้องมี UNIQUE บน external_trip_id"
        )

    def test_11_cron_no_api_url_skips_gracefully(self):
        """ไม่มี API URL → _cron_sync_trips() ต้องไม่ raise exception"""
        ICP = self.env['ir.config_parameter'].sudo()
        ICP.set_param('fleet_telematics.mtd_api_url', '')
        try:
            self.env['fleet.telematics.log']._cron_sync_trips()
        except Exception as e:
            self.fail(f"ต้องไม่ raise เมื่อไม่มี API URL: {e}")
        finally:
            ICP.set_param('fleet_telematics.mtd_api_url', 'http://test-backend:8001')
