# ==============================================================================
# models/telematics_config.py
# โมเดลหน้า Settings + ปุ่ม Test Connection + System Health Dashboard
# ==============================================================================
import requests
from odoo import models, fields, api
from odoo.exceptions import UserError

import logging
_logger = logging.getLogger(__name__)


class TelematicsConfig(models.Model):
    _name        = 'fleet.telematics.config'
    _description = 'Fleet Telematics Configuration'
    _rec_name    = 'name'

    # ============================================================
    # [A] ฟิลด์ตั้งค่าการเชื่อมต่อ MTD Backend
    # ============================================================
    name    = fields.Char(string='Config Name', default='Fleet Telematics Settings')
    api_url = fields.Char(
        string='API URL',
        help='URL หลักของ MTD Backend เช่น https://mtd.example.com')
    api_key = fields.Char(
        string='API Key',
        help='Bearer token สำหรับยืนยันตัวตนกับ MTD Backend')

    # ============================================================
    # [B] System Health — สถานะท่อเชื่อมต่อ
    # ============================================================
    connection_status = fields.Selection([
        ('untested', '⚪ Untested'),
        ('ready',    '🟢 Ready'),
        ('error',    '🔴 Error'),
    ], string='Connection Status', default='untested', readonly=True)

    last_test_at  = fields.Datetime(string='Last Tested At', readonly=True)
    last_sync_at  = fields.Datetime(string='Last Synced At', readonly=True)
    last_error    = fields.Text(string='Last Error', readonly=True)

    # ============================================================
    # [C] โหลดค่าที่บันทึกไว้มาแสดงในฟอร์ม (default_get)
    # ============================================================
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        ICP = self.env['ir.config_parameter'].sudo()
        res['api_url'] = ICP.get_param('fleet_telematics.mtd_api_url', '')
        res['api_key'] = ICP.get_param('fleet_telematics.mtd_api_key', '')
        return res

    # ============================================================
    # [D] ปุ่ม "Test Connection" — ยิง GET /health แล้วอัปเดต status
    # ============================================================
    def action_test_connection(self):
        self.ensure_one()
        api_url = (self.api_url or '').rstrip('/')
        api_key = self.api_key or ''

        if not api_url:
            raise UserError('กรุณาระบุ API URL ก่อนทดสอบการเชื่อมต่อ')

        try:
            resp = requests.get(
                f'{api_url}/health',
                headers={'APIKEY': api_key},
                timeout=10,
            )
            resp.raise_for_status()

            # บันทึกค่าลง System Parameters
            ICP = self.env['ir.config_parameter'].sudo()
            ICP.set_param('fleet_telematics.mtd_api_url', api_url)
            ICP.set_param('fleet_telematics.mtd_api_key', api_key)

            self.write({
                'connection_status': 'ready',
                'last_test_at':      fields.Datetime.now(),
                'last_error':        False,
            })

            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title':   '✅ เชื่อมต่อสำเร็จ',
                    'message': f'MTD Backend ตอบกลับ {resp.status_code} — ระบบพร้อมใช้งาน',
                    'type':    'success',
                    'sticky':  False,
                },
            }

        except requests.RequestException as e:
            self.write({
                'connection_status': 'error',
                'last_test_at':      fields.Datetime.now(),
                'last_error':        str(e),
            })
            raise UserError(f'เชื่อมต่อ Backend ไม่สำเร็จ:\n{e}')

    # ============================================================
    # [E] ปุ่ม "Save" — บันทึกค่าลง System Parameters
    # ============================================================
    def action_save(self):
        self.ensure_one()
        ICP = self.env['ir.config_parameter'].sudo()
        ICP.set_param('fleet_telematics.mtd_api_url', self.api_url or '')
        ICP.set_param('fleet_telematics.mtd_api_key', self.api_key or '')

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title':   '💾 บันทึกสำเร็จ',
                'message': 'ตั้งค่า Fleet Telematics เรียบร้อยแล้ว',
                'type':    'success',
                'sticky':  False,
            },
        }
