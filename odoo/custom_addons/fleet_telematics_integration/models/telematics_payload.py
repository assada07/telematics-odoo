"""models/telematics_payload.py

กล่องจดหมายรับ API (Payload Inbox) — เก็บทุก request ดิบที่ Backend ยิงเข้า
มาที่ webhook ของ Odoo ไว้เสมอ ไม่ว่าข้อมูลจะถูก format ครบหรือไม่ก็ตาม
เพื่อให้มีหลักฐานไว้ตรวจสอบย้อนหลังกับทีม Backend ได้ ไม่ให้ข้อมูลหายไป
เงียบๆ เมื่อเกิดปัญหา (เช่น field ขาด, APIKEY ผิด, JSON ผิด format)
"""
import json
import logging

from odoo import models, fields, api

_logger = logging.getLogger(__name__)


class TelematicsPayload(models.Model):
    """บันทึก 1 record ต่อ 1 HTTP request ที่ Backend ยิงเข้ามา."""

    _name = 'fleet.telematics.payload'
    _description = 'Telematics Incoming Payload (API Inbox)'
    _order = 'received_at desc'
    _rec_name = 'display_ref'

    display_ref = fields.Char(
        string='Reference',
        compute='_compute_display_ref',
        store=True,
    )
    received_at = fields.Datetime(
        string='Received At',
        default=fields.Datetime.now,
        readonly=True,
        index=True,
    )

    # ── ข้อมูล HTTP request ──────────────────────────────────────
    endpoint     = fields.Char(string='Endpoint',     readonly=True)
    http_method  = fields.Char(string='HTTP Method',  readonly=True)
    remote_addr  = fields.Char(string='Remote IP',    readonly=True)
    content_type = fields.Char(string='Content-Type', readonly=True)
    http_headers = fields.Text(string='HTTP Headers', readonly=True)

    # ── เนื้อหา payload ดิบ ──────────────────────────────────────
    raw_payload = fields.Text(string='Raw Payload', readonly=True)

    payload_pretty = fields.Text(
        string='Payload (formatted)',
        compute='_compute_payload_pretty',
        store=False,
    )
    payload_valid_json = fields.Boolean(
        string='Valid JSON',
        compute='_compute_payload_pretty',
        store=True,
    )

    # ── สถานะการประมวลผล ─────────────────────────────────────────
    state = fields.Selection([
        ('new',       '🆕 New'),
        ('processed', '✅ Processed'),
        ('error',     '❌ Error'),
        ('ignored',   '⚪ Ignored'),
    ], default='new', string='State', index=True)

    notes = fields.Text(string='Notes / Error')

    trip_id = fields.Many2one(
        'fleet.telematics.log',
        string='Trip ที่สร้างจาก Payload นี้',
        ondelete='set null',
        readonly=True,
    )

    @api.depends('received_at', 'endpoint')
    def _compute_display_ref(self):
        """สร้างชื่ออ้างอิงของ record จากเวลาที่รับ + id เช่น
        'PAYLOAD/20260717-103000/42' เพื่อให้หาดูใน list view ได้ง่าย."""
        for rec in self:
            ts = rec.received_at or fields.Datetime.now()
            rec.display_ref = f'PAYLOAD/{ts:%Y%m%d-%H%M%S}/{rec.id or "new"}'

    @api.depends('raw_payload')
    def _compute_payload_pretty(self):
        """แปลง raw_payload (string ดิบ) เป็น JSON ที่จัดรูปแบบสวยงามไว้ดู.

        ถ้า parse เป็น JSON ไม่ได้ (ข้อมูลผิด format) ให้แสดง raw string
        เดิมไว้เฉยๆ และตั้ง payload_valid_json = False เพื่อให้ผู้ใช้เห็น
        ทันทีว่า payload นี้มีปัญหาตั้งแต่ต้นทาง
        """
        for rec in self:
            if rec.raw_payload:
                try:
                    obj = json.loads(rec.raw_payload)
                    rec.payload_pretty = json.dumps(obj, ensure_ascii=False, indent=2)
                    rec.payload_valid_json = True
                except (json.JSONDecodeError, ValueError):
                    rec.payload_pretty = rec.raw_payload
                    rec.payload_valid_json = False
            else:
                rec.payload_pretty = ''
                rec.payload_valid_json = False

    # ── ปุ่มเปลี่ยนสถานะด้วยมือ (สำหรับ Admin ตรวจสอบย้อนหลัง) ─────
    def action_mark_processed(self):
        """ทำเครื่องหมายว่า payload นี้ประมวลผลเรียบร้อยแล้ว."""
        self.write({'state': 'processed'})

    def action_mark_ignored(self):
        """ทำเครื่องหมายว่า payload นี้ไม่ต้องประมวลผล (เช่น ข้อมูลซ้ำ/ทดสอบ)."""
        self.write({'state': 'ignored'})

    def action_mark_error(self):
        """ทำเครื่องหมายว่า payload นี้มีปัญหา ต้องตรวจสอบเพิ่มเติม."""
        self.write({'state': 'error'})
