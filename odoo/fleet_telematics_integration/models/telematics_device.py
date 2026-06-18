# ==============================================================================
# models/telematics_device.py
# เก็บ Device list ที่ดึงมาจาก GET /api/v1/devices
# ==============================================================================

from odoo import models, fields


class TelematicsDevice(models.Model):
    _name = 'fleet.telematics.device'
    _description = 'Telematics Device'
    _rec_name = 'device_id'
    _order = 'device_id'

    config_id = fields.Many2one(
        'fleet.telematics.config',
        string='Config',
        ondelete='cascade',
    )

    device_id = fields.Char(
        string='Device ID',
        required=True,
        index=True,
        help='รหัส GPS Device เช่น KTC-001'
    )

    vehicle_id = fields.Many2one(
        'fleet.vehicle',
        string='Vehicle',
        ondelete='set null',
        help='รถที่ผูกกับ Device นี้ใน Odoo'
    )

    active = fields.Boolean(string='Active', default=True)
    available = fields.Boolean(string='Available', default=True)

    date_update_latest = fields.Datetime(
        string='Last Updated (Backend)',
        readonly=True,
    )

    synced_at = fields.Datetime(
        string='Synced At',
        readonly=True,
    )
