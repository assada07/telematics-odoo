# ==============================================================================
# models/telematics_log.py
# โมเดลเก็บประวัติเที่ยววิ่ง (Trip Logs)
# ==============================================================================
import logging
import requests
from datetime import datetime, timezone

from odoo import models, fields, api
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class TelematicsLog(models.Model):
    _name        = 'fleet.telematics.log'
    _description = 'Fleet Telematics Trip Log'
    _order       = 'trip_start desc'
    _rec_name    = 'display_name'

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
    external_trip_id = fields.Char(string='External Trip ID',
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
    # [I] Cron — ดึง Trip Logs จาก MTD Backend ทุก 5 นาที
    # ============================================================
    @api.model
    def _cron_sync_trips(self):
        ICP = self.env['ir.config_parameter'].sudo()
        api_url = ICP.get_param('fleet_telematics.mtd_api_url', '').rstrip('/')
        api_key = ICP.get_param('fleet_telematics.mtd_api_key', '')

        if not api_url or not api_key:
            _logger.warning('fleet_telematics: ยังไม่ได้ตั้งค่า MTD API — ข้าม Cron')
            return

        try:
            resp = requests.get(
                f'{api_url}/api/v1/trips',
                headers={'APIKEY': api_key},
                params={'status': 'completed', 'limit': 50},
                timeout=30,
            )
            resp.raise_for_status()
            trips = resp.json().get('trips', [])
        except requests.RequestException as e:
            _logger.error('fleet_telematics._cron_sync_trips error: %s', e)

            # อัปเดต last_error ลง config record ล่าสุด
            cfg = self.env['fleet.telematics.config'].search([], limit=1)
            if cfg:
                cfg.write({'last_error': str(e)})
            return

        created = updated = 0
        for t in trips:
            ext_id = t.get('trip_id') or t.get('id')
            if not ext_id:
                continue

            # หา vehicle จาก device_id
            device_id_str = t.get('device_id', '')
            vehicle = self.env['fleet.vehicle'].sudo().search(
                [('telematics_device_id', '=', device_id_str)], limit=1)

            # หา driver จาก hr.employee
            driver_name = t.get('driver_name', '')
            driver = self.env['hr.employee'].sudo().search(
                [('name', 'ilike', driver_name)], limit=1) if driver_name else self.env['hr.employee']

            if not vehicle:
                _logger.warning('_cron_sync_trips: ไม่พบรถสำหรับ device_id=%s', device_id_str)
                continue

            vals = {
                'external_trip_id': str(ext_id),
                'vehicle_id':       vehicle.id,
                'driver_id':        driver.id if driver else False,
                'trip_start':       t.get('start_time'),
                'trip_end':         t.get('end_time'),
                'distance_km':      float(t.get('distance_km', 0) or 0),
                'avg_speed':        float(t.get('avg_speed', 0) or 0),
                'max_speed':        float(t.get('max_speed', 0) or 0),
                'idle_min':         float(t.get('idle_min', 0) or 0),
                'fuel_used_est':    float(t.get('fuel_used_est', 0) or 0),
                'driver_score':     float(t.get('driver_score', 0) or 0),
                'harsh_brake_count':  int(t.get('harsh_brake_count', 0) or 0),
                'harsh_accel_count':  int(t.get('harsh_accel_count', 0) or 0),
                'harsh_corner_count': int(t.get('harsh_corner_count', 0) or 0),
                'speeding_count':     int(t.get('speeding_count', 0) or 0),
                'gps_track_json':   t.get('gps_track_json', ''),
                'state':            'synced',
            }

            existing = self.search([('external_trip_id', '=', str(ext_id))], limit=1)
            if existing:
                existing.write(vals)
                updated += 1
            else:
                self.create(vals)
                created += 1

        # อัปเดต last_sync_at ใน config
        cfg = self.env['fleet.telematics.config'].search([], limit=1)
        if cfg:
            cfg.write({'last_sync_at': fields.Datetime.now(), 'last_error': False})

        _logger.info(
            '_cron_sync_trips: สร้าง %d รายการใหม่, อัปเดต %d รายการ', created, updated)
