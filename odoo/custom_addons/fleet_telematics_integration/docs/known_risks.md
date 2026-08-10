# Known Risks / สมมติฐานที่ยังไม่ยืนยัน

รวบรวมจากการตรวจสอบโค้ดเทียบกับ FDD v1.4 (อัปเดตล่าสุด 2026-07-16) —
สถานะอัปเดตหลังผู้ใช้เริ่มทดสอบจริงบน Odoo 19 instance แล้วบางส่วน

## 0. ✅ แก้แล้ว — hr_contract ไม่มีในระบบผู้ใช้
ยืนยันแล้วว่า `hr_contract` ไม่มีอยู่จริง เพราะ **Odoo 19 รวมโมดูลนี้เข้า `hr`
core และเปลี่ยนชื่อโมเดลเป็น `hr.version`** (ยืนยันจาก Model Overview จริงของ
ผู้ใช้: มี field `employee_id`, `wage`, `is_current`) — แก้โค้ดใน
`_apply_backend_bonus()` ให้ query `hr.version` แทน พร้อม fallback หลายชั้น:
hr.version → hr.contract (เผื่อรันเวอร์ชันเก่ากว่า) → telematics_base_salary
บนโปรไฟล์พนักงาน → กรอกมือรายใบ

**บั๊กที่เจอเพิ่มระหว่างแก้จริง (ปิดแล้วทั้งหมด):**
- `is_current` เป็น compute field ไม่ stored → ใช้ใน `search()` ตรงๆ ไม่ได้
  ต้องดึงมาก่อนแล้วกรองด้วย `.filtered()` แทน
- `res.groups.users` ถูกถอดออกใน Odoo 19 — เจอ 2 จุด (`telematics_incentive.py`
  และ `telematics_log.py` ส่วนแจ้งเตือนซ่อมบำรุง) แก้ทั้งคู่ให้ query จาก
  `res.users` โดยเช็ค field `groups_id`/`group_ids` ก่อนใช้งาน
- `res.users.groups_id` ถูกเปลี่ยนชื่อเป็น `group_ids` — เจอจากการรัน
  automated test จริงครั้งแรก (พังทั้ง 11 คลาสที่จุดเดียวกัน) แก้ด้วย helper
  `_users_groups_field()` ตรวจหาชื่อ field แบบไดนามิกในไฟล์ test

## 1. ✅ แก้แล้ว — Portal Self-service (UC-11) ไม่ได้ใช้ work_contact_id
ตรวจโค้ดจริงใน `controllers/portal.py` (`_get_my_employee()`) แล้วยืนยันว่า
**ใช้ field `user_id` (Many2one มาตรฐานที่มั่นคงมาก) ไม่ได้ใช้
`work_contact_id` เลย** — ปิดความเสี่ยงนี้ได้

## 2. ✅ ไม่ใช่ความเสี่ยงจริง — เอกสารเก่าอ้างถึง JWT ที่โค้ดไม่เคยมี
เอกสารรุ่นก่อนหน้าเขียนไว้ว่า `models/telematics_config.py: _login_get_token()`
เดา schema ของ `POST /auth/login` (username/password → JWT token) — ตรวจโค้ด
จริงทั้งไฟล์แล้ว **ไม่มีเมธอดนี้และไม่มี JWT login flow อยู่ในโค้ดเลยแม้แต่
จุดเดียว** ระบบยืนยันตัวตนจริงที่ใช้อยู่ตลอดทั้งโมดูลคือ **APIKEY header
ธรรมดา** (`fields.Char` เก็บค่าไว้ที่ `fleet.telematics.config.api_key` แล้ว
แนบเป็น `headers={'APIKEY': api_key}` ทุก request ไปยัง Backend) — เป็น
ข้อความที่ตกค้างจากดีไซน์รุ่นก่อนที่เลิกใช้ไปแล้ว ไม่ใช่ความเสี่ยงที่มีจริง
ในโค้ดปัจจุบัน ปิดข้อนี้ได้

## 3. ✅ แก้แล้ว — xmlid fleet.module_category_fleet
`security/telematics_groups.xml` ไม่ได้อ้างอิง `category_id`/`privilege_id`
เลยแล้ว (ถอดออกทั้งหมด) ความเสี่ยงนี้ปิดแล้ว

## 4. ✅ แก้แล้ว — Odoo Version ในเอกสารไม่ตรงกับของจริง
FDD §4 เดิมเขียนว่า "Odoo 17 Enterprise" แต่ `__manifest__.py` เขียน
`19.0.1.0.0` จริง — **บริษัทอนุมัติให้ใช้ Odoo 19 อย่างเป็นทางการแล้ว**
ไม่ถือเป็นความเสี่ยงอีกต่อไป (เหลืองานเอกสาร: อัปเดตตัว FDD เป็น v2.0 ให้
ตรงกับของจริง แต่ไม่ใช่ blocker ของการใช้งาน)

