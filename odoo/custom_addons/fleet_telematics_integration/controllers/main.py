"""controllers/main.py

Web controller ฝั่ง Odoo สำหรับ Fleet Telematics ประกอบด้วย 4 route:
  1. GET  /api/v1/devices              — health check ของฝั่ง Odoo เอง
  2. GET  /fleet_telematics/live_proxy — SSE proxy ไปหา Backend สำหรับ Live Map
  3. POST /fleet_telematics/vehicles_location — RPC ให้ OWL widget ดึงตำแหน่งรถ
  4. GET  /api/v1/vehicles             — ให้ Backend ดึงรายชื่อรถจาก Odoo ไป debug/เช็คสถานะ

หมายเหตุสถาปัตยกรรม: Backend ใช้รูปแบบ Cron ดึง (Odoo เป็นฝ่าย GET
/trips/unsynced เข้าหา Backend เป็นระยะ — ดู models/telematics_log.py:
_cron_sync_trips) ไม่มี endpoint ฝั่ง Backend ที่ยิง POST trip/event เข้ามา
ที่ Odoo จึงไม่มี route รับ webhook-push ในไฟล์นี้
"""

import logging

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)

_WEBHOOK_SECRET_PARAM = 'fleet_telematics.webhook_secret'


def _verify_secret(req):
    """ตรวจสอบ APIKEY header ของ request ที่เข้ามา เทียบกับค่าที่ตั้งไว้.

    ถ้ายังไม่ได้ตั้งค่า secret ไว้เลย (ค่าว่าง) จะถือว่าผ่านเสมอ — ใช้สำหรับ
    ช่วง dev/testing ก่อนตั้งค่าจริง

    Args:
        req: Odoo http request object

    Returns:
        bool: True ถ้า APIKEY ตรงกัน (หรือยังไม่ได้ตั้งค่า secret ไว้เลย)
    """
    ICP = request.env['ir.config_parameter'].sudo()
    expected = ICP.get_param(_WEBHOOK_SECRET_PARAM, '')

    if not expected:
        return True

    incoming = req.httprequest.headers.get('APIKEY', '')
    return incoming == expected


