"""models/telematics_report.py

Wizard (TransientModel) สำหรับดึงรายงานสำเร็จรูปจาก Backend API มาแสดงผล
โดยตรง (UC-07/08) แทนที่จะคำนวณ aggregate เองจาก trip log ในฝั่ง Odoo ซึ่ง
มีความเสี่ยงตัวเลขไม่ตรงกับ Backend

Endpoint ที่เชื่อมในไฟล์นี้:
  - GET /api/v1/reports/fuel-efficiency        — สรุปประสิทธิภาพน้ำมันทั้งฟลีท
  - GET /api/v1/drivers/{id}/score             — คะแนนคนขับรายคน
  - GET /api/v1/drivers/{id}/fuel-summary      — สรุปน้ำมันรายคน
  - GET /api/v1/drivers/{id}/events            — ประวัติ harsh events รายคน
  - GET /api/v1/reports/driver-score           — คะแนนรวมทุกคน
  - GET /api/v1/reports/fleet-summary          — ภาพรวม fleet รายวัน
  - GET /api/v1/reports/maintenance-forecast   — พยากรณ์ซ่อมบำรุง

Wizard เหล่านี้ไม่เก็บ state ถาวร (ยิง GET ใหม่ทุกครั้งที่กดปุ่ม) เพราะ
ข้อมูลรายงานควรอ้างอิงจาก Backend สดเสมอ ไม่ใช่ snapshot เก่า
"""

import json
import logging

import requests

from odoo import models, fields, api
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class TelematicsFuelEfficiencyReport(models.TransientModel):
    """Wizard ดึงรายงานน้ำมัน/คะแนนคนขับ 3 endpoint แรก."""

    _name = 'fleet.telematics.fuel.report.wizard'
    _description = 'Fuel Efficiency Report (จาก Backend โดยตรง)'

    driver_id = fields.Many2one('hr.employee', string='Driver (เว้นว่าง = ทั้งหมด)')
    result_html = fields.Html(string='ผลลัพธ์', readonly=True, sanitize=False)

    def _api(self):
        """คืน (api_url, api_key) ของ Backend ที่ตั้งค่าไว้ปัจจุบัน.

        Raises:
            UserError: ถ้ายังไม่ได้ตั้งค่า API URL
        """
        Config = self.env['fleet.telematics.config']
        api_url = Config.get_active_api_url()
        api_key = Config.get_active_api_key()
        if not api_url:
            raise UserError('กรุณาตั้งค่า API URL ของ Backend ใน Settings ก่อน')
        return api_url, api_key

    def action_fetch_fuel_efficiency(self):
        """ดึงรายงานประสิทธิภาพน้ำมันทั้งฟลีทจาก Backend มาแสดงเป็นตาราง."""
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
        """ดึงคะแนนของ driver_id ที่เลือกไว้จาก Backend มาแสดงเป็นตาราง.

        Raises:
            UserError: ถ้ายังไม่ได้เลือก driver
        """
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
        """ดึงสรุปน้ำมันของ driver_id ที่เลือกไว้จาก Backend มาแสดงเป็นตาราง.

        Raises:
            UserError: ถ้ายังไม่ได้เลือก driver
        """
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
        """แปลง JSON response (dict/list ระดับเดียว) เป็นตาราง HTML อ่านง่าย.

        Args:
            data: ข้อมูลที่ได้จาก resp.json() — รองรับ dict, list, หรือ
                ค่าเดี่ยว
            title (str): หัวข้อที่แสดงเหนือตาราง

        Returns:
            str: HTML string พร้อมแสดงในฟิลด์ Html
        """
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


