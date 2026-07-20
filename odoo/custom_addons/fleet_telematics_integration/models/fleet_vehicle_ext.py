"""models/fleet_vehicle_ext.py

ขยายโมเดล fleet.vehicle เพิ่มความสามารถทั้งหมดที่เกี่ยวกับ Telematics:
  - ผูก/ลงทะเบียน GPS Device กับรถ (UC-01) และเช็คว่าตรงกับ Backend จริงไหม
  - ส่งข้อมูลรถไปอัปเดตที่ Backend เมื่อเปลี่ยนบอร์ด/คนขับ (PUT /config/vehicle)
  - ดึงพิกัด/ความเร็ว real-time และประวัติทริปของรถคันนั้นจาก Backend
  - เก็บสถิติสะสม (จำนวนทริป, ระยะทาง, คะแนนเฉลี่ย, ชั่วโมงเดินเครื่อง)
"""
import logging
import requests
from datetime import datetime, timezone

from odoo import models, fields, api
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)


class FleetVehicleExt(models.Model):
    """ส่วนขยายของ fleet.vehicle สำหรับฟีเจอร์ Telematics ทั้งหมด."""

    _inherit = 'fleet.vehicle'

    # ── ฟิลด์ผูก/ลงทะเบียน GPS Device ────────────────────────────
    telematics_device_id = fields.Char(
        string='GPS Device ID',
        help='รหัสกล่อง GPS เช่น KTC-001 — ต้องตรงกับ device_id ใน Backend'
    )

    # ระบบลงทะเบียน Device อยู่ในหน้ารถโดยตรง (ไม่แยกเป็นเมนู "Devices"
    # ต่างหาก เพื่อไม่ให้มี 2 ทางผูก Device กับรถพร้อมกันจนข้อมูลขัดแย้งกัน)
    telematics_device_name = fields.Char(
        string='Device Name',
        help='ชื่อเรียก Device สำหรับแสดงผล (ส่งไป Backend ตอนลงทะเบียนครั้งแรก)'
    )
    telematics_register_status = fields.Selection(
        [('draft', 'ยังไม่ลงทะเบียน'),
         ('registered', 'ลงทะเบียนแล้ว'),
         ('error', 'ลงทะเบียนไม่สำเร็จ')],
        string='สถานะการลงทะเบียน Device',
        default='draft', readonly=True,
    )
    telematics_registered_at = fields.Datetime(
        string='Registered At (Backend)', readonly=True,
    )
    telematics_register_error = fields.Text(string='Register Error', readonly=True)

    # แสดงเลข ID ของคนขับแบบตัวเลขชัดๆ เทียบกับที่ Backend ใช้เป็น driver_id
    # ในรายงานต่างๆ (เช่น JSON ของ /drivers/{id}/bonus คืน "driver_id":
    # "12" — เลขนี้คือ id ของ hr.employee ใน Odoo ตรงๆ)
    driver_backend_id = fields.Integer(
        string='Driver ID (สำหรับเทียบกับ Backend)',
        compute='_compute_driver_backend_id',
        help='เลข ID ของพนักงานคนขับใน Odoo — ตรงกับค่า driver_id ที่ Backend '
             'ใช้อ้างอิงในรายงานต่างๆ (Driver Score, Bonus, Fuel Summary)'
    )

    @api.depends('driver_id')
    def _compute_driver_backend_id(self):
        """สะท้อนค่า driver_id.id ลง field แบบตรงๆ (0 ถ้าไม่มีคนขับผูกอยู่)."""
        for rec in self:
            rec.driver_backend_id = rec.driver_id.id if rec.driver_id else 0

    # จดจำบอร์ดเดิมอัตโนมัติผ่าน write() hook (ดู write() ด้านล่าง) ใช้เป็น
    # old_device_id เมื่อยิง PUT /api/v1/config/vehicle ตอนเปลี่ยนบอร์ด
    previous_device_id = fields.Char(
        string='Previous Device ID',
        readonly=True,
        help='รหัสบอร์ดก่อนการเปลี่ยนครั้งล่าสุด — ระบบบันทึกอัตโนมัติ'
    )

    # ── Verify Device: เทียบสดว่า device ที่ผูกใน Odoo ตรงกับ Backend ──
    device_verified_at = fields.Datetime(
        string='Device Verified At',
        readonly=True,
        help='เวลาที่ตรวจสอบข้อมูล Device กับ Backend ล่าสุด (GET /vehicles/{id}/device)'
    )
    device_verify_mismatch = fields.Boolean(
        string='Device Mismatch',
        readonly=True,
        help='True ถ้า device_id ที่ Backend บันทึกไว้ไม่ตรงกับ Odoo',
    )
    device_verify_note = fields.Text(
        string='Device Verify Note',
        readonly=True,
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

    # ── สถิติสะสม ─────────────────────────────────────────────────
    total_trips        = fields.Integer(string='Total Trips',        default=0)
    total_distance_km  = fields.Float(string='Total Distance (km)',  digits=(10, 2), default=0.0)
    avg_driver_score   = fields.Float(string='Avg Driver Score',     digits=(5,  2), default=0.0)
    # ตาม FDD §2.2 ต้องมี maintenance trigger 3 รูปแบบ (ระยะทาง / ชั่วโมง
    # เดินเครื่อง / ช่วงเวลา) — ฟิลด์นี้คือฐานของ trigger ชั่วโมงเดินเครื่อง
    # สะสมจาก duration_min ของทุกทริปที่ sync แล้ว
    telematics_engine_hours = fields.Float(
        string='Engine Hours (สะสม)', digits=(10, 2), default=0.0,
        help='ชั่วโมงเดินเครื่องสะสม รวมจาก duration_min ของทุกทริปที่ sync แล้ว '
             '— ใช้เป็น Trigger ที่ 2 ของการแจ้งเตือนซ่อมบำรุง (FDD §2.2)')
    telematics_log_ids = fields.One2many(
        'fleet.telematics.log', 'vehicle_id', string='Trip Logs'
    )

    def _get_api_credentials(self):
        """คืน (api_url, api_key) ของ Backend ที่ตั้งค่าไว้ปัจจุบัน.

        Raises:
            UserError: ถ้ายังไม่ได้ตั้งค่า Backend API URL
        """
        Config = self.env['fleet.telematics.config']
        api_url = Config.get_active_api_url()
        api_key = Config.get_active_api_key()
        if not api_url:
            raise UserError(
                'ยังไม่ได้ตั้งค่า Backend API URL\n'
                'ไปที่ Fleet Telematics → Settings แล้วกรอก API URL'
            )
        return api_url, api_key

    def action_register_device(self):
        """ลงทะเบียน GPS Device ครั้งแรกกับ Backend (POST /config_device/register).

        ใช้คู่กับ action_sync_to_backend() (PUT /config/vehicle):
          - ครั้งแรกที่ Device ยังไม่เคยลงทะเบียนเลย → ใช้เมธอดนี้ (Register)
          - หลังจากนั้นถ้าจะย้าย Device ไปผูกรถคันอื่น/เปลี่ยนบอร์ด →
            ใช้ action_sync_to_backend() (Push to Backend) แทน

        Raises:
            UserError: ถ้ายังไม่กรอก Device ID/Name, เชื่อมต่อ Backend ไม่ได้,
                หรือ Backend ตอบ error (409 = device/รถถูกผูกไว้แล้ว)
        """
        self.ensure_one()
        if not self.telematics_device_id:
            raise UserError('กรุณากรอก GPS Device ID ก่อน (รูปแบบ KTC-XXX)')
        if not self.telematics_device_name:
            raise UserError('กรุณากรอก Device Name ก่อน')

        api_url, api_key = self._get_api_credentials()

        payload = {
            'device_id': self.telematics_device_id.upper(),
            'device_name': self.telematics_device_name,
            'vehicle_id': self.id,
        }

        try:
            resp = requests.post(
                f'{api_url}/api/v1/config_device/register',
                json=payload,
                headers={'APIKEY': api_key},
                timeout=15,
            )
        except requests.RequestException as e:
            self.write({
                'telematics_register_status': 'error',
                'telematics_register_error': str(e),
            })
            raise UserError(f'เชื่อมต่อ Backend ไม่สำเร็จ: {e}')

        if resp.status_code == 201:
            data = resp.json()
            self.write({
                'telematics_register_status': 'registered',
                'telematics_registered_at': data.get('registered_at') and
                    data['registered_at'].replace('T', ' ')[:19],
                'telematics_register_error': False,
                'previous_device_id': self.telematics_device_id,
            })
            return True

        if resp.status_code == 409:
            try:
                msg = resp.json().get('message', 'Device/Vehicle ถูกผูกไว้แล้ว')
            except ValueError:
                msg = 'Device/Vehicle ถูกผูกไว้แล้ว'
            self.write({
                'telematics_register_status': 'error',
                'telematics_register_error': msg,
            })
            raise UserError(
                f'ไม่สามารถลงทะเบียนได้ (409): {msg}\n'
                'ถ้า Device นี้เคยลงทะเบียนกับรถคันอื่นมาก่อน ให้ใช้ปุ่ม '
                '"Push to Backend" แทน (จะยิง PUT /config/vehicle เพื่อย้ายการผูกแทน)'
            )

        self.write({
            'telematics_register_status': 'error',
            'telematics_register_error': resp.text[:500],
        })
        raise UserError(f'Backend ตอบกลับผิดพลาด (HTTP {resp.status_code}): {resp.text[:300]}')

    @api.constrains('license_plate', 'telematics_device_id')
    def _check_duplicate_vehicle(self):
        """ห้ามมีรถทะเบียนซ้ำ หรือ Device เดียวกันผูกกับรถหลายคันพร้อมกัน.

        Raises:
            ValidationError: ถ้าพบทะเบียนรถหรือ Device ID ซ้ำกับรถคันอื่น
        """
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

    def write(self, vals):
        """เขียนค่าปกติ พร้อมจดจำ telematics_device_id เดิมไว้ใน
        previous_device_id โดยอัตโนมัติ ถ้าค่ากำลังจะถูกเปลี่ยน.

        ทำในการ write() ครั้งเดียว (ไม่แยก write ซ้อน) เพื่อไม่ trigger
        constrains สองรอบและไม่มี race condition — บันทึก previous เฉพาะ
        รถที่มีค่าเดิมอยู่และค่ากำลังจะเปลี่ยนจริงเท่านั้น
        """
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
            # รถที่ไม่มีค่าเดิม (บอร์ดใหม่) หรือค่าไม่เปลี่ยน → write ตามปกติ
            remaining = self.filtered(
                lambda r: not (r.telematics_device_id and
                               r.telematics_device_id != new_val)
            )
            if remaining:
                return super(FleetVehicleExt, remaining).write(vals)
            return True
        return super().write(vals)

    def action_sync_to_backend(self):
        """ส่งข้อมูลรถ (device/driver ปัจจุบัน) ไปอัปเดตที่ Backend
        (PUT /api/v1/config/vehicle) — ใช้ตอนเปลี่ยนบอร์ดหรือย้าย Device
        ไปผูกรถคันอื่น (ต่างจาก action_register_device ที่ใช้ตอนลงทะเบียน
        ครั้งแรก).

        ส่ง old_device_id (จาก previous_device_id ที่ write() จดจำไว้ให้
        อัตโนมัติ) คู่กับ new_device_id เพื่อให้ Backend รู้ว่าเป็นการย้าย
        บอร์ดจากอันไหนไปอันไหน พร้อมส่ง driver_id ปัจจุบันไปด้วยเสมอ

        หลัง PUT สำเร็จ: อัปเดต previous_device_id = new_device_id ทันที
        เพื่อให้พร้อมสำหรับการเปลี่ยนบอร์ดครั้งถัดไป

        Returns:
            dict: action แสดง notification สำเร็จ

        Raises:
            UserError: ถ้ายังไม่ได้กรอก Device ID หรือส่งไป Backend ไม่สำเร็จ
        """
        self.ensure_one()

        if not self.telematics_device_id:
            raise UserError('กรุณาระบุ GPS Device ID ในแท็บ Telematics ก่อน')

        api_url, api_key = self._get_api_credentials()

        new_device = self.telematics_device_id or ''
        old_device = self.previous_device_id or None  # None ถ้าเป็นบอร์ดใหม่

        # driver_id ส่งเป็น 0 ถ้าไม่มีคนขับผูกอยู่ (ไม่ใช่ null) ตาม schema
        # ของ Backend
        payload = {
            'vehicle_id':    int(self.id),
            'new_device_id': new_device,
            'old_device_id': old_device,   # None หรือ str
            'driver_id':     self.driver_id.id if self.driver_id else 0,
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

    def action_check_vehicle_status(self):
        """ดึงตำแหน่ง GPS และสถานะปัจจุบันของรถคันนี้จาก Backend real-time
        (GET /api/v1/vehicles/{id}/location) แล้วอัปเดตฟิลด์ในฟอร์ม.

        อัปเดต telematics_device_id ตาม response ของ Backend ด้วย เผื่อ
        กรณี Backend สลับบอร์ด — หน้าจอ Odoo จะอัปเดตตามอัตโนมัติ

        Returns:
            dict: action แสดง notification สรุปสถานะรถ (online/offline,
            พิกัด, ความเร็ว, ignition)

        Raises:
            UserError: ถ้าเรียก Backend ไม่สำเร็จ
        """
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

    def action_verify_device(self):
        """เทียบ device_id ที่ Backend ผูกกับรถคันนี้จริง กับ
        telematics_device_id ที่บันทึกไว้ใน Odoo (GET /vehicles/{id}/device).

        ต่างจาก action_check_vehicle_status (ดึงพิกัด/ความเร็ว real-time)
        ตัวนี้เช็คเฉพาะ "ความถูกต้องของการผูก device" เท่านั้น

        Returns:
            dict: action แสดง notification ผลการเทียบ

        Raises:
            UserError: ถ้าเรียก Backend ไม่สำเร็จ หรือ Backend ไม่รู้จักรถ
                คันนี้เลย (404 — ถือเป็น mismatch เสมอ)
        """
        self.ensure_one()

        api_url, api_key = self._get_api_credentials()

        try:
            resp = requests.get(
                f'{api_url}/api/v1/vehicles/{self.id}/device',
                headers={'APIKEY': api_key},
                timeout=10,
            )
        except requests.RequestException as e:
            self.write({
                'device_verified_at':     fields.Datetime.now(),
                'device_verify_mismatch': True,
                'device_verify_note':     f'เรียก Backend ไม่สำเร็จ: {e}',
            })
            raise UserError(f'ตรวจสอบ Device ไม่สำเร็จ — เรียก Backend ไม่ได้:\n{e}')

        if resp.status_code == 404:
            # Backend ไม่รู้จักรถคันนี้เลย (ยังไม่เคย register หรือถูกลบไปแล้ว)
            # 404 คือสัญญาณว่าไม่ตรงกันเสมอ ไม่ว่า Odoo จะมีค่า
            # telematics_device_id อยู่หรือไม่ก็ตาม จึง flag mismatch=True
            # ตรงๆ ไม่มีเงื่อนไข
            mismatch = True
            note = (
                'Backend ไม่มีข้อมูล Device ผูกกับรถคันนี้ '
                f'(Odoo บันทึกไว้ว่า: {self.telematics_device_id or "-"})'
            )
            self.write({
                'device_verified_at':     fields.Datetime.now(),
                'device_verify_mismatch': mismatch,
                'device_verify_note':     note,
            })
            raise UserError(f'⚠️ {note}')

        resp.raise_for_status()
        data = resp.json()

        backend_device_id = (data.get('device_id') or '').strip()
        odoo_device_id     = (self.telematics_device_id or '').strip()
        mismatch = backend_device_id.upper() != odoo_device_id.upper()

        last_update = data.get('date_update_latest') or data.get('registered_at')

        note = (
            f'Odoo: {odoo_device_id or "-"}  |  Backend: {backend_device_id or "-"}'
            + (f'  |  อัปเดตล่าสุด (Backend): {last_update}' if last_update else '')
        )

        self.write({
            'device_verified_at':     fields.Datetime.now(),
            'device_verify_mismatch': mismatch,
            'device_verify_note':     note,
        })

        _logger.info(
            'action_verify_device: vehicle_id=%s odoo=%s backend=%s mismatch=%s',
            self.id, odoo_device_id, backend_device_id, mismatch,
        )

        return {
            'type': 'ir.actions.client',
            'tag':  'display_notification',
            'params': {
                'title':   '⚠️ Device ไม่ตรงกัน!' if mismatch else '✅ Device ตรงกัน',
                'message': note,
                'type':    'danger' if mismatch else 'success',
                'sticky':  mismatch,
            },
        }

    def get_trip_history(self, page=1, limit=20,
                         date_from=None, date_to=None, synced_only=None):
        """ดึงประวัติทริปของรถคันนี้จาก Backend (GET /vehicles/{id}/trips).

        Path param vehicle_id คือ Odoo record ID ของรถ (integer) ไม่ใช่
        device_id (KTC-XXX)

        Args:
            page (int): หน้าที่ต้องการ
            limit (int): จำนวนต่อหน้า (Backend จำกัดสูงสุด 200)
            date_from (str, optional): กรองตั้งแต่วันที่ (ISO8601)
            date_to (str, optional): กรองถึงวันที่ (ISO8601)
            synced_only (bool, optional): กรองเฉพาะที่ sync เข้า Odoo แล้ว

        Returns:
            dict: {total, page, limit, total_pages, trips: [...]}

        Raises:
            requests.HTTPError: ถ้า Backend ตอบ error
        """
        self.ensure_one()

        api_url, api_key = self._get_api_credentials()

        # Path param คือ Odoo vehicle.id (int)
        url = f'{api_url}/api/v1/vehicles/{self.id}/trips'

        params = {'page': page, 'limit': min(limit, 200)}
        if date_from:
            params['date_from'] = date_from
        if date_to:
            params['date_to'] = date_to
        if synced_only is not None:
            params['synced_only'] = 'true' if synced_only else 'false'

        _logger.info('get_trip_history: GET %s params=%s', url, params)

        resp = requests.get(
            url,
            headers={'APIKEY': api_key} if api_key else {},
            params=params,
            timeout=30,
        )
        resp.raise_for_status()

        data = resp.json()
        if isinstance(data, list):
            return {'trips': data, 'total': len(data)}
        return data  # คืน dict เต็ม {total, page, limit, total_pages, trips}

    def action_view_vehicle_trips(self):
        """ปุ่มดู Trip History จาก Backend ในแท็บ Telematics Settings —
        เรียก get_trip_history() แล้วสรุปผลเป็น notification สั้นๆ
        (ดูรายละเอียดเต็มได้ที่เมนู Trip Logs)."""
        self.ensure_one()
        try:
            result = self.get_trip_history(limit=20, synced_only=False)
            trips  = result.get('trips', []) if isinstance(result, dict) else result
            total  = result.get('total', len(trips)) if isinstance(result, dict) else len(trips)
            pages  = result.get('total_pages', 1) if isinstance(result, dict) else 1
            return {
                'type':   'ir.actions.client',
                'tag':    'display_notification',
                'params': {
                    'title':   f'Trip History (Backend) — {self.name}',
                    'message': (
                        f'Backend มี {total} ทริป ({pages} หน้า)\n'
                        f'แสดง {len(trips)} รายการแรก\n'
                        f'ดูรายละเอียดครบที่เมนู Trip Logs'
                    ),
                    'type': 'info',
                },
            }
        except Exception as e:
            raise UserError(f'ดึงประวัติ trip ไม่สำเร็จ: {e}')


class FleetVehicleLogServicesExt(models.Model):
    """ขยายโมเดล fleet.vehicle.log.services (core Odoo) เพิ่ม snapshot
    ของชั่วโมงเดินเครื่องสะสม ณ ตอนที่ทำ service แต่ละครั้ง — ใช้เป็นจุด
    อ้างอิงเทียบ trigger ชั่วโมงเดินเครื่องของรอบซ่อมบำรุงถัดไป
    (ดู models/telematics_log.py: _update_odometer_and_check_maintenance).
    """

    _inherit = 'fleet.vehicle.log.services'

    engine_hours_at_service = fields.Float(
        string='Engine Hours (ตอน Service)', digits=(10, 2),
        help='ชั่วโมงเดินเครื่องสะสมของรถคันนี้ ณ ตอนที่ทำ service ครั้งนี้ '
             '— ใช้เทียบ Trigger ชั่วโมงเดินเครื่องของรอบซ่อมบำรุงถัดไป')