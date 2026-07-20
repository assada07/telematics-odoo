"""controllers/portal.py

Controller หน้า Portal ให้พนักงานดูคะแนนขับขี่และประวัติโบนัสของตนเอง
(UC-11 — Self-service ผ่าน Odoo Portal)

ออกแบบให้ query ด้วย sudo() แล้วกรอง driver_id ด้วยมือในตัว controller เอง
แทนที่จะเปิด ir.model.access ให้กลุ่ม portal เข้าถึงโมเดลโดยตรง เพื่อไม่ให้
portal user มีสิทธิ์เข้าถึงโมเดลกว้างเกินจำเป็นผ่านช่องทางอื่น เช่น RPC
"""

from odoo import http
from odoo.http import request
from odoo.addons.portal.controllers.portal import CustomerPortal


class FleetTelematicsPortal(CustomerPortal):
    """เพิ่ม route หน้า "คะแนนขับขี่ของฉัน" เข้าไปใน Customer Portal เดิม."""

    def _get_my_employee(self):
        """หา hr.employee ที่ผูกกับผู้ใช้ที่ login อยู่ตอนนี้.

        Returns:
            recordset: hr.employee ตัวเดียว (หรือ empty recordset ถ้าไม่พบ)
        """
        return request.env['hr.employee'].sudo().search(
            [('user_id', '=', request.env.user.id)], limit=1
        )

    @http.route(
        ['/my/telematics', '/my/telematics/score'],
        type='http', auth='user', website=True, sitemap=False,
    )
    def portal_my_telematics_score(self, **kwargs):
        """แสดงหน้าคะแนนขับขี่และประวัติโบนัสของผู้ใช้ที่ login อยู่.

        ขั้นตอน:
          1. หา hr.employee ที่ผูกกับผู้ใช้ปัจจุบัน — ถ้าไม่มี (เช่น user
             ไม่ใช่พนักงานขับรถ) แสดงหน้า "ไม่พบข้อมูลพนักงาน" แทน
          2. ดึงประวัติ Incentive (12 รอบล่าสุด) และ Trip Log (15 เที่ยว
             ล่าสุดที่ sync แล้ว) กรองด้วย driver_id = employee.id เสมอ
             เพื่อให้พนักงานเห็นเฉพาะข้อมูลของตัวเองเท่านั้น
          3. คำนวณคะแนนล่าสุดและคะแนนเฉลี่ยจาก trip ล่าสุดที่ดึงมา
          4. render template แสดงผลทั้งหมด

        Returns:
            werkzeug response: หน้า HTML ของ Portal
        """
        employee = self._get_my_employee()
        if not employee:
            return request.render(
                'fleet_telematics_integration.portal_telematics_no_employee', {}
            )

        Incentive = request.env['fleet.telematics.incentive'].sudo()
        TripLog   = request.env['fleet.telematics.log'].sudo()

        incentives = Incentive.search(
            [('driver_id', '=', employee.id)],
            order='period_year desc, period_month desc',
            limit=12,
        )
        recent_trips = TripLog.search(
            [('driver_id', '=', employee.id), ('state', '=', 'synced')],
            order='trip_start desc',
            limit=15,
        )

        latest_score = recent_trips[:1].driver_score if recent_trips else 0.0
        avg_score_recent = (
            round(sum(recent_trips.mapped('driver_score')) / len(recent_trips), 2)
            if recent_trips else 0.0
        )

        values = {
            'employee':          employee,
            'incentives':        incentives,
            'recent_trips':      recent_trips,
            'latest_score':      latest_score,
            'avg_score_recent':  avg_score_recent,
            'page_name':         'telematics_score',
        }
        return request.render(
            'fleet_telematics_integration.portal_my_telematics_score', values
        )
