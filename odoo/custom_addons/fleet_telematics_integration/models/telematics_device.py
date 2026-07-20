"""models/telematics_device.py

จัดการ GPS Device (UC-01): เก็บรายชื่อ Device ที่ดึงมาจาก Backend
(GET /api/v1/devices) และลงทะเบียน Device ใหม่ผูกกับรถผ่าน Backend API
(POST /api/v1/config_device/register แบบทีละตัว หรือ .../register/batch
แบบเป็นชุด)
"""

import logging
import re

import requests

from odoo import models, fields, api
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

# รูปแบบ Device ID ที่ระบบยอมรับ: KTC- ตามด้วยตัวเลขอย่างน้อย 3 หลัก
_DEVICE_ID_RE = re.compile(r'^KTC-\d{3,}$')


class TelematicsDevice(models.Model):
    """Device 1 ตัวต่อ 1 record ผูกกับรถ 1 คัน (required)."""

    _name = 'fleet.telematics.device'
    _description = 'Telematics Device'
    _rec_name = 'device_id'
    _order = 'device_id'

    # UNIQUE constraint ระดับฐานข้อมูลจริง (Odoo 19 ใช้ models.Constraint
    # แทน list-of-tuple _sql_constraints แบบเดิม)
    _device_id_unique = models.Constraint(
        'UNIQUE(device_id)',
        'รหัส Device นี้ถูกลงทะเบียนไว้แล้ว',
    )

    config_id = fields.Many2one(
        'fleet.telematics.config',
        string='Config',
        ondelete='cascade',
    )

    device_id = fields.Char(
        string='Device ID',
        required=True,
        index=True,
        help='รหัส GPS Device รูปแบบ KTC-XXX เช่น KTC-001',
    )

    device_name = fields.Char(
        string='Device Name',
        required=True,
        help='ชื่อเรียก Device สำหรับแสดงผล เช่น "Device 1"',
    )

    # Backend บังคับให้ระบุ vehicle_id เสมอตอนลงทะเบียน (ไม่รองรับ null) —
    # จึงตั้ง required=True ที่นี่ด้วยเพื่อให้ตรงกับข้อบังคับฝั่ง Backend
    # ตั้งแต่ระดับ Odoo, และใช้ ondelete='restrict' แทน 'set null' เพราะ
    # field required ใช้ set null ไม่ได้ (จะ violate NOT NULL ตอนลบรถที่ผูกอยู่)
    vehicle_id = fields.Many2one(
        'fleet.vehicle',
        string='Vehicle',
        required=True,
        ondelete='restrict',
        help='รถที่ผูกกับ Device นี้ใน Odoo — Backend บังคับต้องระบุเสมอ '
             '(ลงทะเบียน device ก่อนแล้วผูกรถทีหลังยังทำไม่ได้ในปัจจุบัน)',
    )

    active = fields.Boolean(string='Active', default=True)
    available = fields.Boolean(string='Available', default=True)

    registered_at = fields.Datetime(
        string='Registered At (Backend)',
        readonly=True,
        help='วันเวลาที่ Backend ยืนยันการลงทะเบียนสำเร็จ',
    )

    date_update_latest = fields.Datetime(
        string='Last Updated (Backend)',
        readonly=True,
    )

    synced_at = fields.Datetime(
        string='Synced At',
        readonly=True,
    )

    register_status = fields.Selection(
        [('draft', 'ยังไม่ลงทะเบียน'),
         ('registered', 'ลงทะเบียนแล้ว'),
         ('error', 'ลงทะเบียนไม่สำเร็จ')],
        string='สถานะการลงทะเบียน',
        default='draft',
    )

    last_error = fields.Text(string='Last Error', readonly=True)

    @api.constrains('device_id')
    def _check_device_id_format(self):
        """บังคับรูปแบบ Device ID ให้เป็น KTC-XXX (ตัวพิมพ์ใหญ่, เลขอย่างน้อย 3 หลัก).

        Raises:
            UserError: ถ้า device_id ไม่ตรงรูปแบบที่กำหนด
        """
        for rec in self:
            if rec.device_id and not _DEVICE_ID_RE.match(rec.device_id.upper()):
                raise UserError(
                    'รูปแบบ Device ID ไม่ถูกต้อง ต้องเป็น KTC-XXX (ตัวพิมพ์ใหญ่) '
                    'เช่น KTC-001 — ที่กรอกมา: %s' % rec.device_id
                )

    def action_register_device(self):
        """ลงทะเบียน Device นี้กับ Backend ทีละตัว.

        เรียก POST /api/v1/config_device/register พร้อม device_id,
        device_name, vehicle_id แล้วอัปเดตสถานะตามผลลัพธ์:
          - HTTP 201: ลงทะเบียนสำเร็จ → register_status = 'registered'
          - HTTP 409: ชนกัน (device/รถถูกผูกไว้แล้วในอีก 3 รูปแบบที่ Backend
            enforce) → แจ้งผู้ใช้ให้ไปใช้ปุ่ม "Update Vehicle Config" แทน
          - อื่นๆ: ถือว่า error ทั้งหมด

        Raises:
            UserError: เมื่อเชื่อมต่อ Backend ไม่ได้ หรือ Backend ตอบ error
        """
        self.ensure_one()
        Config = self.env['fleet.telematics.config']
        api_url = Config.get_active_api_url()
        api_key = Config.get_active_api_key()
        if not api_url:
            raise UserError('กรุณาตั้งค่า API URL ของ Backend ใน Settings ก่อน')

        payload = {
            'device_id': (self.device_id or '').upper(),
            'device_name': self.device_name,
            'vehicle_id': self.vehicle_id.id if self.vehicle_id else None,
        }

        try:
            resp = requests.post(
                f'{api_url}/api/v1/config_device/register',
                json=payload,
                headers={'APIKEY': api_key},
                timeout=15,
            )
        except requests.RequestException as e:
            self.write({'register_status': 'error', 'last_error': str(e)})
            raise UserError(f'เชื่อมต่อ Backend ไม่สำเร็จ: {e}')

        if resp.status_code == 201:
            data = resp.json()
            self.write({
                'register_status': 'registered',
                'registered_at': data.get('registered_at') and
                    data['registered_at'].replace('T', ' ')[:19],
                'last_error': False,
            })
            return True

        if resp.status_code == 409:
            # Backend enforce ความชนกัน 3 รูปแบบ: device ผูกกับรถเดิมอยู่แล้ว /
            # ผูกกับรถอื่นอยู่แล้ว / รถมี device อื่นผูกอยู่แล้ว
            try:
                msg = resp.json().get('message', 'Device/Vehicle ถูกผูกไว้แล้ว')
            except ValueError:
                msg = 'Device/Vehicle ถูกผูกไว้แล้ว'
            self.write({'register_status': 'error', 'last_error': msg})
            raise UserError(
                f'ไม่สามารถลงทะเบียนได้ (409): {msg}\n'
                'หากต้องการเปลี่ยนรถที่ผูกกับ Device นี้ ให้ใช้ปุ่ม '
                '"Update Vehicle Config" ที่หน้า fleet.vehicle แทน'
            )

        # error อื่นๆ ที่ไม่ใช่ 201/409
        self.write({'register_status': 'error', 'last_error': resp.text[:500]})
        raise UserError(f'Backend ตอบกลับผิดพลาด (HTTP {resp.status_code}): {resp.text[:300]}')

    @api.model
    def action_register_devices_batch(self, device_recs):
        """ลงทะเบียน Device หลายตัวพร้อมกันในคำขอเดียว.

        เรียก POST /api/v1/config_device/register/batch ด้วยรายการ device
        ทั้งหมดใน device_recs แล้วตั้งสถานะเป็น 'registered' ให้ทุกตัวถ้า
        Backend ตอบสำเร็จ (ไม่มีการแยกผลลัพธ์รายตัวในเวอร์ชันนี้)

        Args:
            device_recs (recordset): fleet.telematics.device ที่จะลงทะเบียน

        Returns:
            dict: response JSON จาก Backend

        Raises:
            UserError: เมื่อเชื่อมต่อ Backend ไม่ได้ หรือ Backend ตอบ error
        """
        Config = self.env['fleet.telematics.config']
        api_url = Config.get_active_api_url()
        api_key = Config.get_active_api_key()
        if not api_url:
            raise UserError('กรุณาตั้งค่า API URL ของ Backend ใน Settings ก่อน')

        payload = {
            'devices': [
                {
                    'device_id': (d.device_id or '').upper(),
                    'device_name': d.device_name,
                    'vehicle_id': d.vehicle_id.id if d.vehicle_id else None,
                }
                for d in device_recs
            ]
        }

        try:
            resp = requests.post(
                f'{api_url}/api/v1/config_device/register/batch',
                json=payload,
                headers={'APIKEY': api_key},
                timeout=30,
            )
        except requests.RequestException as e:
            raise UserError(f'เชื่อมต่อ Backend ไม่สำเร็จ: {e}')

        if resp.status_code not in (200, 201):
            raise UserError(f'Backend ตอบกลับผิดพลาด (HTTP {resp.status_code}): {resp.text[:300]}')

        device_recs.write({'register_status': 'registered'})
        return resp.json()
