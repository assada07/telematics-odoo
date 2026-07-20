"""hr_employee_ext.py

ขยายโมเดล hr.employee เพื่อเพิ่มฟิลด์เงินเดือนฐานสำรอง สำหรับใช้คำนวณ
Incentive/Bonus ของระบบ Fleet Telematics ในกรณีที่ระบบไม่ได้ติดตั้งโมดูล
hr_contract (โมดูลนี้ไม่ได้ประกาศ hr_contract เป็น hard dependency)
"""
from odoo import models, fields


class HrEmployeeTelematicsExt(models.Model):
    """ส่วนขยายของ hr.employee: เพิ่มฟิลด์เงินเดือนฐานสำหรับคำนวณโบนัส."""

    _inherit = 'hr.employee'

    telematics_base_salary = fields.Float(
        string='Base Salary (สำหรับคำนวณโบนัส Telematics)',
        digits=(10, 2),
        help='เงินเดือนฐานสำหรับคำนวณ Incentive/Bonus ของระบบ Fleet '
             'Telematics โดยเฉพาะ ใช้เป็นค่า fallback เมื่อระบบไม่มีโมดูล '
             'hr_contract ติดตั้งอยู่ — ถ้ามี hr_contract และมีสัญญาจ้างที่ '
             'active อยู่ ระบบจะใช้ค่าจาก hr.contract.wage เป็นหลักก่อนเสมอ',
    )
