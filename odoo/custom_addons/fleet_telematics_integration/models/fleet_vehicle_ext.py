# ==============================================================================
# models/fleet_vehicle_ext.py  [MODIFIED — final]
# ฟิลด์และ Logic ทั้งหมดของ Telematics Extension บน fleet.vehicle
# ==============================================================================
import logging
import requests
from datetime import datetime, timezone

from odoo import models, fields, api
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)


class FleetVehicleExt(models.Model):
    _inherit = 'fleet.vehicle'

    # ============================================================
    # [A] ฟิลด์ Telematics
    # ============================================================

    telematics_device_id = fields.Char(
        string='GPS Device ID',
        help='รหัสกล่อง GPS เช่น KTC-001 — ต้องตรงกับ device_id ใน Backend'
    )

    # จดจำบอร์ดเดิมอัตโนมัติผ่าน write() hook
    # ใช้เป็น old_device_id เมื่อยิง PUT /api/v1/config/vehicle
    previous_device_id = fields.Char(
        string='Previous Device ID',
        readonly=True,
        help='รหัสบอร์ดก่อนการเปลี่ยนครั้งล่าสุด — ระบบบันทึกอัตโนมัติ'
    )

    last_lat      = fields.Float(string='Last Latitude',        digits=(10, 7))
    last_lon      = fields.Float(string='Last Longitude',       digits=(10, 7))
    last_seen     = fields.Datetime(string='Last GPS Update')
    current_speed = fields.Float(string='Current Speed (km/h)', digits=(10, 1))
    ignition      = fields.Boolean(string='Ignition On',         default=False)

    online_status = fields.Selection([
        ('online',  '🟢 Online'),
        ('offline', '🔴 Offline'),
        ('unknown', '⚪ Unknown'),
    ], string='Online Status', default='unknown', readonly=True)

    sync_status = fields.Selection([
        ('idle',    'กำลังทำงาน'),
        ('syncing', 'กำลังรอ'),
        ('synced',  'อัปเดตสำเร็จ'),
    ], string='Sync Status', default='idle', readonly=True,
       help='แสดงสถานะการส่งข้อมูลไป Backend')

    # ============================================================
    # [B] สถิติสะสม
    # ============================================================

    total_trips        = fields.Integer(string='Total Trips',        default=0)
    total_distance_km  = fields.Float(string='Total Distance (km)',  digits=(10, 2), default=0.0)
    avg_driver_score   = fields.Float(string='Avg Driver Score',     digits=(5,  2), default=0.0)
    telematics_log_ids = fields.One2many(
        'fleet.telematics.log', 'vehicle_id', string='Trip Logs'
    )

    # ============================================================
    # [C] Helper — ดึง API URL + Key จาก confirmed URL
    # ============================================================

    def _get_api_credentials(self):
        Config = self.env['fleet.telematics.config']
        api_url = Config.get_active_api_url()
        api_key = Config.get_active_api_key()
        if not api_url:
            raise UserError(
                'ยังไม่ได้ตั้งค่า Backend API URL\n'
                'ไปที่ Fleet Telematics → Settings แล้วกรอก API URL'
            )
        return api_url, api_key

    # ============================================================
    # [D] ดักรถ/บอร์ดซ้ำ — Validation ฝั่ง Python
    # ============================================================

    @api.constrains('license_plate', 'telematics_device_id')
    def _check_duplicate_vehicle(self):
        for rec in self:
            if rec.license_plate:
                dup = self.search([
                    ('license_plate', '=', rec.license_plate),
                    ('id', '!=', rec.id),
                ], limit=1)
                if dup:
                    raise ValidationError(
                        f'🚗 รถคันนี้มีอยู่ในระบบแล้ว!\n'
                        f'ทะเบียน "{rec.license_plate}" ถูกใช้โดยรถ: {dup.name}'
                    )
            if rec.telematics_device_id:
                dup_dev = self.search([
                    ('telematics_device_id', '=', rec.telematics_device_id),
                    ('id', '!=', rec.id),
                ], limit=1)
                if dup_dev:
                    raise ValidationError(
                        f'📡 บอร์ด GPS นี้มีอยู่ในระบบแล้ว!\n'
                        f'Device ID "{rec.telematics_device_id}" ถูกใช้โดยรถ: {dup_dev.name}'
                    )

    # ============================================================
    # [E] Override write() — ดักจับบอร์ดเดิมก่อนเปลี่ยนค่า
    #
    # แนวทาง: ใส่ previous_device_id เข้าไปใน vals dict เดียวกัน
    # แล้วเรียก super().write(vals) ครั้งเดียว → ไม่มี write ซ้อน
    # ไม่ trigger constrains สองรอบ ไม่มี race condition
    # ============================================================

    def write(self, vals):
        if 'telematics_device_id' in vals:
            new_val = vals.get('telematics_device_id') or ''
            for rec in self:
                old_val = rec.telematics_device_id or ''
                # บันทึก previous เฉพาะเมื่อมีค่าเดิมอยู่ และค่าเปลี่ยนจริง
                if old_val and old_val != new_val:
                    # ใส่ลงใน vals ของ rec นั้นๆ เพื่อ write รอบเดียว
                    super(FleetVehicleExt, rec).write(
                        dict(vals, previous_device_id=old_val)
                    )
            # รถที่ไม่มีค่าเดิม (บอร์ดใหม่) หรือค่าไม่เปลี่ยน → write ปกติ
            remaining = self.filtered(
                lambda r: not (r.telematics_device_id and
                               r.telematics_device_id != new_val)
            )
            if remaining:
                return super(FleetVehicleExt, remaining).write(vals)
            return True
        return super().write(vals)

    # ============================================================
    # [F] action_sync_to_backend — PUT /api/v1/config/vehicle
    #
    # Payload สเปก Backend:
    #   { "vehicle_id": int, "new_device_id": str, "old_device_id": str|None }
    #
    # หลัง PUT 200: อัปเดต previous_device_id = telematics_device_id ทันที
    # เพื่อให้พร้อมสำหรับการเปลี่ยนบอร์ดครั้งถัดไป
    # ============================================================

    def action_sync_to_backend(self):
        self.ensure_one()

        if not self.telematics_device_id:
            raise UserError('กรุณาระบุ GPS Device ID ในแท็บ Telematics ก่อน')

        api_url, api_key = self._get_api_credentials()

        new_device = self.telematics_device_id or ''
        old_device = self.previous_device_id or None  # None ถ้าเป็นบอร์ดใหม่

        payload = {
            'vehicle_id':    int(self.id),
            'new_device_id': new_device,
            'old_device_id': old_device,   # None หรือ str
        }

        _logger.info(
            'action_sync_to_backend: vehicle_id=%s new_device=%s old_device=%s payload=%s',
            self.id, new_device, old_device, payload
        )

        # เปลี่ยนสถานะเป็น "กำลังรอ"
        super(FleetVehicleExt, self).write({'sync_status': 'syncing'})

        try:
            resp = requests.put(
                f'{api_url}/api/v1/config/vehicle',
                headers={'APIKEY': api_key, 'Content-Type': 'application/json'},
                json=payload,
                timeout=15,
            )
            resp.raise_for_status()

            # PUT 200 → เคลียร์ previous_device_id = new_device (พร้อมรอบถัดไป)
            # และอัปเดต sync_status → synced
            # ใช้ super() โดยตรงเพื่อข้าม write() hook (ไม่ต้องการบันทึก previous ซ้ำ)
            super(FleetVehicleExt, self).write({
                'sync_status':       'synced',
                'previous_device_id': new_device,
            })

            _logger.info(
                'action_sync_to_backend: success vehicle_id=%s → HTTP %s',
                self.id, resp.status_code
            )

            old_label = f'เปลี่ยนจาก {old_device} → ' if old_device else 'บอร์ดใหม่: '
            return {
                'type': 'ir.actions.client',
                'tag':  'display_notification',
                'params': {
                    'title':   '⬆️ ส่งข้อมูลสำเร็จ',
                    'message': (
                        f'รถ {self.name}  (Vehicle ID: {self.id})\n'
                        f'📡 {old_label}{new_device}\n'
                        f'Backend อัปเดตเรียบร้อยแล้ว'
                    ),
                    'type':   'success',
                    'sticky': False,
                },
            }

        except requests.RequestException as e:
            super(FleetVehicleExt, self).write({'sync_status': 'idle'})
            raise UserError(f'ส่งข้อมูลไป Backend ไม่สำเร็จ:\n{e}')

    # ============================================================
    # [G] action_check_vehicle_status — GET /api/v1/vehicles/{vehicle_id}/location
    # ใช้ self.id (Odoo ID ตัวเลข) ใน path ตามสเปก Backend
    # ============================================================

    def action_check_vehicle_status(self):
        self.ensure_one()

        api_url, api_key = self._get_api_credentials()

        super(FleetVehicleExt, self).write({'sync_status': 'syncing'})

        try:
            resp = requests.get(
                f'{api_url}/api/v1/vehicles/{self.id}/location',
                headers={'APIKEY': api_key},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()

        except requests.RequestException as e:
            super(FleetVehicleExt, self).write({'sync_status': 'idle'})
            raise UserError(f'เรียก Backend API ไม่สำเร็จ:\n{e}')

        # แปลง Response → Odoo fields
        lat      = data.get('lat',      self.last_lat)
        lon      = data.get('lon',      self.last_lon)
        speed    = float(data.get('speed',    0) or 0)
        ignition = bool(data.get('ignition', False))
        ts_raw   = data.get('ts')

        # device_id ที่ Backend ตอบกลับ — ใช้อัปเดตหน้าจอให้ตรงหลังบ้านเสมอ
        backend_device_id = data.get('device_id') or self.telematics_device_id or '-'

        if ts_raw:
            try:
                dt = datetime.fromisoformat(ts_raw)
                last_seen = dt.astimezone(timezone.utc).replace(tzinfo=None)
            except Exception:
                last_seen = fields.Datetime.now()
        else:
            last_seen = fields.Datetime.now()

        is_online = ignition or (speed > 0)

        write_vals = {
            'last_lat':      lat,
            'last_lon':      lon,
            'last_seen':     last_seen,
            'current_speed': speed,
            'ignition':      ignition,
            'online_status': 'online' if is_online else 'offline',
            'sync_status':   'synced',
        }
        # อัปเดต telematics_device_id จาก Backend response
        # เผื่อกรณี Backend สลับบอร์ด → หน้าจอ Odoo อัปเดตตามอัตโนมัติ
        if backend_device_id and backend_device_id != '-':
            write_vals['telematics_device_id'] = backend_device_id

        self.write(write_vals)

        _logger.info(
            'action_check_vehicle_status: vehicle_id=%s device=%s online=%s speed=%s lat=%s lon=%s',
            self.id, backend_device_id, is_online, speed, lat, lon
        )

        device_line = (
            f'📡 รหัสบอร์ดปัจจุบัน: {backend_device_id} (เชื่อมต่อแล้ว)'
            if backend_device_id and backend_device_id != '-'
            else '📡 สถานะบอร์ด: ยังไม่ได้เชื่อมต่อบอร์ด'
        )
        lat_fmt = f'{float(lat):.6f}' if lat else '-'
        lon_fmt = f'{float(lon):.6f}' if lon else '-'

        return {
            'type': 'ir.actions.client',
            'tag':  'display_notification',
            'params': {
                'title':   f'{"🟢 Online" if is_online else "🔴 Offline"} — {self.name}',
                'message': '\n'.join([
                    f'🚗 Vehicle ID: {self.id}  ({self.name})',
                    device_line,
                    f'📍 พิกัดล่าสุด (Real-time): {lat_fmt}, {lon_fmt}',
                    f'🔑 Ignition: {"เปิด ✅" if ignition else "ปิด 🔴"}',
                    f'💨 Speed: {speed} km/h',
                ]),
                'type':   'success' if is_online else 'warning',
                'sticky': True,
            },
        }