## 5. 🔶 บางส่วน — เริ่มรันบน Odoo server จริงแล้ว พบ+แก้บั๊กจริงหลายจุด
รันคำสั่ง `--test-enable --test-tags /fleet_telematics_integration` ครั้งแรก
บน Odoo 19 instance จริงแล้ว พังทั้ง 11 คลาสจากบั๊ก `groups_id`/`group_ids`
(ดูข้อ 0) — แก้แล้ว รอผลการรันซ้ำรอบใหม่เพื่อยืนยันว่าผ่านครบทั้ง 107 test
(อัปเดตจำนวนล่าสุดหลังเพิ่ม test ของ Tier D/History/local fallback)

## 6. ⚠️ ยังไม่วัด — Test Coverage เป็นตัวเลขจริง
มี unit test รวม **107 เทส** ครอบคลุมทุก UC หลักแล้วเชิงคุณภาพ แต่ยังไม่มี
เครื่องมือรัน `coverage.py` คู่กับ Odoo server จริง จึงบอกได้แค่เชิงคุณภาพ
ไม่ใช่ตัวเลข % ตามเกณฑ์ FDD §14.2

## 7. ✅ แก้แล้ว — Data Retention (FDD §13) ไม่เคย implement เลย
เพิ่ม `_cron_purge_old_trips()` ใน `models/telematics_log.py` ทำงานทุกเดือน
ลบ Trip Log (+ Event cascade) ที่เก่ากว่า 3 ปี ปรับได้ผ่าน
`ir.config_parameter: fleet_telematics.trip_retention_years` **ไม่แตะ
Incentive เด็ดขาด** ("raw telemetry 90 วัน" เป็นหน้าที่ TimescaleDB ฝั่ง
Backend ไม่ใช่ Odoo)

## 8. ✅ แก้แล้ว — แถบเตือน Tier D ขึ้นผิดจังหวะ
เดิมแถบเตือน Tier D ขึ้นตั้งแต่สร้าง record ใหม่ก่อนเลือก Driver เลย (เพราะ
field มี default='D') แก้แล้วให้เช็คเพิ่มว่าต้องเลือก driver_id ก่อนด้วย
พร้อมเพิ่ม validation บังคับเลือก Driver ก่อนกด Refresh/Confirm

## 9. ✅ แก้แล้ว — 3 จุดที่ขาดจริงเทียบกับ FDD (พบจากการตรวจ field-by-field)
ไล่เทียบ FDD §12.3-12.5 กับโค้ดจริงทีละ field แล้วพบ 3 จุดที่ขาดจริง (ไม่ใช่
ตีความผิด) — แก้ครบแล้วทั้งหมด:
- **History section หายไป** (§12.5): เพิ่ม `created_date`, `last_used_date`,
  `total_trips_calculated` บน Scoring Config พร้อมฟังก์ชัน `_track_usage()`
  อัปเดตอัตโนมัติทุกครั้งที่ `_cron_sync_trips()` sync ทริปสำเร็จ
- **Tier D ไม่มี field ปรับได้** (§12.3): เพิ่ม `tier_d_min_score`,
  `tier_d_bonus_pct` เป็น field จริง (เดิม hardcode ไว้ในลอจิก) ล็อกเมื่อ
  Active=True เหมือนฟิลด์เกณฑ์อื่น พร้อมส่งไป Backend ใน
  `_build_config_payload()` ด้วย
- **ไม่แจ้ง HR ทุกครั้งที่มี draft ใหม่** (§12.4 ขั้นตอน 4): เดิมมีแค่
  `_notify_hr_tier_d()` แจ้งเฉพาะกรณี Tier D — เพิ่ม
  `_notify_hr_new_drafts_batch()` แจ้งสรุปทุกครั้งที่ cron รายเดือนสร้าง
  draft ใหม่ (ส่งเป็นสรุปเดียวต่อรอบ ไม่แยกอีเมลต่อพนักงาน)

**ยังไม่ตัดสินใจ:** Unique constraint ของ Incentive เปลี่ยนจาก
`driver_id + period_month + period_year` (ตามที่ FDD ระบุ) เป็น
`driver_id + date_from + date_to` (ตามบรีฟภายหลังที่รองรับรอบตัดวิกไม่ตรง
เดือนปฏิทิน) — เป็นการเบี่ยงจาก FDD โดยตั้งใจ ยังไม่ได้ยืนยันกับ Supervisor
ว่าจะใช้ตามบรีฟใหม่นี้ต่อไป หรือย้อนกลับให้ตรงเอกสารเดิม

## 10. ✅ แก้แล้ว — ปิด Active แล้ว Scoring Config หายจากหน้าจอ (เหมือนถูกลบ)
**อาการ:** กดปุ่ม "ปิด Active" แล้ว record หายไปจาก List View ทันที และถ้า
รีเฟรชหน้าฟอร์มที่เปิดค้างไว้ จะเจอหน้า "New" เปล่าๆ แทนที่จะเห็น record
เดิมที่เพิ่งปิด Active — ดูเผินๆ เหมือนข้อมูลถูกลบจริง ต้องสร้าง Config
ใหม่ทั้งหมด (Test Connection → Approve → Push Config ใหม่หมด)

