# ==============================================================================
# models/telematics_scoring.py
#
# เกณฑ์คะแนนพฤติกรรมการขับขี่ (Scoring Config) — Admin ปรับน้ำหนักคะแนน/
# threshold ต่างๆ ได้เองจากหน้าจอ ไม่ต้องแก้โค้ด แล้วกด Push ส่งเกณฑ์นี้ไป
# ให้ Backend ใช้คำนวณคะแนนจริงตอนประมวลผลทริป
#
# workflow: สร้าง (Active=False) → กรอกเกณฑ์ → Approve (ต้องเป็น Fleet
# Manager) → Push Config → เปิด Active (ล็อกฟอร์มถาวรจนกว่าจะปิด Active)
# ==============================================================================
import logging
import requests

from odoo import models, fields, api
from odoo.exceptions import ValidationError, UserError

_logger = logging.getLogger(__name__)


class TelematicsScoringConfig(models.Model):
    """เกณฑ์คะแนน 1 ชุด — อนุญาตให้ Active พร้อมกันได้แค่ชุดเดียวในระบบ"""
    _name        = 'fleet.telematics.scoring.config'
    _description = 'Fleet Telematics Scoring Configuration'
    _order       = 'effective_date desc'

    name           = fields.Char(string='Config Name', required=True)
    is_active      = fields.Boolean(string='Active', default=False,
        help='Active ได้เพียง 1 config เท่านั้น — ตอนสร้างใหม่ต้องปิดไว้ก่อน '
             'เพื่อให้กรอกข้อมูลและทดสอบ Push ได้ก่อนเปิดใช้งานจริง\n\n'
             'หมายเหตุทางเทคนิค: ตั้งชื่อฟิลด์เป็น is_active (ไม่ใช่ active '
             'เฉยๆ) เพราะ Odoo สงวนชื่อฟิลด์ "active" ไว้เป็นกลไก archive '
             'พิเศษ — ถ้าใช้ชื่อ active ตรงๆ พอ Config ถูกปิด (False) Odoo '
             'จะซ่อน record นั้นออกจาก List View/หน้าฟอร์มโดยอัตโนมัติทันที '
             '(เหมือนถูกลบ ทั้งที่ข้อมูลยังอยู่ในฐานข้อมูลจริง) แก้บั๊กนี้โดย '
             'เปลี่ยนชื่อฟิลด์เป็น is_active แทน')
    effective_date = fields.Date(string='Effective Date', required=True)

    # ── คะแนนพื้นฐาน ────────────────────────────────────────────────────
    score_base          = fields.Float(string='Base Score (เต็ม)', default=100.0)
    max_deduct_per_trip = fields.Float(string='Max Deduct / Trip', default=50.0)

    # ── คะแนนที่หักต่อครั้งของแต่ละพฤติกรรม ─────────────────────────────
    harsh_brake_deduct  = fields.Float(string='Harsh Brake Deduct',  default=5.0)
    harsh_accel_deduct  = fields.Float(string='Harsh Accel Deduct',  default=3.0)
    harsh_corner_deduct = fields.Float(string='Harsh Corner Deduct', default=3.0)
    speeding_deduct     = fields.Float(string='Speeding Deduct',     default=10.0)
    idling_deduct       = fields.Float(string='Idling Deduct',       default=2.0)
    bump_deduct         = fields.Float(string='Bump Deduct',         default=4.0)

    # เพิ่มตามที่ผู้ใช้ขอ: Live Formula Preview — โชว์สูตรคำนวณคะแนนสดๆ
    # ตามค่าที่กำลังกรอกอยู่ในฟอร์ม โดยไม่ต้อง save ก่อน (field นี้ตั้งใจ
    # ไม่ store=True เพื่อให้ Odoo คำนวณใหม่ให้อัตโนมัติทุกครั้งที่ field ใน
    # @api.depends เปลี่ยนค่าในฟอร์ม แม้ยังไม่ได้กด save — เป็นพฤติกรรม
    # มาตรฐานของ non-stored compute field ในหน้าฟอร์ม Odoo อยู่แล้ว ไม่ต้อง
    # เขียน JS widget เพิ่มเอง)
    formula_preview = fields.Html(
        string='สูตรคำนวณคะแนน (Live Preview)',
        compute='_compute_formula_preview', sanitize=False)

    @api.depends('score_base', 'max_deduct_per_trip',
                 'harsh_brake_deduct', 'harsh_accel_deduct', 'harsh_corner_deduct',
                 'speeding_deduct', 'idling_deduct', 'bump_deduct')
    def _compute_formula_preview(self):
        for rec in self:
            fmt = lambda v: f'{v:g}'  # ตัด .0 ท้ายตัวเลขที่ไม่จำเป็นออก อ่านง่ายขึ้น
            rec.formula_preview = f'''
                <div style="font-family:monospace; font-size:13px; line-height:1.9; padding:8px 12px; background:#f8f9fa; border-radius:6px; border:1px solid #dee2e6;">
                    <b>Score</b> = {fmt(rec.score_base)}
                    − (Brake × <span style="color:#ef4444">{fmt(rec.harsh_brake_deduct)}</span>)
                    − (Accel × <span style="color:#f97316">{fmt(rec.harsh_accel_deduct)}</span>)
                    − (Corner × <span style="color:#f59e0b">{fmt(rec.harsh_corner_deduct)}</span>)
                    − (Speeding × <span style="color:#8b5cf6">{fmt(rec.speeding_deduct)}</span>)
                    − (Idling × <span style="color:#0ea5e9">{fmt(rec.idling_deduct)}</span>)
                    − (Bump × <span style="color:#64748b">{fmt(rec.bump_deduct)}</span>)
                    <br/>
                    <span class="text-muted">หักได้สูงสุด {fmt(rec.max_deduct_per_trip)} คะแนน/ทริป
                    (ต่อให้พฤติกรรมเสี่ยงรวมกันหักเกินนี้ คะแนนก็จะไม่ต่ำกว่า
                    {fmt(max(rec.score_base - rec.max_deduct_per_trip, 0))})</span>
                </div>
            '''

    # ── เกณฑ์ตัดสินว่าเป็นพฤติกรรมเสี่ยงหรือไม่ ──────────────────────────
    harsh_brake_g      = fields.Float(string='Brake G Threshold',         default=0.40)
    harsh_accel_g      = fields.Float(string='Accel G Threshold',         default=0.40)
    harsh_corner_g     = fields.Float(string='Corner G Threshold',        default=0.40)
    speeding_kmh_over  = fields.Float(string='Speeding (km/h เกินกำหนด)', default=20.0)
    idle_min_threshold = fields.Float(string='Idle Min Threshold (min)',   default=5.0)

    # ── ความเร็วจำกัดแยกโซน (กทม./นอกเมือง) ──────────────────────────────
    # ส่งไปพร้อม Push Config ให้ Backend ใช้ตัดสินว่า event ไหนนับเป็น
    # "speeding" ตามโซนที่รถวิ่งอยู่จริง — ทำงานคู่กับ zone_label/
    # speed_limit_kmh ที่คำนวณไว้บน fleet.telematics.event ฝั่ง Odoo เพื่อ
    # cross-check ย้อนหลังได้
    speed_limit_bkk = fields.Float(
        string='ความเร็วจำกัดในกรุงเทพฯ (km/h)', default=80.0,
        help='ใช้กับ event ที่พิกัดอยู่ในเขตกรุงเทพฯ')
    speed_limit_upcountry = fields.Float(
        string='ความเร็วจำกัดนอกเมือง (km/h)', default=90.0,
        help='ใช้กับ event ที่พิกัดอยู่นอกเขตกรุงเทพฯ')

    # ── เกณฑ์ Tier และ % โบนัส ────────────────────────────────────────────
    # แก้ตามที่ผู้ใช้ขอ 2026-08-04: เดิมเป็น 8 fields ตายตัว (tier_a...tier_d
    # ×2 แต่ละอัน) ตั้งได้แค่ 4 ระดับ A/B/C/D เท่านั้น แก้จำนวน tier ไม่ได้
    # เลยถ้าไม่แก้โค้ด — เปลี่ยนเป็น One2many แบบไดนามิก ให้ Admin เพิ่ม/ลบ/
    # เปลี่ยนชื่อ Tier ได้เองจากหน้าจอ ตรงตาม FDD §12.3 ที่ระบุว่า "Admin
    # กำหนดเป็น Many2many ใน config ให้ Admin เพิ่ม/ลบ tier เองได้"
    #
    # Default 3 แถวตอนสร้างใหม่ (A/B/C) ให้พฤติกรรมเดิมยังใช้ได้ทันทีโดยไม่
    # ต้องตั้งค่าเองใหม่หมด — เข้ากันได้กับพฤติกรรมเดิมที่เคย hardcode ไว้
    # (Tier ต่ำกว่าเกณฑ์ที่ตั้งไว้ทั้งหมด ถือเป็น "Below Minimum" เสมอ ดู
    # _get_tier_for_score() ใน telematics_incentive.py/telematics_log.py)
    def _default_tier_ids(self):
        return [
            (0, 0, {'name': 'A', 'min_score': 90.0, 'bonus_pct': 10.0}),
            (0, 0, {'name': 'B', 'min_score': 75.0, 'bonus_pct': 5.0}),
            (0, 0, {'name': 'C', 'min_score': 60.0, 'bonus_pct': 0.0}),
        ]

    tier_ids = fields.One2many(
        'fleet.telematics.scoring.tier', 'scoring_config_id',
        string='Tiers', default=_default_tier_ids,
        help='กำหนด Tier ได้เอง เพิ่ม/ลบ/เปลี่ยนชื่อได้ตามต้องการ ไม่ต้องมี '
             'แค่ 4 ระดับตายตัวอีกต่อไป — เรียงจาก Min Score มากไปน้อย '
             'ระบบจะจัดพนักงานเข้า Tier แรกที่คะแนนเฉลี่ยของเขา ≥ Min Score')

    # เพิ่ม 2026-08-04: สรุป Tier แบบข้อความสั้นๆ ไว้โชว์ในหน้า List — เดิม
    # หน้า List เคยโชว์ tier_a_min_score/tier_b_min_score/tier_c_min_score
    # เป็นคอลัมน์ตรงๆ ได้ แต่พอเปลี่ยนเป็น tier_ids (One2many) จะโชว์เป็น
    # คอลัมน์แบบนั้นไม่ได้อีกแล้ว (ไม่มีจำนวนคอลัมน์คงที่) เลยสรุปเป็น
    # ข้อความเดียวแทน เช่น "A≥90 / B≥75 / C≥60"
    tier_summary = fields.Char(
        string='Tiers', compute='_compute_tier_summary')

    @api.depends('tier_ids.name', 'tier_ids.min_score')
    def _compute_tier_summary(self):
        for rec in self:
            tiers = rec.tier_ids.sorted(key=lambda t: t.min_score, reverse=True)
            rec.tier_summary = ' / '.join(
                f'{t.name}≥{t.min_score:g}' for t in tiers) if tiers else '—'

    # ── History (readonly) — ตาม FDD §12.5: track ว่า config นี้ถูกใช้กับ
    # กี่ trip แล้ว ต่างจาก last_push_at/last_push_status ที่ track แค่การ
    # ส่งไป Backend เท่านั้น ไม่ใช่การใช้งานจริง ──────────────────────────
    created_date = fields.Datetime(
        string='Created Date', readonly=True,
        default=lambda self: fields.Datetime.now())
    last_used_date = fields.Datetime(
        string='Last Used Date', readonly=True,
        help='วันเวลาล่าสุดที่มี Trip ใช้ config ชุดนี้คำนวณคะแนน')
    total_trips_calculated = fields.Integer(
        string='Total Trips Calculated', readonly=True, default=0,
        help='จำนวน Trip สะสมที่เคยใช้ config ชุดนี้คำนวณคะแนนแล้ว')

    last_push_at     = fields.Datetime(string='Last Pushed At', readonly=True)
    last_push_status = fields.Char(string='Push Status',        readonly=True)

    # ต้องอนุมัติก่อนถึงจะ Push Config ไป Backend ได้จริง (บังคับใน
    # action_push_to_backend) — อนุมัติได้เฉพาะกลุ่ม Fleet Manager
    approved_by_id = fields.Many2one(
        'res.users', string='ผู้อนุมัติ', readonly=True,
        help='ผู้มีอำนาจอนุมัติเกณฑ์คะแนนชุดนี้ก่อนนำไปใช้จริง')
    approved_at = fields.Datetime(string='วันที่อนุมัติ', readonly=True)

    is_locked = fields.Boolean(
        string='ล็อกการแก้ไข', compute='_compute_is_locked',
        help='True เมื่อ Active=True เท่านั้น — ฟิลด์เกณฑ์ทั้งหมดจะแก้ไขไม่ได้ '
             'จนกว่าจะปิด Active (ตอน Active=False แก้ไข/Push ซ้ำได้เรื่อยๆ)')

    @api.depends('is_active')
    def _compute_is_locked(self):
        for rec in self:
            rec.is_locked = bool(rec.is_active)

    # ── ตรวจสอบความสมเหตุสมผลของค่าที่กรอก ──────────────────────────────

    @api.constrains('is_active')
    def _check_single_active(self):
        """ห้ามมี Config ที่ Active=True พร้อมกันเกิน 1 ชุดในระบบ"""
        for rec in self:
            if rec.is_active:
                others = self.search([('is_active', '=', True), ('id', '!=', rec.id)])
                if others:
                    raise ValidationError(
                        f'มี Scoring Config ที่ Active อยู่แล้ว: "{others[0].name}"\n'
                        'กรุณา deactivate config นั้นก่อน'
                    )

    @api.constrains('tier_ids')
    def _check_tier_ids(self):
        """เช็คความสมเหตุสมผลของ Tier แบบไดนามิก (แทนที่ constraint เดิม
        ที่เช็ค tier_a > tier_b > tier_c > 0 แบบตายตัว):
        - min_score ห้ามติดลบ
        - bonus_pct ห้ามติดลบ
        - ห้ามมี 2 tier ที่ min_score เท่ากันใน config เดียวกัน (จะเลือกไม่
          ได้ว่าใครมาก่อน)"""
        for rec in self:
            seen_scores = set()
            for tier in rec.tier_ids:
                if tier.min_score < 0:
                    raise ValidationError(
                        f'Tier "{tier.name}": Min Score ต้องไม่ติดลบ')
                if tier.bonus_pct < 0:
                    raise ValidationError(
                        f'Tier "{tier.name}": Bonus % ต้องไม่ติดลบ')
                if tier.min_score in seen_scores:
                    raise ValidationError(
                        f'มี Tier มากกว่า 1 รายการที่ Min Score = {tier.min_score} '
                        f'ในเกณฑ์ชุดเดียวกัน — ห้ามซ้ำ')
                seen_scores.add(tier.min_score)

    @api.constrains(
        'harsh_brake_deduct', 'harsh_accel_deduct', 'harsh_corner_deduct',
        'speeding_deduct', 'idling_deduct', 'bump_deduct',
        'score_base', 'max_deduct_per_trip',
    )
    def _check_positive_deducts(self):
        """คะแนนหักทุกประเภทต้อง >= 0 และคะแนนเต็มต้อง > 0"""
        deduct_fields = [
            ('harsh_brake_deduct',  'Harsh Brake Deduct'),
            ('harsh_accel_deduct',  'Harsh Accel Deduct'),
            ('harsh_corner_deduct', 'Harsh Corner Deduct'),
            ('speeding_deduct',     'Speeding Deduct'),
            ('idling_deduct',       'Idling Deduct'),
            ('bump_deduct',         'Bump Deduct'),
            ('max_deduct_per_trip', 'Max Deduct / Trip'),
        ]
        for rec in self:
            if rec.score_base <= 0:
                raise ValidationError(f'Base Score ต้องมากกว่า 0 (ค่าที่กรอก: {rec.score_base})')
            for field_name, label in deduct_fields:
                if getattr(rec, field_name, 0) < 0:
                    raise ValidationError(f'{label} ต้องมีค่า >= 0 (ค่าที่กรอก: {getattr(rec, field_name)})')

    @api.constrains('harsh_brake_g', 'harsh_accel_g', 'harsh_corner_g',
                    'speeding_kmh_over', 'idle_min_threshold')
    def _check_positive_thresholds(self):
        """threshold ตรวจจับพฤติกรรมเสี่ยงทุกตัวต้องมากกว่า 0"""
        threshold_fields = [
            ('harsh_brake_g',      'Brake G Threshold'),
            ('harsh_accel_g',      'Accel G Threshold'),
            ('harsh_corner_g',     'Corner G Threshold'),
            ('speeding_kmh_over',  'Speeding km/h'),
            ('idle_min_threshold', 'Idle Min Threshold'),
        ]
        for rec in self:
            for field_name, label in threshold_fields:
                if getattr(rec, field_name, 0) <= 0:
                    raise ValidationError(f'{label} ต้องมากกว่า 0 (ค่าที่กรอก: {getattr(rec, field_name)})')

    @api.constrains('speed_limit_bkk', 'speed_limit_upcountry')
    def _check_speed_limit_zone(self):
        """ความเร็วจำกัดต้องมากกว่า 0 และในกรุงเทพฯ ต้องไม่สูงกว่านอกเมือง"""
        for rec in self:
            if rec.speed_limit_bkk <= 0 or rec.speed_limit_upcountry <= 0:
                raise ValidationError('ความเร็วจำกัดตามโซน (กรุงเทพฯ/นอกเมือง) ต้องมากกว่า 0')
            if rec.speed_limit_bkk > rec.speed_limit_upcountry:
                raise ValidationError(
                    f'ความเร็วจำกัดในกรุงเทพฯ ({rec.speed_limit_bkk}) ไม่ควรสูงกว่า '
                    f'นอกเมือง ({rec.speed_limit_upcountry}) — ตรวจค่าที่กรอกอีกครั้ง'
                )

    @api.constrains('score_base', 'max_deduct_per_trip')
    def _check_max_deduct_not_exceed_base(self):
        """หักคะแนนสูงสุดต่อทริปต้องไม่เกินคะแนนเต็ม"""
        for rec in self:
            if rec.max_deduct_per_trip > rec.score_base:
                raise ValidationError(
                    f'Max Deduct / Trip ({rec.max_deduct_per_trip}) ต้องไม่เกิน Base Score ({rec.score_base})'
                )

    # ── ล็อกฟิลด์เกณฑ์คะแนนทั้งหมดเมื่อ Active=True ──────────────────────
    # นี่คือชั้น Python (บังคับจริงแม้เรียกผ่าน API/RPC ตรงๆ) ส่วนชั้น XML
    # (readonly บนฟอร์ม) อยู่ที่ views/telematics_scoring_views.xml — ไม่ล็อก
    # field สถานะ (last_push_at, approved_by_id ฯลฯ) และไม่ล็อก 'active' เอง
    # เพื่อให้ผู้ใช้ปิด Active ปลดล็อกฟิลด์อื่นได้เสมอ
    _LOCKED_CONFIG_FIELDS = {
        'name', 'effective_date',
        'score_base', 'max_deduct_per_trip',
        'harsh_brake_deduct', 'harsh_accel_deduct', 'harsh_corner_deduct',
        'speeding_deduct', 'idling_deduct', 'bump_deduct',
        'harsh_brake_g', 'harsh_accel_g', 'harsh_corner_g',
        'speeding_kmh_over', 'idle_min_threshold',
        'speed_limit_bkk', 'speed_limit_upcountry',
        'tier_ids',
    }

    def write(self, vals):
        touched = self._LOCKED_CONFIG_FIELDS.intersection(vals.keys())
        if touched:
            for rec in self:
                if rec.is_active:
                    raise UserError(
                        'Config นี้ Active อยู่ — แก้ไขเกณฑ์คะแนนไม่ได้ '
                        'เพื่อความโปร่งใสระหว่างรอบประเมิน\n\n'
                        'วิธีแก้ไข: ปิด Active ก่อน (หรือสร้าง Config เวอร์ชันใหม่แทน)'
                    )
        return super().write(vals)

    def action_deactivate(self):
        """ปิด Active ของ config ชุดนี้ — แค่ปลดล็อกให้แก้ไขเกณฑ์คะแนนได้อีก
        ครั้ง ข้อมูลทั้งหมด (รวมถึงผู้อนุมัติ/วันที่อนุมัติ) ยังเก็บไว้ครบ
        ไม่ได้ล้างหรือลบทิ้ง — popup ยืนยันก่อนกดอยู่ที่ปุ่มในฟอร์ม (ดู
        views/telematics_scoring_views.xml, attribute confirm=)"""
        self.ensure_one()
        self.write({'is_active': False})
        return {
            'type': 'ir.actions.client',
            'tag':  'display_notification',
            'params': {
                'title':   '🔓 ปิด Active แล้ว',
                'message': f'Config "{self.name}" ปิด Active แล้ว — แก้ไขเกณฑ์คะแนนได้อีกครั้ง (ข้อมูลเดิมยังเก็บไว้ครบ)',
                'type':    'success',
                # แก้บั๊ก 2026-07-24: display_notification เฉยๆ ไม่ทำให้ฟอร์ม
                # รีเฟรชค่าฟิลด์ให้อัตโนมัติ (ต้องกดรีเฟรชเองค่าถึงจะขึ้น) —
                # เพิ่ม next: soft_reload ให้รีโหลดฟอร์มปัจจุบันทันทีหลังขึ้น
                # แจ้งเตือน โดยไม่ต้อง refresh หน้าเบราว์เซอร์เอง
                'next': {'type': 'ir.actions.client', 'tag': 'soft_reload'},
            },
        }

    def action_approve(self):
        """อนุมัติเกณฑ์คะแนนชุดนี้ — เฉพาะกลุ่ม Fleet Manager กดได้
        กด Approve แล้วระบบจะติ๊ก Active ให้อัตโนมัติทันที ถ้ามี config อื่นที่
        Active อยู่ก่อนแล้ว ระบบจะปิด Active ของชุดเดิมให้อัตโนมัติเช่นกัน
        (แค่ปิด Active เท่านั้น — ข้อมูล/ประวัติของ config ชุดเดิมยังเก็บไว้
        ครบ ไม่ได้ลบทิ้ง ดูย้อนหลังได้ตามปกติ)"""
        self.ensure_one()
        if not self.env.user.has_group('fleet.fleet_group_manager'):
            raise UserError('เฉพาะ Fleet Manager เท่านั้นที่มีสิทธิ์อนุมัติ Scoring Config')

        others = self.search([('is_active', '=', True), ('id', '!=', self.id)])
        deactivated_name = others[0].name if others else None
        if others:
            others.write({'is_active': False})

        self.write({
            'approved_by_id': self.env.user.id,
            'approved_at':    fields.Datetime.now(),
            'is_active':      True,
        })

        msg = f'{self.env.user.name} อนุมัติ Config "{self.name}" แล้ว และเปิด Active ให้อัตโนมัติ'
        if deactivated_name:
            msg += f'\nปิด Active ของ Config เดิม "{deactivated_name}" ให้อัตโนมัติ (ข้อมูลยังเก็บไว้ครบ ไม่ได้ลบ)'

        return {
            'type': 'ir.actions.client',
            'tag':  'display_notification',
            'params': {
                'title':   '✅ อนุมัติแล้ว',
                'message': msg,
                'type':    'success',
                'sticky':  bool(deactivated_name),
                # แก้บั๊ก 2026-07-24: เดิมกด Approve แล้วช่อง Active/ผู้อนุมัติ
                # บนฟอร์มไม่อัปเดตให้เห็นทันที (ต้องออกไปแล้วกลับเข้ามาใหม่ถึง
                # จะเห็นค่าที่ถูกต้อง) เพิ่ม next: soft_reload ให้รีโหลดฟอร์ม
                # ปัจจุบันทันทีหลังขึ้นแจ้งเตือน เห็นสวิตช์ Active เขียวทันที
                'next': {'type': 'ir.actions.client', 'tag': 'soft_reload'},
            },
        }

    def _get_base_url(self):
        """ดึง Base URL ของ Backend มาจาก config พร้อมตัด path ที่กรอกเกินมา
        (เช่นกรอก .../api/v1 มาด้วย) ให้เหลือแค่ scheme+host+port"""
        ICP     = self.env['ir.config_parameter'].sudo()
        api_url = ICP.get_param('fleet_telematics.mtd_api_url', '').rstrip('/')
        if not api_url:
            raise UserError(
                'ยังไม่ได้ตั้งค่า MTD API URL\n'
                'ไปที่ Fleet Telematics → Settings แล้วกรอก:\n'
                'http://192.168.1.43:8001'
            )
        for suffix in ['/api/v1', '/api']:
            if api_url.endswith(suffix):
                api_url = api_url[: -len(suffix)]
                break
        return api_url

    def _build_config_payload(self):
        """แปลงเกณฑ์คะแนนทั้งหมดในฟอร์มนี้ เป็น dict ตาม schema ที่ Backend
        ต้องการ สำหรับส่งไปตอนกด Push Config (POST /api/v1/config/scoring)

        แก้ 2026-08-04: เปลี่ยน tier_a_min_score...tier_d_bonus_pct (8 key
        ตายตัว) เป็น key เดียว "tiers" ที่เป็น list แทน เพราะ Tier ฝั่ง Odoo
        เปลี่ยนเป็นแบบไดนามิกแล้ว (เพิ่ม/ลบได้ ไม่ตายตัวที่ 4 ระดับอีกต่อไป)
        — ⚠️ เป็น BREAKING CHANGE ของ API contract เดิม ต้องอัปเดตฝั่ง
        Backend ให้อ่าน "tiers" (list ของ {name, min_score, bonus_pct})
        แทนที่ 8 key แบบเดิมด้วย ไม่งั้น Backend จะอ่าน tier ไม่ได้เลย"""
        return {
            'config_name':         self.name,
            'score_base':          self.score_base,
            'speeding_deduct':     self.speeding_deduct,
            'harsh_brake_deduct':  self.harsh_brake_deduct,
            'harsh_accel_deduct':  self.harsh_accel_deduct,
            'harsh_corner_deduct': self.harsh_corner_deduct,
            'idling_deduct':       self.idling_deduct,
            'bump_deduct':         self.bump_deduct,
            'harsh_brake_g':       self.harsh_brake_g,
            'harsh_accel_g':       self.harsh_accel_g,
            'harsh_corner_g':      self.harsh_corner_g,
            'speeding_kmh_over':   self.speeding_kmh_over,
            'idle_min_threshold':  self.idle_min_threshold,
            'speed_limit_bkk':        self.speed_limit_bkk,
            'speed_limit_upcountry':  self.speed_limit_upcountry,
            'tiers': [
                {
                    'name':      tier.name,
                    'min_score': tier.min_score,
                    'bonus_pct': tier.bonus_pct,
                }
                for tier in self.tier_ids.sorted(key=lambda t: t.min_score, reverse=True)
            ],
            'max_deduct_per_trip': self.max_deduct_per_trip,
            'is_active':           self.is_active,
            'synced_from_odoo_at': (
                self.effective_date.isoformat() if self.effective_date else None
            ),
        }

    def action_push_to_backend(self):
        """ส่งเกณฑ์คะแนนทั้งหมดไปให้ Backend ใช้งานจริง (POST /api/v1/
        config/scoring) — ต้องผ่านการอนุมัติก่อนเท่านั้นถึงจะกดได้"""
        self.ensure_one()
        if not self.approved_by_id:
            raise UserError(
                'Config นี้ยังไม่ได้รับการอนุมัติ — กด "✅ Approve" ก่อน Push ไป Backend\n'
                '(เฉพาะ Fleet Manager เท่านั้นที่อนุมัติได้)'
            )
        base_url = self._get_base_url()
        endpoint = f'{base_url}/api/v1/config/scoring'
        payload  = self._build_config_payload()

        # แก้บั๊ก 2026-07-24: เดิมไม่ได้ส่ง APIKEY header ไปด้วย ทำให้ Backend
        # ตอบ 403 Forbidden กลับมาเสมอ — โมเดลอื่นทุกตัวในระบบส่ง header นี้
        # ผ่าน fleet.telematics.config.get_active_api_key() หมดแล้ว จุดนี้จุด
        # เดียวที่ตกหล่นไป
        Config  = self.env['fleet.telematics.config']
        api_key = Config.get_active_api_key()

        _logger.info('action_push_to_backend: POST %s | config_name=%s', endpoint, self.name)

        try:
            resp = requests.post(
                endpoint,
                headers={'APIKEY': api_key, 'Content-Type': 'application/json'},
                json=payload,
                timeout=15,
            )
            resp.raise_for_status()

            try:
                resp_cfg     = resp.json().get('config', {})
                backend_name = resp_cfg.get('config_name', self.name)
                msg = f"Config '{backend_name}' activated บน Backend แล้ว"
            except Exception:
                msg = f'Backend ตอบกลับ {resp.status_code}'

            self.write({
                'last_push_at':     fields.Datetime.now(),
                'last_push_status': f'OK {resp.status_code}',
            })
            return {
                'type': 'ir.actions.client',
                'tag':  'display_notification',
                'params': {
                    'title':   '💾 Push Config สำเร็จ ✅',
                    'message': msg,
                    'type':    'success',
                    'sticky':  False,
                    # แก้บั๊กเดียวกับ Approve/ปิด Active — ให้ Last Pushed At /
                    # Push Status อัปเดตให้เห็นทันทีไม่ต้องรีเฟรชเอง
                    'next': {'type': 'ir.actions.client', 'tag': 'soft_reload'},
                },
            }
        except requests.RequestException as e:
            self.write({'last_push_status': f'ERROR: {e}'})
            raise UserError(f'ส่งค่าไป Backend ไม่สำเร็จ:\n{e}')

    def action_test_connection(self):
        """ทดสอบว่าเชื่อมต่อ Backend ได้ไหม (GET / — ไม่มี /health แยก จึง
        ใช้หน้าแรกซึ่งตอบสถานะ running กลับมาแทน)"""
        self.ensure_one()
        base_url = self._get_base_url()
        url = f'{base_url}/'

        _logger.info('action_test_connection: GET %s', url)

        try:
            resp = requests.get(url, timeout=8)
        except requests.ConnectionError:
            raise UserError(
                f'เชื่อมต่อ Backend ไม่ได้: {url}\n\n'
                'เช็คว่า\n'
                '  • Backend รันอยู่หรือยัง\n'
                '  • IP/Port ถูกต้องไหม (ปัจจุบัน: 192.168.1.43:8001)'
            )
        except requests.RequestException as e:
            raise UserError(f'เชื่อมต่อ Backend ไม่สำเร็จ:\n{e}')

        if resp.status_code == 404:
            raise UserError(
                f'Backend ตอบ 404 — URL อาจผิด: {url}\n'
                'ตรวจ API URL ใน Settings ว่ากรอกแค่: http://192.168.1.43:8001'
            )

        try:
            info    = resp.json()
            project = info.get('project', '')
            version = info.get('version', '')
            msg     = f'Backend ตอบ {resp.status_code}'
            if project:
                msg += f' — {project}'
            if version:
                msg += f' v{version}'
        except Exception:
            msg = f'Backend ตอบกลับ {resp.status_code}'

        return {
            'type': 'ir.actions.client',
            'tag':  'display_notification',
            'params': {
                'title':   '⚡ เชื่อมต่อสำเร็จ',
                'message': msg,
                'type':    'success',
                'sticky':  False,
            },
        }

    @api.model
    def _track_usage(self, count=1):
        """อัปเดต History fields (last_used_date, total_trips_calculated)
        ของ config ที่ Active อยู่ตอนนี้ — เรียกจาก models/telematics_log.py
        ทุกครั้งที่มี Trip ใหม่ sync เข้ามา (Backend คำนวณคะแนนด้วย config
        ที่ Active อยู่ ณ ขณะนั้นเสมอ ตาม FDD §12.5)"""
        active_cfg = self.search([('is_active', '=', True)], limit=1)
        if active_cfg:
            active_cfg.write({
                'last_used_date':          fields.Datetime.now(),
                'total_trips_calculated':  active_cfg.total_trips_calculated + count,
            })