class TelematicsWebhookController(http.Controller):
    """รวม route ทั้งหมดที่เกี่ยวกับการเชื่อมต่อ Backend และ Live Map."""

    @http.route(
        '/api/v1/devices',
        type='http',
        auth='public',
        methods=['GET'],
        csrf=False,
    )
    def health_check(self, **kwargs):
        """คืนสถานะ 'ok' พร้อมชื่อ/เวอร์ชันโมดูล — ใช้เช็คว่า Odoo ยังทำงานอยู่.

        คนละ endpoint กับ /config_device ของ Backend ที่ใช้ลงทะเบียน device
        """
        return request.make_json_response({
            'status': 'ok',
            'service': 'fleet-telematics-odoo',
            'version': '19.0.1.0.0',
        })

    @http.route(
        '/fleet_telematics/live_proxy',
        type='http',
        auth='user',
        methods=['GET'],
        csrf=False,
    )
    def fleet_live_proxy(self, **kwargs):
        """เปิด SSE stream ไปหา Backend แล้ว forward ต่อให้ browser (UC-06).

        เหตุผลที่ต้องผ่าน proxy นี้แทนให้ browser ต่อ Backend ตรงๆ:
          1) native EventSource ของ browser ใส่ custom header (APIKEY) ไม่ได้
          2) ไม่ต้องการ expose API Key ไว้ใน JavaScript ฝั่ง client

        นอกจากส่งต่อ stream แล้ว ยัง enrich ข้อมูลแต่ละ event ด้วย
        vehicle_name/driver_name จาก Odoo database ก่อนส่งให้ browser
        เพราะ Backend ส่งมาแค่ vehicle_id/device_id โดยไม่มีชื่อ

        (อ้างอิง nginx config: docs/nginx_fleet_telematics_sse.conf)

        Returns:
            werkzeug Response: text/event-stream แบบ direct passthrough
        """
        import json as _json
        from odoo.http import Response

        Config  = request.env['fleet.telematics.config'].sudo()
        api_url = Config.get_active_api_url()
        api_key = Config.get_active_api_key()

        if not api_url:
            return Response(
                'data: {"error": "API URL ยังไม่ได้ตั้งค่าใน Settings"}\n\n',
                mimetype='text/event-stream',
            )

        # เตรียม lookup table: vehicle_id → {vehicle_name, driver_name}
        # ดึงรถ "ทั้งหมด" ไม่กรองเฉพาะที่มี device เพราะ SSE จาก Backend
        # อาจส่ง vehicle_id ที่ยังไม่มี device ผูกใน Odoo มาด้วยได้
        vehicles = request.env['fleet.vehicle'].sudo().search([])
        vehicle_info = {
            v.id: {
                'vehicle_name': v.display_name or v.name,
                'driver_name':  v.driver_id.name if v.driver_id else '-',
            }
            for v in vehicles
        }

        def generate():
            """generator ที่ยิง GET แบบ stream ไปหา Backend แล้ว yield
            ทีละบรรทัด SSE กลับไปให้ browser พร้อม enrich ชื่อรถ/คนขับ."""
            import requests as _req
            try:
                with _req.get(
                    f'{api_url}/api/v1/fleet/live',
                    headers={
                        'APIKEY':  api_key,
                        'Accept':  'text/event-stream',
                    },
                    stream=True,
                    timeout=120,
                ) as r:
                    for line in r.iter_lines(decode_unicode=True):
                        if not line:
                            yield b'\n'
                            continue

                        if line.startswith('data:'):
                            raw = line[5:].strip()
                            try:
                                arr = _json.loads(raw)
                                if isinstance(arr, list):
                                    for item in arr:
                                        vid  = item.get('vehicle_id')
                                        info = vehicle_info.get(vid, {})
                                        item['vehicle_name'] = info.get('vehicle_name', f'Vehicle {vid}')
                                        item['driver_name']  = info.get('driver_name', '-')
                                    line = 'data: ' + _json.dumps(arr, ensure_ascii=False)
                            except Exception:
                                pass  # parse ไม่ได้ → ส่ง raw line ต่อไปเลย ไม่ให้ stream หยุด

                        yield (line + '\n').encode('utf-8')

            except _req.RequestException as e:
                _logger.warning('fleet_live_proxy: %s', e)
                yield (
                    'data: {"error": "%s"}\n\n' % str(e).replace('"', "'")
                ).encode('utf-8')

        return Response(
            generate(),
            mimetype='text/event-stream',
            direct_passthrough=True,
            headers=[
                ('Cache-Control', 'no-cache'),
                ('X-Accel-Buffering', 'no'),
                ('Connection', 'keep-alive'),
            ],
        )

    @http.route(
        '/fleet_telematics/vehicles_location',
        type='json',
        auth='user',
        methods=['POST'],
        csrf=False,
    )
    def vehicles_location(self, **kwargs):
        """คืนตำแหน่ง GPS ล่าสุดของรถทุกคันที่มี Device — เรียกจาก OWL widget
        ทุก 30 วินาที (polling ตาม FDD §7.3) แทนการต่อ SSE โดยตรง

        กลยุทธ์การดึงข้อมูล 2 ชั้น:
          1. ลองทางหลักก่อน: GET /api/v1/vehicles (bulk endpoint ตาม FDD)
             ยิงครั้งเดียวได้ข้อมูลรถทุกคัน แทนที่จะวนยิงทีละคัน
             รองรับหลายชื่อ key ที่เป็นไปได้ (lat/latitude, lon/longitude
             ฯลฯ) เพราะ Swagger ไม่ได้ระบุ schema ของ response แบบละเอียด
          2. ถ้าทางหลักล้มเหลว หรือเรียกสำเร็จแต่ parse พิกัดไม่ได้เลยทั้งที่
             มีรถต้องดึง → fallback ไปวน GET /vehicles/{id}/location ทีละ
             คันแทน เพื่อไม่ให้ Live Map พังถ้า schema จริงไม่ตรงกับที่คาด

        Returns:
            list[dict]: รายการรถที่มี Device และมีพิกัด GPS แต่ละรายการมี
            vehicle_id, vehicle_name, device_id, driver_name, lat, lon,
            speed, ignition, ts
        """
        import requests as _requests

        Config  = request.env['fleet.telematics.config'].sudo()
        api_url = Config.get_active_api_url()
        api_key = Config.get_active_api_key()

        if not api_url:
            return []

        # ดึงเฉพาะรถที่ลงทะเบียน Device ไว้แล้ว
        vehicles = request.env['fleet.vehicle'].sudo().search([
            ('telematics_device_id', '!=', False),
        ])
        vehicles_by_id = {v.id: v for v in vehicles}

        def _build_entry(v, lat, lon, speed, ignition, ts):
            """ประกอบ dict ผลลัพธ์ 1 รายการให้อยู่ในรูปแบบเดียวกันเสมอ
            ไม่ว่าจะมาจากทาง bulk หรือ fallback."""
            return {
                'vehicle_id':   v.id,
                'vehicle_name': v.display_name or v.name,
                'device_id':    v.telematics_device_id,
                'driver_name':  v.driver_id.name if v.driver_id else '-',
                'lat':          float(lat),
                'lon':          float(lon),
                'speed':        speed or 0,
                'ignition':     bool(ignition),
                'ts':           ts or '',
            }

        # ── ทางหลัก: GET /api/v1/vehicles (bulk, ยิงครั้งเดียว) ──────────
        try:
            resp = _requests.get(
                f'{api_url}/api/v1/vehicles',
                headers={'APIKEY': api_key},
                timeout=8,
            )
            if resp.status_code == 200:
                payload = resp.json()
                if isinstance(payload, dict):
                    items = (
                        payload.get('vehicles')
                        or payload.get('data')
                        or payload.get('items')
                        or []
                    )
                elif isinstance(payload, list):
                    items = payload
                else:
                    items = []

                bulk_result = []
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    vid = (
                        item.get('vehicle_id')
                        or item.get('id')
                        or item.get('odoo_vehicle_id')
                    )
                    try:
                        vid = int(vid)
                    except (TypeError, ValueError):
                        continue
                    v = vehicles_by_id.get(vid)
                    if not v:
                        continue  # รถคันนี้ไม่มี device ผูกใน Odoo ข้าม

                    # telemetry อาจซ้อนอยู่ใต้ key ชื่ออื่น เช่น 'location'/'telemetry'
                    tel = (
                        item.get('location')
                        or item.get('telemetry')
                        or item.get('last_telemetry')
                        or item
                    )
                    lat = tel.get('lat') or tel.get('latitude')
                    lon = tel.get('lon') or tel.get('longitude')
                    if not lat or not lon:
                        continue  # ยังไม่มีพิกัด ข้าม

                    bulk_result.append(_build_entry(
                        v, lat, lon,
                        tel.get('speed'),
                        tel.get('ignition', False),
                        tel.get('ts') or tel.get('date_update_latest'),
                    ))

                if bulk_result or not vehicles:
                    # ได้ผลลัพธ์ใช้ได้จริง (หรือไม่มีรถให้ดึงตั้งแต่แรก) จบที่นี่
                    return bulk_result
                # bulk เรียกสำเร็จแต่ parse พิกัดไม่ได้เลยทั้งที่มีรถต้องดึง
                # → schema อาจไม่ตรงตามที่คาด ตกไป fallback ด้านล่าง
                _logger.warning(
                    'vehicles_location: GET /api/v1/vehicles คืน 200 แต่ไม่พบพิกัด '
                    'ที่ parse ได้เลย (schema อาจไม่ตรงตามที่คาด) → fallback เป็น per-vehicle'
                )
            else:
                _logger.warning(
                    'vehicles_location: GET /api/v1/vehicles ตอบ HTTP %s → fallback เป็น per-vehicle',
                    resp.status_code,
                )
        except Exception as e:
            _logger.warning(
                'vehicles_location: GET /api/v1/vehicles ล้มเหลว (%s) → fallback เป็น per-vehicle', e)

        # ── Fallback: วน GET /vehicles/{id}/location ทีละคัน ────────────
        result = []
        for v in vehicles:
            try:
                resp = _requests.get(
                    f'{api_url}/api/v1/vehicles/{v.id}/location',
                    headers={'APIKEY': api_key},
                    timeout=5,
                )
                if resp.status_code != 200:
                    continue  # Backend ไม่รู้จักรถคันนี้ยัง ข้าม

                data = resp.json()
                lat  = data.get('lat') or data.get('latitude')
                lon  = data.get('lon') or data.get('longitude')

                if not lat or not lon:
                    continue  # ยังไม่มีพิกัด ข้าม

                result.append(_build_entry(
                    v, lat, lon,
                    data.get('speed'),
                    data.get('ignition', False),
                    data.get('ts'),
                ))
            except Exception as e:
                _logger.warning(
                    'vehicles_location: รถ %s (id=%s) ดึงไม่ได้: %s',
                    v.name, v.id, e)

        return result

    @http.route(
        '/api/v1/vehicles',
        type='json',
        auth='public',
        methods=['GET'],
        csrf=False,
    )
    def vehicles(self, **kwargs):
        """คืนรายชื่อรถทั้งหมดในระบบให้ Backend ดึงไปใช้ debug/เช็คสถานะ.

        ต้องแนบ APIKEY header ที่ตรงกับค่าที่ตั้งไว้ (ดู _verify_secret)
        ไม่เช่นนั้นจะได้ status='error' กลับไป

        Returns:
            dict: {'status', 'count', 'vehicles': [...]}
        """
        if not _verify_secret(request):
            return {'status': 'error', 'message': 'Unauthorized - invalid APIKEY'}

        vehicles = request.env['fleet.vehicle'].sudo().search([])
        return {
            'status': 'ok',
            'count': len(vehicles),
            'vehicles': [
                {
                    'id':           v.id,
                    'name':         v.name,
                    'license_plate': v.license_plate,
                    'device_id':    v.telematics_device_id or None,
                    'vehicle_id':   v.id,
                    'active':       v.active,
                    'available':    not bool(v.driver_id),
                    'date_update_latest': (
                        v.last_seen.strftime('%Y-%m-%dT%H:%M:%SZ')
                        if hasattr(v, 'last_seen') and v.last_seen else None
                    ),
                }
                for v in vehicles
            ],
        }