**สาเหตุจริง:** field ที่ใช้เก็บสถานะเปิด/ปิดของ Scoring Config เดิมตั้งชื่อ
ว่า `active` ตรงๆ ซึ่ง Odoo สงวนชื่อนี้ไว้เป็นกลไกพิเศษ (Archiving) —
ทุก `search()`/List View ที่ไม่ได้ระบุ domain เจาะจง Odoo จะเติม
`active = True` ให้อัตโนมัติเสมอ (เว้นแต่จะเปิด Filters "Archived" เอง)
พอ field นี้ถูกตั้งเป็น False (จากปุ่มปิด Active) record เลยถูกซ่อนจาก
List/Form ทันที ทั้งที่ข้อมูลในฐานข้อมูลยังอยู่ครบทุกอย่าง ไม่ได้ถูกลบเลย

**วิธีแก้:** เปลี่ยนชื่อ field จาก `active` เป็น `is_active` (ไม่ชนกับชื่อ
สงวนของ Odoo) ทั้งใน model, view (list/form), และจุดอื่นที่ query field นี้
(`telematics_incentive.py`) พฤติกรรมที่ผู้ใช้เห็นในหน้าจอเหมือนเดิมทุก
ประการ (ชื่อ label ยังโชว์ว่า "Active" เหมือนเดิม) เปลี่ยนแค่ชื่อ field
ภายในเท่านั้น

## 11. ⚠️ ข้อควรรู้ — Tier บน Trip Log อาจไม่อัปเดตย้อนหลังถ้า Scoring Config เปลี่ยน
FDD §12.6 ระบุว่า Trip Log List ต้องกรองได้ตาม Tier ด้วย (เพิ่มเข้ามาแล้วที่
`fleet.telematics.log.tier`) — field นี้เป็น `compute` + `store=True` โดย
`@api.depends('driver_score')` เท่านั้น (ไม่ผูกกับ Scoring Config โดยตรง
เพราะทริปไม่ได้เก็บ `scoring_config_id` ของตัวเองแบบ Incentive)

**ผลที่ตามมา:** Tier ของทริปจะคำนวณจาก threshold ของ Config ที่ Active
อยู่ ณ ตอนที่ทริปนั้นถูกสร้าง/แก้ไข driver_score เท่านั้น — ถ้า Admin ไป
เปลี่ยน Scoring Config ใหม่ทีหลัง (threshold ต่างจากเดิม) **ทริปเก่าที่มี
อยู่แล้วจะไม่ถูกคำนวณ Tier ใหม่ให้อัตโนมัติ** ต้องรอให้มีการเขียนทับ
driver_score ของทริปนั้นอีกครั้งถึงจะ recompute — เป็นพฤติกรรมที่ตั้งใจ
(เพื่อไม่ต้อง recompute ทริปทั้งหมดทุกครั้งที่แก้ config ซึ่งอาจช้ามากถ้ามี
ทริปสะสมเยอะ) แต่ถ้าต้องการ Tier ที่ sync กับ config ปัจจุบันเสมอ ต้อง
เพิ่ม logic recompute แบบ batch ตอน Approve config ใหม่ (ยังไม่ได้ทำ)

## 12. ✅ แก้แล้ว — ไม่มี Scoring Config Active เลย → ทุกทริปขึ้น Tier D หมด
**อาการ:** ในหน้า Trip Logs ทุกแถวขึ้น badge แดง "D — ต้องปรับปรุง" หมดทุก
รายการ แม้แต่ทริปที่ driver_score = 90, 100, 99.97 ก็ตาม ดูเหมือนข้อมูลไม่
อัปเดต/ดึงไม่ได้ แต่จริงๆ ข้อมูลอื่นครบถูกต้องทุกอย่าง มีแค่ Tier ที่ผิด

**สาเหตุจริง:** ทั้ง `_compute_tier()` (`telematics_log.py`) และ
`_local_tier_from_score()` (`telematics_incentive.py`) เขียนเงื่อนไขแบบ
`if cfg and score >= cfg.tier_a_min_score` — ตอนที่**ไม่มี Scoring Config
Active อยู่เลยในระบบ** (`cfg` เป็น empty recordset ซึ่งเป็น falsy ใน
Python) ทุกเงื่อนไขที่ขึ้นต้นด้วย `cfg and ...` จะเป็น False เสมอ ไม่ว่า
score จะสูงแค่ไหน ตกไปที่ `else: tier = 'D'` หมดทุกครั้ง

**วิธีแก้:** เปลี่ยนมาใช้ threshold ค่าเริ่มต้นมาตรฐาน (A=90 / B=75 / C=60
— ตรงกับ default field ของ Scoring Config เอง) เป็น fallback แทนตอนไม่มี
config active แล้วเทียบ score กับ threshold นั้นตามปกติ ไม่ใช่ข้ามไป D
ตรงๆ ทั้งสองจุดแล้ว พร้อมเทส `test_11b_...`/`test_19b_...` กันบั๊กนี้กลับมา

