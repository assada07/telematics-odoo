"""models/telematics_event.py

โมเดลเก็บเหตุการณ์เสี่ยง (Harsh Event) — พฤติกรรมอันตรายของคนขับที่ตรวจจับ
ได้จากบอร์ด GPS/IMU เช่น เบรกกะทันหัน เร่งกะทันหัน เข้าโค้งแรง ขับเร็วเกิน
กำหนด จอดเดินเบานาน หรือกระแทก

จุดสำคัญของโมเดลนี้:
  - ข้อมูลทั้งหมดต้องมาจาก Backend sync เท่านั้น ห้ามแก้ไข/ลบ/สร้างผ่าน UI
    เพื่อความโปร่งใสของคะแนน/โบนัส (ดู _check_sync_context)
  - คำนวณ speed limit ตามโซนพื้นที่ (กรุงเทพฯ/นอกเมือง) จากพิกัดของ event
    เพื่อใช้เป็นข้อมูลตั้งต้นให้ระบบคะแนนนำไปประมวลผลต่อ
  - มี vehicle_id/driver_id related field สำหรับ Group By ได้ตรงบนหน้า
    Event Logs โดยไม่ต้องผ่าน trip_id
"""
from odoo import models, fields, api
from odoo.exceptions import UserError

# กรอบพิกัดสี่เหลี่ยม (bounding box) ครอบคลุมพื้นที่กรุงเทพฯ+ปริมณฑลชั้นใน
# แบบประมาณการ ไม่ใช่ขอบเขตการปกครองจริงตาม shapefile — ให้ความแม่นยำระดับ
# "ในเมือง/นอกเมือง" เท่านั้น
BANGKOK_BBOX = {
    'lat_min': 13.49, 'lat_max': 13.96,
    'lon_min': 100.32, 'lon_max': 100.93,
}
SPEED_LIMIT_BANGKOK_KMH = 80.0
SPEED_LIMIT_OUTSIDE_KMH = 90.0


