# 🚛 Telematics Odoo

Odoo 17 ERP สำหรับ Fleet Management และ Payroll

## โครงสร้างโฟลเดอร์

```
telematics-odoo/
│
├── odoo/
│   ├── Dockerfile              # build Odoo พร้อม custom modules
│   ├── config/                 # odoo.conf
│   └── custom_addons/          # custom modules
│       └── fleet_telematics/   # module เชื่อมต่อกับ backend API
│
├── docker/
│   └── nginx/
│       ├── nginx.conf          # nginx reverse proxy config
│       └── certs/              # SSL certificates
│
├── .github/
│   └── workflows/
│       ├── ci.yml              # CI: lint odoo module
│       └── deploy.yml          # Deploy Odoo stack
│
├── docker-compose.yml          # Base: networks + volumes
├── docker-compose.odoo.yml     # Odoo services
├── docker-compose.dev.yml      # Development override
└── docker-compose.prod.yml     # Production override
```

## วิธีรัน

### Development
```bash
docker compose \
  -f docker-compose.yml \
  -f docker-compose.odoo.yml \
  -f docker-compose.dev.yml \
  up
```

### Production
```bash
docker compose \
  -f docker-compose.yml \
  -f docker-compose.odoo.yml \
  -f docker-compose.prod.yml \
  up -d
```

## GitHub Secrets ที่ต้องตั้งค่า

| Secret | คำอธิบาย |
|--------|---------|
| `ODOO_DB_USER` | username PostgreSQL ของ Odoo |
| `ODOO_DB_PASSWORD` | password PostgreSQL ของ Odoo |
| `ODOO_DB_NAME` | ชื่อ database Odoo |
| `ODOO_ADMIN_PASSWORD` | password Odoo master admin |
