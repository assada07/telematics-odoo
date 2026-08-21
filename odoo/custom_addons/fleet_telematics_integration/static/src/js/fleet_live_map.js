/** @odoo-module **/
// static/src/js/fleet_live_map.js
// UC-06 — Fleet Live Map (SSE real-time ตาม FDD spec หลัก)
//
// อัปเกรดจาก Polling 30 วินาที → SSE ทุก 5 วินาที ตาม FDD §11 / Layer 2
//
// Data Flow:
//   OWL widget เปิด EventSource → /fleet_telematics/live_proxy (Odoo controller)
//   → Odoo controller ต่อ SSE ไปที่ GET /api/v1/fleet/live (Backend)
//   → Backend ส่ง array ตำแหน่งรถทุกคันทุก 5 วินาที
//   → OWL รับแล้วย้ายหมุดบนแผนที่ทันที
//
// Fallback: ถ้า SSE เชื่อมไม่ได้ (เช่น Backend ไม่พร้อม) จะ fallback เป็น
//   Polling ทุก 30 วินาที แทนโดยอัตโนมัติ — ผู้ใช้เห็น badge "Polling"

import { Component, useState, onMounted, onWillUnmount, useRef } from "@odoo/owl";
import { registry } from "@web/core/registry";

const LEAFLET_JS  = "https://unpkg.com/leaflet@1.9.4/dist/leaflet.js";
const LEAFLET_CSS = "https://unpkg.com/leaflet@1.9.4/dist/leaflet.css";
const FALLBACK_POLL_MS = 30000;  // fallback polling ถ้า SSE ไม่พร้อม

function loadLeaflet() {
    if (window.L) return Promise.resolve(window.L);
    return new Promise((resolve, reject) => {
        const link = document.createElement("link");
        link.rel  = "stylesheet";
        link.href = LEAFLET_CSS;
        document.head.appendChild(link);
        const script   = document.createElement("script");
        script.src     = LEAFLET_JS;
        script.onload  = () => resolve(window.L);
        script.onerror = reject;
        document.head.appendChild(script);
    });
}

// Polling fallback — ใช้ fetch JSON-RPC เหมือนเดิม
async function fetchViaRpc() {
    const res = await fetch("/fleet_telematics/vehicles_location", {
        method: "POST",
        headers: {
            "Content-Type": "application/json",
            "X-Requested-With": "XMLHttpRequest",
        },
        body: JSON.stringify({ jsonrpc: "2.0", method: "call", params: {} }),
    });
    const data = await res.json();
    if (data.error) throw new Error(data.error.data?.message || data.error.message);
    return data.result || [];
}

export class FleetLiveMap extends Component {
    static template = "fleet_telematics_integration.FleetLiveMap";

