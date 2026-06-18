FROM odoo:19.0

USER root

# 1. ติดตั้ง dependencies เพิ่มเติม (เพิ่มสำหรับการดึง API / เชื่อมต่อระบบภายนอก)
#RUN /usr/bin/python3 -m pip install --no-cache-dir \
#    requests \
#    psycopg2-binary \
#    --break-system-packages

# 2. สร้าง log directory และตั้งสิทธิ์ล่วงหน้า
RUN mkdir -p /var/log/odoo && \
    chown -R odoo:odoo /var/log/odoo

# 3. คัดลอกไฟล์ Config เข้าโฟลเดอร์ระบบ (ต้องทำตอนที่เป็น USER root)
COPY ./config/odoo.conf /etc/odoo/odoo.conf
RUN chown odoo:odoo /etc/odoo/odoo.conf

# 4. สลับกลับมาเป็นผู้ใช้ odoo เพื่อความปลอดภัยก่อนรันระบบ
USER odoo

# [ข้อแนะนำเพิ่มเติม]: สำหรับ custom_addons แนะนำให้ลบไลฟ์ COPY ออก 
# แล้วไปพึ่งพาระบบ Mount บน docker-compose.odoo.yml จะทำให้พัฒนาโค้ดได้ไวขึ้นครับ