class TelematicsScoringTier(models.Model):
    """Tier 1 ระดับของ Scoring Config — ไดนามิก เพิ่ม/ลบ/แก้ชื่อได้เอง
    ตาม FDD §12.3 (เดิมเคยตายตัวแค่ A/B/C/D 4 ระดับผ่าน 8 field บน
    fleet.telematics.scoring.config โดยตรง — แก้ 2026-08-04)"""
    _name        = 'fleet.telematics.scoring.tier'
    _description = 'Fleet Telematics Scoring Tier'
    _order       = 'min_score desc'

    scoring_config_id = fields.Many2one(
        'fleet.telematics.scoring.config', string='Scoring Config',
        required=True, ondelete='cascade')
    name = fields.Char(
        string='Tier', required=True,
        help='ชื่อ Tier — จะตั้งเป็น A/B/C/D, Gold/Silver/Bronze, '
             'หรือชื่ออะไรก็ได้ตามต้องการ')
    min_score = fields.Float(
        string='Min Score', required=True,
        help='คะแนนขั้นต่ำที่จะได้ Tier นี้ — พนักงานจะถูกจัดเข้า Tier '
             'แรก (เรียงจากคะแนนมากไปน้อย) ที่คะแนนเฉลี่ยของเขา ≥ ค่านี้')
    bonus_pct = fields.Float(string='Bonus %', required=True, default=0.0)

    def _check_config_not_locked(self):
        """ห้ามแก้ไข Tier ถ้า Scoring Config แม่ Active อยู่ — ใช้เงื่อนไข
        เดียวกับ _LOCKED_CONFIG_FIELDS บน fleet.telematics.scoring.config
        (เผื่อมีคนพยายามแก้ tier ตรงๆ ผ่าน model นี้ ข้าม write() ของ
        parent ไปเลย ก็ยังต้องโดนล็อกเหมือนกัน)"""
        for rec in self:
            if rec.scoring_config_id.is_active:
                raise UserError(
                    'Config นี้ Active อยู่ — แก้ไข Tier ไม่ได้ '
                    'เพื่อความโปร่งใสระหว่างรอบประเมิน\n\n'
                    'วิธีแก้ไข: ปิด Active ก่อน (หรือสร้าง Config เวอร์ชันใหม่แทน)'
                )

    def write(self, vals):
        self._check_config_not_locked()
        return super().write(vals)

    def unlink(self):
        self._check_config_not_locked()
        return super().unlink()

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records._check_config_not_locked()
        return records
