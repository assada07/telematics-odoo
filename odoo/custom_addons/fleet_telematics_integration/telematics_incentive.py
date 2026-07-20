"""models/telematics_incentive.py

คำนวณโบนัสประจำเดือนของแต่ละคนขับ (UC-09) พร้อม audit log ทุก state
change (UC-10) ผ่าน mail.thread — workflow: draft → confirmed → approved
→ paid (แก้ไขข้อมูลได้เฉพาะตอน draft เท่านั้น)

สถิติผลงาน (avg_score, total_trips ฯลฯ) คำนวณจาก Trip Log ในเครื่อง
ส่วน bonus_pct/tier ดึงจาก Backend (GET /drivers/{id}/bonus) เพราะ
Backend เป็นเจ้าของสูตรคำนวณ tier จริง — ถ้าเรียก Backend ไม่สำเร็จ
(offline/timeout) จะ fallback ไปคำนวณ tier เองจาก Scoring Config
thresholds แทน (ดู _apply_backend_bonus / _local_tier_from_score)
"""
import logging
from datetime import date, timedelta

import requests
from markupsafe import Markup

from odoo import models, fields, api
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class TelematicsIncentive(models.Model):
    """ใบโบนัส 1 record ต่อ 1 คนขับ ต่อ 1 รอบวันที่ (date_from–date_to)."""

    _name        = 'fleet.telematics.incentive'
    _description = 'Fleet Telematics Monthly Incentive'
    _order       = 'period_year desc, period_month desc'
    # audit log ทุก state change ผ่าน mail.thread (UC-10) — ข้อมูลการเงิน
    # (โบนัส) จึงต้องมี audit trail ที่แก้ไขเองไม่ได้
    _inherit = ['mail.thread', 'mail.activity.mixin']

    # กันสร้างใบโบนัสซ้ำที่ระดับฐานข้อมูลจริง สำหรับคนขับคนเดียวกันในช่วง
    # วันที่เดียวกัน (รองรับ "รอบตัดวิก" ที่ไม่ตรงเดือนปฏิทิน เช่น
    # 26 มิ.ย. – 25 ก.ค. ด้วย จึง unique ด้วย date_from/date_to ตรงๆ
    # แทนที่จะ unique ด้วย period_month/period_year)
    _driver_period_unique = models.Constraint(
        'UNIQUE(driver_id, date_from, date_to)',
        'พนักงานคนนี้มีรายการโบนัสของช่วงวันที่นี้อยู่แล้ว — ห้ามสร้างซ้ำ',
    )

    # ── ระบุว่าคำนวณโบนัสของใคร รอบไหน ───────────────────────────
    driver_id = fields.Many2one(
        'hr.employee', string='Driver', required=True)
    # field related+store แบบ flat (ระดับเดียว) สำหรับ ir.rule — ใช้แทน
    # domain แบบ dotted-path ('driver_id.user_id', ...) ที่ทำให้ ir.rule
    # ของโมเดลนี้โหลดไม่ได้ในบางเวอร์ชัน Odoo
    driver_user_id = fields.Many2one(
        'res.users', string='Driver User (internal)',
        related='driver_id.user_id', store=True, readonly=True,
        help='ใช้ภายในสำหรับ record rule เท่านั้น — ไม่ต้องแสดงในฟอร์ม')
    scoring_config_id = fields.Many2one(
        'fleet.telematics.scoring.config',
        string='Scoring Config (snapshot)',
        help='Snapshot ของ config ที่ใช้คำนวณรอบนี้')

    # ช่วงวันที่จริง (ไม่ใช่แค่เดือน/ปี) เพื่อรองรับรอบตัดวิกที่ไม่ตรง
    # เดือนปฏิทิน เช่น 26 ของเดือนก่อน ถึง 25 เดือนนี้
    date_from = fields.Date(string='วันที่เริ่มต้น', required=True)
    date_to   = fields.Date(string='วันที่สิ้นสุด', required=True)

    # period_month/period_year คำนวณอัตโนมัติจาก date_from (ผู้ใช้ไม่ต้อง
    # กรอกเอง) เก็บไว้เพื่อให้ report/portal เดิมที่อ้างอิง field นี้อยู่
    # ยังทำงานได้ปกติ
    period_month = fields.Integer(
        string='Month', compute='_compute_period_ints', store=True)
    period_year = fields.Integer(
        string='Year', compute='_compute_period_ints', store=True)
    period_label = fields.Char(
        string='Period',
        compute='_compute_period_label', store=True,
        help='แสดงผลเป็นช่วงวันที่ เช่น 01/06/2026 - 25/06/2026')

    # ── สถิติสรุปจาก Trip Logs ของรอบนั้น (คำนวณจาก _compute_incentive) ──
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

    # ── ผลลัพธ์ Tier และจำนวนโบนัสที่ได้รับ ───────────────────────
    # ดึง bonus_pct/tier จาก Backend (GET /drivers/{id}/bonus) แล้วคูณ
    # base_salary เอง — ไม่ใช่ compute field อัตโนมัติเพราะต้องเรียก API
    # ภายนอก ตั้งค่าผ่าน _apply_backend_bonus() (เรียกตอน cron หรือกดปุ่ม
    # "Refresh from Backend")
    incentive_tier = fields.Selection([
        ('A', 'A — Excellent'),
        ('B', 'B — Good'),
        ('C', 'C — Fair'),
        ('D', 'D — Needs Improvement'),
    ], string='Tier', default='D', readonly=True)
    bonus_pct    = fields.Float(string='Bonus %',      digits=(5, 2),  default=0.0, readonly=True)
    # readonly ควบคุมโดย View ตาม is_locked เท่านั้น (แก้ไขได้ตอน Draft
    # เท่านั้น) — ไม่บังคับ readonly=True ระดับ Python เพื่อไม่ให้ override
    # ทับ attrs ของ View
    base_salary  = fields.Float(string='Base Salary',  digits=(10, 2), default=0.0)
    # compute field ผูกสูตรตายตัว (Base Salary × Bonus %) ป้องกันไม่ให้
    # ตัวเลขหลุดจากสูตรได้ (ไม่ใช่ field ที่พิมพ์แก้ตรงๆ ได้)
    bonus_amount = fields.Float(
        string='Bonus (THB)', digits=(10, 2),
        compute='_compute_bonus_amount', store=True, readonly=True,
        help='คำนวณอัตโนมัติ = Base Salary × Bonus % — แก้ไขตรงๆ ไม่ได้')
    bonus_source = fields.Selection([
        ('backend', 'Backend API'),
        ('local_fallback', 'Local Fallback (Backend ไม่พร้อมใช้งาน)'),
    ], string='Bonus Source', readonly=True,
        help='ระบุว่า bonus_pct ปัจจุบันมาจาก Backend จริง หรือคำนวณสำรองในเครื่อง')
    bonus_last_synced = fields.Datetime(string='Bonus Synced At', readonly=True)
    # กันแจ้งเตือน HR ซ้ำหลายรอบ ถ้ากด Refresh from Backend ซ้ำๆ ขณะที่ยัง
    # เป็น Tier D อยู่เหมือนเดิม (ดู _notify_hr_tier_d)
    tier_d_notified = fields.Boolean(string='แจ้งเตือน Tier D แล้ว', default=False, readonly=True)

    # ── ล็อกทั้งฟอร์มถาวรเมื่อพ้น Draft ────────────────────────────
    is_locked = fields.Boolean(
        string='ล็อกการแก้ไข', compute='_compute_is_locked',
        help='True เมื่อ state ไม่ใช่ Draft แล้ว — ฟิลด์ทั้งหมดแก้ไขไม่ได้ '
             'จนกว่าจะกด Reset กลับเป็น Draft')

    @api.depends('state')
    def _compute_is_locked(self):
        """is_locked = True เมื่อ state ไม่ใช่ 'draft'."""
        for rec in self:
            rec.is_locked = rec.state != 'draft'

    # ── Workflow State ของใบโบนัส: draft → confirmed → approved → paid ──
    state = fields.Selection([
        ('draft',     'Draft'),
        ('confirmed', 'Confirmed'),
        ('approved',  'Approved'),
        ('paid',      'Paid'),
    ], default='draft', tracking=True)  # tracking=True → chatter บันทึก log
                                         # อัตโนมัติทุกครั้งที่ state เปลี่ยน (UC-10)
    approved_by = fields.Many2one('res.users', string='Approved By', readonly=True, tracking=True)
    note        = fields.Text(string='Notes')

    @api.depends('date_from')
    def _compute_period_ints(self):
        """แยก period_month/period_year จาก date_from (เก็บไว้ให้ฟีเจอร์เดิม
        ที่อ้างอิง field นี้อยู่ยังทำงานได้ปกติ)."""
        for rec in self:
            if rec.date_from:
                rec.period_month = rec.date_from.month
                rec.period_year  = rec.date_from.year
            else:
                rec.period_month = 0
                rec.period_year  = 0

    @api.depends('date_from', 'date_to')
    def _compute_period_label(self):
        """แสดง period เป็นช่วงวันที่อ่านง่าย เช่น '01/06/2026 - 25/06/2026'."""
        for rec in self:
            if rec.date_from and rec.date_to:
                rec.period_label = (
                    f'{rec.date_from.strftime("%d/%m/%Y")} - '
                    f'{rec.date_to.strftime("%d/%m/%Y")}'
                )
            else:
                rec.period_label = '-'

    @api.depends('driver_id', 'date_from', 'date_to')
    def _compute_incentive(self):
        """คำนวณสถิติผลงาน (avg_score, total_trips ฯลฯ) จาก Trip Log ในช่วง
        date_from–date_to (date_to นับรวมอยู่ในช่วงด้วย/inclusive).

        ไม่คำนวณ bonus_pct/tier ในนี้ — ย้ายไปทำใน _apply_backend_bonus()
        เพราะต้องเรียก Backend API
        """
        TripLog = self.env['fleet.telematics.log'].sudo()
        for rec in self:
            if not (rec.driver_id and rec.date_from and rec.date_to):
                rec.avg_score = rec.min_score = 0.0
                rec.total_trips = rec.total_harsh_events = 0
                rec.total_distance_km = rec.total_idle_min = 0.0
                continue

            date_from_excl = rec.date_to + timedelta(days=1)  # date_to รวมอยู่ในช่วง

            logs = TripLog.search([
                ('driver_id',  '=', rec.driver_id.id),
                ('trip_start', '>=', str(rec.date_from)),
                ('trip_start', '<',  str(date_from_excl)),
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

    def _apply_backend_bonus(self):
        """ดึง bonus_pct/tier จาก Backend มาตั้งค่าให้ record นี้ (คูณกับ
        base_salary เพื่อได้ bonus_amount).

        ขั้นตอน:
          1. หา base_salary ของคนขับ: ลองจาก hr.version (Odoo 19) หรือ
             hr.contract (เวอร์ชันเก่ากว่า) ก่อน ถ้าไม่พบใช้
             telematics_base_salary บนโปรไฟล์พนักงาน หรือค่าที่กรอกเองไว้
             ในใบนี้เป็นลำดับสุดท้าย
          2. เรียก GET /drivers/{id}/bonus — ถ้าสำเร็จ ใช้ bonus_pct/tier
             จาก Backend ตรงๆ (bonus_source='backend')
          3. ถ้าเรียกไม่สำเร็จ fallback ไปคำนวณ tier เองจาก Scoring Config
             thresholds (bonus_source='local_fallback')
          4. ถ้าผลลัพธ์เป็น Tier D แจ้งเตือน HR (_notify_hr_tier_d) โดยไม่
             ให้ error จากการแจ้งเตือนกระทบการคำนวณโบนัสหลัก

        Raises:
            UserError: ถ้ายังไม่ได้เลือก driver ของ record ใดๆ ใน self
        """
        no_driver = self.filtered(lambda r: not r.driver_id)
        if no_driver:
            raise UserError('กรุณาเลือก Driver ก่อน ถึงจะคำนวณโบนัสได้')

        Config = self.env['fleet.telematics.config']
        api_url = Config.get_active_api_url()
        api_key = Config.get_active_api_key()

        for rec in self:
            # ดึง base_salary จากสัญญาจ้างพนักงาน — Odoo 19 ใช้โมเดล
            # hr.version แทน hr.contract เดิม (field: employee_id, wage,
            # is_current — is_current=True คือสัญญาปัจจุบันที่ใช้งานอยู่)
            base_salary = 0.0
            found_from_contract = False

            if 'hr.version' in self.env:
                # is_current เป็น compute field ที่ไม่ได้ stored ในฐานข้อมูล
                # จึงใส่ในเงื่อนไข search() ตรงๆ ไม่ได้ ต้องดึงทุก version
                # ของพนักงานคนนั้นมาก่อน แล้วกรองด้วย Python ภายหลัง
                all_versions = self.env['hr.version'].sudo().search([
                    ('employee_id', '=', rec.driver_id.id),
                ])
                version = all_versions.filtered(lambda v: v.is_current)[:1]
                if version:
                    base_salary = version.wage or 0.0
                    found_from_contract = True
            elif 'hr.contract' in self.env:
                # fallback สำหรับ Odoo เวอร์ชันเก่ากว่า 19 ที่ยังใช้
                # hr.contract แบบเดิม
                contract = self.env['hr.contract'].sudo().search([
                    ('employee_id', '=', rec.driver_id.id),
                    ('state', '=', 'open'),
                ], limit=1)
                base_salary = contract.wage if contract else 0.0
                found_from_contract = bool(contract)

            if not found_from_contract:
                if rec.driver_id.telematics_base_salary:
                    _logger.info(
                        '_apply_backend_bonus: ไม่พบ hr.version ที่ is_current=True '
                        'สำหรับพนักงาน %s — ใช้ telematics_base_salary ที่กรอกไว้บน '
                        'โปรไฟล์พนักงานแทน', rec.driver_id.name,
                    )
                    base_salary = rec.driver_id.telematics_base_salary
                else:
                    _logger.info(
                        '_apply_backend_bonus: ไม่พบเงินเดือนจากทั้ง hr.version และ '
                        'โปรไฟล์พนักงาน — คงค่า Base Salary ที่กรอกเองไว้ในใบนี้ '
                        '(แก้ไขได้ตอน state=Draft เท่านั้น)'
                    )
                    base_salary = rec.base_salary

            bonus_pct = None
            tier = None

            if api_url:
                try:
                    resp = requests.get(
                        f'{api_url}/api/v1/drivers/{rec.driver_id.id}/bonus',
                        headers={'APIKEY': api_key},
                        timeout=10,
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        bonus_pct = float(data.get('bonus_pct', 0) or 0)
                        # ชื่อ field จริงจาก Backend คือ 'incentive_tier' ไม่ใช่ 'tier'
                        tier = data.get('incentive_tier')
                    else:
                        _logger.warning(
                            'Bonus API HTTP %s สำหรับ driver_id=%s — ใช้ fallback',
                            resp.status_code, rec.driver_id.id,
                        )
                except requests.RequestException as e:
                    _logger.warning(
                        'เรียก /drivers/%s/bonus ไม่สำเร็จ (%s) — ใช้ fallback',
                        rec.driver_id.id, e,
                    )

            if bonus_pct is not None:
                rec.write({
                    'base_salary':       base_salary,
                    'bonus_pct':         bonus_pct,
                    'incentive_tier':    tier or rec._local_tier_from_score(),
                    'bonus_source':      'backend',
                    'bonus_last_synced': fields.Datetime.now(),
                })
            else:
                # Fallback: คำนวณ tier เองจาก scoring config thresholds
                tier_fb, pct_fb = rec._local_tier_from_score(return_pct=True)
                rec.write({
                    'base_salary':       base_salary,
                    'bonus_pct':         pct_fb,
                    'incentive_tier':    tier_fb,
                    'bonus_source':      'local_fallback',
                    'bonus_last_synced': fields.Datetime.now(),
                })

            # ตาม FDD Tier D ต้องมี "0% + แจ้งเตือน HR" ไม่ใช่แค่ตั้ง
            # bonus_pct=0 เฉยๆ — ครอบด้วย try/except เพราะเป็นฟีเจอร์เสริม
            # ไม่ควรทำให้ปุ่ม Confirm/คำนวณโบนัสหลักพังไปด้วยถ้าระบบแจ้ง
            # เตือนมีปัญหา
            if rec.incentive_tier == 'D':
                try:
                    rec._notify_hr_tier_d()
                except Exception:
                    _logger.exception(
                        '_notify_hr_tier_d ล้มเหลวสำหรับใบโบนัส id=%s — ข้ามไป '
                        'ไม่ให้กระทบการคำนวณโบนัสหลัก', rec.id,
                    )

    def _local_tier_from_score(self, return_pct=False):
        """คำนวณ Tier จาก avg_score เทียบกับ threshold ของ Scoring Config
        (ใช้เป็น fallback เท่านั้น เมื่อเรียก Backend ไม่สำเร็จ).

        Args:
            return_pct (bool): ถ้า True คืนคู่ (tier, bonus_pct) แทนที่จะ
                คืนแค่ tier เฉยๆ

        Returns:
            str หรือ tuple: tier ('A'/'B'/'C'/'D') หรือ (tier, pct)
        """
        self.ensure_one()
        cfg = self.scoring_config_id or self.env['fleet.telematics.scoring.config'].search(
            [('active', '=', True)], limit=1)
        if cfg and self.avg_score >= cfg.tier_a_min_score:
            tier, pct = 'A', cfg.tier_a_bonus_pct
        elif cfg and self.avg_score >= cfg.tier_b_min_score:
            tier, pct = 'B', cfg.tier_b_bonus_pct
        elif cfg and self.avg_score >= cfg.tier_c_min_score:
            tier, pct = 'C', cfg.tier_c_bonus_pct
        else:
            tier, pct = 'D', 0.0
        return (tier, pct) if return_pct else tier

    def _notify_hr_tier_d(self):
        """แจ้งเตือน HR/Fleet Manager เมื่อคนขับได้ Tier D (0% โบนัส)
        ผ่าน 2 ช่องทาง: chatter ของ record นี้เอง และอีเมลตรงถึงกลุ่ม
        Fleet Manager ที่ทำหน้าที่เป็น HR/ผู้อนุมัติ.

        กันแจ้งซ้ำหลายรอบด้วย flag tier_d_notified — เรียกครั้งเดียวพอ
        แม้จะกด Refresh from Backend ซ้ำๆ ขณะยังเป็น Tier D อยู่เหมือนเดิม
        """
        self.ensure_one()
        if self.tier_d_notified:
            return  # กันแจ้งซ้ำถ้ากด Refresh from Backend หลายรอบ

        # ใช้ Markup(...).format(...) แทนการต่อ f-string ตรงๆ เพื่อให้ค่าที่
        # แทรกเข้าไป (ชื่อพนักงาน, period_label) ถูก escape อัตโนมัติ
        # ป้องกันปัญหาถ้าชื่อพนักงานมีอักขระพิเศษปนอยู่ ในขณะที่ tag HTML
        # ของ template เองยัง render ปกติ
        body = Markup(
            '⚠️ <b>แจ้งเตือน Tier D — พนักงานคะแนนต่ำกว่าเกณฑ์</b><br/>'
            'พนักงาน: <b>{driver_name}</b><br/>'
            'รอบ: {period}<br/>'
            'คะแนนเฉลี่ย: {score} (ต่ำกว่าเกณฑ์ขั้นต่ำของ Tier C)<br/>'
            'ผลลัพธ์: ไม่ได้รับโบนัสในรอบนี้ (0%)<br/><br/>'
            'ตาม FDD §12.4 — Tier D ต้องแจ้งเตือน HR/Fleet Manager เพื่อพิจารณา'
            'ติดตามพฤติกรรมการขับขี่ของพนักงานคนนี้'
        ).format(
            driver_name=self.driver_id.name,
            period=self.period_label,
            score=f'{self.avg_score:.2f}',
        )

        # 1) บันทึกไว้ใน chatter ของ Incentive record เอง
        self.message_post(body=body)

        # หาผู้ใช้ในกลุ่ม Fleet Manager ผ่าน query จาก res.users โดยตรง
        # (เช็คชื่อ field ที่มีอยู่จริงก่อนใช้งาน เพราะ Odoo เปลี่ยนชื่อ field
        # เชื่อมกลุ่มบน res.groups ไปมาระหว่างเวอร์ชัน — 'groups_id' ใน
        # เวอร์ชันเก่า, 'group_ids' ใน Odoo 19+)
        managers_group = self.env.ref('fleet.fleet_group_manager', raise_if_not_found=False)
        manager_users = self.env['res.users']
        if managers_group:
            User = self.env['res.users']
            group_field_candidates = ['groups_id', 'group_ids']
            for fname in group_field_candidates:
                if fname in User._fields:
                    manager_users = User.sudo().search([(fname, 'in', managers_group.ids)])
                    break
            else:
                _logger.warning(
                    '_notify_hr_tier_d: หา field เชื่อมกลุ่มบน res.users ไม่เจอ '
                    '(ลองแล้ว: %s) — ข้ามการส่งอีเมล แจ้งได้แค่ chatter เท่านั้น',
                    group_field_candidates,
                )

        if manager_users:
            self.message_notify(
                partner_ids=manager_users.partner_id.ids,
                body=body,
                subject=f'⚠️ Tier D — {self.driver_id.name} ({self.period_label})',
            )
        else:
            _logger.warning(
                '_notify_hr_tier_d: ไม่พบผู้ใช้ในกลุ่ม Fleet Manager ที่จะแจ้งเตือน '
                '— แจ้งเตือนได้แค่ทาง chatter ของ record นี้เท่านั้น (ใบโบนัส %s)',
                self.id,
            )

        self.tier_d_notified = True

    @api.depends('base_salary', 'bonus_pct')
    def _compute_bonus_amount(self):
        """Bonus (THB) = Base Salary × Bonus % เสมอ — สูตรตายตัว แก้ไข
        ตรงๆ ไม่ได้ ป้องกันตัวเลขหลุดจากสูตร."""
        for rec in self:
            rec.bonus_amount = round(rec.base_salary * rec.bonus_pct / 100, 2)

    def action_refresh_bonus_from_backend(self):
        """ปุ่มในฟอร์ม — ดึง bonus_pct ล่าสุดจาก Backend ใหม่ด้วยมือ"""
        self._apply_backend_bonus()

    # ── ล็อกทุกฟิลด์ถาวรเมื่อพ้น Draft (Confirmed/Approved/Paid) ──────
    # แก้ไขได้ทางเดียวคือกด Reset กลับ Draft ก่อน ป้องกันไม่ให้มีใครแก้
    # ตัวเลขยอดบาทกลางคัน
    _LOCKED_INCENTIVE_FIELDS = {
        'driver_id', 'date_from', 'date_to', 'scoring_config_id', 'note',
        'total_trips', 'total_distance_km', 'avg_score', 'min_score',
        'total_harsh_events', 'total_idle_min',
        'incentive_tier', 'bonus_pct', 'base_salary', 'bonus_amount',
        'bonus_source', 'bonus_last_synced',
    }

    def write(self, vals):
        """บล็อกการแก้ไขฟิลด์ที่ล็อกไว้ (_LOCKED_INCENTIVE_FIELDS) ถ้าใบ
        โบนัสนี้ผ่านพ้น state='draft' ไปแล้ว.

        Raises:
            UserError: ถ้าพยายามแก้ไขฟิลด์ที่ล็อกไว้ในขณะที่ state != 'draft'
        """
        touched = self._LOCKED_INCENTIVE_FIELDS.intersection(vals.keys())
        if touched:
            for rec in self:
                if rec.state != 'draft':
                    raise UserError(
                        'ใบโบนัสนี้ผ่านสถานะ Draft ไปแล้ว (Confirmed ขึ้นไป) — '
                        'แก้ไขข้อมูลผลงาน/โบนัสไม่ได้อีก เพื่อความโปร่งใส\n\n'
                        'ถ้าต้องการแก้ไข: กด "Reset" กลับเป็น Draft ก่อน'
                    )
        return super().write(vals)

    def action_export_to_appraisal(self):
        """ส่งสรุปผลโบนัสไปบันทึกในประวัติพนักงาน (chatter ของ hr.employee)
        และผูกเข้า appraisal ล่าสุด ถ้ามีโมดูล hr_appraisal ติดตั้งอยู่.

        ไม่ผูก hard-dependency กับ hr_appraisal (อาจไม่ได้ติดตั้ง) — เขียน
        สรุปลง chatter ของพนักงานเสมอเป็นหลัก

        Returns:
            dict: action แสดง notification สำเร็จ

        Raises:
            UserError: ถ้าใบโบนัสยังไม่ผ่าน state 'approved' หรือ 'paid'
        """
        self.ensure_one()
        if self.state not in ('approved', 'paid'):
            raise UserError(
                'ต้อง Approve ใบโบนัสนี้ก่อน ถึงจะส่งออกไปยังระบบประเมินผลได้'
            )
        summary = (
            f'📊 สรุปผลโบนัส Fleet Telematics — {self.period_label}\n'
            f'Avg Score: {self.avg_score:.2f} | Min Score: {self.min_score:.2f} | '
            f'Total Trips: {self.total_trips}\n'
            f'Tier: {self.incentive_tier} | Bonus: {self.bonus_pct:.2f}% '
            f'= {self.bonus_amount:,.2f} THB'
        )
        self.driver_id.message_post(body=summary)

        # ถ้ามีโมดูล hr_appraisal ติดตั้งอยู่ (optional) ผูกเข้า appraisal
        # ล่าสุดของพนักงานคนนี้ด้วย — ถ้าไม่มีโมดูลนี้ก็แค่ข้ามไปเงียบๆ
        Appraisal = self.env.get('hr.appraisal')
        appraisal_linked = False
        if Appraisal is not None:
            appraisal = Appraisal.sudo().search(
                [('employee_id', '=', self.driver_id.id)],
                order='create_date desc', limit=1,
            )
            if appraisal:
                appraisal.message_post(body=summary)
                appraisal_linked = True

        self.message_post(body=f'📤 ส่งออกสรุปผลไปยังประวัติพนักงานแล้ว โดย {self.env.user.name}')
        return {
            'type': 'ir.actions.client',
            'tag':  'display_notification',
            'params': {
                'title': '📤 ส่งออกสำเร็จ',
                'message': (
                    'บันทึกสรุปผลไปที่ประวัติพนักงานแล้ว'
                    + (' และผูกเข้า Appraisal ล่าสุดแล้ว' if appraisal_linked else '')
                ),
                'type': 'success',
            },
        }

    def action_confirm(self):
        """Draft → Confirmed — ดึง/อัปเดต bonus_pct จาก Backend ครั้งสุดท้าย
        ก่อนล็อกตัวเลข แล้วบันทึก audit log ลง chatter."""
        for rec in self:
            if rec.state == 'draft':
                rec._apply_backend_bonus()
                rec.state = 'confirmed'
                rec.message_post(
                    body=(
                        f'✅ Confirmed โดย {self.env.user.name} — '
                        f'Tier {rec.incentive_tier}, Bonus {rec.bonus_pct}% '
                        f'= {rec.bonus_amount:,.2f} THB (Source: {rec.bonus_source or "-"})'
                    )
                )

    def action_approve(self):
        """Confirmed → Approved — บันทึกผู้อนุมัติและ audit log ลง chatter."""
        for rec in self:
            if rec.state == 'confirmed':
                rec.state       = 'approved'
                rec.approved_by = self.env.user
                rec.message_post(
                    body=(
                        f'👍 Approved โดย {self.env.user.name} — '
                        f'ยอดโบนัสที่อนุมัติ: {rec.bonus_amount:,.2f} THB '
                        f'({rec.driver_id.name}, {rec.period_label})'
                    )
                )

    def action_mark_paid(self):
        """Approved → Paid — บันทึก audit log ลง chatter ว่าจ่ายเงินแล้ว."""
        for rec in self:
            if rec.state == 'approved':
                rec.state = 'paid'
                rec.message_post(
                    body=(
                        f'💰 Marked as Paid โดย {self.env.user.name} — '
                        f'{rec.bonus_amount:,.2f} THB ({rec.driver_id.name}, {rec.period_label})'
                    )
                )

    def action_reset(self):
        """Confirmed/Approved → Draft — ปลดล็อกให้แก้ไขข้อมูลได้ใหม่
        พร้อมล้างผู้อนุมัติเดิมและบันทึก audit log ลง chatter."""
        for rec in self:
            if rec.state in ('confirmed', 'approved'):
                old_state        = rec.state
                rec.state       = 'draft'
                rec.approved_by = False
                rec.message_post(
                    body=(
                        f'↩️ Reset กลับเป็น Draft โดย {self.env.user.name} '
                        f'(เดิม: {old_state}) — {rec.driver_id.name}, {rec.period_label}'
                    )
                )

    @api.model
    def _cron_calculate_monthly_incentive(self):
        """สร้างใบโบนัส Draft อัตโนมัติทุกวันที่ 1 ของเดือน สำหรับคนขับ
        ทุกคนที่มี trip ในเดือนปฏิทินก่อนหน้า.

        สร้างด้วย date_from/date_to ที่ครอบคลุมเต็มเดือนปฏิทินก่อนหน้า
        (ไม่ใช่รอบตัดวิกแบบกำหนดเอง — อันนั้นต้องสร้างด้วยมือผ่านฟอร์ม)
        กันสร้างซ้ำด้วยการเช็คว่ามี record ของคนขับ+ช่วงวันที่นี้อยู่แล้ว
        หรือยัง ก่อน create() ทุกครั้ง (คนขับแต่ละคนมีได้เพียง 1 record
        ต่อรอบ)
        """
        today = date.today()
        if today.month == 1:
            period_year, period_month = today.year - 1, 12
        else:
            period_year, period_month = today.year, today.month - 1

        date_from = date(period_year, period_month, 1)
        date_to = (
            date(period_year + 1, 1, 1) if period_month == 12
            else date(period_year, period_month + 1, 1)
        ) - timedelta(days=1)  # date_to เป็น "วันสุดท้ายที่รวมอยู่ในช่วง"

        cfg = self.env['fleet.telematics.scoring.config'].sudo().search(
            [('active', '=', True)], limit=1)

        TripLog     = self.env['fleet.telematics.log'].sudo()
        date_to_excl = date_to + timedelta(days=1)

        logs = TripLog.search([
            ('trip_start', '>=', str(date_from)),
            ('trip_start', '<',  str(date_to_excl)),
            ('state', '=', 'synced'),
        ])

        created = 0
        for driver in logs.mapped('driver_id'):
            if self.search([
                ('driver_id', '=', driver.id),
                ('date_from', '=', str(date_from)),
                ('date_to',   '=', str(date_to)),
            ], limit=1):
                continue  # dedup — driver แต่ละคนมีได้เพียง 1 record ต่อรอบ

            new_rec = self.create({
                'driver_id':         driver.id,
                'scoring_config_id': cfg.id if cfg else False,
                'date_from':         date_from,
                'date_to':           date_to,
                'state':             'draft',
            })
            new_rec._apply_backend_bonus()
            created += 1

        _logger.info(
            'cron_monthly_incentive: สร้าง %d records สำหรับ %02d/%d',
            created, period_month, period_year
        )