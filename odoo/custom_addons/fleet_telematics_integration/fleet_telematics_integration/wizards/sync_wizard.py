"""wizards/sync_wizard.py

Manual Sync Wizard (UC-05) — ให้ผู้ดูแลระบบสั่ง sync trip จาก Backend API
ได้เองทันที โดยไม่ต้องรอ Cron ที่ตั้งเวลาไว้ทุก 5 นาที

Wizard นี้เรียก fleet.telematics.log._cron_sync_trips() เมธอดเดียวกับที่
Cron เรียก เพื่อให้พฤติกรรม "sync เอง" กับ "sync อัตโนมัติ" เหมือนกันเสมอ
ไม่มี logic แยกกันสองชุดที่อาจทำงานไม่ตรงกัน
"""
import logging

from odoo import models, fields

_logger = logging.getLogger(__name__)


class FleetTelematicsSyncWizard(models.TransientModel):
    """หน้าต่าง wizard เดียว กดปุ่มแล้วสั่ง sync ทันที พร้อมสรุปผลให้ดู."""

    _name = 'fleet.telematics.sync.wizard'
    _description = 'Manual Sync from Backend API (UC-05)'

    result_message = fields.Text(string='ผลลัพธ์', readonly=True)
    last_sync_at   = fields.Datetime(string='Synced At', readonly=True)

    def action_sync_now(self):
        """สั่ง sync trip จาก Backend ทันที แล้วสรุปผลกลับมาแสดงในฟอร์ม.

        ขั้นตอน:
          1. จำจำนวน trip และค่า timestamp ล่าสุดไว้ก่อนเริ่ม (ใช้เทียบผลลัพธ์)
          2. เรียก fleet.telematics.log._cron_sync_trips()
          3. ถ้า sync สำเร็จ: คำนวณจำนวน trip ใหม่ที่เพิ่มเข้ามา แล้วเขียน
             สรุปผลลง result_message
          4. ถ้า sync ล้มเหลว (exception ใดๆ): log แบบเต็ม stack trace และ
             แสดงข้อความ error ให้ผู้ใช้เห็นในฟอร์ม แทนที่จะปล่อยให้หน้าจอ
             ค้าง/error แบบไม่มีคำอธิบาย

        Returns:
            dict: action เปิด wizard เดิมค้างไว้ (ผ่าน _reopen_wizard)
            เพื่อให้ผู้ใช้เห็นผลลัพธ์ในฟอร์มเดียวกัน แทนที่จะปิดหน้าต่างทันที
        """
        self.ensure_one()
        Log = self.env['fleet.telematics.log']

        # ชื่อ ir.config_parameter ต้องตรงกับที่ _cron_sync_trips() ใช้จริง
        # (fleet_telematics.trip_last_sync_timestamp) ไม่เช่นนั้นจะอ่านค่า
        # "ก่อน sync" ผิด param แล้วเทียบผลลัพธ์คลาดเคลื่อน
        ICP = self.env['ir.config_parameter'].sudo()
        before_ts = ICP.get_param('fleet_telematics.trip_last_sync_timestamp', '')
        count_before = Log.search_count([])

        try:
            Log._cron_sync_trips()
        except Exception as e:
            _logger.exception('Manual sync ล้มเหลว')
            self.write({
                'result_message': f'❌ Sync ล้มเหลว: {e}',
                'last_sync_at':   fields.Datetime.now(),
            })
            return self._reopen_wizard()

        after_ts = ICP.get_param('fleet_telematics.trip_last_sync_timestamp', '')
        count_after = Log.search_count([])
        new_trips = count_after - count_before

        self.write({
            'result_message': (
                f'✅ Sync สำเร็จ\n'
                f'Trip ใหม่ที่ถูกบันทึก: {new_trips} รายการ\n'
                f'Last Poll Timestamp: {after_ts or "-"}'
                + ('\n(ไม่เปลี่ยนจากเดิม — อาจไม่มีข้อมูลใหม่จาก Backend)'
                   if before_ts == after_ts else '')
            ),
            'last_sync_at': fields.Datetime.now(),
        })
        return self._reopen_wizard()

    def _reopen_wizard(self):
        """คืน action เปิดฟอร์ม wizard ตัวเดิม (target=new) เพื่อให้ผู้ใช้
        เห็นผลลัพธ์ที่เพิ่งเขียนลง result_message แทนที่จะถูกปิดหน้าต่างทิ้ง.
        """
        return {
            'type':      'ir.actions.act_window',
            'res_model': self._name,
            'res_id':    self.id,
            'view_mode': 'form',
            'target':    'new',
        }
