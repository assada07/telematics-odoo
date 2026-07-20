"""models/telematics_report_providers.py

Provider ของ QWeb PDF Report สองตัว (Energy Report, Monthly Score Report)
ทำหน้าที่ดึงข้อมูลสรุปสดจาก Backend API มาแปะเพิ่มในหน้ารายงาน นอกเหนือจาก
ตารางข้อมูลที่คำนวณจาก local data ตามปกติ

ใช้กลไกมาตรฐานของ Odoo: AbstractModel ชื่อ
`report.<module>.<report_template_id>` พร้อมเมธอด `_get_report_values`
ซึ่ง Odoo เรียกอัตโนมัติตอน render QWeb report — ไม่ต้องแก้ report action
หรือโครงสร้าง template เดิม

หมายเหตุ: endpoint ทั้งสองฝั่ง Backend คืนข้อมูลสรุปรวมทั้งฟลีท/ทุกคน
ไม่รองรับ filter ตาม vehicle/driver/ช่วงเวลาที่กำลังพิมพ์ จึงแปะเป็นกล่อง
"ข้อมูลอ้างอิงจาก Backend (สด)" แยกต่างหากจากตารางข้อมูลรายตัวปกติ
"""
import logging

import requests

from odoo import models

_logger = logging.getLogger(__name__)


def _fetch_backend_summary(env, path):
    """เรียก GET ไปที่ Backend API พร้อมแนบ auth header แล้วคืนผลลัพธ์.

    Args:
        env: Odoo environment (ใช้เข้าถึง fleet.telematics.config)
        path (str): path ของ endpoint บน Backend เช่น '/api/v1/reports/...'

    Returns:
        tuple: (data, error) — สำเร็จได้ (dict, None), ล้มเหลวได้
        (None, error_message) โดย error_message ครอบคลุมทั้งกรณียังไม่ตั้ง
        ค่า API URL, login ไม่สำเร็จ, และเรียก API แล้วได้ error กลับมา
    """
    Config = env['fleet.telematics.config']
    api_url = Config.get_active_api_url()
    if not api_url:
        return None, 'ยังไม่ได้ตั้งค่า Backend API URL'
    try:
        headers = Config.get_auth_headers()
    except Exception as e:
        return None, f'Login ไม่สำเร็จ: {e}'
    try:
        resp = requests.get(f'{api_url}{path}', headers=headers, timeout=15)
        resp.raise_for_status()
        return resp.json(), None
    except requests.RequestException as e:
        return None, str(e)


class ReportEnergyDocument(models.AbstractModel):
    """Provider ของ Energy Report (reports/energy_report.xml).

    เพิ่ม backend_fuel_summary / backend_fuel_error เข้าไปใน values ที่ส่ง
    ให้ template ใช้ ดึงจาก GET /api/v1/reports/fuel-efficiency
    """

    _name = 'report.fleet_telematics_integration.report_energy_document'
    _description = 'Energy Report — Backend Summary Provider'

    def _get_report_values(self, docids, data=None):
        """สร้าง context สำหรับ render Energy Report.

        Args:
            docids (list[int]): id ของ fleet.telematics.log ที่กำลังพิมพ์
            data (dict, optional): ข้อมูลเพิ่มเติมจาก report action (ไม่ใช้)

        Returns:
            dict: values สำหรับ QWeb template — เอกสารในเครื่อง (docs) และ
            ข้อมูลสรุปสดจาก Backend (backend_fuel_summary/error)
        """
        docs = self.env['fleet.telematics.log'].browse(docids)
        summary, err = _fetch_backend_summary(
            self.env, '/api/v1/reports/fuel-efficiency')
        return {
            'doc_ids':  docids,
            'doc_model': 'fleet.telematics.log',
            'docs':     docs,
            'backend_fuel_summary': summary,
            'backend_fuel_error':   err,
        }


class ReportDriverScoreDocument(models.AbstractModel):
    """Provider ของ Monthly Score Report (reports/driver_score_report.xml).

    เพิ่ม backend_driver_score / backend_score_error เข้าไปใน values ที่ส่ง
    ให้ template ใช้ ดึงจาก GET /api/v1/reports/driver-score
    """

    _name = 'report.fleet_telematics_integration.report_driver_score'
    _description = 'Monthly Score Report — Backend Summary Provider'

    def _get_report_values(self, docids, data=None):
        """สร้าง context สำหรับ render Monthly Score Report.

        Args:
            docids (list[int]): id ของ fleet.telematics.incentive ที่กำลังพิมพ์
            data (dict, optional): ข้อมูลเพิ่มเติมจาก report action (ไม่ใช้)

        Returns:
            dict: values สำหรับ QWeb template — เอกสารในเครื่อง (docs) และ
            ข้อมูลสรุปสดจาก Backend (backend_driver_score/error)
        """
        docs = self.env['fleet.telematics.incentive'].browse(docids)
        summary, err = _fetch_backend_summary(
            self.env, '/api/v1/reports/driver-score')
        return {
            'doc_ids':  docids,
            'doc_model': 'fleet.telematics.incentive',
            'docs':     docs,
            'backend_driver_score': summary,
            'backend_score_error':  err,
        }
