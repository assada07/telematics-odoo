import logging
from datetime import date

from odoo import models, fields, api

_logger = logging.getLogger(__name__)


class FleetTelematicsIncentive(models.Model):
    _name        = 'fleet.telematics.incentive'
    _description = 'Fleet Telematics Monthly Incentive'
    _order       = 'period_year desc, period_month desc'

    # === ส่วนที่ 1: ระบุว่าคำนวณโบนัสของใคร รอบไหน ===
    # กำหนด driver, เดือน/ปี และ snapshot ของ scoring config ที่ใช้รอบนี้
    # (snapshot เพื่อให้แก้ config ภายหลังไม่กระทบตัวเลขที่คำนวณไปแล้ว)
    driver_id = fields.Many2one(
        'hr.employee', string='Driver', required=True)
    scoring_config_id = fields.Many2one(
        'fleet.telematics.scoring.config',
        string='Scoring Config (snapshot)',
        help='Snapshot ของ config ที่ใช้คำนวณรอบนี้ — ถ้า admin เปลี่ยน config ภายหลัง จะไม่กระทบ record นี้')
    period_month = fields.Integer(string='Month')
    period_year  = fields.Integer(string='Year')
    period_label = fields.Char(
        string='Period',
        compute='_compute_period_label', store=True,
        help='แสดงผลเป็น MM/YYYY เช่น 05/2025')

    # === ส่วนที่ 2: สถิติสรุปจาก Trip Logs ของเดือนนั้น ===
    # ดึงจาก trip logs ที่ synced ในเดือน/คนขับนั้น เพื่อใช้คำนวณ tier และโบนัส
    avg_score = fields.Float(
        string='Avg Score', digits=(5, 2),
        compute='_compute_incentive', store=True)
    min_score = fields.Float(
        string='Min Score', digits=(5, 2),
        compute='_compute_incentive', store=True)
    total_trips = fields.Integer(
        string='Total Trips',
        compute='_compute_incentive', store=True)
    total_distance_km = fields.Float(
        string='Total Distance (km)', digits=(10, 2),
        compute='_compute_incentive', store=True)
    total_harsh_events = fields.Integer(
        string='Total Harsh Events',
        compute='_compute_incentive', store=True)
    total_idle_min = fields.Float(
        string='Total Idle (min)', digits=(10, 2),
        compute='_compute_incentive', store=True)

    # === ส่วนที่ 3: ผลลัพธ์ Tier และจำนวนโบนัสที่ได้รับ ===
    # tier จาก avg_score เทียบกับเกณฑ์ใน scoring config → คำนวณ % โบนัส × เงินเดือนฐาน
    incentive_tier = fields.Selection([
        ('A', 'A — Excellent'),
        ('B', 'B — Good'),
        ('C', 'C — Fair'),
        ('D', 'D — Needs Improvement'),
    ], string='Tier', compute='_compute_incentive', store=True)
    bonus_pct    = fields.Float(string='Bonus %',      digits=(5, 2),   compute='_compute_incentive', store=True)
    base_salary  = fields.Float(string='Base Salary',  digits=(10, 2),  compute='_compute_incentive', store=True)
    bonus_amount = fields.Float(string='Bonus (THB)',  digits=(10, 2),  compute='_compute_incentive', store=True)

    # === ส่วนที่ 4: Workflow State ของใบโบนัส ===
    # ควบคุมขั้นตอนการอนุมัติ draft → confirmed → approved → paid
    state = fields.Selection([
        ('draft',     'Draft'),
        ('confirmed', 'Confirmed'),
        ('approved',  'Approved'),
        ('paid',      'Paid'),
    ], default='draft')
    approved_by = fields.Many2one('res.users', string='Approved By', readonly=True)
    note        = fields.Text(string='Notes')

    # === ส่วนที่ 5: Computed — แสดง Period เป็น MM/YYYY ===
    @api.depends('period_month', 'period_year')
    def _compute_period_label(self):
        for rec in self:
            if rec.period_month and rec.period_year:
                rec.period_label = f'{rec.period_month:02d}/{rec.period_year}'
            else:
                rec.period_label = '-'

    # === ส่วนที่ 6: คำนวณโบนัสทั้งหมดจาก Trip Logs ===
    # 1) ดึง trip ของ driver ในช่วงเดือน/ปีที่กำหนด (เฉพาะ synced)
    # 2) คำนวณ avg/min score, จำนวน harsh events, idle time
    # 3) จับ tier จาก scoring config ที่ active หรือ snapshot
    # 4) ดึง base_salary จาก hr.contract → bonus_amount = base_salary × bonus_pct / 100
    @api.depends('driver_id', 'period_month', 'period_year', 'scoring_config_id')
    def _compute_incentive(self):
        TripLog = self.env['fleet.telematics.log'].sudo()
        for rec in self:
            if not (rec.driver_id and rec.period_month and rec.period_year):
                rec.avg_score = rec.min_score = 0.0
                rec.bonus_pct = rec.base_salary = rec.bonus_amount = 0.0
                rec.total_trips = rec.total_harsh_events = 0
                rec.total_distance_km = rec.total_idle_min = 0.0
                rec.incentive_tier = 'D'
                continue

            y, m = rec.period_year, rec.period_month
            date_from = date(y, m, 1)
            date_to   = date(y + 1, 1, 1) if m == 12 else date(y, m + 1, 1)

            logs = TripLog.search([
                ('driver_id',  '=', rec.driver_id.id),
                ('trip_start', '>=', str(date_from)),
                ('trip_start', '<',  str(date_to)),
                ('state',      '=',  'synced'),
            ])

            scores = [l.driver_score for l in logs if l.driver_score]
            rec.avg_score          = round(sum(scores) / len(scores), 2) if scores else 0.0
            rec.min_score          = round(min(scores), 2) if scores else 0.0
            rec.total_trips        = len(logs)
            rec.total_distance_km  = round(sum(logs.mapped('distance_km')), 2)
            rec.total_idle_min     = round(sum(logs.mapped('idle_min')), 2)
            rec.total_harsh_events = sum(
                l.harsh_brake_count + l.harsh_accel_count + l.harsh_corner_count
                for l in logs
            )

            cfg = rec.scoring_config_id or self.env['fleet.telematics.scoring.config'].search(
                [('active', '=', True)], limit=1)
            if cfg and rec.avg_score >= cfg.tier_a_min_score:
                rec.incentive_tier, rec.bonus_pct = 'A', cfg.tier_a_bonus_pct
            elif cfg and rec.avg_score >= cfg.tier_b_min_score:
                rec.incentive_tier, rec.bonus_pct = 'B', cfg.tier_b_bonus_pct
            elif cfg and rec.avg_score >= cfg.tier_c_min_score:
                rec.incentive_tier, rec.bonus_pct = 'C', cfg.tier_c_bonus_pct
            else:
                rec.incentive_tier, rec.bonus_pct = 'D', 0.0

            contract = self.env['hr.contract'].sudo().search([
                ('employee_id', '=', rec.driver_id.id),
                ('state', '=', 'open'),
            ], limit=1)
            rec.base_salary  = contract.wage if contract else 0.0
            rec.bonus_amount = round(rec.base_salary * rec.bonus_pct / 100, 2)

    # === ส่วนที่ 7: ปุ่มเปลี่ยนสถานะตาม Workflow ===
    # HR ยืนยัน → ผู้จัดการอนุมัติ → บันทึกการจ่าย / หรือย้อนกลับ draft ได้
    def action_confirm(self):
        for rec in self:
            if rec.state == 'draft':
                rec.state = 'confirmed'

    def action_approve(self):
        for rec in self:
            if rec.state == 'confirmed':
                rec.state      = 'approved'
                rec.approved_by = self.env.user

    def action_mark_paid(self):
        for rec in self:
            if rec.state == 'approved':
                rec.state = 'paid'

    def action_reset(self):
        for rec in self:
            if rec.state in ('confirmed', 'approved'):
                rec.state       = 'draft'
                rec.approved_by = False

    # === ส่วนที่ 8: Cron — สร้างใบโบนัส Draft อัตโนมัติทุกวันที่ 1 ของเดือน ===
    # สร้าง incentive record (draft) สำหรับทุก driver ที่มี trip เดือนที่แล้ว
    # ตรวจ dedup ก่อนสร้าง — driver แต่ละคนมีได้เพียง 1 record ต่อเดือน
    @api.model
    def _cron_calculate_monthly_incentive(self):
        today = date.today()
        if today.month == 1:
            period_year, period_month = today.year - 1, 12
        else:
            period_year, period_month = today.year, today.month - 1

        cfg = self.env['fleet.telematics.scoring.config'].sudo().search(
            [('active', '=', True)], limit=1)

        TripLog   = self.env['fleet.telematics.log'].sudo()
        date_from = date(period_year, period_month, 1)
        date_to   = date(period_year + 1, 1, 1) if period_month == 12 \
                    else date(period_year, period_month + 1, 1)

        logs = TripLog.search([
            ('trip_start', '>=', str(date_from)),
            ('trip_start', '<',  str(date_to)),
            ('state', '=', 'synced'),
        ])

        created = 0
        for driver in logs.mapped('driver_id'):
            if self.search([
                ('driver_id',    '=', driver.id),
                ('period_month', '=', period_month),
                ('period_year',  '=', period_year),
            ], limit=1):
                continue

            self.create({
                'driver_id':         driver.id,
                'scoring_config_id': cfg.id if cfg else False,
                'period_month':      period_month,
                'period_year':       period_year,
                'state':             'draft',
            })
            created += 1

        _logger.info(
            'cron_monthly_incentive: สร้าง %d records สำหรับ %02d/%d',
            created, period_month, period_year
        )
