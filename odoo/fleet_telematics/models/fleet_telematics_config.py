from odoo import models, fields
import requests


class FleetTelematicsConfig(models.TransientModel):
    _name        = 'fleet.telematics.config'
    _description = 'Fleet Telematics Configuration'

    # === ส่วนที่ 1: ฟิลด์รับค่าการตั้งค่าการเชื่อมต่อ MTD ===
    # รับค่า URL, API Key และข้อมูล Device จากผู้ใช้ผ่านหน้า form
    api_url     = fields.Char(string='API URL')
    api_key     = fields.Char(string='API Key')
    device_name = fields.Char(string='device_name')
    device_id   = fields.Char(string='device_id')

    # === ส่วนที่ 2: โหลดค่าที่บันทึกไว้มาแสดงในฟอร์ม ===
    # เมื่อเปิดหน้า config ระบบจะดึงค่าจาก System Parameters มาเติมให้อัตโนมัติ
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        ICP = self.env['ir.config_parameter'].sudo()
        res['api_url']     = ICP.get_param('fleet_telematics.api_url',     '')
        res['api_key']     = ICP.get_param('fleet_telematics.api_key',     '')
        res['device_name'] = ICP.get_param('fleet_telematics.device_name', '')
        res['device_id']   = ICP.get_param('fleet_telematics.device_id',   '')
        return res

    # === ส่วนที่ 3: บันทึกค่าและทดสอบการเชื่อมต่อ ===
    # กดปุ่ม "บันทึก" → เขียนค่าลง System Parameters → ยิง request ทดสอบ MTD API ทันที
    def action_save(self):
        ICP = self.env['ir.config_parameter'].sudo()
        ICP.set_param('fleet_telematics.api_url',     self.api_url     or '')
        ICP.set_param('fleet_telematics.api_key',     self.api_key     or '')
        ICP.set_param('fleet_telematics.device_name', self.device_name or '')
        ICP.set_param('fleet_telematics.device_id',   self.device_id   or '')
        response = requests.get(self.api_url +"/api/v1/vehicles/1/location")

        print(response.json())
        return {
            'type': 'ir.actions.client',
            'tag':  'display_notification',
            'params': {
                'title':   'บันทึกสำเร็จ',
                'message': 'ตั้งค่า Fleet Telematics เรียบร้อยแล้ว',
                'type':    'success',
                'sticky':  False,
            }
        }