**⚠️ สำคัญ — ข้อมูลเก่าที่มีอยู่แล้วไม่ recompute อัตโนมัติ:** เพราะ `tier`
เป็น stored computed field การอัปเกรดโมดูลเฉยๆ ไม่ทำให้ค่าเก่าที่เคยถูก
เขียนผิดเป็น D ไปแล้วถูกคำนวณใหม่ให้อัตโนมัติ ต้อง trigger recompute เอง
ครั้งเดียวหลังอัปเกรด เช่น รันผ่าน Odoo shell:
```python
env['fleet.telematics.log'].search([])._compute_tier()
env.cr.commit()
```

## 13. ✅ แก้แล้ว — ต้นตอที่แท้จริงของ error "Invalid field ...scoring.config.active" ที่ค้างมานาน
หลังแก้ field `active` → `is_active` ในรอบก่อนๆ (ข้อ 9) มีคนแจ้งว่ายังเจอ
error เดิมซ้ำอีกที่หน้า **Driver Dashboard** ทั้งที่ grep ทั้งโปรเจกต์ (`*.py`,
`*.xml`) ก็ไม่เจอ field `active` เหลืออยู่ที่ไหนแล้ว

**สาเหตุจริง:** จุดที่หลงเหลืออยู่คือใน **`static/src/js/driver_dashboard.js`**
(โค้ด JavaScript ของ Driver Dashboard ซึ่งเป็น OWL client action เรียก RPC
เอง) มีการ hardcode ชื่อ field เป็น `"active"` ตรงๆ ในตัวแปร `args` ของ
`search_read` — ไฟล์ `.js` ไม่เคยถูก grep ด้วยตอนไล่แก้ field รอบก่อนๆ เลย
(เพราะไล่แค่ `*.py`/`*.xml`) จึงหลุดรอดมาได้นานหลายรอบ

**วิธีแก้:** เปลี่ยน `["active", "=", true]` → `["is_active", "=", true]`
ใน `static/src/js/driver_dashboard.js` — เช็คซ้ำทั้ง `static/src/` ทั้ง
โฟลเดอร์แล้วไม่มี field `active` (ชื่อเก่า) หลงเหลืออยู่ที่ไหนอีก

**บทเรียน:** เวลาไล่หา field reference ที่เหลือหลังเปลี่ยนชื่อ field ต้อง
grep ให้ครอบคลุม **`static/src/**/*.js`** ด้วยเสมอ ไม่ใช่แค่ `*.py`/`*.xml`
— โมดูลที่มี OWL component เรียก RPC เองมักมี field name/domain ฝังอยู่ใน
JS ตรงๆ แบบนี้

## 14. ✅ แก้แล้ว — ใบโบนัสขึ้น "จำนวนทริปทั้งหมด: 0" ค้างตลอด ทั้งที่พนักงานมีทริปจริง
**อาการ:** เปิดใบ Incentive/Bonus ของพนักงานคนหนึ่ง เห็น "จำนวนทริปทั้งหมด:
0", "คะแนนเฉลี่ย: 0.00" ทั้งที่พนักงานคนนั้นมีทริป synced จริงในช่วงวันที่
เดียวกัน (เช็คจากหน้า Trip Logs แล้วเจอจริง) ผลคือได้ Tier D และ Bonus 0%
ทั้งที่ไม่ควรเป็นแบบนั้น

**สาเหตุจริง:** `total_trips`, `avg_score`, `min_score`, `total_distance_km`,
`total_harsh_events`, `total_idle_min` เป็น stored computed field ผูก
`@api.depends('driver_id', 'date_from', 'date_to')` เท่านั้น — Odoo จะ
คำนวณให้แค่ตอนที่ 3 field นี้เปลี่ยนค่า (ปกติคือตอนสร้าง record จาก cron
รายเดือน) แล้ว**ไม่มีวัน recompute อัตโนมัติอีกเลย** แม้จะมีทริปใหม่ของ
พนักงานคนนั้น sync เข้ามาเพิ่มทีหลังก็ตาม เพราะ dependency ไม่ได้ผูกกับ
record ใน `fleet.telematics.log` โดยตรง (เป็นแค่ `search()` ข้าม model
ซึ่ง Odoo ไม่รู้จักเป็น dependency ที่ track ได้) — ถ้าใบโบนัสถูกสร้างตอน
Backend ยังมีปัญหา sync ไม่ได้ (ตามที่ debug กันมาก่อนหน้านี้) ตัวเลขจะค้าง
เป็น 0 ตลอดไปแม้ทริปจะ sync สำเร็จภายหลังแล้วก็ตาม — เป็นบั๊กแพทเทิร์น
เดียวกับข้อ 11 (Tier บน Trip Log) เป๊ะๆ

**วิธีแก้:** เพิ่มการเรียก `self._compute_incentive()` ซ้ำในตอนต้นของ
`_apply_backend_bonus()` — ฟังก์ชันนี้ถูกเรียกทุกครั้งที่กดปุ่ม "Refresh
from Backend" หรือกด "Confirm" (ทั้งสองจุดใช้ได้เฉพาะตอน Draft เท่านั้น จึง
ไม่ชนกับ write-lock หลัง Confirm) ทำให้ตัวเลขทริปสดใหม่เสมอก่อนจะคำนวณ
Tier/Bonus และก่อนจะล็อกตัวเลขถาวร

