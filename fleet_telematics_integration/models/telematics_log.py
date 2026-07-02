# ==============================================================================
# models/telematics_log.py
# โมเดลเก็บประวัติเที่ยววิ่ง (Trip Logs)
#
# UC-05 Sync Trip Log — ตาม FDD §11.3 / §12.5 (อัปเดต 2026-07-01)
#
# endpoint ที่ใช้จริง (ยืนยันจาก Backend API doc ล่าสุด):
#   1) POST /api/v1/webhook/odoo-sync   → ส่ง last_sync_timestamp รับ trips[]
#   2) PATCH /api/v1/trips/batch/mark-synced → mark สำเร็จทั้งชุด
#   3) PATCH /api/v1/trips/{id}/mark-synced  → mark รายตัว (retry เดี่ยว)
#
# เปลี่ยนจาก GET /trips/unsynced (cursor last_id) เป็น POST /webhook/odoo-sync
# (timestamp-based) ตาม FDD §11.3 — ห้ามคำนวณ timestamp เอง ต้องใช้ค่า
# last_sync_timestamp ที่ Backend ส่งกลับมาเท่านั้น (ป้องกัน clock drift)
#   [I]  _cron_sync_trips()    — Cron Entry Point (ทุก 5 นาที)
#   [J]  _fetch_trips_batch()  — POST /webhook/odoo-sync
#   [K]  _mark_trips_synced()  — PATCH /trips/batch/mark-synced
#   [L]  _retry_single_trip()  — PATCH /trips/{id}/mark-synced
#   [M]  _parse_trip_dt()      — แปลง ISO datetime → UTC
#   [N]  _build_trip_vals()    — แปลง dict → vals
# ==============================================================================
import logging
import requests
from datetime import datetime, timezone

from odoo import models, fields, api
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

_PARAM_LAST_TS = 'fleet_telematics.trip_last_sync_timestamp'
_BATCH_FULL    = 200


