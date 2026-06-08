# ==============================================================================
# wizards/device_selector.py
# Transient model: wizard เลือก Device จาก dropdown
# ==============================================================================
import json
import logging

from odoo import models, fields, api
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class FleetTelematicsDeviceSelector(models.TransientModel):
    _name        = 'fleet.telematics.device.selector'
    _description = 'เลือก Device จาก Backend API'

    vehicle_id          = fields.Many2one('fleet.vehicle', string='Vehicle', readonly=True)
    device_json         = fields.Text(string='Device JSON', readonly=True)
    selected_device_id  = fields.Char(string='Device ID ที่เลือก')

    # Selection field — สร้างจาก device_json
    device_selection = fields.Selection(
        selection='_get_device_selection',
        string='เลือก Device',
    )

    @api.model
    def _get_device_selection(self):
        """ใช้ดึง selection list — return empty ตอน class load"""
        return []

    @api.onchange('device_json')
    def _onchange_device_json(self):
        """rebuild selection เมื่อ json พร้อม"""
        pass  # Odoo จะ re-evaluate _get_device_selection อัตโนมัติ

    def _build_selection_from_json(self):
        try:
            devices = json.loads(self.device_json or '[]')
            return [(d['device_id'], f"{d['device_id']} — {d['name']}") for d in devices]
        except Exception:
            return []

    def fields_get(self, allfields=None, attributes=None):
        res = super().fields_get(allfields, attributes)
        return res

    @api.onchange('device_selection')
    def _onchange_device_selection(self):
        if self.device_selection:
            self.selected_device_id = self.device_selection

    def action_confirm(self):
        """กดยืนยัน — บันทึก device_id กลับไปที่รถ แล้วปิด wizard"""
        self.ensure_one()
        if not self.selected_device_id:
            raise UserError('กรุณาเลือก Device ก่อนกดยืนยัน')

        vehicle = self.vehicle_id
        if vehicle:
            vehicle.telematics_device_id = self.selected_device_id
            _logger.info(
                'Fleet Telematics: vehicle %s → device_id = %s',
                vehicle.id, self.selected_device_id
            )
        return {'type': 'ir.actions.act_window_close'}

    def action_confirm_and_send(self):
        """กดยืนยัน + ส่ง mapping ไป Backend ทันที"""
        self.ensure_one()
        if not self.selected_device_id:
            raise UserError('กรุณาเลือก Device ก่อนกดยืนยัน')

        vehicle = self.vehicle_id
        if not vehicle:
            raise UserError('ไม่พบข้อมูลรถ')

        vehicle.telematics_device_id = self.selected_device_id

        # ปิด wizard แล้ว trigger action_save_mapping
        vehicle.action_save_mapping()
        return {'type': 'ir.actions.act_window_close'}
