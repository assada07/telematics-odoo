# ==============================================================================
# __manifest__.py — Fleet Telematics Integration
# ลงทะเบียนโมดูล กำหนดข้อมูลผู้พัฒนา และ depends: fleet, hr
# ==============================================================================
{
    'name': 'Fleet Telematics Integration',
    'version': '19.0.1.0.0',
    'category': 'Fleet',
    'summary': 'Driver Behavior Monitoring, Scoring & Incentive via Telematics API',

    'author': 'Kotchasaan Technology Invention Co., Ltd.',

    'depends': [
        'fleet',
        'hr',
        'web',
    ],

    # controllers/ ไม่ต้องระบุใน data — Odoo โหลดอัตโนมัติผ่าน __init__.py

    'data': [

        # 1. Security — เปิดสิทธิ์ Read/Write/Create ให้โมเดลที่สร้างใหม่
        'security/ir.model.access.csv',

        # 2. Cron Jobs — ตั้งเวลา Scheduled Action ดึง API ทุก 5 นาที
        'data/telematics_cron.xml',

        # 3. Views — หน้าจอ UI ทั้งหมด
        'views/telematics_config_views.xml',
        'views/fleet_vehicle_ext_views.xml',
        'views/telematics_log_views.xml',
        'views/telematics_event_views.xml',
        'views/telematics_scoring_views.xml',
        'views/telematics_incentive_views.xml',

        # 4. Menu — แถบเมนูหลักและเมนูย่อย
        'views/telematics_menus.xml',
    ],

    'installable': True,
    'application': True,
    'license': 'LGPL-3',
}
