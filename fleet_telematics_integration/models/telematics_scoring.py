# ==============================================================================
# models/telematics_scoring.py
# โมเดลเก็บเกณฑ์คะแนนและการคิด Tier
# หน้า Scoring Config: Weight & Threshold Setup + Push Config ไป Backend
# ==============================================================================
import logging
import requests

from odoo import models, fields, api
from odoo.exceptions import ValidationError, UserError

_logger = logging.getLogger(__name__)


class TelematicsScoringConfig(models.Model):
    _name        = 'fleet.telematics.scoring.config'
    _description = 'Fleet Telematics Scoring Configuration'
    _order       = 'effective_date desc'

    # ============================================================
    # [A] ข้อมูลระบุ Config
    # Active ได้เพียง 1 config เท่านั้น
    # ============================================================
    name           = fields.Char(string='Config Name', required=True)
    active         = fields.Boolean(string='Active', default=True,
        help='Active ได้เพียง 1 config เท่านั้น — config นี้จะถูกใช้คำนวณโบนัส')
    effective_date = fields.Date(string='Effective Date', required=True)

    # ============================================================
    # [B] คะแนนพื้นฐานและเพดานการหักคะแนน
    # ============================================================
    score_base          = fields.Float(string='Base Score (เต็ม)', default=100.0)
    max_deduct_per_trip = fields.Float(string='Max Deduct / Trip', default=50.0,
        help='หักได้สูงสุดต่อเที่ยวไม่เกินค่านี้ เพื่อป้องกัน score ติดลบ')

    # ============================================================
    # [C] ค่าหักคะแนนแต่ละประเภทเหตุการณ์
    # ============================================================
    harsh_brake_deduct  = fields.Float(string='Harsh Brake Deduct',  default=5.0)
    harsh_accel_deduct  = fields.Float(string='Harsh Accel Deduct',  default=3.0)
    harsh_corner_deduct = fields.Float(string='Harsh Corner Deduct', default=3.0)
    speeding_deduct     = fields.Float(string='Speeding Deduct',     default=10.0)
    idling_deduct       = fields.Float(string='Idling Deduct',       default=2.0)
    bump_deduct         = fields.Float(string='Bump Deduct',         default=4.0)

    # ============================================================
    # [D] Threshold ตรวจจับเหตุการณ์อันตราย
    # ============================================================
    harsh_brake_g      = fields.Float(string='Brake G Threshold',         default=0.40,
        help='ค่า G-force ที่ถือว่า "เบรกกะทันหัน" เช่น 0.40G')
    harsh_accel_g      = fields.Float(string='Accel G Threshold',         default=0.40)
    harsh_corner_g     = fields.Float(string='Corner G Threshold',        default=0.40)
    speeding_kmh_over  = fields.Float(string='Speeding (km/h เกินกำหนด)', default=20.0,
        help='เกินความเร็วจำกัดเท่าไหร่ถือว่า speeding')
    idle_min_threshold = fields.Float(string='Idle Min Threshold (min)',   default=5.0,
        help='จอดติดเครื่องกี่นาทีขึ้นไปถือว่า idling')

    # ============================================================
    # [E] เกณฑ์และ % โบนัสแต่ละ Tier
    # A=ดีเยี่ยม → B=ดี → C=พอใช้ → D=ต้องปรับปรุง
    # ============================================================
    tier_a_min_score = fields.Float(string='Tier A — Min Score', default=90.0)
    tier_a_bonus_pct = fields.Float(string='Tier A — Bonus %',  default=10.0)
    tier_b_min_score = fields.Float(string='Tier B — Min Score', default=75.0)
    tier_b_bonus_pct = fields.Float(string='Tier B — Bonus %',  default=5.0)
    tier_c_min_score = fields.Float(string='Tier C — Min Score', default=60.0)
    tier_c_bonus_pct = fields.Float(string='Tier C — Bonus %',  default=0.0)

    # ============================================================
    # [F] สถานะการ Push ล่าสุด (อ่านอย่างเดียว)
    # ============================================================
    last_push_at     = fields.Datetime(string='Last Pushed At', readonly=True)
    last_push_status = fields.Char(string='Push Status',        readonly=True,
        help='ผลลัพธ์ล่าสุดของการ push config ไป Backend เช่น OK 200 หรือ ERROR')

    # ============================================================
    # [G] Constraints — ตรวจสอบความถูกต้องก่อนบันทึก
    # ============================================================
    @api.constrains('active')
    def _check_single_active(self):
        for rec in self:
            if rec.active:
                others = self.search([('active', '=', True), ('id', '!=', rec.id)])
                if others:
                    raise ValidationError(
                        f'มี Scoring Config ที่ Active อยู่แล้ว: "{others[0].name}"\n'
                        'กรุณา deactivate config นั้นก่อน แล้วค่อย activate config ใหม่'
                    )

    @api.constrains('tier_a_min_score', 'tier_b_min_score', 'tier_c_min_score')
    def _check_tier_order(self):
        for rec in self:
            if not (rec.tier_a_min_score > rec.tier_b_min_score > rec.tier_c_min_score > 0):
                raise ValidationError(
                    'Tier min score ต้องเรียงจากมากไปน้อย:\n'
                    'Tier A > Tier B > Tier C > 0'
                )

    # ============================================================
    # [H] สร้าง Payload สำหรับส่งไป MTD Backend
    # ============================================================
    def _build_config_payload(self):
        return {
            'score_base':          self.score_base,
            'harsh_brake_deduct':  self.harsh_brake_deduct,
            'harsh_accel_deduct':  self.harsh_accel_deduct,
            'harsh_corner_deduct': self.harsh_corner_deduct,
            'speeding_deduct':     self.speeding_deduct,
            'idling_deduct':       self.idling_deduct,
            'bump_deduct':         self.bump_deduct,
            'max_deduct_per_trip': self.max_deduct_per_trip,
            'harsh_brake_g':       self.harsh_brake_g,
            'harsh_accel_g':       self.harsh_accel_g,
            'harsh_corner_g':      self.harsh_corner_g,
            'speeding_kmh_over':   self.speeding_kmh_over,
            'idle_min_threshold':  self.idle_min_threshold,
            'tier_a_min_score':    self.tier_a_min_score,
            'tier_a_bonus_pct':    self.tier_a_bonus_pct,
            'tier_b_min_score':    self.tier_b_min_score,
            'tier_b_bonus_pct':    self.tier_b_bonus_pct,
            'tier_c_min_score':    self.tier_c_min_score,
            'tier_c_bonus_pct':    self.tier_c_bonus_pct,
        }

    # ============================================================
    # [I] ปุ่ม "💾 Push Config" — POST /api/v1/config/scoring
    # MTD จะใช้ config นี้คำนวณ driver_score ใน trip ถัดไปทันที
    # ============================================================
    def action_push_to_backend(self):
        self.ensure_one()
        ICP = self.env['ir.config_parameter'].sudo()
        api_url = ICP.get_param('fleet_telematics.mtd_api_url', '').rstrip('/')
        api_key = ICP.get_param('fleet_telematics.mtd_api_key', '')

        if not api_url or not api_key:
            raise UserError(
                'ยังไม่ได้ตั้งค่า MTD API\n'
                'ไปที่ Fleet Telematics → Settings แล้วกรอก API URL และ API Key'
            )

        try:
            resp = requests.post(
                f'{api_url}/api/v1/config/scoring',
                headers={'APIKEY': api_key, 'Content-Type': 'application/json'},
                json=self._build_config_payload(),
                timeout=15,
            )
            resp.raise_for_status()
            self.write({
                'last_push_at':     fields.Datetime.now(),
                'last_push_status': f'OK {resp.status_code}',
            })
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title':   '💾 Push Config สำเร็จ ✅',
                    'message': f'MTD ตอบกลับ {resp.status_code} — จะใช้ config นี้คำนวณ trip ถัดไป',
                    'type':    'success',
                    'sticky':  False,
                },
            }
        except requests.RequestException as e:
            self.write({'last_push_status': f'ERROR: {e}'})
            raise UserError(f'ส่งค่าไป Backend ไม่สำเร็จ:\n{e}')

    # ============================================================
    # [J] ปุ่ม "⚡ Test Connection" — GET /health
    # ============================================================
    def action_test_connection(self):
        self.ensure_one()
        ICP = self.env['ir.config_parameter'].sudo()
        api_url = ICP.get_param('fleet_telematics.mtd_api_url', '').rstrip('/')
        api_key = ICP.get_param('fleet_telematics.mtd_api_key', '')

        if not api_url:
            raise UserError('ยังไม่ได้ตั้งค่า API URL — ไปที่ Fleet Telematics → Settings')

        try:
            resp = requests.get(
                f'{api_url}/health',
                headers={'APIKEY': api_key},
                timeout=10,
            )
            resp.raise_for_status()
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title':   '⚡ เชื่อมต่อสำเร็จ',
                    'message': f'Backend ตอบกลับ {resp.status_code}',
                    'type':    'success',
                    'sticky':  False,
                },
            }
        except requests.RequestException as e:
            raise UserError(f'เชื่อมต่อ Backend ไม่สำเร็จ:\n{e}')
