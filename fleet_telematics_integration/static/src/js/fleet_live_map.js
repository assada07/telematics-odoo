/** @odoo-module **/
// static/src/js/fleet_live_map.js
// UC-06 — Fleet Live Map (Polling ทุก 30 วินาที ตาม FDD §7.3)
//
// แก้ไข 2026-07-01: เปลี่ยนจาก useService("rpc") เป็น fetch() ตรงๆ
// เพราะ Odoo 19 ไม่มี service ชื่อ "rpc" แล้ว (เปลี่ยนไปใน Odoo 17+)

import { Component, useState, onMounted, onWillUnmount, useRef } from "@odoo/owl";
import { registry } from "@web/core/registry";

const LEAFLET_JS  = "https://unpkg.com/leaflet@1.9.4/dist/leaflet.js";
const LEAFLET_CSS = "https://unpkg.com/leaflet@1.9.4/dist/leaflet.css";
const POLL_MS     = 30000;

function loadLeaflet() {
    if (window.L) return Promise.resolve(window.L);
    return new Promise((resolve, reject) => {
        const link = document.createElement("link");
        link.rel  = "stylesheet";
        link.href = LEAFLET_CSS;
        document.head.appendChild(link);

        const script = document.createElement("script");
        script.src    = LEAFLET_JS;
        script.onload = () => resolve(window.L);
        script.onerror = reject;
        document.head.appendChild(script);
    });
}

// เรียก Odoo JSON-RPC ด้วย fetch แทน useService("rpc")
async function odooCall(route, params = {}) {
    const response = await fetch(route, {
        method: "POST",
        headers: {
            "Content-Type": "application/json",
            "X-Requested-With": "XMLHttpRequest",
        },
        body: JSON.stringify({
            jsonrpc: "2.0",
            method:  "call",
            params:  params,
        }),
    });
    const data = await response.json();
    if (data.error) throw new Error(data.error.data?.message || data.error.message);
    return data.result;
}

export class FleetLiveMap extends Component {
    static template = "fleet_telematics_integration.FleetLiveMap";

    setup() {
        this.mapRef = useRef("mapContainer");

        this.state = useState({
            vehicles:   [],
            loading:    false,
            error:      null,
            lastUpdate: null,
        });

        this.map        = null;
        this.markers    = {};
        this.pollTimer  = null;
        this._fittedOnce = false;

        onMounted(async () => {
            try {
                const L = await loadLeaflet();
                this.map = L.map(this.mapRef.el).setView([13.7563, 100.5018], 10);
                L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
                    attribution: "&copy; OpenStreetMap contributors",
                    maxZoom: 19,
                }).addTo(this.map);
            } catch (e) {
                this.state.error = "โหลดแผนที่ไม่สำเร็จ: " + e;
                return;
            }
            this._startPolling();
        });

        onWillUnmount(() => {
            if (this.pollTimer) clearInterval(this.pollTimer);
        });
    }

    _startPolling() {
        this._fetchLocations();
        this.pollTimer = setInterval(() => this._fetchLocations(), POLL_MS);
    }

    async _fetchLocations() {
        this.state.loading = true;
        try {
            const result = await odooCall("/fleet_telematics/vehicles_location");
            this.state.vehicles   = result || [];
            this.state.lastUpdate = new Date().toLocaleTimeString("th-TH");
            this.state.error      = null;
            this._updateMarkers();
        } catch (e) {
            this.state.error = "ดึงข้อมูลไม่สำเร็จ: " + (e.message || e);
        } finally {
            this.state.loading = false;
        }
    }

    _updateMarkers() {
        if (!this.map || !window.L) return;

        // ลบหมุดรถที่หายไปจากรายการ
        const activeKeys = new Set(this.state.vehicles.map(v => String(v.vehicle_id)));
        for (const key of Object.keys(this.markers)) {
            if (!activeKeys.has(key)) {
                this.markers[key].remove();
                delete this.markers[key];
            }
        }

        for (const v of this.state.vehicles) {
            if (!v.lat || !v.lon) continue;

            const key   = String(v.vehicle_id);
            const color = v.ignition ? "#22c55e" : "#ef4444";

            const icon = window.L.divIcon({
                className: "",
                iconSize:  [18, 18],
                iconAnchor:[9, 9],
                html: `<div style="
                    background:${color};
                    width:14px;height:14px;border-radius:50%;
                    border:2px solid white;
                    box-shadow:0 1px 4px rgba(0,0,0,0.4);
                    margin:2px;
                "></div>`,
            });

            const ts      = v.ts ? new Date(v.ts).toLocaleString("th-TH") : "-";
            const ignText = v.ignition ? "🟢 ON" : "🔴 OFF";
            const popup   = `
                <div style="min-width:180px;line-height:1.7">
                    <b>${v.vehicle_name || "-"}</b><br/>
                    <span style="color:#666">คนขับ:</span> ${v.driver_name || "-"}<br/>
                    <span style="color:#666">Device:</span> ${v.device_id || "-"}<br/>
                    <span style="color:#666">ความเร็ว:</span> ${v.speed ?? "-"} km/h<br/>
                    <span style="color:#666">Ignition:</span> ${ignText}<br/>
                    <span style="color:#999;font-size:11px">อัปเดต: ${ts}</span>
                </div>`;

            if (this.markers[key]) {
                this.markers[key]
                    .setLatLng([v.lat, v.lon])
                    .setIcon(icon)
                    .setPopupContent(popup);
            } else {
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
