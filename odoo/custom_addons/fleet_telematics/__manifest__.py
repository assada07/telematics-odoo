# ==============================================================================
# __manifest__.py
# ==============================================================================
{
    'name': 'Fleet Telematics',
    'version': '19.0.4.0.0',
    'category': 'Fleet',
    'summary': 'Driver Behavior Monitoring, Scoring, Incentives & Maintenance via MTD API',

    'author': 'Kotchasaan Technology Invention Co., Ltd.',

    'depends': [
        'fleet',
        'hr',
        'web'
    ],

    'data': [

        # 1. Security
        'security/ir.model.access.csv',

        # 2. Cron Jobs
        'data/cron_sync.xml',

        # 3. Config Settings
        'views/fleet_telematics_config_views.xml',

        # 4. Views
        'views/fleet_telematics_log_views.xml',
        'views/fleet_telematics_event_views.xml',
        'views/fleet_telematics_scoring_views.xml',
        'views/fleet_telematics_incentive_views.xml',
        'views/fleet_vehicle_ext_views.xml',
        'views/fleet_telematics_maintenance_views.xml',
        'views/fleet_telematics_dashboard_views.xml',
        'views/fleet_telematics_reports_views.xml',
        'views/fleet_telematics_portal_views.xml',

        # 5. Menu
        'views/menu_items.xml',

        # 6. Reports
        'reports/trip_summary_report.xml',
        'reports/driver_score_report.xml',
    ],

    'installable': True,
    'application': True,
    'license': 'LGPL-3',
}