**สำหรับใบโบนัสเก่าที่ค้างเป็น 0 อยู่แล้ว (เช่นใบที่ Confirm ไปแล้ว):**
ต้องกด **"รีเซ็ต"** กลับเป็น Draft ก่อน แล้วกด **"Refresh from Backend"**
อีกครั้ง ตัวเลขถึงจะอัปเดตให้ถูกต้อง — ระบบไม่ recompute ใบที่ Confirm/
Approve ไปแล้วให้อัตโนมัติ (ตั้งใจ เพื่อความโปร่งใส ป้องกันตัวเลขเปลี่ยน
เองหลังอนุมัติไปแล้ว)

## 15. ✅ แก้แล้ว — Upgrade module พังด้วย ParseError "โดเมนไม่ถูกต้อง" ที่ ir.rule ของ Incentive
**อาการ:** กด Upgrade module แล้วเจอ `RPC_ERROR` เต็มจอ ข้อความสำคัญคือ
`odoo.tools.convert.ParseError: while parsing
file:.../security/telematics_security.xml:50 โดเมนไม่ถูกต้อง:
'fleet.telematics.incentive'` — module ติดตั้ง/อัปเกรดไม่สำเร็จเลย

**สาเหตุ:** field `driver_user_id` บน `fleet.telematics.incentive` เดิม
เป็น `related='driver_id.user_id', store=True` (related field ผ่าน
`hr.employee.user_id`) — บน Odoo 19 ของผู้ใช้คนนี้ related field แบบ
dotted-path ผ่าน `hr.employee` ดันไม่ resolve ได้เสถียรตอนโหลด data file
(`ir.rule`) ที่อ้างอิง field นี้ในเงื่อนไข domain ทำให้ Odoo validate
domain ไม่ผ่านตั้งแต่ตอน parse XML เลย — โมดูลเลยติดตั้งไม่สำเร็จทั้งโมดูล
(ไม่ใช่แค่ rule เดียว เพราะ ParseError หยุด load_modules ทั้ง process)

น่าจะเป็นปัญหาตระกูลเดียวกับที่เจอเรื่อง `hr.version` (Odoo 19) แทนที่
`hr.contract` ใน `_apply_backend_bonus()` — คือ Odoo 19 ปรับโครงสร้าง HR
module ไปพอสมควร ทำให้ pattern เดิมที่เคยใช้ได้กับ Odoo เวอร์ชันก่อนหน้า
มีปัญหาบนเวอร์ชันนี้

**วิธีแก้:** เลิกใช้ `related=` ไปเลย เปลี่ยน `driver_user_id` เป็น field
ธรรมดา (`store=True` แต่ไม่ใช่ related) แล้วจัดการ sync ค่าเอง (จาก
`driver_id.user_id` ที่ browse ตรงๆ ใน Python) ผ่าน `create()`/`write()`
override แทน — field ธรรมดาแบบนี้ไม่ผ่านกลไก related-field-resolution
ของ Odoo ที่มีปัญหา ir.rule เลยโหลดผ่านปกติ พฤติกรรมที่ผู้ใช้เห็นเหมือนเดิม
ทุกอย่าง (driver เห็นแค่โบนัสของตัวเอง) แค่เปลี่ยนกลไกภายในเท่านั้น

## 16. ✅ ทำแล้ว — Tier เปลี่ยนจาก 4 ระดับตายตัว (A/B/C/D) เป็นไดนามิก
ตาม FDD §12.3 ที่ระบุว่า "Admin กำหนดเป็น Many2many ใน config ให้ Admin
เพิ่ม/ลบ tier เองได้" (ก่อนหน้านี้เคยเป็นแค่ 8 field ตายตัว เพิ่ม/ลด
จำนวน tier ไม่ได้เลยถ้าไม่แก้โค้ด — บันทึกไว้เป็นข้อ ⚠️ ในเอกสารรอบตรวจ
สอบก่อนหน้านี้)

**สิ่งที่เปลี่ยน:**
- โมเดลใหม่ `fleet.telematics.scoring.tier` (name, min_score, bonus_pct,
  scoring_config_id) — ล็อกแก้ไข/ลบไม่ได้เองถ้า config แม่ Active อยู่
  (เหมือน field อื่นบน Scoring Config)
- `fleet.telematics.scoring.config.tier_ids` (One2many) แทนที่
  `tier_a_min_score`...`tier_d_bonus_pct` (8 field เดิม) — สร้าง config
  ใหม่จะได้ default 3 แถว A(90/10%)/B(75/5%)/C(60/0%) ให้อัตโนมัติ
  เหมือนพฤติกรรมเดิม ไม่ต้องตั้งเองใหม่หมด
