import logging
import requests
from datetime import datetime, timezone

from odoo import models, fields, api
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class FleetVehicleExt(models.Model):
    _inherit = 'fleet.vehicle'

    # === ส่วนที่ 1: ฟิลด์ Telematics ที่เพิ่มเข้าไปใน fleet.vehicle ===
    # ขยาย model รถ Odoo เดิม โดยเพิ่มข้อมูล GPS, สถานะออนไลน์ และสถิติจาก MTD
    telematics_device_id = fields.Char(
        string='Device ID',
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

    # === ส่วนที่ 2: สถิติสะสมของรถ ===
    # รวม trip ทั้งหมด, ระยะทางรวม และคะแนนเฉลี่ยของคนขับที่ใช้รถคันนี้
    total_trips       = fields.Integer(string='Total Trips',       default=0)
    total_distance_km = fields.Float(string='Total Distance (km)', digits=(10, 2), default=0.0)
    avg_driver_score  = fields.Float(string='Avg Driver Score',    digits=(5, 2),  default=0.0)

    telematics_log_ids = fields.One2many(
        'fleet.telematics.log', 'vehicle_id', string='Trip Logs')

    # === ส่วนที่ 3: ปุ่ม "เช็คสถานะรถ" — ดึงตำแหน่ง GPS ล่าสุดแบบ On-demand ===
    # เรียก GET /api/v1/vehicles/{id}/location จาก MTD → อัปเดตพิกัด ความเร็ว สถานะออนไลน์
    def action_check_vehicle_status(self):
        self.ensure_one()
        ICP = self.env['ir.config_parameter'].sudo()
        api_url = ICP.get_param('fleet_telematics.mtd_api_url', '').rstrip('/')
        api_key = ICP.get_param('fleet_telematics.mtd_api_key', '')

        if not api_url or not api_key:
            raise UserError(
                'ยังไม่ได้ตั้งค่า MTD API\n'
                'Settings → Technical → System Parameters\n'
                'เพิ่ม fleet_telematics.mtd_api_url และ fleet_telematics.mtd_api_key'
            )

        # === ส่วนที่ 4: เรียก MTD API ดึงข้อมูลตำแหน่งปัจจุบันของรถ ===
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

        # === ส่วนที่ 5: แปลง JSON Response → Odoo Fields ===
        # แปลง ISO timestamp เป็น naive UTC datetime และตัดสิน online_status
        # จาก ignition หรือ speed > 0 (MTD ไม่มี field "online" โดยตรง)
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

        # === ส่วนที่ 6: แสดงผล Notification หลังอัปเดตสถานะสำเร็จ ===
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
