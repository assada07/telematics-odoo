from odoo import models, fields


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    # === ส่วนที่ 1: ฟิลด์ตั้งค่า Telematics ใน Settings หน้าหลักของ Odoo ===
    # ผูกแต่ละฟิลด์เข้ากับ System Parameter โดยตรง — บันทึกหน้า Settings แล้วค่าถูกเก็บอัตโนมัติ
    telematics_api_url = fields.Char(
        string='API URL',
        config_parameter='fleet_telematics.api_url'
    )

    telematics_api_key = fields.Char(
        string='API Key',
        config_parameter='fleet_telematics.api_key'
    )

    telematics_device_name = fields.Char(
        string='Device Name',
        config_parameter='fleet_telematics.device_name'
    )

    telematics_device_id = fields.Char(
        string='Device ID',
        config_parameter='fleet_telematics.device_id'
    )