class TelematicsBackendReports(models.TransientModel):
    """Wizard ดึงรายงานสำเร็จรูปอีก 6 endpoint จาก Backend."""

    _name = 'fleet.telematics.backend.report'
    _description = 'Fleet Telematics Backend Reports (ดึงตรงจาก Backend)'

    driver_id    = fields.Many2one('hr.employee', string='Driver (เว้นว่าง = ทั้งหมด)')
    result_html  = fields.Html(string='ผลลัพธ์', readonly=True, sanitize=False)

    def _api(self):
        """คืน (api_url, api_key) ของ Backend ที่ตั้งค่าไว้ปัจจุบัน.

        Raises:
            UserError: ถ้ายังไม่ได้ตั้งค่า API URL
        """
        Config  = self.env['fleet.telematics.config']
        api_url = Config.get_active_api_url()
        api_key = Config.get_active_api_key()
        if not api_url:
            raise UserError('กรุณาตั้งค่า API URL ของ Backend ใน Settings ก่อน')
        return api_url, api_key

    def action_fetch_driver_events(self):
        """ดึงประวัติ harsh events ของ driver ที่เลือก — GET /drivers/{id}/events.

        Raises:
            UserError: ถ้ายังไม่ได้เลือก driver
        """
        self.ensure_one()
        if not self.driver_id:
            raise UserError('กรุณาเลือก Driver ก่อนดึงประวัติ Harsh Events')
        api_url, api_key = self._api()
        try:
            resp = requests.get(
                f'{api_url}/api/v1/drivers/{self.driver_id.id}/events',
                headers={'APIKEY': api_key},
                timeout=15,
            )
        except requests.RequestException as e:
            raise UserError(f'เชื่อมต่อ Backend ไม่สำเร็จ: {e}')
        if resp.status_code != 200:
            raise UserError(f'Backend ตอบ (HTTP {resp.status_code}): {resp.text[:300]}')
        self.result_html = self._render_table(
            resp.json(), f'Harsh Events — {self.driver_id.name}')
        return self._reopen()

    def action_fetch_all_driver_scores(self):
        """ดึงคะแนนคนขับรวมทุกคน — GET /reports/driver-score."""
        self.ensure_one()
        api_url, api_key = self._api()
        try:
            resp = requests.get(
                f'{api_url}/api/v1/reports/driver-score',
                headers={'APIKEY': api_key},
                timeout=15,
            )
        except requests.RequestException as e:
            raise UserError(f'เชื่อมต่อ Backend ไม่สำเร็จ: {e}')
        if resp.status_code != 200:
            raise UserError(f'Backend ตอบ (HTTP {resp.status_code}): {resp.text[:300]}')
        self.result_html = self._render_table(
            resp.json(), 'Driver Score Report (ทุกคน)')
        return self._reopen()

    def action_fetch_fleet_summary(self):
        """ดึงภาพรวมฟลีทรายวัน — GET /reports/fleet-summary."""
        self.ensure_one()
        api_url, api_key = self._api()
        try:
            resp = requests.get(
                f'{api_url}/api/v1/reports/fleet-summary',
                headers={'APIKEY': api_key},
                timeout=15,
            )
        except requests.RequestException as e:
            raise UserError(f'เชื่อมต่อ Backend ไม่สำเร็จ: {e}')
        if resp.status_code != 200:
            raise UserError(f'Backend ตอบ (HTTP {resp.status_code}): {resp.text[:300]}')
        self.result_html = self._render_table(
            resp.json(), 'Fleet Summary (ภาพรวม Fleet)')
        return self._reopen()

    def action_fetch_maintenance_forecast(self):
        """ดึงพยากรณ์ซ่อมบำรุง — GET /reports/maintenance-forecast."""
        self.ensure_one()
        api_url, api_key = self._api()
        try:
            resp = requests.get(
                f'{api_url}/api/v1/reports/maintenance-forecast',
                headers={'APIKEY': api_key},
                timeout=15,
            )
        except requests.RequestException as e:
            raise UserError(f'เชื่อมต่อ Backend ไม่สำเร็จ: {e}')
        if resp.status_code != 200:
            raise UserError(f'Backend ตอบ (HTTP {resp.status_code}): {resp.text[:300]}')
        self.result_html = self._render_table(
            resp.json(), 'Maintenance Forecast (พยากรณ์ซ่อมบำรุง)')
        return self._reopen()

    def action_fetch_driver_score_single(self):
        """ดึงคะแนนคนขับรายคน — GET /drivers/{id}/score.

        แยกจาก action_fetch_all_driver_scores ที่ดึงคะแนนรวมทุกคน
        (ตรงกับ Swagger: "Get Driver Score" ที่ต้องระบุ driver)

        Raises:
            UserError: ถ้ายังไม่ได้เลือก driver
        """
        self.ensure_one()
        if not self.driver_id:
            raise UserError('กรุณาเลือก Driver ก่อนดึง Driver Score รายคน')
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
            raise UserError(f'Backend ตอบ (HTTP {resp.status_code}): {resp.text[:300]}')
        self.result_html = self._render_table(
            resp.json(), f'Driver Score — {self.driver_id.name}')
        return self._reopen()

    def action_fetch_fuel_summary(self):
        """ดึงสรุปน้ำมันของ driver ที่เลือก — GET /drivers/{id}/fuel-summary.

        Raises:
            UserError: ถ้ายังไม่ได้เลือก driver
        """
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
            raise UserError(f'Backend ตอบ (HTTP {resp.status_code}): {resp.text[:300]}')
        self.result_html = self._render_table(
            resp.json(), f'Fuel Summary — {self.driver_id.name}')
        return self._reopen()

    def action_fetch_fuel_efficiency(self):
        """ดึงประสิทธิภาพน้ำมันทั้งฟลีท — GET /reports/fuel-efficiency."""
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
            raise UserError(f'Backend ตอบ (HTTP {resp.status_code}): {resp.text[:300]}')
        self.result_html = self._render_table(
            resp.json(), 'Fuel Efficiency Report (ทั้งฟลีท)')
        return self._reopen()

    def _reopen(self):
        """คืน action เปิดฟอร์ม wizard ตัวเดิมค้างไว้ (target=current) เพื่อ
        ให้เห็นผลลัพธ์ที่เพิ่งเขียนลง result_html."""
        return {
            'type':      'ir.actions.act_window',
            'res_model': self._name,
            'res_id':    self.id,
            'view_mode': 'form',
            'target':    'current',
        }

    @staticmethod
    def _render_table(data, title):
        """แปลง JSON response (dict/list ระดับเดียว) เป็นตาราง HTML อ่านง่าย.

        Args:
            data: ข้อมูลที่ได้จาก resp.json()
            title (str): หัวข้อที่แสดงเหนือตาราง

        Returns:
            str: HTML string พร้อมแสดงในฟิลด์ Html
        """
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
            f'<h5 class="mt-2">{title}</h5>'
            f'<table class="table table-bordered table-sm">{rows}</table>'
        )
