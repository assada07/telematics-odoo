FROM odoo:17.0

USER root

# ติดตั้ง dependencies เพิ่มเติม
RUN pip3 install --no-cache-dir \
    requests \
    psycopg2-binary

# Copy custom addons เข้าไปใน Odoo
COPY ./odoo/custom_addons /mnt/extra-addons

# สร้าง log directory
RUN mkdir -p /var/log/odoo && \
    chown -R odoo:odoo /var/log/odoo

USER odoo

# Config file
COPY ./config/odoo.conf /etc/odoo/odoo.conf