class TelematicsEvent(models.Model):
    """เหตุการณ์เสี่ยง 1 record ต่อ 1 เหตุการณ์ ผูกกับ trip ที่เกิดเหตุการณ์นั้น."""

    _name        = 'fleet.telematics.event'
    _description = 'Fleet Telematics Harsh Event'
    _order       = 'occurred_at desc'

    # ── ความสัมพันธ์กับ Trip ─────────────────────────────────────
    # ลบ trip แล้ว event ที่ผูกอยู่จะถูกลบตามไปด้วยอัตโนมัติ (cascade)
    trip_id = fields.Many2one(
        'fleet.telematics.log',
        string='Trip Log',
        required=True,
        ondelete='cascade',
        readonly=True,
    )

    # related field เพื่อให้ Group By vehicle/driver ได้ตรงบนหน้า Event Logs
    # โดยไม่ต้องผ่าน trip_id
    vehicle_id = fields.Many2one(
        'fleet.vehicle', string='Vehicle',
        related='trip_id.vehicle_id', store=True, readonly=True, index=True)
    driver_id = fields.Many2one(
        'hr.employee', string='Driver',
        related='trip_id.driver_id', store=True, readonly=True, index=True)

    # ── รายละเอียดเหตุการณ์ ──────────────────────────────────────
    event_type = fields.Selection([
        ('harsh_brake',  'Harsh Brake'),
        ('harsh_accel',  'Harsh Acceleration'),
        ('harsh_corner', 'Harsh Cornering'),
        ('speeding',     'Speeding'),
        ('idling',       'Idling'),
        ('bump',         'Bump'),
    ], string='Event Type', required=True, readonly=True)

    occurred_at    = fields.Datetime(string='Occurred At', required=True, readonly=True)
    lat            = fields.Float(string='Latitude',          digits=(10, 7), readonly=True)
    lon            = fields.Float(string='Longitude',         digits=(10, 7), readonly=True)
    severity       = fields.Float(string='Severity (0–100)', digits=(5, 2), readonly=True)
    speed_at_event = fields.Float(string='Speed (km/h)',      digits=(10, 2), readonly=True)
    description    = fields.Char(string='Description', readonly=True)

    # ── Speed Limit ตามโซนพื้นที่ ────────────────────────────────
    speed_limit_kmh = fields.Float(
        string='Speed Limit ตามโซน (km/h)',
        compute='_compute_speed_zone', store=True, digits=(10, 1),
        help='80 กม./ชม. ถ้าอยู่ในกรอบกรุงเทพฯ, 90 กม./ชม. ถ้านอกเมือง '
             '(คำนวณจาก lat/lon ของ event นี้)',
    )
    is_over_speed_limit = fields.Boolean(
        string='เกินความเร็วตามโซน',
        compute='_compute_speed_zone', store=True,
        help='True ถ้า speed_at_event > speed_limit_kmh ของโซนนั้น',
    )
    zone_label = fields.Selection([
        ('bangkok', 'ในเขตกรุงเทพฯ'),
        ('outside', 'นอกเขตกรุงเทพฯ'),
    ], string='โซน', compute='_compute_speed_zone', store=True)

    @api.depends('lat', 'lon', 'speed_at_event')
    def _compute_speed_zone(self):
        """คำนวณโซนพื้นที่และ speed limit ที่เกี่ยวข้องจากพิกัดของ event.

        ตรวจว่าพิกัด (lat, lon) อยู่ในกรอบ BANGKOK_BBOX หรือไม่ แล้วกำหนด
        zone_label / speed_limit_kmh ตามนั้น จากนั้นเทียบ speed_at_event
        กับ speed_limit_kmh เพื่อตั้งค่า is_over_speed_limit
        """
        for rec in self:
            in_bkk = (
                BANGKOK_BBOX['lat_min'] <= rec.lat <= BANGKOK_BBOX['lat_max']
                and BANGKOK_BBOX['lon_min'] <= rec.lon <= BANGKOK_BBOX['lon_max']
            ) if rec.lat and rec.lon else False

            rec.zone_label      = 'bangkok' if in_bkk else 'outside'
            rec.speed_limit_kmh = (
                SPEED_LIMIT_BANGKOK_KMH if in_bkk else SPEED_LIMIT_OUTSIDE_KMH
            )
            rec.is_over_speed_limit = rec.speed_at_event > rec.speed_limit_kmh

    # ── ล็อกไม่ให้แก้ไข/ลบ/สร้างผ่านหน้าจอปกติ ───────────────────
    # ACL (security/ir.model.access.csv) ตัดสิทธิ์ write/create/unlink ของ
    # ทุกกลุ่มไว้เป็นชั้นแรกอยู่แล้ว แต่ ACL ป้องกันโค้ดที่ sudo() เขียนตรงๆ
    # จากที่อื่นไม่ได้ — เมธอดด้านล่างเป็นชั้นป้องกันที่สอง: อนุญาตให้เขียน
    # ได้เฉพาะตอนมี context flag 'fleet_telematics_allow_sync' เท่านั้น ซึ่ง
    # มีแค่ path sync อัตโนมัติของโมดูลนี้เอง (models/telematics_log.py) ที่
    # ตั้ง flag นี้ได้ ผู้ใช้ทั่วไปหรือ Admin ผ่านหน้าจอปกติทำไม่ได้
    def _check_sync_context(self, action):
        """ตรวจว่ามี context flag ที่อนุญาตให้ sync เขียนข้อมูลได้หรือไม่.

        Args:
            action (str): คำอธิบายการกระทำ (สร้าง/แก้ไข/ลบ) ใช้แสดงใน
                ข้อความ error เท่านั้น

        Raises:
            UserError: ถ้าไม่มี context flag 'fleet_telematics_allow_sync'
        """
        if not self.env.context.get('fleet_telematics_allow_sync'):
            raise UserError(
                'Event Logs เป็นข้อมูลที่ดึงจากบอร์ด GPS อัตโนมัติเท่านั้น — '
                f'ไม่อนุญาตให้{action}ผ่านหน้าจอ เพื่อความโปร่งใสของคะแนน/โบนัส'
            )

    @api.model_create_multi
    def create(self, vals_list):
        """สร้าง record ได้เฉพาะตอน sync อัตโนมัติเท่านั้น (ดู _check_sync_context)."""
        self._check_sync_context('สร้าง')
        return super().create(vals_list)

    def write(self, vals):
        """แก้ไข record ได้เฉพาะตอน sync อัตโนมัติเท่านั้น (ดู _check_sync_context)."""
        self._check_sync_context('แก้ไข')
        return super().write(vals)

    def unlink(self):
        """ลบ record ได้เฉพาะตอน sync อัตโนมัติเท่านั้น (ดู _check_sync_context)."""
        self._check_sync_context('ลบ')
        return super().unlink()
