from odoo import http
from odoo.http import request


class VehicleLocationController(http.Controller):

    # === ส่วนที่ 1: Webhook Endpoint รับข้อมูล GPS แบบ Real-time ===
    # เปิด route /api/v1/vehicles/{id}/location รอรับข้อมูลที่กล่อง GPS ส่งเข้ามา
    # ตรวจสอบสิทธิ์ด้วย api_key และรับเฉพาะ HTTP GET
    @http.route(
        '/api/v1/vehicles/<int:vehicle_id>/location',
        type='json',
        auth='api_key',
        methods=['GET'],
        csrf=False,
    )
    def webhook_sync(self, vehicle_id, **kwargs):

        # === ส่วนที่ 2: อ่านค่า GPS จาก Request JSON ===
        # แยกแต่ละ field จาก JSON payload ที่กล่อง GPS ส่งมา
        data = request.jsonrequest
        device_id = data.get('device_id')
        ts        = data.get('ts')
        lat       = data.get('lat')
        lon       = data.get('lon')
        speed     = data.get('speed')
        heading   = data.get('heading')
        ignition  = data.get('ignition')
        event     = data.get('event')

        # === ส่วนที่ 3: ตรวจสอบว่ารถและ Device มีอยู่ในระบบ ===
        # ถ้าไม่พบรถ → ส่ง error กลับทันที โดยไม่บันทึกข้อมูล
        vehicle = request.env['fleet.vehicle'].sudo().browse(vehicle_id)
        if not vehicle.exists():
            return {
                'status': 'error',
                'message': 'Vehicle not found'
            }

        device = request.env['your.device.model'].sudo().search([
            ('name', '=', device_id)
        ], limit=1)

        # === ส่วนที่ 4: บันทึก GPS Log ลงฐานข้อมูล ===
        # สร้าง trip log record จากข้อมูลที่รับมา แล้วส่ง id กลับเป็น confirmation
        location_log = request.env['fleet.telematics.log'].sudo().create({
            'vehicle_id': vehicle.id,
            'device_id':  device.id if device else False,
            'timestamp':  ts,
            'latitude':   lat,
            'longitude':  lon,
            'speed':      speed,
            'heading':    heading,
            'ignition':   ignition,
            'event':      event,
        })

        return {
            'status':  'success',
            'message': 'Location synced successfully',
            'data_id': location_log.id
        }