    setup() {
        this.mapRef = useRef("mapContainer");

        this.state = useState({
            vehicles:    [],
            mode:        "connecting",  // connecting | sse | polling | error
            error:       null,
            lastUpdate:  null,
            count:       0,
        });

        this.map          = null;
        this.markers      = {};
        this.eventSource  = null;
        this.pollTimer    = null;
        this._fittedOnce  = false;

        // ── ทิศทางรถ (heading) ──────────────────────────────────────────────
        // Backend ไม่ได้ส่ง field ทิศทางมาให้ (มีแค่ lat/lon/speed/ignition)
        // จึงต้องคำนวณ bearing เองจากตำแหน่งเก่า→ใหม่ของรถแต่ละคัน
        this._lastPos  = {};  // key(vehicle_id) -> {lat, lon}
        this._heading  = {};  // key(vehicle_id) -> องศาล่าสุดที่ใช้แสดง (0-360)
        this._MIN_MOVE_M = 3; // ขยับน้อยกว่านี้ (เมตร) ถือว่าจอดนิ่ง ไม่อัปเดตทิศ (กัน GPS jitter)

        onMounted(async () => {
            try {
                const L = await loadLeaflet();
                this.map = L.map(this.mapRef.el).setView([13.7563, 100.5018], 10);
                L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
                    attribution: "&copy; OpenStreetMap contributors",
                    maxZoom: 19,
                }).addTo(this.map);
            } catch (e) {
                this.state.mode  = "error";
                this.state.error = "โหลดแผนที่ไม่สำเร็จ: " + e;
                return;
            }
            this._connectSSE();
        });

        onWillUnmount(() => {
            this._closeSSE();
            if (this.pollTimer) clearInterval(this.pollTimer);
        });
    }

    // ── SSE Connection ────────────────────────────────────────────────────────
    _connectSSE() {
        this.state.mode = "connecting";

        // ปิด connection เก่าก่อนเปิดใหม่
        this._closeSSE();

        this.eventSource = new EventSource("/fleet_telematics/live_proxy");

        this.eventSource.onopen = () => {
            // SSE เชื่อมสำเร็จ — หยุด polling fallback ถ้ามี
            if (this.pollTimer) {
                clearInterval(this.pollTimer);
                this.pollTimer = null;
            }
            this.state.mode  = "sse";
            this.state.error = null;
        };

        this.eventSource.onmessage = (ev) => {
            try {
                const data = JSON.parse(ev.data);

                // Backend ส่ง error มา
                if (data.error) {
                    this.state.error = data.error;
                    return;
                }

                // รับ array ตำแหน่งรถ — กรองเฉพาะที่มีพิกัดจริง
                const vehicles = (Array.isArray(data) ? data : [])
                    .filter(v => v.lat && v.lon);  // ← นับเฉพาะที่มีพิกัด

                this.state.vehicles = vehicles.map(v => ({
                    vehicle_id:   v.vehicle_id,
                    vehicle_name: this._getVehicleName(v.vehicle_id) || v.vehicle_name || `Vehicle ${v.vehicle_id}`,
                    device_id:    v.device_id,
                    driver_name:  this._getDriverName(v.vehicle_id) || v.driver_name || "-",
                    lat:          v.lat,
                    lon:          v.lon,
                    speed:        v.speed,
                    ignition:     v.ignition,
                    ts:           v.ts,
                }));

                this.state.count      = this.state.vehicles.length;  // นับเฉพาะที่มีพิกัด
                this.state.lastUpdate = new Date().toLocaleTimeString("th-TH");
                this._updateMarkers();

            } catch (e) {
                // parse ไม่ได้ ข้ามไป รอ event ถัดไป
            }
        };

        this.eventSource.onerror = () => {
            // SSE หลุด — เปลี่ยนเป็น fallback polling
            this.state.mode  = "polling";
            this.state.error = "SSE ขาดการเชื่อมต่อ — ใช้ Polling ทุก 30 วินาทีแทน";
            this._closeSSE();
            this._startFallbackPolling();
        };
    }

    _closeSSE() {
        if (this.eventSource) {
            this.eventSource.close();
            this.eventSource = null;
        }
    }

    // ── Fallback Polling (ถ้า SSE ไม่พร้อม) ──────────────────────────────────
    _startFallbackPolling() {
        if (this.pollTimer) return; // ไม่เริ่มซ้ำ
        this._pollOnce();
        this.pollTimer = setInterval(() => this._pollOnce(), FALLBACK_POLL_MS);
    }

    async _pollOnce() {
        try {
            const vehicles = await fetchViaRpc();
            this.state.vehicles  = vehicles;
            this.state.count     = vehicles.length;
            this.state.lastUpdate = new Date().toLocaleTimeString("th-TH");
            this.state.error     = null;
            this._updateMarkers();

            // ถ้า poll สำเร็จ ลอง reconnect SSE ใหม่
            if (!this.eventSource) {
                setTimeout(() => this._connectSSE(), 5000);
            }
        } catch (e) {
            this.state.error = "ดึงข้อมูลไม่สำเร็จ: " + (e.message || e);
        }
    }

    // ── helper ดึงชื่อจาก markers เก่า ────────────────────────────────────────
    _getVehicleName(vehicleId) {
        const v = this.state.vehicles.find(x => x.vehicle_id === vehicleId);
        return v ? v.vehicle_name : null;
    }

    _getDriverName(vehicleId) {
        const v = this.state.vehicles.find(x => x.vehicle_id === vehicleId);
        return v ? v.driver_name : null;
    }

    // ── คำนวณทิศ (bearing) จากจุดเก่า → จุดใหม่ หน่วยองศา 0-360 (0=เหนือ) ──────
    _computeBearing(lat1, lon1, lat2, lon2) {
        const toRad = (d) => (d * Math.PI) / 180;
        const φ1 = toRad(lat1), φ2 = toRad(lat2);
        const Δλ = toRad(lon2 - lon1);
        const y = Math.sin(Δλ) * Math.cos(φ2);
        const x = Math.cos(φ1) * Math.sin(φ2) - Math.sin(φ1) * Math.cos(φ2) * Math.cos(Δλ);
        const θ = Math.atan2(y, x);
        return (θ * 180 / Math.PI + 360) % 360;
    }

    // ── ระยะทางระหว่าง 2 จุด (เมตร) — สูตร Haversine ────────────────────────
    _distanceMeters(lat1, lon1, lat2, lon2) {
        const R = 6371000;
        const toRad = (d) => (d * Math.PI) / 180;
        const dφ = toRad(lat2 - lat1);
        const dλ = toRad(lon2 - lon1);
        const a = Math.sin(dφ / 2) ** 2 +
            Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dλ / 2) ** 2;
        return 2 * R * Math.asin(Math.sqrt(a));
    }

    // ── หาทิศทางที่จะใช้แสดงสำหรับรถคันหนึ่ง ──────────────────────────────────
    _resolveHeading(key, v) {
        // 1) ถ้า Backend ส่ง heading/course/bearing มาเองในอนาคต ใช้ค่านั้นเลย
        const fromApi = v.heading ?? v.course ?? v.bearing;
        if (fromApi !== undefined && fromApi !== null && !isNaN(fromApi)) {
            this._heading[key] = Number(fromApi);
            this._lastPos[key] = { lat: v.lat, lon: v.lon };
            return this._heading[key];
        }

        // 2) ไม่มี ต้องคำนวณเองจากตำแหน่งเก่า→ใหม่
        const prev = this._lastPos[key];
        if (prev) {
            const moved = this._distanceMeters(prev.lat, prev.lon, v.lat, v.lon);
            if (moved >= this._MIN_MOVE_M) {
                this._heading[key] = this._computeBearing(prev.lat, prev.lon, v.lat, v.lon);
            }
            // ขยับน้อยกว่า threshold (จอดนิ่ง/GPS jitter) → คงทิศเดิมไว้
        }
        this._lastPos[key] = { lat: v.lat, lon: v.lon };
        return this._heading[key] ?? 0; // ยังไม่เคยรู้ทิศ → ชี้เหนือไว้ก่อน
    }

    // ── วางหมุดบนแผนที่ ───────────────────────────────────────────────────────
    _updateMarkers() {
        if (!this.map || !window.L) return;

        // ลบหมุดรถที่ไม่อยู่ใน list แล้ว
        const activeKeys = new Set(this.state.vehicles.map(v => String(v.vehicle_id)));
        for (const key of Object.keys(this.markers)) {
            if (!activeKeys.has(key)) {
                this.markers[key].remove();
                delete this.markers[key];
                delete this._lastPos[key];
                delete this._heading[key];
            }
        }

        for (const v of this.state.vehicles) {
            if (!v.lat || !v.lon) continue;

            const key     = String(v.vehicle_id);
            const color   = v.ignition ? "#22c55e" : "#ef4444";
            const heading = this._resolveHeading(key, v);

            // ลูกศร (สามเหลี่ยม) หมุนตามทิศ — 0deg = ชี้เหนือ, หมุนตามเข็มนาฬิกา
            const icon = window.L.divIcon({
                className: "",
                iconSize:  [24, 24],
                iconAnchor:[12, 12],
                html: `<div style="
                    width:24px;height:24px;
                    transform:rotate(${heading}deg);
                    transform-origin:12px 12px;
                ">
                    <svg width="24" height="24" viewBox="0 0 24 24">
                        <path d="M12 2 L20 21 L12 16.5 L4 21 Z"
                              fill="${color}" stroke="white" stroke-width="1.5"
                              stroke-linejoin="round"
                              style="filter:drop-shadow(0 1px 2px rgba(0,0,0,0.4))"/>
                    </svg>
                </div>`,
            });

            const ts      = v.ts ? new Date(v.ts).toLocaleString("th-TH") : "-";
            const ignText = v.ignition ? "🟢 ON" : "🔴 OFF";
            const popup   = `
                <div style="min-width:180px;line-height:1.7">
                    <b>${v.vehicle_name || `Vehicle ${v.vehicle_id}`}</b><br/>
                    <span style="color:#666">คนขับ:</span> ${v.driver_name || "-"}<br/>
                    <span style="color:#666">Device:</span> ${v.device_id || "-"}<br/>
                    <span style="color:#666">ความเร็ว:</span> ${v.speed ?? "-"} km/h<br/>
                    <span style="color:#666">ทิศทาง:</span> ${Math.round(heading)}°<br/>
                    <span style="color:#666">Ignition:</span> ${ignText}<br/>
                    <span style="color:#999;font-size:11px">อัปเดต: ${ts}</span>
                </div>`;

            if (this.markers[key]) {
                // ย้ายหมุดเดิม — ไม่สร้างใหม่ (แสดงเฉพาะล่าสุด)
                this.markers[key]
                    .setLatLng([v.lat, v.lon])
                    .setIcon(icon)
                    .setPopupContent(popup);
            } else {
                // สร้างหมุดใหม่ครั้งแรก
                this.markers[key] = window.L.marker([v.lat, v.lon], { icon })
                    .addTo(this.map)
                    .bindPopup(popup);
            }
        }

        // Auto-fit แผนที่ครอบทุกหมุดครั้งแรก
        const keys = Object.keys(this.markers);
        if (keys.length > 0 && !this._fittedOnce) {
            const latlngs = keys.map(k => this.markers[k].getLatLng());
            if (latlngs.length === 1) {
                this.map.setView(latlngs[0], 13);
            } else {
                this.map.fitBounds(window.L.latLngBounds(latlngs), { padding: [40, 40] });
            }
            this._fittedOnce = true;
        }
    }
}

registry.category("actions").add("fleet_telematics_live_map", FleetLiveMap);
