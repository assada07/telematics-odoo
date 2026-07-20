"""models/telematics_config.py

ตั้งค่าการเชื่อมต่อกับ Backend API (URL, API Key) ในที่เดียว — เป็น
singleton (record เดียวในระบบ, บล็อกการสร้างซ้ำผ่าน create()) พร้อม:
  - ทดสอบการเชื่อมต่อและจดจำ URL ล่าสุดที่ใช้งานได้ (action_save_and_test)
  - helper ให้โมเดลอื่นดึง URL/Key ที่ใช้งานได้จริง (get_active_api_url/key)
  - Device Reconciliation: เทียบ device ที่ผูกไว้ใน Odoo กับที่ Backend
    บันทึกจริง เพื่อจับความไม่ตรงกัน (action_reconcile_devices)
"""

import logging
import requests

from odoo import models, fields, api
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

_PARAM_URL           = 'fleet_telematics.api_url_input'
_PARAM_CONFIRMED_URL = 'fleet_telematics.last_confirmed_url'
_PARAM_API_KEY       = 'fleet_telematics.mtd_api_key'
_PARAM_API_URL       = 'fleet_telematics.mtd_api_url'   # compat เดิม


class TelematicsConfig(models.Model):
    """ค่าตั้งต้นการเชื่อมต่อ Backend — มีได้เพียง record เดียวในระบบ."""

    _name = 'fleet.telematics.config'
    _description = 'Fleet Telematics Configuration'
    _rec_name = 'name'

    # ── ฟิลด์ตั้งค่าการเชื่อมต่อ ─────────────────────────────────
    name = fields.Char(
        string='Config Name',
        default='Fleet Telematics Settings'
    )

    api_url = fields.Char(
        string='API URL ของ Backend',
        help='API URL ของ Backend เช่น http://192.168.1.43:8001'
    )

    api_key = fields.Char(
        string='API Key',
        help='Bearer token / APIKEY สำหรับยืนยันตัวตน'
    )

    # API URL ล่าสุดที่ทดสอบแล้วใช้งานได้จริง — ระบบจดจำให้อัตโนมัติทุก
    # ครั้งที่ action_save_and_test() ทดสอบสำเร็จ
    last_confirmed_url = fields.Char(
        string='API URL ล่าสุดที่ใช้งานได้',
        readonly=True,
        help='ระบบบันทึก URL นี้อัตโนมัติเมื่อทดสอบการเชื่อมต่อสำเร็จ'
    )

    # ── System Health Dashboard ──────────────────────────────────
    connection_status = fields.Selection([
        ('untested', '⚪ Untested'),
        ('ready',    '🟢 Ready'),
        ('error',    '🔴 Error'),
    ],
        string='Connection Status',
        default='untested',
        readonly=True
    )

    # หมายเหตุ: fields.Datetime ของ Odoo เก็บค่าใน DB เป็น UTC เสมอ ใช้
    # fields.Datetime.now() เขียนค่าลงฟิลด์นี้ — การแปลงไปแสดงตาม timezone
    # ของผู้ใช้เป็นหน้าที่ของ Odoo ตอน render UI ให้อัตโนมัติอยู่แล้ว
    last_test_at = fields.Datetime(string='Last Tested At', readonly=True)
    last_sync_at = fields.Datetime(string='Last Synced At', readonly=True)
    last_error   = fields.Text(string='Last Error',         readonly=True)

    # ── ผลลัพธ์ล่าสุดของ Device Reconciliation ───────────────────
    # (เทียบ device ที่ผูกไว้ใน Odoo กับที่ Backend บันทึกจริง — ป้องกัน
    # กรณีมีคนไป register/แก้ device ตรงที่ Backend โดยตรงไม่ผ่าน Odoo
    # แล้วข้อมูลไม่ตรงกันแบบเงียบๆ โดยไม่มีใครรู้)
    last_reconciled_at   = fields.Datetime(string='Last Device Reconcile At', readonly=True)
    device_mismatch_count = fields.Integer(string='Device Mismatch Found', readonly=True)
    device_mismatch_note  = fields.Text(string='Device Mismatch Detail', readonly=True)

    @staticmethod
    def _normalize_url(raw):
        """เติม scheme (http://) ให้ input ที่ผู้ใช้กรอกมา ถ้ายังไม่มี.

        Args:
            raw (str): URL หรือ IP/hostname ดิบที่ผู้ใช้กรอก

        Returns:
            str: URL เต็มรูปแบบ (ไม่มี trailing slash) หรือ '' ถ้า input ว่าง
        """
        raw = (raw or '').strip()
        if not raw:
            return ''
        if raw.startswith('http://') or raw.startswith('https://'):
            return raw.rstrip('/')
        return f'http://{raw}'.rstrip('/')

    @api.model_create_multi
    def create(self, vals_list):
        """สร้าง record ได้เฉพาะครั้งแรกของระบบเท่านั้น (บังคับ singleton).

        บล็อกการสร้างเรคคอร์ดใหม่จากทุกช่องทาง (ปุ่ม New, RPC, import
        CSV/XLSX, หรือโค้ดโมดูลอื่นที่เรียก .create() ตรงๆ) ยกเว้นกรณี
        "สร้างเรคคอร์ดแรกของระบบ" ที่มาจาก server action
        action_open_telematics_config ซึ่งต้องส่ง context key
        'allow_telematics_config_create=True' มาด้วยเท่านั้น

        Raises:
            UserError: ถ้าพยายามสร้างโดยไม่มี context flag ที่อนุญาต
        """
        if not self.env.context.get('allow_telematics_config_create'):
            raise UserError(
                'ไม่อนุญาตให้สร้างเรคคอร์ด Fleet Telematics Config เพิ่ม '
                '(Database Lockdown)\n'
                'ระบบนี้อนุญาตให้มีค่าตั้งค่าได้เพียง 1 รายการในระบบเท่านั้น '
                'กรุณาเปิดเมนู "Fleet Telematics Settings" แล้วแก้ไข (Edit) '
                'เรคคอร์ดที่มีอยู่แทนการสร้างใหม่'
            )
        return super().create(vals_list)

    def default_get(self, fields_list):
        """โหลดค่าปัจจุบันจาก ir.config_parameter มาตั้งเป็นค่าเริ่มต้นของ
        ฟอร์ม (ใช้ตอนเปิดฟอร์ม 'New' — ให้ผู้ใช้เห็นค่าที่ตั้งไว้ล่าสุดเสมอ
        แทนที่จะเป็นฟอร์มว่างเปล่า)."""
        res = super().default_get(fields_list)
        ICP = self.env['ir.config_parameter'].sudo()

        stored_url = ICP.get_param(_PARAM_URL, '') \
                     or ICP.get_param(_PARAM_API_URL, '')  # fallback compat

        res.update({
            'api_url':           stored_url,
            'api_key':           ICP.get_param(_PARAM_API_KEY, ''),
            'last_confirmed_url': ICP.get_param(_PARAM_CONFIRMED_URL, ''),
        })
        return res

    def action_save_and_test(self):
        """บันทึกค่า API URL/Key ลง ir.config_parameter แล้วทดสอบเชื่อมต่อทันที.

        ขั้นตอน:
          1. normalize URL (เติม scheme ถ้าจำเป็น) แล้วบันทึกทั้ง URL ดิบ
             และ URL ที่ normalize แล้วลง ir.config_parameter
          2. ยิง GET /api/v1/devices ทดสอบว่าเชื่อมต่อได้จริง
          3. สำเร็จ: ตั้ง connection_status='ready', จดจำ URL นี้เป็น
             last_confirmed_url, แสดง notification สำเร็จ
          4. ล้มเหลว: ตั้ง connection_status='error', บันทึก error, แจ้ง
             ผู้ใช้ผ่าน UserError พร้อมบอกว่าระบบจะ fallback ไปใช้ URL
             ล่าสุดที่เคยเชื่อมต่อได้ (ถ้ามี)

        ใช้ write() ทับเรคคอร์ดปัจจุบันเสมอ (self มาจาก Form View ที่เปิด
        อยู่โดยตรง) ไม่สร้างแถวใหม่ซ้ำ

        Returns:
            dict: action แสดง notification สำเร็จ

        Raises:
            UserError: ถ้ายังไม่กรอก API URL หรือเชื่อมต่อไม่สำเร็จ
        """
        self.ensure_one()  # ป้องกันการเรียกพร้อมกันหลายรายการ

        raw_url = (self.api_url or '').strip()
        if not raw_url:
            raise UserError('กรุณาระบุ API URL ของ Backend ก่อน')

        api_url = self._normalize_url(raw_url)
        api_key = self.api_key or ''

        # ── บันทึกค่าลง ir.config_parameter ──
        ICP = self.env['ir.config_parameter'].sudo()
        ICP.set_param(_PARAM_URL,    raw_url)
        ICP.set_param(_PARAM_API_URL, api_url)   # compat เดิม
        ICP.set_param(_PARAM_API_KEY, api_key)

        # ── ทดสอบการเชื่อมต่อ ──
        try:
            resp = requests.get(
                f'{api_url}/api/v1/devices',
                headers={'APIKEY': api_key},
                timeout=10,
            )
            resp.raise_for_status()

            # Backend GET /api/v1/devices คืนเป็น dict {"total": N, "devices": [...]}
            # ไม่ใช่ list ตรงๆ — รองรับทั้งสองรูปแบบเผื่อ schema เปลี่ยน
            try:
                payload = resp.json()
                if isinstance(payload, dict):
                    device_count = payload.get('total')
                    if device_count is None:
                        devices_list = payload.get('devices')
                        device_count = len(devices_list) if isinstance(devices_list, list) else '-'
                elif isinstance(payload, list):
                    device_count = len(payload)
                else:
                    device_count = '-'
            except Exception:
                device_count = '-'

            # เชื่อมต่อสำเร็จ → fix URL นี้เป็น last_confirmed_url
            ICP.set_param(_PARAM_CONFIRMED_URL, raw_url)

            # write() ทับเรคคอร์ดเดิม — ไม่ create แถวใหม่
            self.write({
                'connection_status': 'ready',
                'last_test_at':      fields.Datetime.now(),
                'last_confirmed_url': raw_url,
                'last_error':        False,
            })

            _logger.info(
                'action_save_and_test: connected to %s | devices=%s',
                api_url, device_count
            )

            return {
                'type': 'ir.actions.client',
                'tag':  'display_notification',
                'params': {
                    'title':   '✅ บันทึกและเชื่อมต่อสำเร็จ',
                    'message': (
                        f'Backend ตอบกลับ {resp.status_code} | พบ {device_count} devices\n'
                        f'ระบบจดจำ API URL: {raw_url} เรียบร้อยแล้ว'
                    ),
                    'type':   'success',
                    'sticky': True,
                },
            }

        except requests.RequestException as e:
            confirmed = ICP.get_param(_PARAM_CONFIRMED_URL, '')

            # write() ทับเรคคอร์ดเดิม — ไม่ create แถวใหม่
            self.write({
                'connection_status': 'error',
                'last_test_at':      fields.Datetime.now(),
                'last_error':        str(e),
            })

            fallback_msg = (
                f'\n\n⚠️ ระบบจะใช้ API URL ล่าสุดที่เคยใช้ได้: {confirmed}'
                if confirmed else ''
            )

            raise UserError(
                f'เชื่อมต่อ Backend ไม่สำเร็จ:\n{e}{fallback_msg}'
            )

    @api.model
    def get_active_api_url(self):
        """คืน API URL ที่ใช้งานได้จริง สำหรับให้โมเดลอื่นเรียกใช้.

        ลำดับความสำคัญ: last_confirmed_url (URL ล่าสุดที่ทดสอบผ่าน) ก่อน
        ถ้าไม่มีค่อย fallback ไปใช้ api_url ปัจจุบันที่ตั้งไว้

        Returns:
            str: URL ที่ normalize แล้ว (มี scheme, ไม่มี trailing slash)
        """
        ICP = self.env['ir.config_parameter'].sudo()
        confirmed = ICP.get_param(_PARAM_CONFIRMED_URL, '').strip()
        current   = ICP.get_param(_PARAM_URL, '').strip() \
                    or ICP.get_param(_PARAM_API_URL, '').strip()
        raw = confirmed or current
        return self._normalize_url(raw)

    @api.model
    def get_active_api_key(self):
        """คืน API Key ปัจจุบันที่ตั้งค่าไว้ใน ir.config_parameter."""
        ICP = self.env['ir.config_parameter'].sudo()
        return ICP.get_param(_PARAM_API_KEY, '')

    def action_reconcile_devices(self):
        """เทียบรายการ Device ระหว่าง Odoo กับ Backend เพื่อจับความไม่ตรงกัน.

        ดึงรายการ device ทั้งหมดจาก Backend (GET /api/v1/devices) มาเทียบ
        กับรถใน Odoo ที่มี telematics_device_id (เฉพาะรถที่ผูก device ไว้
        แล้วเท่านั้น) แล้วตรวจหาความไม่ตรงกัน 3 รูปแบบ:
          1. Odoo ผูก device ไว้ แต่ Backend ไม่มี device นั้นเลย
          2. Odoo กับ Backend ผูก device เดียวกันไว้กับคนละรถ
          3. Backend มี device ที่ Odoo ไม่รู้จักเลย (register ตรงที่
             Backend โดยไม่ผ่าน Odoo)

        ไม่ auto-fix ให้ — แค่รายงานผลไว้ให้ Fleet Manager ตัดสินใจเอง
        เพราะการแก้ข้อมูลรถ/device มีผลกับ Trip/Score จึงเสี่ยงเกินไปที่จะ
        ให้ระบบแก้เองแบบเงียบๆ

        Returns:
            dict: action แสดง notification สรุปจำนวนที่ไม่ตรงกัน

        Raises:
            UserError: ถ้ายังไม่ได้ตั้งค่า API URL หรือดึงข้อมูลจาก Backend
                ไม่สำเร็จ
        """
        self.ensure_one()
        api_url = self.get_active_api_url()
        api_key = self.get_active_api_key()
        if not api_url:
            raise UserError('กรุณาตั้งค่า API URL ของ Backend ก่อน')

        try:
            resp = requests.get(
                f'{api_url}/api/v1/devices',
                headers={'APIKEY': api_key},
                timeout=20,
            )
            resp.raise_for_status()
            payload = resp.json()
            if isinstance(payload, dict):
                backend_devices = payload.get('devices', [])
            elif isinstance(payload, list):
                backend_devices = payload
            else:
                backend_devices = []
        except requests.RequestException as e:
            self.write({
                'last_reconciled_at': fields.Datetime.now(),
                'last_error':         f'Reconcile devices ล้มเหลว: {e}',
            })
            raise UserError(f'ดึงรายการ Device จาก Backend ไม่สำเร็จ:\n{e}')

        # index Backend devices ด้วย device_id (upper-case) — response ใช้
        # key "id" เป็นหลัก รองรับ "device_id" ด้วยเผื่อ schema เปลี่ยน
        backend_by_id = {
            (d.get('id') or d.get('device_id') or '').upper(): d
            for d in backend_devices
            if d.get('id') or d.get('device_id')
        }

        Vehicle = self.env['fleet.vehicle'].sudo()
        odoo_vehicles = Vehicle.search([('telematics_device_id', '!=', False)])
        odoo_by_device = {
            (v.telematics_device_id or '').upper(): v for v in odoo_vehicles
        }

        mismatches = []

        # เทียบฝั่ง Odoo → Backend (ครอบคลุมทั้งกรณี 1 และ 2)
        for dev_id, vehicle in odoo_by_device.items():
            b = backend_by_id.get(dev_id)
            if not b:
                mismatches.append(
                    f'⚠️ {vehicle.name}: Odoo ผูก device {dev_id} แต่ Backend '
                    f'ไม่มี device นี้เลย (ยังไม่ได้ register จริง)'
                )
                continue
            backend_vehicle_id = b.get('vehicle_id')
            if backend_vehicle_id and int(backend_vehicle_id) != vehicle.id:
                mismatches.append(
                    f'⚠️ {vehicle.name}: Odoo ผูก device {dev_id} กับรถ id={vehicle.id} '
                    f'แต่ Backend ผูก device นี้กับ vehicle_id={backend_vehicle_id} แทน'
                )

        # เทียบฝั่ง Backend → Odoo (กรณี 3: device ที่ Backend มีแต่ Odoo ไม่รู้จัก)
        for dev_id, b in backend_by_id.items():
            if dev_id not in odoo_by_device:
                mismatches.append(
                    f'⚠️ Backend มี device {dev_id} (vehicle_id={b.get("vehicle_id")}) '
                    f'แต่ไม่มีรถคันไหนใน Odoo ผูกกับ device นี้เลย'
                )

        note = '\n'.join(mismatches) if mismatches else 'ไม่พบความไม่ตรงกัน — ข้อมูลตรงกันทั้งหมด ✅'

        self.write({
            'last_reconciled_at':    fields.Datetime.now(),
            'device_mismatch_count': len(mismatches),
            'device_mismatch_note':  note,
        })

        _logger.info(
            'action_reconcile_devices: ตรวจ %d devices (Backend) เทียบกับ %d รถ (Odoo) → พบ %d mismatch',
            len(backend_by_id), len(odoo_by_device), len(mismatches),
        )

        return {
            'type': 'ir.actions.client',
            'tag':  'display_notification',
            'params': {
                'title':   f'พบ {len(mismatches)} รายการไม่ตรงกัน' if mismatches else '✅ Device ตรงกันทั้งหมด',
                'message': note[:500],
                'type':    'warning' if mismatches else 'success',
                'sticky':  bool(mismatches),
            },
        }

    @api.model
    def _cron_reconcile_devices(self):
        """เรียกจาก ir.cron รายวัน — สั่ง reconcile ให้เรคคอร์ด config แรก
        ของระบบโดยอัตโนมัติ (ห่อ UserError ไว้เพื่อไม่ให้ cron ล้มทั้งงาน)."""
        config = self.search([], limit=1, order='id asc')
        if config:
            try:
                config.action_reconcile_devices()
            except UserError as e:
                _logger.warning('_cron_reconcile_devices: %s', e)
