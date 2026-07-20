"""models/telematics_log.py

โมเดลเก็บประวัติเที่ยววิ่ง (Trip Log) — หัวใจของ UC-04/05: sync ข้อมูลทริป
จาก Backend เข้า Odoo อัตโนมัติทุก 5 นาทีผ่าน Cron

Flow การ sync (ดู _cron_sync_trips สำหรับรายละเอียดเต็ม):
  1. POST /webhook/odoo-sync         — ดึง trip ใหม่ตั้งแต่ last_sync_timestamp
  2. บันทึกแต่ละ trip ลง Odoo (idempotent write/create)
  3. PATCH /trips/batch/mark-synced  — แจ้ง Backend ว่า sync สำเร็จทั้งชุด
  4. เก็บ last_sync_timestamp ใหม่จาก Backend (ห้ามคำนวณเอง ป้องกัน clock drift)
  5. ถ้า batch เต็ม (200 รายการ) วน loop ต่อทันที เผื่อมี trip เหลืออีก

เมธอดหลักในไฟล์นี้:
  _cron_sync_trips()      — Cron entry point
  _fetch_trips_batch()    — POST /webhook/odoo-sync
  _mark_trips_synced()    — PATCH /trips/batch/mark-synced
  _retry_single_trip()    — PATCH /trips/{id}/mark-synced (manual tool)
  _parse_trip_dt()        — แปลง ISO datetime (มี timezone) → UTC naive
  _build_trip_vals()      — แปลง dict จาก Backend → vals dict ของ Odoo
  _sync_trip_events()     — ดึง harsh events ของแต่ละ trip มาเก็บ
  _update_odometer_and_check_maintenance() — อัปเดตสถิติรถ + trigger ซ่อมบำรุง
  _cron_purge_old_trips() — ลบทริปเก่าตาม data retention policy
"""
import logging
import requests
from datetime import datetime, timezone
from markupsafe import Markup

from odoo import models, fields, api
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

_PARAM_LAST_TS = 'fleet_telematics.trip_last_sync_timestamp'
_BATCH_FULL    = 200


