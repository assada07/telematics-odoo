# ==============================================================================
# models/telematics_log.py
# โมเดลเก็บประวัติเที่ยววิ่ง (Trip Logs)
#
# UC-04 GPS บันทึกพิกัดและทริป — เพิ่ม logic ทั้งหมดไว้ในไฟล์นี้:
#   [I]  _cron_sync_trips()     — Cron Entry Point (ทุก 5 นาที)
#   [J]  _get_poll_window()     — คำนวณ since/until (Dedup ชั้น 1)
#   [K]  _fetch_trips()         — GET /api/v1/trips
#   [L]  _filter_new_trips()    — กรอง external_trip_id ที่มีใน DB แล้ว (Dedup ชั้น 2)
#   [M]  _save_trips_in_batches() — แบ่ง batch ส่งทีละ 5 วินาที
#   [N]  _build_trip_vals()     — แปลง dict → vals
# ==============================================================================
import logging
import time
import requests
from datetime import datetime, timezone, timedelta

from odoo import models, fields, api
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

# ── ค่าคงที่ปรับได้ ──────────────────────────────────────────────────────────
_PARAM_LAST_POLL = 'fleet_telematics.trip_last_poll_ts'   # ir.config_parameter key
_POLL_WINDOW_MIN = 5      # ดึงย้อนหลัง 5 นาที (Cold Start)
_BATCH_SIZE      = 10     # บันทึกทีละกี่ trip ต่อ batch
_BATCH_SEC       = 5      # รอ N วินาทีระหว่าง batch (UC-04 ข้อ 2)
_FETCH_LIMIT     = 50     # limit per API call