- `_local_tier_from_score()` (telematics_incentive.py) และ
  `_compute_tier()` (telematics_log.py) เปลี่ยนมา loop ผ่าน tier_ids
  เรียงจาก min_score มากไปน้อย แทนเทียบทีละ field — ถ้าคะแนนต่ำกว่า tier
  ต่ำสุดที่ตั้งไว้ทั้งหมด ผลลัพธ์คือ `"Below Minimum"` (ไม่ใช่ `"D"` ตายตัว
  อีกต่อไป เพราะไม่รู้ว่า tier ไหนคือ "ต่ำสุด" ตามชื่อได้แล้ว ต้องดูจาก
  min_score เท่านั้น)

**⚠️ ผลกระทบที่ต้องรู้ (breaking changes):**
1. **`incentive_tier`** (บน `fleet.telematics.incentive`) และ **`tier`**
   (บน `fleet.telematics.log`) เปลี่ยนจาก `Selection` (จำกัดแค่ A/B/C/D)
   เป็น **`Char`** — เพราะ Selection field เขียนค่านอก list ตัวเลือกไม่ได้
   เลย จะพังทันทีถ้า Admin ตั้งชื่อ Tier เป็นอย่างอื่นที่ไม่ใช่ A/B/C/D
2. **"แจ้งเตือน HR เมื่อ Tier D"** เปลี่ยนเงื่อนไขจากเช็คชื่อ tier ตรงๆ
   (`incentive_tier == 'D'`) เป็นเช็ค **`bonus_pct <= 0`** แทน — ตรงกับ
   ความหมายทางธุรกิจจริงๆ ("ไม่ได้โบนัส") ไม่ขึ้นกับชื่อ tier
3. **`_build_config_payload()`** (ส่งไป Backend ตอนกด Push Config)
   เปลี่ยนจาก 8 key แบน (`tier_a_min_score`, `tier_a_bonus_pct`, ...) เป็น
   **key เดียว `"tiers"` ที่เป็น list** ของ `{name, min_score, bonus_pct}`
   — **ต้องอัปเดตฝั่ง Backend ให้อ่านฟอร์แมตใหม่นี้ด้วย** ไม่งั้น Backend
   จะอ่าน Tier จาก Config ที่ Push ไปไม่ได้เลย (คนละ repo ที่ผมไม่ได้แก้ให้)
4. **หน้า List ของ Scoring Config** ไม่โชว์ Tier เป็นคอลัมน์แยกได้อีกแล้ว
   (เพราะไม่มีจำนวนคอลัมน์คงที่) — เพิ่ม field คำนวณ `tier_summary` แสดง
   สรุปเป็นข้อความแทน เช่น `"A≥90 / B≥75 / C≥60"`
5. **สีป้าย Tier ใน `driver_score_report.xml`** (PDF Monthly Score Report)
   ยัง hardcode สีไว้แค่ 3 สีสำหรับชื่อ A/B/C — ถ้า Admin ตั้งชื่อ Tier อื่น
   จะได้สีแดง (fallback) เสมอ เป็นข้อจำกัดเชิง cosmetic เท่านั้น ไม่กระทบ
   ตัวเลข ยังไม่ได้ทำให้ไดนามิกเต็มรูปแบบ — ทำได้ถ้าต้องการ แต่ต้องคิด
   scheme สีแบบไดนามิกใหม่ (เช่นไล่สีตามลำดับ เหมือนที่ทำใน
   `driver_dashboard.js` แล้ว)
6. **`static/src/js/driver_dashboard.js`** ก็เคย hardcode field เดิมไว้
   เหมือนกัน (เจอพร้อมกับตอนแก้ครั้งนี้ ไม่ใช่จุดใหม่ที่เพิ่งพัง) แก้ไป
   พร้อมกันแล้ว — ใช้ RPC 2 รอบ (ดึง tier_ids ก่อน แล้วดึงรายละเอียด tier
   แต่ละอันอีกที) และไล่สีตามลำดับ (index) แทนชื่อ tier ตรงๆ

**สิ่งที่ยังไม่ได้ทำ (นอกขอบเขตของรอบแก้นี้):**
- Backend repo (`assada07/telematics-odoo`) ยังไม่ได้อัปเดตให้อ่าน
  payload รูปแบบใหม่ — Config เก่าที่เคย Push ไปแล้วด้วยฟอร์แมตเดิมจะยัง
  ใช้งานได้ปกติจนกว่าจะ Push ใหม่อีกครั้ง แต่ Push ครั้งถัดไปจะพังถ้า
  Backend ยังอ่านฟอร์แมตเดิมอยู่
- สีป้าย Tier ใน PDF report ยังไม่ไดนามิกเต็มรูปแบบ (ข้อ 5 ด้านบน)

## 17. ✅ แก้แล้ว — Backend (assada07/telematics-backend) ไม่รองรับ "tiers" เลย — Silent Data Loss
ตรวจ repo Backend จริงแล้วยืนยัน: `ScoringConfigRequest` (Pydantic) และ
ตาราง `scoring_config_cache` **ไม่มี field/column `tiers` เลย** — เพราะ
Pydantic ไม่ได้ตั้ง `extra="forbid"` ทำให้ POST `/config/scoring` ตอบ
**HTTP 201 สำเร็จเสมอ** แม้ `tiers` ที่ Odoo ส่งไปจะถูกทิ้งเงียบๆ โดยไม่มี
error ใดๆ — Admin เห็น "Push Config สำเร็จ ✅" ทั้งที่ Tier ไม่ได้ถูกบันทึก

