from odoo import models, fields, api


class FleetTelematicsVehicleMapping(models.Model):
    _name        = 'fleet.telematics.vehicle.mapping'
    _description = 'Fleet Telematics — Vehicle / Driver Mapping (Sync Board)'
    _order       = 'vehicle_id'
    _rec_name    = 'vehicle_id'

    # === ส่วนที่ 1: เลือกรถเพื่อสร้าง Mapping ===
    # รถคือจุดศูนย์กลาง — เมื่อเลือกรถแล้วระบบจะดึงข้อมูลที่เกี่ยวข้องมาให้อัตโนมัติ
    vehicle_id = fields.Many2one(
        'fleet.vehicle', string='Vehicle',
        required=True, ondelete='cascade',
        help='เลือกรถ — ข้อมูลทะเบียน/คนขับจะดึงมาอัตโนมัติ')

    # === ส่วนที่ 2: ข้อมูลรถที่ดึงมาอัตโนมัติเมื่อเลือก Vehicle ===
    # ทะเบียนและรุ่นรถดึงจาก fleet.vehicle — แก้ไขทะเบียนได้ แต่รุ่นรถอ่านอย่างเดียว
    license_plate = fields.Char(
        string='License Plate',
        compute='_compute_from_vehicle', store=True, readonly=False)
    model_name = fields.Char(
        string='Vehicle Model',
        compute='_compute_from_vehicle', store=True, readonly=True)

    # === ส่วนที่ 3: ข้อมูลคนขับที่ผูกกับรถ ===
    # ดึงคนขับจากรถอัตโนมัติ หรือเลือกเองได้ และแสดงเบอร์โทร/ตำแหน่งงานอ่านอย่างเดียว
    driver_id = fields.Many2one(
        'hr.employee', string='Driver',
        compute='_compute_from_vehicle', store=True, readonly=False,
        help='ดึงจากรถอัตโนมัติ หรือเลือกเองได้')
    driver_phone = fields.Char(
        string='Driver Phone',
        compute='_compute_from_driver', store=True, readonly=True)
    driver_job = fields.Char(
        string='Job Position',
        compute='_compute_from_driver', store=True, readonly=True)

    # === ส่วนที่ 4: รหัสอุปกรณ์ GPS ที่ติดตั้งในรถ ===
    # ดึงจากรถอัตโนมัติ แต่แก้ไขได้ — ต้องตรงกับ device_id ใน MTD Backend
    telematics_device_id = fields.Char(
        string='Device ID',
        compute='_compute_from_vehicle', store=True, readonly=False,
        help='รหัสกล่อง GPS เช่น KTC-001 — ดึงจากรถอัตโนมัติ')

    # === ส่วนที่ 5: สถานะ Sync ของคู่รถ-คนขับ ===
    # แสดงว่า mapping นี้กำลัง active / inactive / รอเปิดใช้งาน
    sync_status  = fields.Selection([
        ('active',   '🟢 Active'),
        ('inactive', '⚫ Inactive'),
        ('pending',  '🟡 Pending'),
    ], string='Sync Status', default='pending',
       help='สถานะการ sync ของคู่รถ-คนขับนี้')
    last_sync_at = fields.Datetime(string='Last Synced', readonly=True)
    notes        = fields.Char(string='Notes')

    # === ส่วนที่ 6: Auto-fill เมื่อเลือก Vehicle หรือ Driver ===
    # เมื่อเลือกรถ → ดึงทะเบียน รุ่น คนขับ และ Device ID มาเติมอัตโนมัติ
    # เมื่อเลือกคนขับ → ดึงเบอร์โทรและตำแหน่งงานมาแสดง
    @api.depends('vehicle_id')
    def _compute_from_vehicle(self):
        for rec in self:
            v = rec.vehicle_id
            if v:
                rec.license_plate         = v.license_plate or ''
                rec.model_name            = v.model_id.name if v.model_id else ''
                rec.telematics_device_id  = v.telematics_device_id or ''
                if v.driver_id:
                    rec.driver_id = v.driver_id
            else:
                rec.license_plate        = ''
                rec.model_name           = ''
                rec.telematics_device_id = ''

    @api.depends('driver_id')
    def _compute_from_driver(self):
        for rec in self:
            emp = rec.driver_id
            rec.driver_phone = emp.mobile_phone or emp.work_phone or '' if emp else ''
            rec.driver_job   = emp.job_id.name if emp and emp.job_id else ''

    # === ส่วนที่ 7: ปุ่มเปิด/ปิดการ Sync และอัปเดต Device กลับไปที่รถ ===
    # Activate → เปลี่ยนสถานะเป็น active และ sync device_id กลับไปที่ fleet.vehicle
    # Deactivate → หยุดการ sync โดยไม่ลบข้อมูล
    def action_activate(self):
        for rec in self:
            rec.write({
                'sync_status': 'active',
                'last_sync_at': fields.Datetime.now(),
            })
            if rec.vehicle_id and rec.telematics_device_id:
                rec.vehicle_id.telematics_device_id = rec.telematics_device_id

    def action_deactivate(self):
        for rec in self:
            rec.write({'sync_status': 'inactive'})

    # === ส่วนที่ 8: Constraint ป้องกัน Vehicle ซ้ำ ===
    # รถแต่ละคันสร้าง Mapping ได้เพียง 1 รายการเท่านั้น
    _sql_constraints = [
        ('vehicle_uniq', 'unique(vehicle_id)',
         'รถแต่ละคันมี Mapping ได้เพียง 1 รายการเท่านั้น'),
    ]