class TelematicsLog(models.Model):
    _name        = 'fleet.telematics.log'
    _description = 'Fleet Telematics Trip Log'
    _order       = 'trip_start desc'
    _rec_name    = 'display_name'

    # Dedup ชั้น 3: DB-level UNIQUE — กัน race condition กรณี Cron รันซ้อนกัน
    _sql_constraints = [
        ('external_trip_id_unique',
         'UNIQUE(external_trip_id)',
         'external_trip_id ต้องไม่ซ้ำกัน — ห้ามบันทึก Trip ซ้ำจาก Backend'),
    ]

    # ============================================================
    # [A] ข้อมูลหลักของ Trip — รถ คนขับ และอุปกรณ์ GPS
    # ============================================================
    vehicle_id = fields.Many2one(
        'fleet.vehicle', string='Vehicle',
        required=True, ondelete='restrict')
    driver_id = fields.Many2one(
        'hr.employee', string='Driver',
        required=True)
    telematics_device_id = fields.Char(
        string='Device ID',
        help='รหัสกล่องพ่วง GPS เช่น KTC-001')

    # ============================================================
    # [B] ช่วงเวลาของ Trip
    # ============================================================
    trip_start   = fields.Datetime(string='Trip Start', required=True)
    trip_end     = fields.Datetime(string='Trip End')
    duration_min = fields.Float(
        string='Duration (min)',
        compute='_compute_duration', store=True,
        digits=(10, 2))

    # ============================================================
    # [C] สถิติการเดินทาง
    # ============================================================
    distance_km   = fields.Float(string='Distance (km)',    digits=(10, 2))
    max_speed     = fields.Float(string='Max Speed (km/h)', digits=(10, 2))
    avg_speed     = fields.Float(string='Avg Speed (km/h)', digits=(10, 2))
    idle_min      = fields.Float(string='Idle Time (min)',  digits=(10, 2))
    fuel_used_est = fields.Float(string='Fuel Est. (L)',    digits=(10, 3))

    # ============================================================
    # [D] คะแนนและสถิติเหตุการณ์อันตราย
    # ============================================================
    driver_score       = fields.Float(string='Driver Score',        digits=(5, 2))
    harsh_brake_count  = fields.Integer(string='Harsh Brakes')
    harsh_accel_count  = fields.Integer(string='Harsh Accelerations')
    harsh_corner_count = fields.Integer(string='Harsh Cornering')
    speeding_count     = fields.Integer(string='Speeding Events')

    # ============================================================
    # [E] ข้อมูลเส้นทาง GPS และการอ้างอิงกับระบบภายนอก
    # ============================================================
    gps_track_json   = fields.Text(string='GPS Track (JSON)',
        help='เก็บ GPS track ทั้งสาย เช่น [{"lat": 18.7883, "lon": 98.9853, "ts": "..."}]')
    external_trip_id = fields.Char(
        string='External Trip ID',
        index=True,
        help='Trip ID จาก MTD Backend สำหรับ sync และ dedup')

    # ============================================================
    # [F] สถานะและความสัมพันธ์กับ Events
    # ============================================================
    state = fields.Selection([
        ('draft',     'Draft'),
        ('confirmed', 'Confirmed'),
        ('synced',    'Synced'),
        ('failed',    'Failed'),
    ], string='Sync Status', default='draft')

    event_ids = fields.One2many(
        'fleet.telematics.event', 'trip_id', string='Harsh Events')

    display_name = fields.Char(
        compute='_compute_display_name', store=True)

    # ============================================================
    # [G] Computed Fields — ชื่อแสดงผลและระยะเวลา
    # ============================================================
    @api.depends('vehicle_id', 'trip_start')
    def _compute_display_name(self):
        for rec in self:
            v = rec.vehicle_id.name or '?'
            t = rec.trip_start.strftime('%d/%m/%y %H:%M') if rec.trip_start else '-'
            rec.display_name = f'{v} — {t}'

    @api.depends('trip_start', 'trip_end')
    def _compute_duration(self):
        for rec in self:
            if rec.trip_start and rec.trip_end:
                rec.duration_min = (rec.trip_end - rec.trip_start).total_seconds() / 60
            else:
                rec.duration_min = 0.0

    # ============================================================
    # [H] Action เปลี่ยนสถานะ Trip
    # ============================================================
    def action_confirm(self):
        for rec in self:
            if rec.state == 'draft':
                rec.state = 'confirmed'

    # ============================================================
    # [I] Cron Entry Point — เรียกจาก telematics_cron.xml ทุก 5 นาที
    #
    # Flow:
    #   1. ดึง API credentials
    #   2. คำนวณ since/until window  ← Dedup ชั้น 1
    #   3. Fetch trips จาก Backend
    #   4. กรอง trip ที่มีใน DB แล้ว ← Dedup ชั้น 2
    #   5. บันทึกเป็น batch ทุก 5 วินาที
    #   6. อัปเดต last_poll_ts
    # ============================================================
    @api.model
    def _cron_sync_trips(self):
        ICP     = self.env['ir.config_parameter'].sudo()
        api_url = ICP.get_param('fleet_telematics.mtd_api_url', '').rstrip('/')
        api_key = ICP.get_param('fleet_telematics.mtd_api_key', '')

        if not api_url or not api_key:
            _logger.warning('fleet_telematics: ยังไม่ได้ตั้งค่า MTD API — ข้าม Cron')
            return

        since_dt, until_dt = self._get_poll_window()
        _logger.info(
            '_cron_sync_trips: window since=%s until=%s',
            since_dt.isoformat(), until_dt.isoformat(),
        )

        trips = self._fetch_trips(api_url, api_key, since_dt, until_dt)
        if not trips:
            ICP.set_param(_PARAM_LAST_POLL, until_dt.isoformat())
            return

        new_trips, existing_map = self._filter_new_trips(trips)

        _logger.info(
            '_cron_sync_trips: ได้ %d trips, ใหม่ %d, อัปเดต %d',
            len(trips), len(new_trips), len(existing_map),
        )

        # อัปเดต existing trips (write ทันที ไม่ต้อง batch)
        updated = 0
        for t in trips:
            ext_id = str(t.get('trip_id') or t.get('id') or '')
            if ext_id in existing_map:
                vals = self._build_trip_vals(t)
                if vals:
                    existing_map[ext_id].write(vals)
                    updated += 1

        # บันทึก new_trips แบ่ง batch ทุก 5 วินาที
        created = self._save_trips_in_batches(new_trips, api_url=api_url, api_key=api_key)

        # อัปเดต last_poll_ts และ config
        ICP.set_param(_PARAM_LAST_POLL, until_dt.isoformat())
        cfg = self.env['fleet.telematics.config'].search([], limit=1)
        if cfg:
            cfg.write({'last_sync_at': fields.Datetime.now(), 'last_error': False})

        _logger.info(
            '_cron_sync_trips: สร้าง %d รายการใหม่, อัปเดต %d รายการ',
            created, updated,
        )

    # ============================================================
    # [J] _get_poll_window — Dedup ชั้น 1: window เวลา since/until
    #
    # - since_dt = last_poll_ts จาก ir.config_parameter
    #              ถ้าไม่มี (Cold Start) → ใช้ now - 5 นาที
    # - until_dt = now()
    # - since_dt มาจาก until_dt ของรอบก่อน → ไม่มี gap ไม่ overlap
    # ============================================================
    @api.model
    def _get_poll_window(self):
        ICP      = self.env['ir.config_parameter'].sudo()
        last_ts  = ICP.get_param(_PARAM_LAST_POLL, '')
        until_dt = datetime.now(timezone.utc)

        if last_ts:
            try:
                since_dt = datetime.fromisoformat(last_ts)
                if since_dt.tzinfo is None:
                    since_dt = since_dt.replace(tzinfo=timezone.utc)
            except ValueError:
                since_dt = until_dt - timedelta(minutes=_POLL_WINDOW_MIN)
        else:
            # Cold Start: ครั้งแรก → ดึงย้อนหลัง 5 นาที
            since_dt = until_dt - timedelta(minutes=_POLL_WINDOW_MIN)

        return since_dt, until_dt

    # ============================================================
    # [K] _fetch_trips — GET /api/v1/trips/sync-batch
    # endpoint นี้ดึงเฉพาะ trip ที่ synced_to_odoo=false อยู่แล้ว
    # → ไม่ต้องส่ง since/status เพราะ Backend จัดการ dedup ให้เอง
    # param: limit เท่านั้น
    # ============================================================
    @api.model
    def _fetch_trips(self, api_url, api_key, since_dt, until_dt):
        url = f'{api_url}/api/v1/trips/sync-batch'
        _logger.info('_fetch_trips: GET %s limit=%s', url, _FETCH_LIMIT)

        try:
            resp = requests.get(
                url,
                headers={'APIKEY': api_key},
                params={'limit': _FETCH_LIMIT},
                timeout=30,
            )
            resp.raise_for_status()

            data = resp.json()
            # Response: {"total": N, "trips": [...]}
            return data.get('trips') or []

        except requests.RequestException as e:
            _logger.error('_fetch_trips error: %s', e)
            cfg = self.env['fleet.telematics.config'].search([], limit=1)
            if cfg:
                cfg.write({'last_error': str(e)})
            return []

    # ============================================================
    # [K2] _mark_trip_synced — PATCH /api/v1/trips/{id}/mark-synced
    # เรียกหลัง import trip แต่ละตัวสำเร็จ
    # บอก Backend ว่า trip นี้ sync ไป Odoo แล้ว ไม่ต้องส่งซ้ำ
    # ============================================================
    @api.model
    def _mark_trip_synced(self, api_url, api_key, trip_id):
        url = f'{api_url}/api/v1/trips/{trip_id}/mark-synced'
        try:
            resp = requests.patch(url, headers={'APIKEY': api_key}, timeout=10)
            if resp.status_code not in (200, 204):
                _logger.warning(
                    '_mark_trip_synced: trip_id=%s status=%s', trip_id, resp.status_code)
        except requests.RequestException as e:
            _logger.warning('_mark_trip_synced: trip_id=%s error=%s', trip_id, e)

    # ============================================================
    # [L] _filter_new_trips — Dedup ชั้น 2: batch lookup ใน DB
    #
    # - ดึง external_trip_id ทั้งหมดจาก incoming trips (1 query)
    # - แยกเป็น new_trips (ยังไม่มีใน DB) และ existing_map (มีแล้ว)
    # - ทำงานใน 1 query เดียว → ดีกว่า search() ทีละ trip
    # ============================================================
    @api.model
    def _filter_new_trips(self, trips):
        valid = [t for t in trips if (t.get('trip_id') or t.get('id'))]
        if not valid:
            return [], {}

        incoming_ids  = [str(t.get('trip_id') or t.get('id')) for t in valid]
        existing_recs = self.search([('external_trip_id', 'in', incoming_ids)])
        existing_map  = {r.external_trip_id: r for r in existing_recs}

        new_trips = [
            t for t in valid
            if str(t.get('trip_id') or t.get('id')) not in existing_map
        ]
        return new_trips, existing_map

    # ============================================================
    # [M] _save_trips_in_batches — แบ่งส่ง batch ทุก 5 วินาที (UC-04 ข้อ 2)
    #
    # - แบ่ง new_trips เป็น batch ขนาด _BATCH_SIZE
    # - บันทึกแต่ละ batch แล้วรอ _BATCH_SEC วินาที
    # - ถ้า UNIQUE constraint ดัก (Dedup ชั้น 3) → log warning แล้วไปต่อ
    # ============================================================
    @api.model
    def _save_trips_in_batches(self, new_trips, api_url='', api_key=''):
        created = 0
        total   = len(new_trips)

        for batch_start in range(0, total, _BATCH_SIZE):
            batch = new_trips[batch_start: batch_start + _BATCH_SIZE]

            for t in batch:
                vals = self._build_trip_vals(t)
                if not vals:
                    continue
                try:
                    self.create(vals)
                    created += 1
                    # แจ้ง Backend ว่า trip นี้ sync แล้ว ไม่ต้องส่งซ้ำ
                    self._mark_trip_synced(api_url, api_key, t.get('id') or t.get('trip_id'))
                except Exception as e:
                    # Dedup ชั้น 3: UNIQUE constraint ดัก race condition
                    ext_id = t.get('trip_id') or t.get('id') or '?'
                    _logger.warning(
                        '_save_trips_in_batches: ข้าม trip %s (อาจซ้ำ): %s',
                        ext_id, e,
                    )

            # รอ 5 วินาทีระหว่าง batch (ยกเว้น batch สุดท้าย)
            is_last_batch = (batch_start + _BATCH_SIZE) >= total
            if not is_last_batch:
                _logger.debug(
                    '_save_trips_in_batches: batch %d/%d done — รอ %ds',
                    (batch_start // _BATCH_SIZE) + 1,
                    -(-total // _BATCH_SIZE),
                    _BATCH_SEC,
                )
                time.sleep(_BATCH_SEC)

        return created

    # ============================================================
    # [N] _build_trip_vals — แปลง dict จาก Backend → vals dict
    #     คืน {} ถ้าหารถหรือ ext_id ไม่ได้ (caller จะ skip ให้เอง)
    # ============================================================
    @api.model
    def _build_trip_vals(self, t):
        ext_id = t.get('trip_id') or t.get('id')
        if not ext_id:
            return {}

        device_id_str = t.get('device_id', '')
        vehicle = self.env['fleet.vehicle'].sudo().search(
            [('telematics_device_id', '=', device_id_str)], limit=1)
        if not vehicle:
            _logger.warning(
                '_build_trip_vals: ไม่พบรถสำหรับ device_id=%s', device_id_str)
            return {}

        driver_name = t.get('driver_name', '')
        driver = (
            self.env['hr.employee'].sudo().search(
                [('name', 'ilike', driver_name)], limit=1)
            if driver_name else self.env['hr.employee']
        )

        # map field ตาม response จริงจาก GET /api/v1/trips/sync-batch:
        #   id, device_id, trip_start, trip_end, distance_km,
        #   idle_min, max_speed, avg_speed, harsh_*_count,
        #   speeding_count, driver_score, fuel_used
        return {
            'external_trip_id':   str(ext_id),
            'vehicle_id':         vehicle.id,
            'driver_id':          driver.id if driver else False,
            'trip_start':         t.get('trip_start'),      # ← ชื่อ field ตาม Backend
            'trip_end':           t.get('trip_end'),        # ← ชื่อ field ตาม Backend
            'distance_km':        float(t.get('distance_km',    0) or 0),
            'avg_speed':          float(t.get('avg_speed',      0) or 0),
            'max_speed':          float(t.get('max_speed',      0) or 0),
            'idle_min':           float(t.get('idle_min',       0) or 0),
            'fuel_used_est':      float(t.get('fuel_used',      0) or 0),  # ← fuel_used ไม่ใช่ fuel_used_est
            'driver_score':       float(t.get('driver_score',   0) or 0),
            'harsh_brake_count':  int(t.get('harsh_brake_count',  0) or 0),
            'harsh_accel_count':  int(t.get('harsh_accel_count',  0) or 0),
            'harsh_corner_count': int(t.get('harsh_corner_count', 0) or 0),
            'speeding_count':     int(t.get('speeding_count',     0) or 0),
            'gps_track_json':     t.get('gps_track_json', ''),
            'state':              'synced',
        }