class TelematicsLog(models.Model):
    """Trip 1 เที่ยววิ่ง ต่อ 1 record — sync มาจาก Backend เท่านั้น."""

    _name        = 'fleet.telematics.log'
    _description = 'Fleet Telematics Trip Log'
    _order       = 'trip_start desc'
    _rec_name    = 'display_name'

    _external_trip_id_unique = models.Constraint(
        'UNIQUE(external_trip_id)',
        'external_trip_id ต้องไม่ซ้ำกัน — ห้ามบันทึก Trip ซ้ำจาก Backend',
    )

    # ── ข้อมูลหลักของ Trip: รถ คนขับ และอุปกรณ์ GPS ──────────────
    vehicle_id = fields.Many2one(
        'fleet.vehicle', string='Vehicle',
        required=True, ondelete='restrict')
    # optional เพราะ Backend อาจส่ง driver_id=null/0 มาสำหรับ trip ที่ยัง
    # ไม่ได้ assign คนขับ — ต้องบันทึกได้ก่อน แล้วไปผูกคนขับทีหลังใน Odoo ได้
    driver_id = fields.Many2one(
        'hr.employee', string='Driver',
        required=False,
        ondelete='set null')
    telematics_device_id = fields.Char(
        string='Device ID',
        help='รหัสกล่องพ่วง GPS เช่น KTC-001')

    # ── ช่วงเวลาของ Trip ─────────────────────────────────────────
    trip_start   = fields.Datetime(string='Trip Start', required=True)
    trip_end     = fields.Datetime(string='Trip End')
    duration_min = fields.Float(
        string='Duration (min)',
        compute='_compute_duration', store=True,
        digits=(10, 2))

    # ── สถิติการเดินทาง ──────────────────────────────────────────
    distance_km   = fields.Float(string='Distance (km)',    digits=(10, 2))
    max_speed     = fields.Float(string='Max Speed (km/h)', digits=(10, 2))
    avg_speed     = fields.Float(string='Avg Speed (km/h)', digits=(10, 2))
    idle_min      = fields.Float(string='Idle Time (min)',  digits=(10, 2))
    fuel_used_est = fields.Float(string='Fuel Est. (L)',    digits=(10, 3))

    # ── คะแนนและสถิติเหตุการณ์อันตราย ────────────────────────────
    driver_score       = fields.Float(string='Driver Score',        digits=(5, 2))
    harsh_brake_count  = fields.Integer(string='Harsh Brakes')
    harsh_accel_count  = fields.Integer(string='Harsh Accelerations')
    harsh_corner_count = fields.Integer(string='Harsh Cornering')
    speeding_count     = fields.Integer(string='Speeding Events')

    # ── ข้อมูลเส้นทาง GPS และการอ้างอิงกับระบบภายนอก ──────────────
    gps_track_json   = fields.Text(string='GPS Track (JSON)',
        help='เก็บ GPS track ทั้งสาย เช่น [{"lat": 18.7883, "lon": 98.9853, "ts": "..."}]')
    external_trip_id = fields.Char(
        string='External Trip ID',
        index=True,
        help='Trip ID จาก MTD Backend สำหรับ sync และ dedup')

    # ── สถานะและความสัมพันธ์กับ Events ───────────────────────────
    state = fields.Selection([
        ('draft',     'Draft'),
        ('confirmed', 'Confirmed'),
        ('synced',    'Synced'),
        ('failed',    'Failed'),
    ], string='Sync Status', default='draft')

    # กัน trip เดิมถูกยิง GET /trips/{id} ซ้ำทุกรอบ cron เพื่อดึง harsh
    # events — ดึงครั้งเดียวตอนสร้าง trip ใหม่ก็พอ
    events_synced = fields.Boolean(
        string='Events Synced', default=False, readonly=True,
        help='True แล้วเมื่อดึง Harsh Events ของ trip นี้จาก Backend มาเก็บครบแล้ว')

    event_ids = fields.One2many(
        'fleet.telematics.event', 'trip_id', string='Harsh Events')

    display_name = fields.Char(
        compute='_compute_display_name', store=True)

    @api.depends('vehicle_id', 'trip_start')
    def _compute_display_name(self):
        """ชื่อแสดงผล เช่น 'BASE-001 — 15/06/26 08:00'."""
        for rec in self:
            v = rec.vehicle_id.name or '?'
            t = rec.trip_start.strftime('%d/%m/%y %H:%M') if rec.trip_start else '-'
            rec.display_name = f'{v} — {t}'

    @api.depends('trip_start', 'trip_end')
    def _compute_duration(self):
        """คำนวณระยะเวลาทริปเป็นนาที จาก trip_end - trip_start."""
        for rec in self:
            if rec.trip_start and rec.trip_end:
                rec.duration_min = (rec.trip_end - rec.trip_start).total_seconds() / 60
            else:
                rec.duration_min = 0.0

    def action_confirm(self):
        """Draft → Confirmed (ยืนยันข้อมูลทริปด้วยมือ กรณีจำเป็น)."""
        for rec in self:
            if rec.state == 'draft':
                rec.state = 'confirmed'

    @api.model
    def _cron_sync_trips(self):
        """Cron entry point — sync trip จาก Backend เข้า Odoo (ทุก 5 นาที).

        ขั้นตอน:
          1. POST /webhook/odoo-sync ส่ง last_sync_timestamp เดิม (รอบแรก
             ไม่ส่ง field นี้ → Backend ส่ง trip ที่ยังไม่ sync ทั้งหมด)
          2. บันทึกแต่ละ trip ลง Odoo (write ถ้ามีอยู่แล้ว/create ถ้ายังไม่มี)
             พร้อมดึง harsh events และอัปเดตสถิติรถ/เช็ค maintenance ของ
             แต่ละ trip ที่บันทึกสำเร็จ
          3. PATCH /trips/batch/mark-synced สำหรับ trip ที่บันทึกสำเร็จทั้งชุด
          4. trip ที่บันทึกไม่สำเร็จ (failed_ids) จะไม่ถูก mark-synced เลย
             เพื่อให้ Backend ส่งมาให้ลองใหม่อัตโนมัติในรอบ cron ถัดไป —
             ห้าม mark-synced ให้ trip ที่ Odoo บันทึกไม่สำเร็จเด็ดขาด
             ไม่เช่นนั้น Backend จะคิดว่า sync แล้วและไม่ส่งมาอีก
             ทำให้ข้อมูลหายถาวรแบบเงียบๆ
          5. เก็บ last_sync_timestamp ใหม่จาก Backend เท่านั้น (ห้ามคิดเอง
             จาก datetime.now() — ป้องกัน clock drift) ถ้า Backend ไม่ส่ง
             ค่าใหม่มา หรือส่งค่าเดิมซ้ำ ให้หยุด loop ทันทีป้องกันวนไม่สิ้นสุด
          6. ถ้า total เต็ม batch (200 รายการ) วน loop ต่อทันที เผื่อมี trip
             เหลืออีก
        """
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

            # ขั้นที่ 1: ดึง trip ชุดใหม่จาก Backend
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

            # ขั้นที่ 2: บันทึกแต่ละ trip ลง Odoo
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
                        trip_rec = existing
                    else:
                        trip_rec = self.create(vals)
                    synced_ids.append(int(ext_id))
                    # ดึง Harsh Event ของ trip นี้จาก Backend มาเก็บ — ทำครั้ง
                    # เดียวต่อ trip (กันยิง API ซ้ำทุกรอบ cron) ห่อ try/except
                    # ไม่ให้พัง flow หลัก
                    if not trip_rec.events_synced:
                        try:
                            self._sync_trip_events(trip_rec, api_url, api_key)
                        except Exception as e:
                            _logger.warning(
                                '_cron_sync_trips: ดึง events ของ trip %s ไม่สำเร็จ: %s',
                                ext_id, e)
                    # อัปเดต odometer + engine hours + สถิติสะสมของรถ แล้ว
                    # ตรวจสอบ maintenance threshold → สร้าง service record
                    # ถ้าถึงกำหนด
                    if vals.get('vehicle_id') and vals.get('distance_km'):
                        self._update_odometer_and_check_maintenance(
                            vals['vehicle_id'], vals['distance_km'],
                            duration_min=trip_rec.duration_min or 0.0,
                            driver_score=vals.get('driver_score'))
                except Exception as e:
                    _logger.warning(
                        '_cron_sync_trips: บันทึก trip %s ล้มเหลว: %s', ext_id, e)
                    failed_ids.append(int(ext_id))

            # ขั้นที่ 3: แจ้ง Backend ว่า trip ที่บันทึกสำเร็จ sync แล้ว (batch)
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

            # ขั้นที่ 4: trip ที่บันทึกลง Odoo ไม่สำเร็จ (search/write/create
            # หรือ _update_odometer_and_check_maintenance ล้มเหลว) — ห้ามยิง
            # mark-synced ให้ trip เหล่านี้เด็ดขาด ปล่อยไว้เฉยๆ (ไม่ mark ว่า
            # synced) เพื่อให้รอบ cron ถัดไปที่ยิง POST /webhook/odoo-sync
            # ดึง trip ชุดนี้กลับมาลองบันทึกใหม่อีกครั้งโดยอัตโนมัติ (Backend
            # ยังไม่รู้ว่า sync แล้ว จึงส่งซ้ำให้เอง — นี่คือ safety net ที่
            # ตั้งใจออกแบบไว้) log เป็น error พร้อมบันทึกจำนวน/รายการไว้ใน
            # config เพื่อให้ Fleet Manager เห็นและตามสาเหตุที่แท้จริงได้
            if failed_ids:
                _logger.error(
                    '_cron_sync_trips: บันทึก %d trip ลง Odoo ไม่สำเร็จ '
                    '(external_trip_id=%s) — Backend ยังไม่ถูกแจ้งว่า synced '
                    'จะดึงมาลองใหม่อัตโนมัติในรอบ cron ถัดไป',
                    len(failed_ids), failed_ids,
                )
                cfg = cfg_model.search([], limit=1)
                if cfg:
                    cfg.write({
                        'last_error': (
                            f'{len(failed_ids)} trip บันทึกไม่สำเร็จ (ids: {failed_ids}) '
                            f'— รอ retry อัตโนมัติรอบหน้า ดู log ระดับ error สำหรับสาเหตุ'
                        ),
                    })

            # ขั้นที่ 5: เก็บ last_sync_timestamp ใหม่จาก Backend เท่านั้น
            # ห้ามคิดเองจาก datetime.now() — ป้องกัน loop ไม่สิ้นสุด: ถ้า
            # Backend ไม่ส่ง new_ts กลับมา หรือส่งค่าเดิมซ้ำ ให้หยุด loop ทันที
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

            # ขั้นที่ 6: ถ้า batch เต็ม (200 รายการ) อาจมี trip เหลืออีก วน loop ต่อ
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

    @api.model
    def _fetch_trips_batch(self, api_url, api_key, last_ts):
        """ดึง trip ชุดใหม่จาก Backend (POST /api/v1/webhook/odoo-sync).

        Args:
            api_url (str): Base URL ของ Backend
            api_key (str): API Key สำหรับ header APIKEY
            last_ts (str หรือ None): last_sync_timestamp รอบก่อน — None
                หมายถึงรอบแรก (ไม่ส่ง field นี้ Backend จะส่ง trip ที่ยังไม่
                sync ทั้งหมด)

        Returns:
            tuple: (trips: list[dict], new_ts: str, total: int)
        """
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

    @api.model
    def _retry_single_trip(self, api_url, api_key, trip_id):
        """แจ้ง Backend ว่า trip ตัวเดียว sync แล้ว (PATCH /trips/{id}/mark-synced).

        idempotent เต็มรูปแบบ เรียกซ้ำกี่ครั้งก็ได้ — ⚠️ ไม่ถูกเรียกจาก
        _cron_sync_trips() แล้ว (เดิมเคยเรียกให้ trip ที่บันทึกลง Odoo
        ไม่สำเร็จ ทำให้ Backend เข้าใจผิดว่า sync แล้วทั้งที่ไม่มีจริง —
        ข้อมูลหายถาวร) เมธอดนี้เหลือไว้เป็นเครื่องมือ manual สำหรับ Admin
        เท่านั้น ใช้ได้เฉพาะกรณีตรวจสอบแล้วว่า Odoo มี record ของ trip_id
        นี้ถูกต้องสมบูรณ์จริง แต่ Backend ไม่รู้ (เช่น batch mark-synced
        ครั้งก่อนล้มเหลวบางส่วน) ห้ามเรียกกับ trip ที่ยังไม่ยืนยันว่าบันทึก
        สำเร็จ

        Args:
            api_url (str): Base URL ของ Backend
            api_key (str): API Key
            trip_id: external_trip_id ของ trip ที่จะ mark-synced
        """
        url = f'{api_url}/api/v1/trips/{trip_id}/mark-synced'
        _logger.info('_retry_single_trip: PATCH %s', url)

        # Backend ต้องการ synced_at ใน body ถ้าไม่ส่งจะไม่รู้ว่า sync เมื่อไหร่
        from datetime import datetime, timezone as _tz
        synced_at = datetime.now(_tz.utc).strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'

        resp = requests.patch(
            url,
            json={'synced_at': synced_at},
            headers={'APIKEY': api_key} if api_key else {},
            timeout=15,
        )
        resp.raise_for_status()

    @api.model
    def _mark_trips_synced(self, api_url, api_key, trip_ids):
        """แจ้ง Backend ว่า trip ทั้งชุด sync แล้ว (PATCH /trips/batch/mark-synced).

        เป็น all-or-nothing transaction ฝั่ง Backend — ถ้า trip ตัวใด
        update ไม่ได้ ทั้ง batch จะ rollback (ไม่ commit บางส่วน) และ
        idempotent: trip ที่ synced อยู่แล้วจะถูกข้ามเงียบๆ ไม่ error

        ปล่อยให้ requests.RequestException ลอยขึ้นไปให้ caller จัดการ —
        caller จะไม่อัปเดต last_sync_timestamp ถ้า PATCH ล้ม ทำให้รอบหน้า
        Backend ส่ง trip ชุดนี้มาอีก (idempotent ปลอดภัย)

        Args:
            api_url (str): Base URL ของ Backend
            api_key (str): API Key
            trip_ids (list[int]): external_trip_id ของ trip ที่ sync สำเร็จ
        """
        url = f'{api_url}/api/v1/trips/batch/mark-synced'

        _logger.info('_mark_trips_synced: PATCH %s trip_ids=%s', url, trip_ids)

        resp = requests.patch(
            url,
            headers={'APIKEY': api_key} if api_key else {},
            json={'trip_ids': trip_ids},
            timeout=30,
        )
        resp.raise_for_status()

    @api.model
    def _parse_trip_dt(self, value):
        """แปลงสตริงเวลาจาก Backend (ISO 8601 พร้อม timezone offset เช่น
        '2026-06-15T08:00:00+07:00') เป็น UTC naive datetime สำหรับเก็บใน
        fields.Datetime ของ Odoo.

        fields.Datetime ต้องการ string รูปแบบ '%Y-%m-%d %H:%M:%S' (naive,
        UTC) หรือ datetime object เท่านั้น จึง parse ด้วย
        datetime.fromisoformat() แล้วแปลงเป็น UTC + ตัด tzinfo ออกก่อนเก็บ

        Args:
            value (str): สตริงเวลาจาก Backend

        Returns:
            datetime หรือ False: False ถ้า parse ไม่ได้หรือ value ว่างเปล่า
        """
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

    @api.model
    def _build_trip_vals(self, t):
        """แปลง dict trip จาก Backend (POST /webhook/odoo-sync) เป็น vals
        dict สำหรับ create()/write() ของ Odoo.

        สมมติฐานสำคัญ 2 จุด (ยืนยันกับทีม Backend แล้ว):
          1. 'vehicle_id' คือ Odoo record ID ของ fleet.vehicle โดยตรง
             (ส่งไปให้ Backend ผ่าน PUT /config/vehicle ตอน sync รถ)
          2. 'driver_id' คือ Odoo record ID ของ hr.employee โดยตรง อาจเป็น
             null/0 ถ้าทริปนั้นยังไม่ได้ assign คนขับ (ปลอดภัย เพราะ
             driver_id เป็น required=False)

        'duration_min' ไม่ต้องรับจาก Backend เพราะเป็น compute field
        (คำนวณจาก trip_start/trip_end อัตโนมัติใน Odoo)

        Args:
            t (dict): ข้อมูล 1 trip จาก Backend

        Returns:
            dict: vals พร้อมส่งให้ create()/write() — คืน {} ถ้าหารถไม่ได้
                หรือ trip_start parse ไม่ได้ (caller จะ skip ให้เอง)
        """
        ext_id = t.get('id')
        if not ext_id:
            return {}

        # หา vehicle: ใช้ vehicle_id (Odoo record ID) เป็นหลัก
        vehicle = self.env['fleet.vehicle']
        raw_vehicle_id = t.get('vehicle_id')
        if raw_vehicle_id:
            vehicle = self.env['fleet.vehicle'].sudo().browse(int(raw_vehicle_id))
            if not vehicle.exists():
                vehicle = self.env['fleet.vehicle']

        # ถ้า vehicle_id ใช้ไม่ได้/ไม่มี ลองหาด้วย device_id แทน
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

        # หา driver: ใช้ driver_id (Odoo record ID)
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


    def _sync_trip_events(self, trip_rec, api_url, api_key):
        """ดึง harsh events ของ trip นี้จาก Backend มาเก็บ
        (GET /api/v1/trips/{trip_id} คืน trip + GPS track + events).

        Backend ไม่ได้ระบุ schema ของแต่ละ event ชัดเจน จึงเขียนแบบ
        defensive รองรับ key หลายแบบที่เป็นไปได้ (event/event_type,
        ts/timestamp/occurred_at, severity/event_severity, ฯลฯ) กันสร้าง
        event ซ้ำด้วยการเทียบ trip_id + event_type + occurred_at ก่อน
        create() ทุกครั้ง

        Args:
            trip_rec (recordset): fleet.telematics.log ตัวเดียว
            api_url (str): Base URL ของ Backend
            api_key (str): API Key
        """
        if not trip_rec.external_trip_id:
            return

        resp = requests.get(
            f'{api_url}/api/v1/trips/{trip_rec.external_trip_id}',
            headers={'APIKEY': api_key} if api_key else {},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()

        raw_events = (
            data.get('events') or data.get('event_list')
            or data.get('harsh_events') or []
        )
        if not raw_events:
            trip_rec.write({'events_synced': True})
            return

        EventModel = self.env['fleet.telematics.event']
        vals_list = []
        for ev in raw_events:
            occurred_at = self._parse_trip_dt(
                ev.get('occurred_at') or ev.get('ts') or ev.get('timestamp'))
            event_type = ev.get('event_type') or ev.get('event') or ev.get('type')
            if not occurred_at or not event_type:
                _logger.warning(
                    '_sync_trip_events: ข้าม event ที่ไม่มี occurred_at/event_type '
                    '(trip=%s, raw=%s)', trip_rec.external_trip_id, ev)
                continue

            # กันสร้างซ้ำ — เทียบ trip_id + event_type + occurred_at
            dup = EventModel.sudo().search([
                ('trip_id', '=', trip_rec.id),
                ('event_type', '=', event_type),
                ('occurred_at', '=', occurred_at),
            ], limit=1)
            if dup:
                continue

            severity = ev.get('severity')
            if severity is None:
                severity = ev.get('event_severity')
            # normalize: ถ้า Backend ส่งเป็นสัดส่วน 0-1 (เช่น 0.82) แปลงเป็น 0-100
            if isinstance(severity, (int, float)) and 0 <= severity <= 1:
                severity = severity * 100

            vals_list.append({
                'trip_id':        trip_rec.id,
                'event_type':     event_type,
                'occurred_at':    occurred_at,
                'lat':            ev.get('lat', 0.0) or 0.0,
                'lon':            ev.get('lon', 0.0) or 0.0,
                'severity':       severity or 0.0,
                'speed_at_event': ev.get('speed', ev.get('speed_at_event', 0.0)) or 0.0,
                'description':    ev.get('description') or '',
            })

        if vals_list:
            # with_context flag พิเศษ — เดียวที่ผ่าน create() override ของ
            # fleet.telematics.event ได้ (ดู models/telematics_event.py: _check_sync_context)
            EventModel.sudo().with_context(
                fleet_telematics_allow_sync=True
            ).create(vals_list)

        trip_rec.write({'events_synced': True})

    def action_load_trip_detail(self):
        """โหลด GPS track ของ trip นี้จาก Backend มาเก็บใน gps_track_json
        (GET /api/v1/trips/{trip_id}) ให้หน้าจอ GPS Track tab อ่านไปวาด
        แผนที่ (Leaflet) ได้.

        Returns:
            dict: action แสดง notification สรุปจำนวนจุด GPS ที่ได้รับ

        Raises:
            UserError: ถ้า trip ยังไม่มี external_trip_id, ยังไม่ได้ตั้งค่า
                API URL, หรือเรียก Backend ไม่สำเร็จ
        """
        self.ensure_one()
        if not self.external_trip_id:
            raise UserError('Trip นี้ยังไม่มี External Trip ID — ไม่สามารถดึงจาก Backend ได้')

        cfg_model = self.env['fleet.telematics.config']
        api_url   = cfg_model.get_active_api_url()
        api_key   = cfg_model.get_active_api_key()
        if not api_url:
            raise UserError('กรุณาตั้งค่า API URL ใน Settings ก่อน')

        try:
            resp = requests.get(
                f'{api_url}/api/v1/trips/{self.external_trip_id}',
                headers={'APIKEY': api_key},
                timeout=15,
            )
        except requests.RequestException as e:
            raise UserError(f'เชื่อมต่อ Backend ไม่สำเร็จ: {e}')

        if resp.status_code != 200:
            raise UserError(f'Backend ตอบ (HTTP {resp.status_code}): {resp.text[:300]}')

        data      = resp.json()
        gps_track = data.get('gps_track') or data.get('gps_points') or []

        import json as _json
        self.write({'gps_track_json': _json.dumps(gps_track, ensure_ascii=False)})

        return {
            'type':    'ir.actions.client',
            'tag':     'display_notification',
            'params': {
                'title':   'โหลด GPS Track สำเร็จ',
                'message': f'ได้รับ {len(gps_track)} จุด GPS จาก Backend',
                'type':    'success',
            },
        }


    @api.model
    def _update_odometer_and_check_maintenance(self, vehicle_id, distance_km,
                                                duration_min=0.0, driver_score=None):
        """อัปเดตสถิติสะสมของรถหลัง sync ทริปแต่ละครั้ง แล้วเช็คว่าถึงกำหนด
        แจ้งเตือนซ่อมบำรุงหรือยัง.

        ขั้นตอน:
          1. อัปเดต odometer, engine hours สะสม, total_trips,
             total_distance_km, และ avg_driver_score (running average)
             ของรถคันนี้
          2. เช็ค maintenance threshold 3 รูปแบบ (ค่า threshold ดึงจาก
             ir.config_parameter ให้ Admin ปรับได้):
               - ระยะทางสะสมตั้งแต่ service ล่าสุด (เช่น ทุก 10,000 km)
               - ชั่วโมงเดินเครื่องสะสมตั้งแต่ service ล่าสุด (เช่น ทุก
                 250 ชั่วโมง)
               - จำนวนวันตั้งแต่ service ล่าสุด (เช่น ทุก 90 วัน)
             ถ้าถึงเกณฑ์ข้อใดข้อหนึ่ง สร้าง fleet.vehicle.log.services
             อัตโนมัติ พร้อมแจ้งเตือนกลุ่ม Fleet Manager ผ่าน chatter

        Args:
            vehicle_id (int): id ของ fleet.vehicle
            distance_km (float): ระยะทางของทริปที่เพิ่งบันทึก
            duration_min (float): ระยะเวลาของทริป (นาที) สำหรับสะสม
                engine hours
            driver_score (float, optional): คะแนนของทริปนี้ สำหรับคำนวณ
                avg_driver_score สะสม
        """
        Vehicle = self.env['fleet.vehicle'].sudo().browse(vehicle_id)
        if not Vehicle.exists():
            return

        # อัปเดต odometer + ชั่วโมงเดินเครื่องสะสม
        current_odometer = Vehicle.odometer
        new_odometer     = current_odometer + distance_km
        new_engine_hours = (Vehicle.telematics_engine_hours or 0.0) + (duration_min / 60.0)

        # สะสม total_trips/total_distance_km/avg_driver_score ของรถ ณ จุด
        # เดียวกับที่อัปเดต odometer ต่อทริป
        new_total_trips = (Vehicle.total_trips or 0) + 1
        new_total_km    = (Vehicle.total_distance_km or 0.0) + distance_km
        vals = {
            'odometer':                new_odometer,
            'telematics_engine_hours': new_engine_hours,
            'total_trips':             new_total_trips,
            'total_distance_km':       round(new_total_km, 2),
        }
        if driver_score is not None:
            # ค่าเฉลี่ยสะสมแบบ running average — ไม่ต้อง query trip ทั้งหมดซ้ำ
            old_avg = Vehicle.avg_driver_score or 0.0
            old_n   = (Vehicle.total_trips or 0)
            vals['avg_driver_score'] = round(
                (old_avg * old_n + driver_score) / new_total_trips, 2)

        Vehicle.write(vals)

        # เช็ค maintenance threshold 3 รูปแบบ
        ICP = self.env['ir.config_parameter'].sudo()
        km_threshold    = float(ICP.get_param('fleet_telematics.maintenance_km',    10000))
        hour_threshold  = float(ICP.get_param('fleet_telematics.maintenance_hours',  250))
        day_threshold   = int(  ICP.get_param('fleet_telematics.maintenance_days',    90))

        Service = self.env['fleet.vehicle.log.services'].sudo()

        # เช็ค last service date และ odometer ของ service ล่าสุด
        last_service = Service.search([
            ('vehicle_id', '=', vehicle_id),
        ], order='date desc', limit=1)

        should_create = False
        reason        = ''

        if last_service:
            # Trigger 1: ระยะทาง
            km_since = new_odometer - (last_service.odometer or 0)
            if km_since >= km_threshold:
                should_create = True
                reason = f'ระยะทางสะสม {km_since:,.0f} km (threshold {km_threshold:,.0f} km)'

            # Trigger 2: ชั่วโมงเดินเครื่อง — เทียบชั่วโมงสะสมทั้งหมดกับ
            # threshold นับจากชั่วโมงสะสม ณ ตอน service ล่าสุด
            hours_since = new_engine_hours - (last_service.engine_hours_at_service or 0.0)
            if hours_since >= hour_threshold:
                should_create = True
                reason = (reason + ' / ' if reason else '') + \
                    f'ชั่วโมงเดินเครื่องสะสม {hours_since:,.1f} ชม. (threshold {hour_threshold:,.0f} ชม.)'

            # Trigger 3: ช่วงเวลา
            if last_service.date:
                from datetime import date as _date
                days_since = (_date.today() - last_service.date).days
                if days_since >= day_threshold:
                    should_create = True
                    reason = (reason + ' / ' if reason else '') + \
                        f'ผ่านมา {days_since} วัน (threshold {day_threshold} วัน)'
        else:
            # ไม่มี service record เลย — เช็คทั้ง odometer และชั่วโมงเครื่อง
            # เทียบกับ threshold แรก (ไม่ใช่แค่ odometer อย่างเดียว)
            if new_odometer >= km_threshold:
                should_create = True
                reason = f'odometer {new_odometer:,.0f} km ถึง threshold แรก {km_threshold:,.0f} km'
            if new_engine_hours >= hour_threshold:
                should_create = True
                reason = (reason + ' / ' if reason else '') + \
                    f'ชั่วโมงเดินเครื่อง {new_engine_hours:,.1f} ชม. ถึง threshold แรก {hour_threshold:,.0f} ชม.'

        if should_create:
            # สร้าง fleet.vehicle.log.services record อัตโนมัติ
            service_rec = Service.create({
                'vehicle_id':             vehicle_id,
                'date':                   fields.Date.today(),
                'odometer':               new_odometer,
                'engine_hours_at_service': new_engine_hours,
                'description':            f'[Auto] แจ้งเตือนซ่อมบำรุง: {reason}',
                'state':                  'new',
            })
            _logger.info(
                '_check_maintenance: สร้าง service alert รถ %s (id=%s): %s',
                Vehicle.name, vehicle_id, reason,
            )

            # ส่ง Odoo notification ให้ Fleet Manager ทราบ — หาผู้ใช้ในกลุ่ม
            # Fleet Manager ผ่าน query จาก res.users โดยตรง (เช็คชื่อ field
            # เชื่อมกลุ่มที่มีอยู่จริงก่อนใช้งาน เพราะ Odoo เปลี่ยนชื่อ field
            # นี้ไปมาระหว่างเวอร์ชัน) พร้อม wrap body ด้วย Markup() ให้ HTML
            # tag render ถูกต้อง
            managers_group = self.env.ref('fleet.fleet_group_manager', raise_if_not_found=False)
            manager_users = self.env['res.users']
            if managers_group:
                User = self.env['res.users']
                for fname in ('groups_id', 'group_ids'):
                    if fname in User._fields:
                        manager_users = User.sudo().search([(fname, 'in', managers_group.ids)])
                        break

            if manager_users:
                body = Markup(
                    '🔧 <b>แจ้งเตือนซ่อมบำรุงอัตโนมัติ</b><br/>'
                    'รถ: <b>{vehicle}</b><br/>'
                    'เหตุผล: {reason}<br/>'
                    'Odometer ปัจจุบัน: {odo} km'
                ).format(vehicle=Vehicle.name, reason=reason, odo=f'{new_odometer:,.0f}')
                service_rec.sudo().message_post(
                    body=body,
                    partner_ids=manager_users.partner_id.ids,
                    message_type='notification',
                    subtype_xmlid='mail.mt_note',
                )
            else:
                _logger.warning(
                    '_check_maintenance: ไม่พบผู้ใช้ในกลุ่ม Fleet Manager ที่จะแจ้งเตือน '
                    '(vehicle_id=%s)', vehicle_id,
                )
    @api.model
    def _cron_purge_old_trips(self):
        """ลบ Trip Log ที่เก่ากว่า retention period ทิ้ง (Data Retention Policy).

        ขอบเขตความรับผิดชอบ: "raw telemetry" (ข้อมูลดิบจาก MQTT) เก็บอยู่
        ใน TimescaleDB ฝั่ง Backend เท่านั้น ไม่เคยถูกเก็บใน Odoo — การลบ
        ส่วนนั้นเป็นหน้าที่ของทีม Backend ไม่ใช่โมดูลนี้ ส่วนที่ Odoo
        รับผิดชอบคือ "trip summary" (fleet.telematics.log) เท่านั้น —
        Event ที่ผูกอยู่จะถูกลบตามไปด้วยอัตโนมัติผ่าน ondelete='cascade'
        ไม่ต้องเขียนโค้ดลบแยก

        ไม่แตะ fleet.telematics.incentive เด็ดขาด (เก็บตลอดชีพ ไม่มี
        retention)

        ระยะเวลาเก็บ (ปี) ปรับได้ผ่าน ir.config_parameter
        'fleet_telematics.trip_retention_years' (ค่าเริ่มต้น 3 ปี)
        """
        ICP = self.env['ir.config_parameter'].sudo()
        retention_years = int(ICP.get_param('fleet_telematics.trip_retention_years', 3))

        from dateutil.relativedelta import relativedelta
        cutoff = fields.Datetime.now() - relativedelta(years=retention_years)

        old_trips = self.sudo().search([('trip_start', '<', cutoff)])
        count = len(old_trips)

        if count:
            _logger.info(
                '_cron_purge_old_trips: กำลังลบ Trip Log ที่เก่ากว่า %s ปี '
                '(ก่อน %s) จำนวน %s รายการ (Event ที่ผูกอยู่จะถูกลบตามด้วย '
                'ผ่าน cascade)', retention_years, cutoff, count,
            )
            old_trips.with_context(fleet_telematics_allow_purge=True).unlink()
        else:
            _logger.info(
                '_cron_purge_old_trips: ไม่พบ Trip Log ที่เก่ากว่า %s ปี — '
                'ไม่มีอะไรต้องลบ', retention_years,
            )
