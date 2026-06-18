# ==============================================================================
# controllers/main.py
# ==============================================================================

import logging
from datetime import datetime, timezone

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)

_WEBHOOK_SECRET_PARAM = 'fleet_telematics.webhook_secret'


def _verify_secret(req):
    """ตรวจสอบ APIKEY header"""
    ICP = request.env['ir.config_parameter'].sudo()
    expected = ICP.get_param(_WEBHOOK_SECRET_PARAM, '')

    if not expected:
        return True

    incoming = req.httprequest.headers.get('APIKEY', '')
    return incoming == expected


class TelematicsWebhookController(http.Controller):


    # ==========================================================================
    # GET /health
    # ==========================================================================

    @http.route(
        '/api/v1/devices',
        type='http',
        auth='public',
        methods=['GET'],
        csrf=False,
    )
    def health_check(self, **kwargs):

        return request.make_json_response({
            'status': 'ok',
            'service': 'fleet-telematics-odoo',
            'version': '19.0.1.0.0',
        })



    # ==========================================================================
    # POST /api/v1/telematics/trip-webhook   "device_id": "KTC-001",
    #         "vehicle_id": null,
    #         "active": true,
    #         "available": true,
    #         "date_update_latest": null
    #     }
    # ==========================================================================

    # ==========================================================================
    # POST /api/v1/vehicles  — รับ trip+event จาก Backend GPS
    # GET  /api/v1/vehicles  — ดึงรายการรถ+สถานะ device ทั้งหมด
    # type='json': Odoo parse JSON body / ห่อ response เป็น JSON-RPC
    # ==========================================================================
    @http.route(
        '/api/v1/vehicles',
        type='json',
        auth='public',
        methods=['POST', 'GET'],
        csrf=False,
    )
    def vehicles(self, **kwargs):

        if not _verify_secret(request):
            return {'status': 'error', 'message': 'Unauthorized - invalid APIKEY'}

        # ─── GET: คืนรายการรถทั้งหมดพร้อมสถานะ device ─────────────────────
        if request.httprequest.method == 'GET':
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

        # ─── POST: รับ trip+event จาก Backend GPS ──────────────────────────
        data = request.get_json_data() or {}

        _logger.info(
            'vehicles POST: trip_id=%s device_id=%s',
            data.get('trip_id'), data.get('device_id'),
        )

        required = ['trip_id', 'device_id', 'start_time']
        missing  = [f for f in required if not data.get(f)]
        if missing:
            return {'status': 'error', 'message': f'Missing fields: {", ".join(missing)}'}

        TripLog = request.env['fleet.telematics.log'].sudo()
        ext_id  = str(data['trip_id'])

        existing = TripLog.search([('external_trip_id', '=', ext_id)], limit=1)

        device_id_str = data.get('device_id', '')
        vehicle = request.env['fleet.vehicle'].sudo().search(
            [('telematics_device_id', '=', device_id_str)], limit=1)
        if not vehicle:
            return {'status': 'error', 'message': f'Vehicle with device_id "{device_id_str}" not found'}

        driver_name = data.get('driver_name', '')
        driver = (
            request.env['hr.employee'].sudo().search(
                [('name', 'ilike', driver_name)], limit=1)
            if driver_name else False
        )

        vals = {
            'external_trip_id':   ext_id,
            'vehicle_id':         vehicle.id,
            'driver_id':          driver.id if driver else False,
            'telematics_device_id': device_id_str,
            'trip_start':         _parse_dt(data.get('start_time')),
            'trip_end':           _parse_dt(data.get('end_time')),
            'distance_km':        float(data.get('distance_km',    0) or 0),
            'avg_speed':          float(data.get('avg_speed',      0) or 0),
            'max_speed':          float(data.get('max_speed',      0) or 0),
            'idle_min':           float(data.get('idle_min',       0) or 0),
            'fuel_used_est':      float(data.get('fuel_used_est',  0) or 0),
            'driver_score':       float(data.get('driver_score',   0) or 0),
            'harsh_brake_count':  int(data.get('harsh_brake_count',  0) or 0),
            'harsh_accel_count':  int(data.get('harsh_accel_count',  0) or 0),
            'harsh_corner_count': int(data.get('harsh_corner_count', 0) or 0),
            'speeding_count':     int(data.get('speeding_count',     0) or 0),
            'gps_track_json':     data.get('gps_track_json', ''),
            'state':              'synced',
        }

        if existing:
            existing.write(vals)
            trip   = existing
            action = 'updated'
        else:
            trip   = TripLog.create(vals)
            action = 'created'

        events_data = data.get('events', [])
        if events_data:
            Event = request.env['fleet.telematics.event'].sudo()
            if action == 'updated':
                trip.event_ids.unlink()
            for ev in events_data:
                etype = ev.get('event_type')
                if etype not in ('harsh_brake','harsh_accel','harsh_corner','speeding','idling','bump'):
                    continue
                Event.create({
                    'trip_id':       trip.id,
                    'event_type':    etype,
                    'occurred_at':   _parse_dt(ev.get('occurred_at')) or vals['trip_start'],
                    'lat':           float(ev.get('lat',           0) or 0),
                    'lon':           float(ev.get('lon',           0) or 0),
                    'severity':      float(ev.get('severity',      0) or 0),
                    'speed_at_event':float(ev.get('speed_at_event',0) or 0),
                    'description':   ev.get('description', ''),
                })

        _logger.info('vehicles POST: trip_id=%s %s → odoo_id=%s', ext_id, action, trip.id)

        return {
            'status':       'success',
            'action':       action,
            'odoo_trip_id': trip.id,
            'message':      f'Trip {ext_id} {action} successfully',
        }


# ==============================================================================
# Helper
# ==============================================================================

def _parse_dt(value):

    if not value:
        return None

    try:
        dt = datetime.fromisoformat(
            str(value).replace('Z', '+00:00')
        )

        return dt.astimezone(
            timezone.utc
        ).replace(
            tzinfo=None
        )

    except (ValueError, TypeError):
        return None