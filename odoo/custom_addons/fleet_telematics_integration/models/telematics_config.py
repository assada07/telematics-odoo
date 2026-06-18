# ==============================================================================
# models/telematics_config.py  [MODIFIED v2]
# แก้บั๊ก:
#   1. action_save_and_test() → write() ทับเรคคอร์ดเดิมเสมอ ไม่ create ซ้ำ
#   2. get_active_api_url() / _api_url — แก้ bug ตัวแปร ip/url_input ปนกัน
# ==============================================================================

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
    _name = 'fleet.telematics.config'
    _description = 'Fleet Telematics Configuration'
    _rec_name = 'name'

    # ============================================================
    # [A] ฟิลด์ตั้งค่า — api_url + api_key
    # ============================================================

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

    # ============================================================
    # [B] API URL ล่าสุดที่ใช้งานได้ (จดจำอัตโนมัติเมื่อ Test ผ่าน)
    # ============================================================

    last_confirmed_url = fields.Char(
        string='API URL ล่าสุดที่ใช้งานได้',
        readonly=True,
        help='ระบบบันทึก URL นี้อัตโนมัติเมื่อทดสอบการเชื่อมต่อสำเร็จ'
    )

    # ============================================================
    # [C] System Health Dashboard
    # ============================================================

    connection_status = fields.Selection([
        ('untested', '⚪ Untested'),
        ('ready',    '🟢 Ready'),
        ('error',    '🔴 Error'),
    ],
        string='Connection Status',
        default='untested',
        readonly=True
    )

    last_test_at = fields.Datetime(string='Last Tested At', readonly=True)
    last_sync_at = fields.Datetime(string='Last Synced At', readonly=True)
    last_error   = fields.Text(string='Last Error',         readonly=True)

    # ============================================================
    # [D] Helper: แปลง input → URL เต็ม
    # ============================================================

    @staticmethod
    def _normalize_url(raw):
        """เติม http:// ถ้า input ยังไม่มี scheme"""
        raw = (raw or '').strip()
        if not raw:
            return ''
        if raw.startswith('http://') or raw.startswith('https://'):
            return raw.rstrip('/')
        return f'http://{raw}'.rstrip('/')

    # ============================================================
    # [E] โหลดค่าจาก ir.config_parameter เมื่อเปิดฟอร์มใหม่ (New)
    # ============================================================

    def default_get(self, fields_list):
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

    # ============================================================
    # [F] action_save_and_test  ← แก้บั๊กหลัก #1
    #   - ใช้ write() ทับเรคคอร์ดปัจจุบันเสมอ → ไม่สร้างแถวซ้ำ
    #   - เรคคอร์ด self มาจาก Form View ที่เปิดอยู่โดยตรง
    # ============================================================

    def action_save_and_test(self):
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

            try:
                payload = resp.json()
                device_count = len(payload) if isinstance(payload, list) else '-'
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

    # ============================================================
    # [G] Helper: ดึง API URL ที่ใช้งานได้จริง (เรียกจากโมเดลอื่น)
    #   ลำดับ: 1) last_confirmed_url  2) api_url ปัจจุบัน
    # ============================================================

    @api.model
    def get_active_api_url(self):
        ICP = self.env['ir.config_parameter'].sudo()
        confirmed = ICP.get_param(_PARAM_CONFIRMED_URL, '').strip()
        current   = ICP.get_param(_PARAM_URL, '').strip() \
                    or ICP.get_param(_PARAM_API_URL, '').strip()
        raw = confirmed or current
        return self._normalize_url(raw)

    @api.model
    def get_active_api_key(self):
        ICP = self.env['ir.config_parameter'].sudo()
        return ICP.get_param(_PARAM_API_KEY, '')
