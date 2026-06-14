import logging
import requests

from odoo import models, fields, api
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class FleetTelematicsSyncWizard(models.TransientModel):
    _name        = 'fleet.telematics.sync.wizard'
    _description = 'Manual Sync from MTD Backend'

    # === ส่วนที่ 1: Input — ผู้ใช้กำหนดช่วงเวลาที่ต้องการ Sync ===
    # ให้ Fleet Manager ระบุว่าจะดึง trip ที่เกิดขึ้นหลังจากเวลาใด
    # เหมาะสำหรับกรณี cron ล้มเหลวหรือต้องการดึงข้อมูลย้อนหลังเฉพาะช่วง
    sync_from = fields.Datetime(
        string='Sync From',
        default=fields.Datetime.now,
        required=True,
        help='ดึง trip ที่เกิดขึ้นหลังจากเวลานี้เท่านั้น')

    # === ส่วนที่ 2: Output — แสดงผลสรุปหลัง Sync เสร็จ ===
    # แสดงจำนวน record ที่สร้างใหม่ ข้ามซ้ำ และเกิด error (อ่านอย่างเดียว)
    state          = fields.Selection([
        ('draft', 'Ready'),
        ('done',  'Done'),
        ('error', 'Error'),
    ], default='draft')
    result_message = fields.Text(string='Result',   readonly=True)
    created_count  = fields.Integer(string='Created', readonly=True)
    skipped_count  = fields.Integer(string='Skipped', readonly=True)
    error_count    = fields.Integer(string='Errors',  readonly=True)

    # === ส่วนที่ 3: ปุ่ม "Sync Now" — ดึงข้อมูล Trip จาก MTD และบันทึกลงระบบ ===
    # 1) ดึง API Key และ URL จาก System Parameters
    # 2) GET /trips?since=...&status=scored เฉพาะ trip ที่ประมวลผล score แล้ว
    # 3) _upsert_trip() สำหรับแต่ละ trip (สร้างใหม่หรือข้ามถ้าซ้ำ)
    # 4) PATCH /trips/{id}/mark-synced กลับ MTD เพื่อป้องกันการดึงซ้ำ
    def action_sync(self):
        self.ensure_one()
        ICP = self.env['ir.config_parameter'].sudo()
        api_url = ICP.get_param('fleet_telematics.mtd_api_url', '').rstrip('/')
        api_key = ICP.get_param('fleet_telematics.mtd_api_key', '')
        if not api_url or not api_key:
            raise UserError(
                'ยังไม่ได้ตั้งค่า MTD API\n'
                'Settings → Technical → System Parameters'
            )

        try:
            resp = requests.get(
                f'{api_url}/trips',
                headers={'Authorization': f'Bearer {api_key}'},
                params={
                    'since':  self.sync_from.isoformat() if self.sync_from else '',
                    'status': 'scored',
                },
                timeout=30,
            )
            resp.raise_for_status()
            trips = resp.json().get('trips', [])
        except requests.RequestException as e:
            self.write({'state': 'error', 'result_message': f'API Error: {e}'})
            return self._reload()

        TripLog = self.env['fleet.telematics.log'].sudo()
        created = skipped = errors = 0

        # === ส่วนที่ 4: วนประมวลผลแต่ละ Trip และ Mark-synced กลับ MTD ===
        # ถ้า _upsert_trip คืน 'created' → PATCH mark-synced กลับไปที่ MTD
        # ถ้าซ้ำ → ข้าม / ถ้า error → นับแต่ไม่หยุดประมวลผล trip อื่น
        for trip in trips:
            try:
                status, _ = TripLog._upsert_trip(trip)
                if status == 'created':
                    created += 1
                    ext_id = trip.get('id') or trip.get('external_trip_id')
                    if ext_id:
                        try:
                            requests.patch(
                                f'{api_url}/trips/{ext_id}/mark-synced',
                                headers={'Authorization': f'Bearer {api_key}'},
                                timeout=10,
                            )
                        except requests.RequestException:
                            pass
                else:
                    skipped += 1
            except Exception:
                _logger.exception('sync_wizard: error บน trip %s', trip.get('id'))
                errors += 1

        # === ส่วนที่ 5: เขียนผลสรุปและแสดงใน Wizard เดิม ===
        self.write({
            'state':          'done' if errors == 0 else 'error',
            'created_count':  created,
            'skipped_count':  skipped,
            'error_count':    errors,
            'result_message': (
                f'✅ สร้างใหม่ : {created} รายการ\n'
                f'⏭ ข้ามซ้ำ  : {skipped} รายการ\n'
                f'❌ Error    : {errors} รายการ'
            ),
        })
        return self._reload()

    # === ส่วนที่ 6: โหลด Wizard ซ้ำเพื่อแสดงผลลัพธ์ ===
    # เปิดหน้าต่าง wizard เดิมอีกครั้งหลัง sync เสร็จ เพื่อให้ผู้ใช้เห็นสรุปผล
    def _reload(self):
        return {
            'type':      'ir.actions.act_window',
            'res_model': self._name,
            'res_id':    self.id,
            'view_mode': 'form',
            'target':    'new',
        }
