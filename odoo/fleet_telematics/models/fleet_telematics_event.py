from odoo import models, fields


class FleetTelematicsEvent(models.Model):
    _name        = 'fleet.telematics.event'
    _description = 'Fleet Telematics Harsh Event'
    _order       = 'occurred_at desc'

    # === ส่วนที่ 1: ผูก Event กับ Trip ===
    # แต่ละ event ต้องอยู่ภายใต้ trip เสมอ ถ้าลบ trip → events ถูกลบทั้งหมดอัตโนมัติ
    trip_id = fields.Many2one(
        'fleet.telematics.log',
        string='Trip Log',
        required=True,
        ondelete='cascade',
    )

    # === ส่วนที่ 2: ประเภทและรายละเอียดของเหตุการณ์ ===
    # จำแนกชนิดของพฤติกรรมอันตราย พร้อมเวลา พิกัด ความเร็ว และระดับความรุนแรง
    event_type = fields.Selection([
        ('harsh_brake',  'Harsh Brake'),
        ('harsh_accel',  'Harsh Acceleration'),
        ('harsh_corner', 'Harsh Cornering'),
        ('speeding',     'Speeding'),
        ('idling',       'Idling'),
        ('bump',         'Bump'),
    ], string='Event Type', required=True)

    occurred_at    = fields.Datetime(string='Occurred At', required=True)
    lat            = fields.Float(string='Latitude',          digits=(10, 7))
    lon            = fields.Float(string='Longitude',         digits=(10, 7))
    severity       = fields.Float(string='Severity (0–100)', digits=(5, 2))
    speed_at_event = fields.Float(string='Speed (km/h)',      digits=(10, 2))
    description    = fields.Char(string='Description')
