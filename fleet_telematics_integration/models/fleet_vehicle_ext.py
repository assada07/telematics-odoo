# ==============================================================================
# models/fleet_vehicle_ext.py
# โค้ด Inherit รถยนต์เดิม เพิ่มช่อง GPS Device ID + ปุ่มซิงค์รถ
# ==============================================================================
import logging
import requests
from datetime import datetime, timezone

from odoo import models, fields, api
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class FleetVehicleExt(models.Model):
    _inherit = 'fleet.vehicle'

    # ============================================================
    # [A] ฟิลด์ Telematics ที่เพิ่มเข้าไปใน fleet.vehicle
    # ============================================================
    telematics_device_id = fields.Char(
        string='GPS Device ID',
        help='รหัสกล่องพ่วง GPS เช่น KTC-001 — ต้องตรงกับ device_id ใน MTD')

    last_lat      = fields.Float(string='Last Latitude',        digits=(10, 7))
    last_lon      = fields.Float(string='Last Longitude',       digits=(10, 7))
    last_seen     = fields.Datetime(string='Last GPS Update')
    current_speed = fields.Float(string='Current Speed (km/h)', digits=(10, 1))
    ignition      = fields.Boolean(string='Ignition On', default=False)

    online_status = fields.Selection([
        ('online',  '🟢 Online'),
        ('offline', '🔴 Offline'),
        ('unknown', '⚪ Unknown'),
    ], string='Online Status', default='unknown', readonly=True)

    # ============================================================
    # [B] สถิติสะสมของรถ
    # ============================================================
    total_trips       = fields.Integer(string='Total Trips',       default=0)
    total_distance_km = fields.Float(string='Total Distance (km)', digits=(10, 2), default=0.0)
    avg_driver_score  = fields.Float(string='Avg Driver Score',    digits=(5, 2),  default=0.0)

    telematics_log_ids = fields.One2many(
        'fleet.telematics.log', 'vehicle_id', string='Trip Logs')

    # ============================================================
    # [C] ปุ่ม "⬆️ Sync to Backend" — ยิง POST /api/v1/config/vehicle
    # ผูกรถ + คนขับ + กล่อง GPS ไปให้ MTD Backend
    # ============================================================
    def action_sync_to_backend(self):
        self.ensure_one()
        ICP = self.env['ir.config_parameter'].sudo()
        api_url = ICP.get_param('fleet_telematics.mtd_api_url', '').rstrip('/')
        api_key = ICP.get_param('fleet_telematics.mtd_api_key', '')

        if not api_url or not api_key:
            raise UserError(
                'ยังไม่ได้ตั้งค่า MTD API\n'
                'ไปที่ Fleet Telematics → Settings แล้วกรอก API URL และ API Key'
            )

        if not self.telematics_device_id:
            raise UserError('กรุณาระบุ GPS Device ID ในแท็บ Telematics ก่อน')

        payload = {
            'vehicle_id':  self.id,
            'license_plate': self.license_plate or '',
            'device_id':   self.telematics_device_id,
            'driver_id':   self.driver_id.id if self.driver_id else None,
            'driver_name': self.driver_id.name if self.driver_id else '',
        }

        try:
            resp = requests.post(
                f'{api_url}/api/v1/config/vehicle',
                headers={'APIKEY': api_key, 'Content-Type': 'application/json'},
                json=payload,
                timeout=15,
            )
            resp.raise_for_status()
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title':   '⬆️ Sync สำเร็จ',
                    'message': f'ผูกรถ {self.name} เข้ากับ MTD Backend เรียบร้อยแล้ว',
                    'type':    'success',
                    'sticky':  False,
                },
            }
        except requests.RequestException as e:
            raise UserError(f'Sync ไป Backend ไม่สำเร็จ:\n{e}')

    # ============================================================
    # [D] ปุ่ม "☀️ เช็คสถานะรถ" — ยิง GET /api/v1/vehicles/{id}/location
    # ดึงพิกัดปัจจุบันมาแสดง
    # ============================================================
    def action_check_vehicle_status(self):
        self.ensure_one()
        ICP = self.env['ir.config_parameter'].sudo()
        api_url = ICP.get_param('fleet_telematics.mtd_api_url', '').rstrip('/')
        api_key = ICP.get_param('fleet_telematics.mtd_api_key', '')

        if not api_url or not api_key:
            raise UserError(
                'ยังไม่ได้ตั้งค่า MTD API\n'
                'ไปที่ Fleet Telematics → Settings แล้วกรอก API URL และ API Key'
            )

        try:
            resp = requests.get(
                f'{api_url}/api/v1/vehicles/{self.id}/location',
                headers={'APIKEY': api_key},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as e:
            raise UserError(f'เรียก MTD API ไม่สำเร็จ:\n{e}')

        # แปลง JSON Response → Odoo Fields
        lat      = data.get('lat', self.last_lat)
        lon      = data.get('lon', self.last_lon)
        speed    = float(data.get('speed', 0) or 0)
        ignition = bool(data.get('ignition', False))
        ts_raw   = data.get('ts')

        if ts_raw:
            try:
                dt = datetime.fromisoformat(ts_raw)
                last_seen = dt.astimezone(timezone.utc).replace(tzinfo=None)
            except Exception:
                last_seen = fields.Datetime.now()
        else:
            last_seen = fields.Datetime.now()

        is_online = ignition or (speed > 0)

        self.write({
            'last_lat':      lat,
            'last_lon':      lon,
            'last_seen':     last_seen,
            'current_speed': speed,
            'ignition':      ignition,
            'online_status': 'online' if is_online else 'offline',
        })

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title':   f'{self.name} — {"🟢 Online" if is_online else "🔴 Offline"}',
                'message': (
                    f'Ignition: {"เปิด ✅" if ignition else "ปิด"} | '
                    f'Speed: {speed} km/h | '
                    f'GPS: {lat:.5f}, {lon:.5f}'
                ),
                'type':   'success' if is_online else 'warning',
                'sticky': False,
            },
        }