**ร้ายแรงกว่านั้น:** endpoint `GET /drivers/{id}/bonus` (ที่ Odoo ใช้เป็น
แหล่งข้อมูลโบนัสหลักใน `_apply_backend_bonus()`) hardcode threshold ไว้
ตายตัวที่ A=90/B=75/C=60 — ถ้า Admin ตั้ง Tier ต่างจาก default ใน Odoo
**Backend จะคำนวณโบนัสผิดโดยไม่มี error แจ้งเตือนเลย** (path fallback ใน
เครื่องที่ถูกต้องกว่ากลับไม่ถูกใช้ เพราะ Backend เรียกได้ปกติ ไม่ error)

**แก้แล้วทั้ง 2 ฝั่ง:**
- **Backend** (แก้แยก repo, ไม่ใช่ repo นี้): เพิ่มตาราง `scoring_tier_cache`
  + column `speed_limit_bkk`/`speed_limit_upcountry`, endpoint `/bonus`,
  `/score`, `/reports/driver-score` เปลี่ยนมาอ่าน Dynamic Tier จริงจาก DB
  แทน hardcode (พร้อม fallback ปลอดภัยถ้า DB ยังไม่ migrate) — มี migration
  script แยก (`migration_add_tier_support.sql`) สำหรับ DB ที่รันอยู่แล้ว
- **Odoo** (repo นี้, แก้ 2026-08-10): `action_push_to_backend()` เพิ่มการ
  เช็ค response หลัง push — ถ้า Backend ไม่ส่ง `tiers` กลับมาเลย หรือ
  จำนวนไม่ตรงกับที่ส่งไป จะแจ้งเตือน Admin ทันที (⚠️ warning แทน ✅ success)
  ไม่ใช่แค่เชื่อ HTTP status code เหมือนเดิม — ป้องกันปัญหาแบบนี้ไม่ให้
  เงียบหายอีกในอนาคต แม้ Backend จะ regress กลับไปแบบไม่รองรับ tiers อีก
  ก็ตาม

**Test เพิ่ม:** `test_20e/f/g_push_*` ใน `test_fleet_integration.py`
ยืนยัน warning ทำงานถูกทั้ง 3 กรณี (ไม่มี tiers เลย / จำนวนไม่ตรง / ตรงกัน)

## 18. ✅ แก้แล้ว — Tier Table ขาด "เกรด" และ "สี Badge" ตามที่ FDD §12.3 ระบุ
FDD §12.3 ตาราง Tier Table มี 5 คอลัมน์: Tier / คะแนนขั้นต่ำ / **เกรด** /
**สี Badge** / % โบนัส แต่ตอนแก้เป็น Dynamic Tier (ข้อ 16, 2026-08-04)
โมเดล `fleet.telematics.scoring.tier` มีแค่ 3 field (`name`, `min_score`,
`bonus_pct`) — ขาด "เกรด" (คำอธิบายสั้นๆ) และ "สี Badge" ไปเลย ทั้งที่
เอกสารเขียนกำกับไว้ชัดว่าทั้งคู่ "(ปรับได้)" คือ Admin ควรตั้งเองได้ ไม่ใช่
hardcode ในโค้ด

**แก้แล้ว:**
- เพิ่ม field `grade_label` (Char, เกรด) และ `badge_color` (Char เก็บ hex
  code, สี Badge) บน `fleet.telematics.scoring.tier`
- เพิ่ม `@api.constrains('badge_color')` ตรวจรูปแบบ hex (`#RGB`/`#RRGGBB`)
  กันพิมพ์ผิดแล้วไป break หน้าจอ/PDF เงียบๆ