class TelematicsLog(models.Model):
    _name        = 'fleet.telematics.log'
    _description = 'Fleet Telematics Trip Log'
    _order       = 'trip_start desc'
    _rec_name    = 'display_name'

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
        required=False,  # แก้ 2026-07-01: เดิม required=True ทำให้ cron crash
                         # ทันทีถ้า Backend ส่ง driver_id=null/0 มา (trip ที่ยัง
                         # ไม่ได้ assign คนขับ) — เปลี่ยนเป็น optional เพื่อให้
                         # บันทึกได้ก่อน แล้วไปผูกคนขับทีหลังใน Odoo ได้
        ondelete='set null')
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
    # [I] _cron_sync_trips — Cron Entry (ทุก 5 นาที, §12.5)
    #
    # Flow ตาม FDD §11.3:
    #   1. POST /webhook/odoo-sync ส่ง last_sync_timestamp เดิม
    #      (รอบแรก: ไม่ส่ง field นี้ → Backend ส่ง trip ที่ยังไม่ sync ทั้งหมด)
    #   2. บันทึกแต่ละ trip ลง Odoo (idempotent write/create)
    #   3. PATCH /trips/batch/mark-synced สำหรับที่สำเร็จทั้งชุด
    #   4. PATCH /trips/{id}/mark-synced รายตัวสำหรับที่ fail (retry เดี่ยว)
    #   5. เก็บ last_sync_timestamp ใหม่จาก Backend
    #      ⚠️ ห้ามใช้ datetime.now() ของ Odoo เอง — ต้องใช้ค่าจาก Backend เท่านั้น
    #         (ป้องกัน clock drift / race condition ที่รอยต่อ timestamp)
    #   6. ถ้า total == 200 (batch เต็ม) → loop ต่อทันที อาจมี trip เหลืออีก
    # ============================================================
    @api.model
    def _cron_sync_trips(self):
        cfg_model = self.env['fleet.telematics.config']
        api_url   = cfg_model.get_active_api_url()
        api_key   = cfg_model.get_active_api_key()

        if not api_url:
            _logger.warning('fleet_telematics: ยังไม่ได้ตั้งค่า API URL — ข้าม Cron')
            return

        ICP     = self.env['ir.config_parameter'].sudo()
        last_ts = ICP.get_param(_PARAM_LAST_TS, '') or None

        total_synced = 0
        loop_count   = 0

        while True:
            loop_count += 1

            # 1) POST /webhook/odoo-sync
            try:
                trips, new_ts, total = self._fetch_trips_batch(api_url, api_key, last_ts)
            except requests.RequestException as e:
                _logger.error('_cron_sync_trips: POST /webhook/odoo-sync ล้มเหลว: %s', e)
                cfg = cfg_model.search([], limit=1)
                if cfg:
                    cfg.write({'last_error': str(e)})
                return

            if not trips:
                _logger.info('_cron_sync_trips: ไม่มี trip ใหม่ (last_ts=%s)', last_ts)
                break

            _logger.info(
                '_cron_sync_trips loop %d: %d trips (total=%d) last_ts=%s',
                loop_count, len(trips), total, last_ts,
            )

            # 2) บันทึกลง Odoo
            synced_ids = []
            failed_ids = []
            for t in trips:
                ext_id = t.get('id')
                if not ext_id:
                    continue
                vals = self._build_trip_vals(t)
                if not vals:
                    continue
                try:
                    existing = self.search(
                        [('external_trip_id', '=', str(ext_id))], limit=1)
                    if existing:
                        existing.write(vals)
                    else:
                        self.create(vals)
                    synced_ids.append(int(ext_id))
                except Exception as e:
                    _logger.warning(
                        '_cron_sync_trips: บันทึก trip %s ล้มเหลว: %s', ext_id, e)
                    failed_ids.append(int(ext_id))

            # 3) PATCH batch mark-synced
            if synced_ids:
                try:
                    self._mark_trips_synced(api_url, api_key, synced_ids)
                    total_synced += len(synced_ids)
                except requests.RequestException as e:
                    _logger.error(
                        '_cron_sync_trips: batch mark-synced ล้มเหลว: %s '
                        '— ไม่อัปเดต last_ts รอบหน้าดึงซ้ำ idempotent', e)
                    cfg = cfg_model.search([], limit=1)
                    if cfg:
                        cfg.write({'last_error': str(e)})
                    return

            # 4) PATCH รายตัว retry สำหรับที่ fail
            for fid in failed_ids:
                try:
                    self._retry_single_trip(api_url, api_key, fid)
                except requests.RequestException as e:
                    _logger.warning(
                        '_cron_sync_trips: retry trip %s ล้มเหลว: %s '
                        '— Backend จะส่งมาใหม่รอบหน้า', fid, e)

            # 5) เก็บ last_sync_timestamp ใหม่จาก Backend เท่านั้น
            # ⚠️ ห้ามคิดเองจาก datetime.now() — ใช้ค่าจาก Backend เท่านั้น
            # ป้องกัน loop ไม่สิ้นสุด: ถ้า Backend ไม่ส่ง new_ts กลับมา
            # หรือส่งค่าเดิมซ้ำ → หยุด loop ทันที (ไม่ใช่สถานะปกติ)
            if new_ts and new_ts != last_ts:
                ICP.set_param(_PARAM_LAST_TS, new_ts)
                last_ts = new_ts
            elif not new_ts:
                _logger.warning(
                    '_cron_sync_trips: Backend ไม่ส่ง last_sync_timestamp กลับมา '
                    '(loop %d) — หยุด loop ป้องกันวนซ้ำไม่สิ้นสุด', loop_count)
                break
            elif new_ts == last_ts:
                _logger.warning(
                    '_cron_sync_trips: last_sync_timestamp ไม่เปลี่ยน (=%s, loop %d) '
                    '— หยุด loop ป้องกันวนซ้ำ', new_ts, loop_count)
                break

            # 6) ถ้า total < 200 หมดแล้ว หยุด loop
            if total < _BATCH_FULL:
                break
            _logger.warning(
                '_cron_sync_trips: total=%d batch เต็ม → loop ต่อรอบ %d',
                _BATCH_FULL, loop_count + 1,
            )

        cfg = cfg_model.search([], limit=1)
        if cfg:
            cfg.write({'last_sync_at': fields.Datetime.now(), 'last_error': False})
        _logger.info(
            '_cron_sync_trips: เสร็จ %d trips ใน %d loop',
            total_synced, loop_count,
        )

    # ============================================================
    # [J] _fetch_trips_batch — POST /api/v1/webhook/odoo-sync (FDD §11.3)
    #   - last_ts=None → รอบแรก ไม่ส่ง field นี้ Backend ส่ง trip ทั้งหมด
    #   - last_ts มีค่า → Backend ส่งเฉพาะ trip ใหม่หลังเวลานั้น
    #   - ค่า last_sync_timestamp ที่ส่งกลับมาต้องเก็บไว้ใช้รอบถัดไปเสมอ
    # ============================================================
    @api.model
    def _fetch_trips_batch(self, api_url, api_key, last_ts):
        url  = f'{api_url}/api/v1/webhook/odoo-sync'
        body = {}
        if last_ts:
            body['last_sync_timestamp'] = last_ts

        _logger.info('_fetch_trips_batch: POST %s body=%s', url, body)
        resp = requests.post(
            url,
            json=body,
            headers={'APIKEY': api_key} if api_key else {},
            timeout=30,
        )
        resp.raise_for_status()
        data   = resp.json()
        trips  = data.get('trips') or []
        new_ts = data.get('last_sync_timestamp')
        total  = int(data.get('total', len(trips)))
        return trips, new_ts, total

    # ============================================================
    # [L] _retry_single_trip — PATCH /api/v1/trips/{id}/mark-synced
    #   ใช้เฉพาะ retry รายตัวที่ fail ใน batch เท่านั้น (FDD §11.3)
    #   idempotent เต็มรูปแบบ เรียกซ้ำกี่ครั้งก็ได้
    # ============================================================
    @api.model
    def _retry_single_trip(self, api_url, api_key, trip_id):
        url = f'{api_url}/api/v1/trips/{trip_id}/mark-synced'
        _logger.info('_retry_single_trip: PATCH %s', url)
        resp = requests.patch(
            url,
            json={},
            headers={'APIKEY': api_key} if api_key else {},
            timeout=15,
        )
        resp.raise_for_status()

    # ============================================================
    # [K] _mark_trips_synced — PATCH /api/v1/trips/batch/mark-synced
    #
    # - ส่ง List ของ Trip IDs (Backend ID) ที่บันทึกลง Odoo สำเร็จในรอบนี้
    # - All-or-Nothing transaction: ถ้า trip ตัวใด update ไม่ได้
    #   ทั้ง batch จะ rollback (ไม่ commit บางส่วน)
    # - Idempotent: trip ที่ synced อยู่แล้วจะถูกข้ามเงียบๆ ไม่ error
    # - ปล่อยให้ requests.RequestException ลอยขึ้นไปให้ caller จัดการ
    #   (caller จะไม่อัปเดต last_sync_timestamp ถ้า PATCH ล้ม
    #    → รอบหน้า Backend จะส่ง trip ชุดนี้มาอีก idempotent ปลอดภัย)
    # ============================================================
    @api.model
    def _mark_trips_synced(self, api_url, api_key, trip_ids):
        url = f'{api_url}/api/v1/trips/batch/mark-synced'

        _logger.info('_mark_trips_synced: PATCH %s trip_ids=%s', url, trip_ids)

        resp = requests.patch(
            url,
            headers={'APIKEY': api_key} if api_key else {},
            json={'trip_ids': trip_ids},
            timeout=30,
        )
        resp.raise_for_status()

    # ============================================================
    # [M] _parse_trip_dt — แปลงสตริงเวลาจาก Backend → UTC naive datetime
    #
    # ⚠️ บั๊กสำคัญที่แก้จากเอกสารจริง: ตัวอย่าง response ของ Backend ส่ง
    # trip_start/trip_end เป็น ISO 8601 "พร้อม timezone offset" เช่น
    # "2026-06-15T08:00:00+07:00" — ไม่ใช่ string UTC เปล่า ๆ
    # ถ้าเอาสตริงนี้ยัดลง fields.Datetime ตรง ๆ (ของเดิมทำแบบนี้) Odoo
    # จะ parse ผิดพลาด/error เพราะ fields.Datetime ต้องการ string รูปแบบ
    # '%Y-%m-%d %H:%M:%S' (naive, UTC) หรือ datetime object เท่านั้น
    # จึงต้อง parse ด้วย datetime.fromisoformat() แล้วแปลงเป็น UTC +
    # ตัด tzinfo ออกก่อนเก็บ (ตามหลัก "Datetime เก็บเป็น UTC เสมอ")
    # ============================================================
    @api.model
    def _parse_trip_dt(self, value):
        if not value:
            return False
        try:
            dt = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
            if dt.tzinfo is not None:
                dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
            return dt
        except (ValueError, TypeError):
            _logger.warning('_parse_trip_dt: parse ไม่ได้ value=%s', value)
            return False

    # ============================================================
    # [N] _build_trip_vals — แปลง dict จาก Backend → vals dict
    #     คืน {} ถ้าหารถไม่ได้ (caller จะ skip ให้เอง)
    #
    # Mapping ตาม JSON จริงจาก POST /api/v1/webhook/odoo-sync (FDD §11.3):
    #   id (→ external_trip_id), device_id, vehicle_id, driver_id,
    #   trip_start, trip_end, distance_km, duration_min, idle_min,
    #   max_speed, avg_speed, harsh_*_count, speeding_count,
    #   driver_score, fuel_used, created_at
    #
    # สมมติฐานสำคัญ 2 จุด (ยืนยันกับทีม Backend แล้ว):
    #   1) 'vehicle_id' คือ Odoo record ID ของ fleet.vehicle โดยตรง
    #      (ส่งไปให้ Backend ผ่าน PUT /config/vehicle ตอน sync รถ)
    #   2) 'driver_id' คือ Odoo record ID ของ hr.employee โดยตรง
    #      (ส่งไปให้ Backend ผ่าน PUT /config/vehicle → field driver_id)
    #      อาจเป็น null/0 ถ้าทริปนั้นยังไม่ได้ assign คนขับ — ปลอดภัยแล้ว
    #      เพราะแก้ driver_id เป็น required=False แล้ว
    #
    #   'duration_min' ที่ Backend ส่งมาไม่ต้องเซ็ต เพราะเป็น computed field
    #   (calculate จาก trip_start/trip_end อัตโนมัติใน Odoo)
    # ============================================================
    @api.model
    def _build_trip_vals(self, t):
        ext_id = t.get('id')
        if not ext_id:
            return {}

        # ── หา vehicle: ใช้ vehicle_id (Odoo record ID) เป็นหลัก ───────────
        vehicle = self.env['fleet.vehicle']
        raw_vehicle_id = t.get('vehicle_id')
        if raw_vehicle_id:
            vehicle = self.env['fleet.vehicle'].sudo().browse(int(raw_vehicle_id))
            if not vehicle.exists():
                vehicle = self.env['fleet.vehicle']

        # fallback: ถ้า vehicle_id ใช้ไม่ได้/ไม่มี ลองหาด้วย device_id แทน
        device_id_str = t.get('device_id', '')
        if not vehicle and device_id_str:
            vehicle = self.env['fleet.vehicle'].sudo().search(
                [('telematics_device_id', '=', device_id_str)], limit=1)

        if not vehicle:
            _logger.warning(
                '_build_trip_vals: ไม่พบรถ (vehicle_id=%s, device_id=%s) — ข้าม trip id=%s',
                raw_vehicle_id, device_id_str, ext_id,
            )
            return {}

        # ── หา driver: ใช้ driver_id (Odoo record ID) ───────────────────
        driver = self.env['hr.employee']
        raw_driver_id = t.get('driver_id')
        if raw_driver_id:
            driver = self.env['hr.employee'].sudo().browse(int(raw_driver_id))
            if not driver.exists():
                _logger.warning(
                    '_build_trip_vals: ไม่พบ driver_id=%s ใน Odoo (trip id=%s)',
                    raw_driver_id, ext_id,
                )
                driver = self.env['hr.employee']

        trip_start = self._parse_trip_dt(t.get('trip_start'))
        if not trip_start:
            _logger.warning(
                '_build_trip_vals: trip_start parse ไม่ได้ (ค่าเดิม=%s) — ข้าม trip id=%s',
                t.get('trip_start'), ext_id,
            )
            return {}

        return {
            'external_trip_id':     str(ext_id),
            'vehicle_id':            vehicle.id,
            'driver_id':             driver.id if driver else False,
            'telematics_device_id':  device_id_str or vehicle.telematics_device_id,
            'trip_start':            trip_start,
            'trip_end':              self._parse_trip_dt(t.get('trip_end')),  # ตัดจบโดย Backend แล้ว
            'distance_km':           float(t.get('distance_km',    0) or 0),
            'avg_speed':             float(t.get('avg_speed',      0) or 0),
            'max_speed':             float(t.get('max_speed',      0) or 0),
            'idle_min':              float(t.get('idle_min',       0) or 0),
            'fuel_used_est':         float(t.get('fuel_used',      0) or 0),  # backend ใช้ชื่อ 'fuel_used'
            'driver_score':          float(t.get('driver_score',   0) or 0),
            'harsh_brake_count':     int(t.get('harsh_brake_count',  0) or 0),
            'harsh_accel_count':     int(t.get('harsh_accel_count',  0) or 0),
            'harsh_corner_count':    int(t.get('harsh_corner_count', 0) or 0),
            'speeding_count':        int(t.get('speeding_count',     0) or 0),
            'gps_track_json':        t.get('gps_track_json', ''),
            'state':                 'synced',
        }
