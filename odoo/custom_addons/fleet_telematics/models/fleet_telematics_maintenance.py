import logging
from datetime import date, timedelta

from odoo import models, fields, api

_logger = logging.getLogger(__name__)


class FleetTelematicsMaintenance(models.Model):
    _name        = 'fleet.telematics.maintenance'
    _description = 'Fleet Telematics Maintenance Record'
    _order       = 'service_date desc'

    # === ส่วนที่ 1: ข้อมูลการซ่อมบำรุงแต่ละครั้ง ===
    # บันทึกว่ารถคันไหน ซ่อมอะไร วันไหน เดินมากี่ km ค่าใช้จ่ายเท่าไหร่ และใครเป็นคนซ่อม
    vehicle_id   = fields.Many2one('fleet.vehicle', string='Vehicle', required=True, ondelete='restrict')
    service_type = fields.Selection([
        ('oil_change',    'Oil Change'),
        ('tire_rotation', 'Tire Rotation'),
        ('brake_check',   'Brake Check'),
        ('engine_check',  'Engine Check'),
        ('full_service',  'Full Service'),
        ('other',         'Other'),
    ], string='Service Type', required=True)
    service_date      = fields.Date(string='Service Date',   required=True, default=fields.Date.today)
    mileage_km        = fields.Float(string='Mileage (km)',  digits=(10, 1))
    cost              = fields.Float(string='Cost (THB)',    digits=(10, 2))
    notes             = fields.Text(string='Notes')
    technician        = fields.Char(string='Technician / Shop')

    # === ส่วนที่ 2: กำหนดรอบซ่อมถัดไป ===
    # ระบุได้ทั้งจากวันที่และระยะทาง ระบบจะใช้ทั้งสองเงื่อนไขในการแจ้งเตือน
    next_service_km   = fields.Float(string='Next Service at (km)', digits=(10, 1))
    next_service_date = fields.Date(string='Next Service Date')

    # === ส่วนที่ 3: คำนวณสถานะแจ้งเตือนอัตโนมัติ ===
    # ตรวจสอบทั้งวันที่และ km ที่เหลือ
    # — ถ้าเลยกำหนดหรือ km เหลือ ≤ 0 → Overdue
    # — ถ้าใกล้ถึงภายใน 14 วัน หรือ km เหลือ ≤ 500 → Due Soon
    alert_status = fields.Selection([
        ('ok',      '✅ OK'),
        ('due',     '⚠️ Due Soon'),
        ('overdue', '🔴 Overdue'),
    ], string='Alert', compute='_compute_alert', store=True)

    @api.depends('next_service_date', 'next_service_km', 'vehicle_id.total_distance_km')
    def _compute_alert(self):
        today = date.today()
        for rec in self:
            overdue = False
            due_soon = False
            if rec.next_service_date:
                if rec.next_service_date < today:
                    overdue = True
                elif rec.next_service_date <= today + timedelta(days=14):
                    due_soon = True
            if rec.next_service_km and rec.vehicle_id:
                km_remaining = rec.next_service_km - (rec.vehicle_id.total_distance_km or 0)
                if km_remaining <= 0:
                    overdue = True
                elif km_remaining <= 500:
                    due_soon = True
            rec.alert_status = 'overdue' if overdue else ('due' if due_soon else 'ok')
