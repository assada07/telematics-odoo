from odoo import models, fields, api


class FleetTelematicsLog(models.Model):
    _name        = 'fleet.telematics.log'
    _description = 'Fleet Telematics Trip Log'
    _order       = 'trip_start desc'
    _rec_name    = 'display_name'

    # === ส่วนที่ 1: ข้อมูลหลักของ Trip — รถ คนขับ และอุปกรณ์ GPS ===
    # ระบุว่า trip นี้เกิดขึ้นกับรถคันไหน ขับโดยใคร และใช้กล่อง GPS ตัวไหน
    vehicle_id = fields.Many2one(
        'fleet.vehicle', string='Vehicle',
        required=True, ondelete='restrict')
    driver_id = fields.Many2one(
        'hr.employee', string='Driver',
        required=True)
    telematics_device_id = fields.Char(
        string='Device ID',
        help='รหัสกล่องพ่วง GPS เช่น KTC-001')

    # === ส่วนที่ 2: ช่วงเวลาของ Trip ===
    # เก็บเวลาเริ่ม-สิ้นสุด และคำนวณระยะเวลาเดินทางเป็นนาทีอัตโนมัติ
    trip_start   = fields.Datetime(string='Trip Start', required=True)
    trip_end     = fields.Datetime(string='Trip End')
    duration_min = fields.Float(
        string='Duration (min)',
        compute='_compute_duration', store=True,
        digits=(10, 2))

    # === ส่วนที่ 3: สถิติการเดินทาง ===
    # ตัวเลขสรุปภาพรวมของ trip เช่น ระยะทาง ความเร็ว เวลาจอดนิ่ง และน้ำมัน
    distance_km   = fields.Float(string='Distance (km)',    digits=(10, 2))
    max_speed     = fields.Float(string='Max Speed (km/h)', digits=(10, 2))
    avg_speed     = fields.Float(string='Avg Speed (km/h)', digits=(10, 2))
    idle_min      = fields.Float(string='Idle Time (min)',  digits=(10, 2))
    fuel_used_est = fields.Float(string='Fuel Est. (L)',    digits=(10, 3))

    # === ส่วนที่ 4: ข้อมูล GPS Raw (Backward Compatibility) ===
    # เก็บข้อมูล GPS ดิบจาก webhook real-time ไว้เพื่อรองรับระบบเดิม
    timestamp  = fields.Datetime(string='Timestamp')
    latitude   = fields.Float(string='Latitude')
    longitude  = fields.Float(string='Longitude')
    speed      = fields.Float(string='Speed')
    heading    = fields.Integer(string='Heading')
    ignition   = fields.Boolean(string='Ignition')
    event      = fields.Char(string='Event')

    # === ส่วนที่ 5: คะแนนและสถิติเหตุการณ์อันตราย ===
    # ผลลัพธ์จากการประเมินพฤติกรรมการขับขี่ในแต่ละ trip
    driver_score       = fields.Float(string='Driver Score',        digits=(5, 2))
    harsh_brake_count  = fields.Integer(string='Harsh Brakes')
    harsh_accel_count  = fields.Integer(string='Harsh Accelerations')
    harsh_corner_count = fields.Integer(string='Harsh Cornering')
    speeding_count     = fields.Integer(string='Speeding Events')

    # === ส่วนที่ 6: ข้อมูลเส้นทาง GPS และการอ้างอิงกับระบบภายนอก ===
    # เก็บ GPS track ทั้งสาย (JSON) และ ID อ้างอิงฝั่ง MTD สำหรับ sync
    gps_track_json   = fields.Text(string='GPS Track (JSON)')
    external_trip_id = fields.Char(string='External Trip ID')

    # === ส่วนที่ 7: สถานะและความสัมพันธ์กับ Events ===
    # ควบคุม workflow ของ trip (draft → confirmed → synced)
    # และเชื่อมกับ event อันตรายที่เกิดขึ้นระหว่างทาง
    state = fields.Selection([
        ('draft',     'Draft'),
        ('confirmed', 'Confirmed'),
        ('synced',    'Synced'),
    ], string='Status', default='draft')

    event_ids = fields.One2many(
        'fleet.telematics.event', 'trip_id', string='Harsh Events')

    display_name = fields.Char(
        compute='_compute_display_name', store=True)

    # === ส่วนที่ 8: Computed Fields — ชื่อแสดงผลและระยะเวลา ===
    # คำนวณชื่อ trip จากชื่อรถ+วันที่ และคำนวณระยะเวลาจากเวลาเริ่ม-สิ้นสุด
    @api.depends('vehicle_id', 'trip_start')
    def _compute_display_name(self):
        for rec in self:
            v = rec.vehicle_id.name or '?'
            t = rec.trip_start.strftime('%d/%m/%y %H:%M') if rec.trip_start else '-'
            rec.display_name = f'{v} — {t}'

    @api.depends('trip_start', 'trip_end')
    def _compute_duration(self):
        for rec in self:
            if rec.trip_start and rec.trip_end:
                rec.duration_min = (rec.trip_end - rec.trip_start).total_seconds() / 60
            else:
                rec.duration_min = 0.0

    # === ส่วนที่ 9: Action เปลี่ยนสถานะ Trip ===
    # ปุ่มยืนยัน trip เมื่อตรวจสอบข้อมูลเรียบร้อยแล้ว (draft → confirmed)
    def action_confirm(self):
        for rec in self:
            if rec.state == 'draft':
                rec.state = 'confirmed'
