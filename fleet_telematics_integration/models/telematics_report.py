# ==============================================================================
# models/telematics_report.py  (ไฟล์ใหม่)
#
# UC-07/08 — เดิม Energy Report และ Driver Scorecard คำนวณ aggregate เอง
# จาก trip log ทั้งหมดในฝั่ง Odoo ทำให้มีความเสี่ยงตัวเลขไม่ตรงกับ Backend
#
# ตามคำตอบยืนยันจาก Backend (2026-06-30): endpoint สำเร็จรูปต่อไปนี้
# มีไว้ให้ Odoo ดึงไปแสดงตรงๆ ได้เลย ไม่ต้องคำนวณซ้ำ:
#   - GET /api/v1/reports/fuel-efficiency
#   - GET /api/v1/drivers/{id}/score
#   - GET /api/v1/drivers/{id}/fuel-summary
#
# Implementation: Wizard (TransientModel) ที่กดปุ่มแล้วยิง GET ตรงๆ
# แล้วแสดงผลลัพธ์ดิบในรูปตาราง/ข้อความที่อ่านง่าย — ไม่เก็บ state ถาวร
# เพราะข้อมูลควรอ้างอิงจาก Backend สดทุกครั้งที่เปิดดู
# ==============================================================================

import json
import logging

import requests

from odoo import models, fields, api
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class TelematicsFuelEfficiencyReport(models.TransientModel):
    _name = 'fleet.telematics.fuel.report.wizard'
    _description = 'Fuel Efficiency Report (จาก Backend โดยตรง)'

    driver_id = fields.Many2one('hr.employee', string='Driver (เว้นว่าง = ทั้งหมด)')
    result_html = fields.Html(string='ผลลัพธ์', readonly=True, sanitize=False)

    def _api(self):
        Config = self.env['fleet.telematics.config']
        api_url = Config.get_active_api_url()
        api_key = Config.get_active_api_key()
        if not api_url:
            raise UserError('กรุณาตั้งค่า API URL ของ Backend ใน Settings ก่อน')
        return api_url, api_key

    def action_fetch_fuel_efficiency(self):
        self.ensure_one()
        api_url, api_key = self._api()
        try:
            resp = requests.get(
                f'{api_url}/api/v1/reports/fuel-efficiency',
                headers={'APIKEY': api_key},
                timeout=15,
            )
        except requests.RequestException as e:
            raise UserError(f'เชื่อมต่อ Backend ไม่สำเร็จ: {e}')

        if resp.status_code != 200:
            raise UserError(f'Backend ตอบกลับผิดพลาด (HTTP {resp.status_code}): {resp.text[:300]}')

        self.result_html = self._render_json_table(resp.json(), 'Fuel Efficiency Report')
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }

    def action_fetch_driver_score(self):
        self.ensure_one()
        if not self.driver_id:
            raise UserError('กรุณาเลือก Driver ก่อนดึง Driver Score')
        api_url, api_key = self._api()
        try:
            resp = requests.get(
                f'{api_url}/api/v1/drivers/{self.driver_id.id}/score',
                headers={'APIKEY': api_key},
                timeout=15,
            )
        except requests.RequestException as e:
            raise UserError(f'เชื่อมต่อ Backend ไม่สำเร็จ: {e}')

        if resp.status_code != 200:
            raise UserError(f'Backend ตอบกลับผิดพลาด (HTTP {resp.status_code}): {resp.text[:300]}')

        self.result_html = self._render_json_table(
            resp.json(), f'Driver Score — {self.driver_id.name}')
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }

    def action_fetch_fuel_summary(self):
        self.ensure_one()
        if not self.driver_id:
            raise UserError('กรุณาเลือก Driver ก่อนดึง Fuel Summary')
        api_url, api_key = self._api()
        try:
            resp = requests.get(
                f'{api_url}/api/v1/drivers/{self.driver_id.id}/fuel-summary',
                headers={'APIKEY': api_key},
                timeout=15,
            )
        except requests.RequestException as e:
            raise UserError(f'เชื่อมต่อ Backend ไม่สำเร็จ: {e}')

        if resp.status_code != 200:
            raise UserError(f'Backend ตอบกลับผิดพลาด (HTTP {resp.status_code}): {resp.text[:300]}')

        self.result_html = self._render_json_table(
            resp.json(), f'Fuel Summary — {self.driver_id.name}')
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }

    @staticmethod
    def _render_json_table(data, title):
        """แปลง JSON response เป็นตาราง HTML อ่านง่าย (รองรับ dict/list ระดับเดียว)"""
        rows = ''
        if isinstance(data, dict):
            items = data.items()
        elif isinstance(data, list):
            items = enumerate(data)
        else:
            items = [('value', data)]

        for k, v in items:
            if isinstance(v, (dict, list)):
                v_display = f'<pre>{json.dumps(v, ensure_ascii=False, indent=2)}</pre>'
            else:
                v_display = str(v)
            rows += f'<tr><td><b>{k}</b></td><td>{v_display}</td></tr>'

        return (
            f'<h4>{title}</h4>'
            f'<table class="table table-bordered table-sm">{rows}</table>'
        )
