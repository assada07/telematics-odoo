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
        'security/telematics_security.xml',   # Record Rules — driver เห็นแค่ตัวเอง (เพิ่มใหม่)

        # 2. Cron Jobs — ตั้งเวลา Scheduled Action ดึง API ทุก 5 นาที
        'data/telematics_cron.xml',

        # 3. Views — หน้าจอ UI ทั้งหมด
        'views/telematics_config_views.xml',
        'views/fleet_vehicle_ext_views.xml',
        'views/telematics_device_views.xml',  # UC-01 Device Register (เพิ่มใหม่)
        'views/telematics_report_views.xml',  # UC-07/08 Backend Report Wizard (เพิ่มใหม่)
        'views/telematics_log_views.xml',
        'views/telematics_event_views.xml',
        'views/telematics_scoring_views.xml',
        'views/telematics_incentive_views.xml',
        'views/telematics_payload_views.xml',  # เปิดใช้งานแล้ว 2026-06-30 (เดิมเป็น dead code)

        # 4. Menu — แถบเมนูหลักและเมนูย่อย
        'views/telematics_menus.xml',
    ],

    'installable': True,
    'application': True,
    'license': 'LGPL-3',

    # Live Map widget (UC-06) — โหลด Leaflet ผ่าน CDN ตอน runtime ในไฟล์ JS เอง
    'assets': {
        'web.assets_backend': [
            'fleet_telematics_integration/static/src/js/fleet_live_map.js',
            'fleet_telematics_integration/static/src/xml/fleet_live_map.xml',
        ],
    },

    # Seed ค่า Base URL / API Key จริงลง ir.config_parameter ตอนติดตั้งโมดูล
    # ดูคำเตือนเรื่องความปลอดภัยของ API Key ที่ฝังในโค้ดได้ที่ __init__.py
    'post_init_hook': '_post_init_seed_telematics_config',
}