- Default tier A/B/C ตอนสร้าง config ใหม่ตั้งค่าให้ตรงตัวอย่างใน FDD เป๊ะ
  (A=ดีเยี่ยม/#28a745, B=ดี/#17a2b8, C=พอใช้/#ffc107)
- `_build_config_payload()` ส่ง `grade_label`/`badge_color` ไปให้ Backend
  ด้วย (เผื่อ Backend อยากใช้แสดงผลฝั่งตัวเองในอนาคต)
- เพิ่มคอลัมน์ "เกรด" และ "สี Badge" ในหน้าจอ Incentive Tiers
  (`telematics_scoring_views.xml`)
- **`driver_score_report.xml`** (PDF Monthly Score Report) — จุดที่ข้อ 16.5
  เคยบันทึกไว้ว่า "ยัง hardcode สีไว้แค่ 3 สี" **แก้ให้ไดนามิกเต็มรูปแบบ
  แล้ว**: อ่าน `badge_color`/`grade_label` จริงจาก
  `doc.scoring_config_id.tier_ids` แทน hardcode `'A' == ... and '#15803D' or ...`
  — ถ้าหา tier ที่ชื่อตรงกันไม่เจอ fallback เป็นสีเทากลาง (`#6c757d`) แทน
  สีแดงเดิม (สีแดงเดิมสื่อความหมายผิดว่าเป็นสถานะแย่เสมอ ทั้งที่บางครั้ง
  แค่ config เก่าไม่มี tier_ids ผูกอยู่)

**Test เพิ่ม:** `test_01b`–`test_01f` ใน `test_fleet_integration.py`
ยืนยัน default grade/color ตรง FDD, validation hex ทำงานถูก, และ
`_build_config_payload()` ส่งครบ

**ยังไม่ได้ทำ (นอกขอบเขตรอบนี้):** `telematics_log_views.xml` (Trip Log
list/kanban) ยังใช้ `decoration-success="tier=='A'"` ฯลฯ และ filter
`domain="[('tier','=','A')]"` แบบ hardcode 4 ตัวอักษร A/B/C/D ตรงๆ — ถ้า
Admin ตั้งชื่อ Tier เป็นอย่างอื่น (เช่น Gold/Silver) filter จะหาไม่เจอ/
badge จะไม่ขึ้นสี (field `tier` เองคำนวณถูกต้องอยู่แล้ว กระทบแค่ตัว
filter/decoration ในหน้าจอ ไม่กระทบข้อมูลหรือการจ่ายโบนัส) — ต้องใช้
custom widget ถ้าจะทำให้ badge ในหน้า list ใช้สีไดนามิกจาก `badge_color`
จริง (ตัว native `decoration-*` ของ Odoo รองรับแค่ class Bootstrap ตายตัว
ไม่รองรับ hex สีที่กำหนดเองในหน้าจอ list) — ยังไม่ได้ทำเพราะต้องทดสอบกับ
Odoo instance จริง

## 19. ✅ แก้แล้ว — Trip Log badge/filter hardcode A/B/C/D (ตามที่บันทึกไว้เป็น "ยังไม่ได้ทำ" ในข้อ 18)
กลับมาแก้จุดที่ข้อ 18 บันทึกไว้ว่ายังไม่ทำ เพราะตอนแรกเข้าใจผิดว่าต้องใช้
custom JS widget ถึงจะทำให้ badge/filter ใช้ได้กับชื่อ Tier ที่ Admin ตั้ง
เอง — จริงๆ แล้วมีทางแก้แบบ Odoo native ล้วนๆ โดยไม่ต้องพึ่ง custom widget
เลย ใช้ pattern เดียวกับที่ `static/src/js/driver_dashboard.js` เคยแก้
ปัญหานี้ไปแล้ว (ข้อ 16.6): **ไล่สีตามลำดับ (rank) แทนชื่อ tier ตรงๆ**

**สิ่งที่เปลี่ยน:**
- เพิ่ม field ใหม่ `tier_rank` (Integer, stored, compute พร้อมกับ `tier`
  ในเมธอดเดียวกัน `_compute_tier()` — ไม่ query ซ้ำ) บน
  `fleet.telematics.log`: 1 = Tier สูงสุด, 2 = รองลงมา, ..., 0 = "Below
  Minimum" (ต่ำกว่าทุก Tier ที่ตั้งไว้)
- **Badge decoration** (`telematics_log_views.xml` ทั้ง list และ form)
  เปลี่ยนจาก `decoration-success="tier=='A'"` เป็น
  `decoration-success="tier_rank==1"` (และ 2/3/≥4-หรือ-0 ตามลำดับ) — badge
  ขึ้นสีถูกต้องไม่ว่า Admin จะตั้งชื่อ Tier เป็นอะไรก็ตาม (Gold/Silver/
  Bronze ก็ได้ ไม่จำกัดแค่ A/B/C/D อีกต่อไป)
- **Search filter** เปลี่ยนจาก `domain="[('tier','=','A')]"` (4 ตัว
  ตายตัว) เป็น `domain="[('tier_rank','=',1)]"` ฯลฯ พร้อมเปลี่ยนชื่อ label
  เป็น "Tier อันดับ 1 (สูงสุด)" แทน "Tier A — ดีเยี่ยม" (ไม่ผูกกับชื่อ
  Tier ที่อาจเปลี่ยนได้) และเพิ่ม filter แยกสำหรับ "Below Minimum"
  (tier_rank=0) ที่ไม่เคย filter แยกได้มาก่อนเลยด้วย
- `group_tier` (group by ชื่อ tier ตรงๆ) **ไม่ต้องแก้** — ใช้งานได้ปกติ
  อยู่แล้วเพราะ group_by ไม่ต้อง enumerate ชื่อล่วงหน้าเหมือน filter

**Test เพิ่ม:** `test_11d` (ยืนยัน tier_rank ถูกต้องกับชื่อ Tier แบบ
กำหนดเอง เช่น Gold/Silver/Bronze) และ `test_11e` (ยืนยัน fallback
threshold เดิม A/B/C/D ยังได้ rank 1/2/3/4 ตรงตามลำดับเหมือนเดิม ไม่กระทบ
พฤติกรรมเก่า)

