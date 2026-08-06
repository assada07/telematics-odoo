/** @odoo-module **/
// static/src/js/trip_map_widget.js
// Trip Detail Map Widget — FDD §12.6
// แสดง GPS track เส้นทางวิ่งของทริปบนแผนที่ Leaflet
// + markers จุด harsh events ระบุสี/ไอคอนตามประเภท

import { Component, onMounted, useRef, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

const LEAFLET_JS  = "https://unpkg.com/leaflet@1.9.4/dist/leaflet.js";
const LEAFLET_CSS = "https://unpkg.com/leaflet@1.9.4/dist/leaflet.css";

// สีตาม event type
const EVENT_COLORS = {
    harsh_brake:   "#ef4444",   // แดง
    harsh_accel:   "#f97316",   // ส้ม
    harsh_corner:  "#eab308",   // เหลือง
    speeding:      "#8b5cf6",   // ม่วง
    idling:        "#0ea5e9",   // ฟ้า
    bump:          "#6b7280",   // เทา
};

// ชื่อเหตุการณ์ภาษาไทย + ไอคอนกำกับ (แก้ตามที่ผู้ใช้ขอ 2026-08-05: อยากให้
// หมุดเหตุการณ์เป็น "รูปรถ" และบอกได้ว่าเกิดเหตุการณ์อะไร — ใช้ไอคอนรถ 🚗
// เป็นหมุดหลัก สีพื้นหลังเปลี่ยนตามประเภทเหตุการณ์ แล้วใส่ tooltip ชื่อไทย
// ลอยค้างไว้ตลอด (ไม่ต้องคลิกก่อนถึงจะเห็น) เพื่อให้ "รถบอกเหตุการณ์" ได้
// ทันทีที่มองแผนที่ ไม่ต้องกดหมุดทีละอันเอง
const EVENT_LABELS_TH = {
    harsh_brake:   { text: "เบรกกะทันหัน",   icon: "🛑" },
    harsh_accel:   { text: "เร่งกะทันหัน",   icon: "💨" },
    harsh_corner:  { text: "เข้าโค้งแรง",     icon: "↩️" },
    speeding:      { text: "ขับเร็วเกิน",     icon: "⚡" },
    idling:        { text: "จอดเครื่องติด",  icon: "⏱️" },
    bump:          { text: "กระแทก",         icon: "💥" },
};

function loadLeaflet() {
    if (window.L) return Promise.resolve(window.L);
    return new Promise((resolve, reject) => {
        const link = document.createElement("link");
        link.rel = "stylesheet"; link.href = LEAFLET_CSS;
        document.head.appendChild(link);
        const script = document.createElement("script");
        script.src = LEAFLET_JS;
        script.onload = () => resolve(window.L);
        script.onerror = reject;
        document.head.appendChild(script);
    });
}

export class TripMapWidget extends Component {
    static template = "fleet_telematics_integration.TripMapWidget";

    setup() {
        this.mapRef = useRef("tripMapContainer");
        this.state  = useState({ error: null, pointCount: 0 });
        this.map    = null;

        onMounted(async () => {
            try {
                const L = await loadLeaflet();
                this.map = L.map(this.mapRef.el).setView([13.7563, 100.5018], 11);
                L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
                    attribution: "&copy; OpenStreetMap contributors",
                }).addTo(this.map);

                // แก้บั๊ก 2026-08-05: widget นี้อยู่ในแท็บ "GPS Track" ของ
                // notebook — ถ้าตอน widget mount แท็บนี้ยังไม่ใช่แท็บที่
                // active อยู่ (เช่น หลัง soft_reload ตอนกดปุ่ม "โหลด GPS
                // Track" ฟอร์มมักรีเซ็ตกลับไปแท็บแรกเสมอ) container ของ
                // แผนที่จะมีขนาด 0x0 ตอน Leaflet คำนวณพิกัด/ซูมครั้งแรก ทำให้
                // fitBounds คำนวณผิดเพี้ยนไปหมด กลายเป็นซูมออกกว้างผิดปกติ
                // (เห็นทั้งกรุงเทพฯ แทนที่จะซูมเข้าเส้นทางจริง) แก้ด้วยการ
                // สั่ง invalidateSize() ให้ Leaflet เช็คขนาด container ใหม่
                // อีกครั้งหลังจากรอ 1 เฟรมให้ layout นิ่งตัวก่อน ก่อนจะวาด
                // เส้นทาง/fitBounds จริง
                await new Promise(resolve => requestAnimationFrame(resolve));
                this.map.invalidateSize();

                this._drawTrack(L);
            } catch (e) {
                this.state.error = "โหลดแผนที่ไม่สำเร็จ: " + e;
            }
        });
    }

    // แก้บั๊กรอบ 3 (2026-08-05): เช็คจาก console.warn ที่ผู้ใช้ส่งมาแล้วว่า
    // extractProps ของ registry "view_widgets" ในเวอร์ชันนี้ได้รับ props
    // แค่ { name, widget, options, attrs } เท่านั้น — "record" ไม่ได้ถูกส่ง
    // เข้า extractProps เลย (คนละแบบกับตัวอย่างส่วนใหญ่ที่หาเจอ ซึ่งพูดถึง
    // registry "fields" ไม่ใช่ "view_widgets") — ลองเปลี่ยนมาอ่าน record
    // จาก this.props ที่ระดับ Component โดยตรงแทน (สมมติฐาน: Odoo อาจ merge
    // "record" เข้า props ตอนสร้าง Component จริง แยกจากที่ extractProps
    // เห็น เหมือน pattern "standardFieldProps" ของ registry "fields")
    _getRecordData(fieldName) {
        const record = this.props.record;
        if (!record || !record.data) {
            console.warn(
                "[trip_map widget] this.props.record (ระดับ Component) ก็ไม่มีอีก — " +
                "props keys ที่ Component เห็นจริงๆ คือ:",
                Object.keys(this.props)
            );
            return undefined;
        }
        return record.data[fieldName];
    }

    _drawTrack(L) {
        // อ่าน GPS track จาก field gps_track_json — ลองอ่านจาก
        // this.props.record ก่อน (ถ้ามี) ถ้าไม่มีค่อย fallback ไปที่ prop
        // ที่ extractProps ส่งมา
        //
        // แก้บั๊กรอบ 4 (2026-08-05): เดิมใช้ `??` (nullish coalescing) เช็ค
        // fallback แต่ Odoo แทนค่าว่างของ field ประเภท Text ด้วย `false`
        // (ไม่ใช่ null/undefined/"") ซึ่ง `??` ไม่ตีความ false ว่าต้อง
        // fallback เลย ทำให้ raw กลายเป็น `false` ตรงๆ แล้ว JSON.parse(false)
        // จะได้ผลลัพธ์เป็น boolean false ต่อ (ไม่ throw เพราะ JSON.parse
        // แปลง argument เป็น string ก่อนเสมอ "false" ก็เป็น valid JSON)
        // แล้ว false.map() ก็เลย error ตามที่เจอ — เช็ค falsy ตรงๆ แทน
        const gpsFieldName = this.props.attrs?.gps_track_json || "gps_track_json";
        let raw = this._getRecordData(gpsFieldName);
        if (!raw) raw = this.props.gpsTrackJson;
        if (!raw) raw = "[]";

        let parsed;
        try { parsed = JSON.parse(raw); } catch { return; }

        // แก้บั๊กรอบ 6 (2026-08-05): ยืนยันจาก console diagnostic แล้วว่า
        // parsed หลัง JSON.parse รอบแรกยังเป็น string อยู่ (typeof: string,
        // constructor: String) ไม่ใช่ array — แปลว่าข้อมูลถูก JSON encode
        // ซ้อนกัน 2 ชั้น (double-encoded) ตั้งแต่ต้นทาง เช่น Backend อาจส่ง
        // gps_track กลับมาเป็น "ข้อความ JSON" (string) แทนที่จะเป็น JSON
        // array ตรงๆ ในตัว response เอง แล้วโค้ด Python (_json.dumps() ใน
        // action_load_trip_detail) ก็ห่อ string นั้นอีกชั้นหนึ่งตอนบันทึกลง
        // DB — ต้อง JSON.parse() ซ้ำอีกรอบถึงจะได้ array จริง
        if (typeof parsed === "string") {
            try { parsed = JSON.parse(parsed); } catch { return; }
        }

        // เผื่อ Backend/โค้ดบางจุดห่อ array ไว้ในอีก object ชั้นหนึ่ง เช่น
        // {"points": [...]} หรือ {"gps_track": [...]} แทนที่จะเป็น array
        // เปลือยๆ ตรงๆ — ลองแกะดูก่อนจะถือว่า "ไม่มีข้อมูล"
        //
        // แก้บั๊กรอบ 5 (2026-08-05): เดิมเช็คด้วย Array.isArray() ตรงๆ แต่
        // ข้อมูลที่เห็นจริงใน console.warn หน้าตาเป็น array ที่ถูกต้องแล้ว
        // (มี lat/lon/speed/ts ครบ) กลับโดนตัดสินว่า "ไม่ใช่ array" — ไม่
        // แน่ใจสาเหตุแน่ชัด (อาจเป็นเรื่อง cross-realm object ที่พบได้ยาก
        // ใน Owl/Odoo) เปลี่ยนมาเช็คแบบ duck-typing แทน (มี .length เป็น
        // ตัวเลข + วน for...of ได้จริง) ซึ่งครอบคลุมกรณีแปลกๆ ได้กว้างกว่า
        // Array.isArray() เดิม
        const isArrayLike = (v) =>
            v != null && typeof v === "object" && typeof v.length === "number";

        let points = isArrayLike(parsed) ? parsed
                   : isArrayLike(parsed?.points) ? parsed.points
                   : isArrayLike(parsed?.gps_track) ? parsed.gps_track
                   : null;

        if (!points) {
            console.warn(
                "[trip_map widget] gps_track_json parse ได้ แต่ไม่ใช่ array/array-like " +
                "ที่คาดไว้ — typeof:", typeof parsed,
                "| Array.isArray:", Array.isArray(parsed),
                "| constructor:", parsed?.constructor?.name,
                "| ค่าจริง:", parsed
            );
            return;
        }
        // แปลงเป็น plain Array เสมอ (เผื่อ points เป็นแค่ array-like ไม่ใช่
        // Array แท้ๆ — Array.from ใช้กับ array-like/iterable ได้ทั้งคู่)
        points = Array.from(points);
        if (!points.length) return;

        // วาดเส้นทาง
        const latlngs = points.map(p => [p.lat, p.lon]);
        const polyline = L.polyline(latlngs, {
            color: "#3b82f6", weight: 3, opacity: 0.8,
        }).addTo(this.map);

        // จุดเริ่ม
        L.marker(latlngs[0], {
            icon: L.divIcon({
                className: "",
                html: `<div style="background:#22c55e;width:12px;height:12px;border-radius:50%;border:2px solid white;box-shadow:0 1px 3px rgba(0,0,0,.4)"></div>`,
                iconSize: [12, 12], iconAnchor: [6, 6],
            }),
        }).addTo(this.map).bindPopup("จุดเริ่มต้น");

        // จุดสุดท้าย
        L.marker(latlngs[latlngs.length - 1], {
            icon: L.divIcon({
                className: "",
                html: `<div style="background:#ef4444;width:12px;height:12px;border-radius:50%;border:2px solid white;box-shadow:0 1px 3px rgba(0,0,0,.4)"></div>`,
                iconSize: [12, 12], iconAnchor: [6, 6],
            }),
        }).addTo(this.map).bindPopup("จุดสิ้นสุด");

        // วาด harsh event markers เป็น "รูปรถ" ตามที่ผู้ใช้ขอ — สีพื้นหลัง
        // เปลี่ยนตามประเภทเหตุการณ์ พร้อม tooltip ชื่อเหตุการณ์ภาษาไทย
        // ลอยค้างไว้ตลอดข้างๆ ไอคอน (permanent: true) ไม่ต้องคลิกก่อนถึงจะ
        // รู้ว่าเกิดอะไรขึ้น ณ จุดนั้น — คลิกแล้วยังขึ้น popup รายละเอียด
        // เต็ม (severity/speed) เหมือนเดิมด้วย
        //
        // แก้บั๊กรอบ 7 (2026-08-05): ยืนยันแล้วว่า Event List มี lat/lon
        // ครบทุกแถวจริง (เช็คจากหน้าจอ Event List โดยตรง) แต่รูปรถไม่ขึ้น
        // บนแผนที่เลย — ใช้แนวทางเดียวกับที่แก้ gps_track_json สำเร็จ:
        // ลองหาตัวแทน array ด้วย duck-typing (.length เป็นตัวเลข) แทน
        // Array.isArray()/.records ตรงๆ ที่อาจพลาดกรณีโครงสร้างข้อมูลจริง
        // ไม่ตรงกับที่คาดไว้ พร้อม log ไว้เผื่อยังไม่เจอ
        //
        // แก้บั๊กรอบ 8 (2026-08-06): เดิมประกาศ `const isArrayLike` ซ้ำอีก
        // ครั้งตรงนี้ ทั้งที่ประกาศไปแล้วรอบแก้ gps_track_json ด้านบนใน
        // _drawTrack() เดียวกัน (scope เดียวกัน) ทำให้เกิด SyntaxError
        // "Identifier 'isArrayLike' has already been declared" — JS
        // syntax error แบบนี้ทำให้ทั้ง web.assets_web.min.js (ทั้ง bundle
        // รวมทุกไฟล์ JS ของ Odoo) พังไปด้วยทันที ไม่ใช่แค่ widget นี้ —
        // เป็นสาเหตุที่ทำให้หน้าเว็บทั้งหน้าขึ้นขาวโล่ง ไม่มีเมนู/อะไรขึ้น
        // เลย ตัดบรรทัดซ้ำนี้ออก ใช้ isArrayLike ตัวที่ประกาศไว้แล้วด้านบน
        const eventsFieldName = this.props.attrs?.events || "event_ids";
        let eventList = this._getRecordData(eventsFieldName);
        if (!eventList) eventList = this.props.events;
        if (!eventList) eventList = [];

        let rawEvents = isArrayLike(eventList) ? Array.from(eventList)
                      : isArrayLike(eventList?.records) ? Array.from(eventList.records)
                      : null;

        if (!rawEvents) {
            console.warn(
                "[trip_map widget] event_ids parse ได้ แต่ไม่ใช่ array/array-like " +
                "ที่คาดไว้ — typeof:", typeof eventList,
                "| constructor:", eventList?.constructor?.name,
                "| keys:", eventList && typeof eventList === "object" ? Object.keys(eventList) : eventList,
                "| ค่าจริง:", eventList
            );
            rawEvents = [];
        } else if (rawEvents.length) {
            // log ตัวอย่าง record แรกไว้เผื่อ .data ไม่มีจริง จะได้เห็นว่า
            // โครงสร้างจริงหน้าตาเป็นยังไง (เปิด Console เช็คได้)
            console.warn(
                "[trip_map widget] เจอ", rawEvents.length, "events — ตัวอย่าง record แรก:",
                rawEvents[0], "| มี .data ไหม:", !!rawEvents[0]?.data
            );
        }

        const events = rawEvents.map(r => ({
            lat:            r.data ? r.data.lat : r.lat,
            lon:            r.data ? r.data.lon : r.lon,
            event_type:     r.data ? r.data.event_type : r.event_type,
            severity:       r.data ? r.data.severity : r.severity,
            speed_at_event: r.data ? r.data.speed_at_event : r.speed_at_event,
        }));
        for (const ev of events) {
            if (!ev.lat || !ev.lon) continue;
            const color = EVENT_COLORS[ev.event_type] || "#6b7280";
            const label = EVENT_LABELS_TH[ev.event_type] || { text: ev.event_type, icon: "🚗" };

            const carIcon = L.divIcon({
                className: "",
                html: `
                    <div style="
                        background:${color};
                        width:28px;height:28px;border-radius:50%;
                        border:2px solid white;
                        box-shadow:0 1px 4px rgba(0,0,0,.5);
                        display:flex;align-items:center;justify-content:center;
                        font-size:15px;
                    ">🚗</div>
                `,
                iconSize: [28, 28],
                iconAnchor: [14, 14],
            });

            L.marker([ev.lat, ev.lon], { icon: carIcon })
                .addTo(this.map)
                .bindTooltip(
                    `${label.icon} ${label.text}`,
                    { permanent: true, direction: "top", offset: [0, -16], className: "o_trip_map_event_tooltip" }
                )
                .bindPopup(
                    `<b>${label.icon} ${label.text}</b><br/>Severity: ${ev.severity}<br/>Speed: ${ev.speed_at_event} km/h`
                );
        }

        // Fit map — invalidateSize() ซ้ำอีกครั้งกันไว้ เผื่อ container
        // เปลี่ยนขนาดไปแล้วตั้งแต่ onMounted (เช่น สลับแท็บไปมาก่อนจะมาถึง
        // จุดนี้) ต้นทุนต่ำมาก ทำซ้ำได้ไม่มีผลเสีย
        this.map.invalidateSize();
        this.map.fitBounds(polyline.getBounds(), { padding: [20, 20], maxZoom: 17 });
        this.state.pointCount = points.length;
    }
}

// แก้บั๊กหลายรอบ 2026-08-05:
// รอบ 1: เดิม register widget ด้วย Component class เปล่าๆ ตรงๆ
//   (registry.category("view_widgets").add("trip_map", TripMapWidget))
//   ไม่มี extractProps เลย
// รอบ 2: เพิ่ม extractProps แบบ { attrs, record } ตามตัวอย่างทั่วไปที่หาเจอ
//   แต่ error/console.warn ยืนยันว่า extractProps ของ registry
//   "view_widgets" ในเวอร์ชันนี้ได้รับ props แค่ { name, widget, options,
//   attrs } เท่านั้น — ไม่มี "record" เลยสักครั้ง
// รอบ 3 (ปัจจุบัน): ย้าย logic การอ่าน record ทั้งหมดไปไว้ใน Component เอง
//   (_getRecordData()) แทน ลองอ่านจาก this.props.record ที่ระดับ Component
//   โดยตรง (เผื่อ Odoo merge เข้ามาแยกจาก extractProps) — extractProps แค่
//   ส่ง attrs ผ่านไปเฉยๆ ให้ Component รู้ชื่อ field ที่ต้องอ่าน
//   ถ้ารอบนี้ยังไม่เจอ record อีก จะมี console.warn บอกด้วยว่า props ที่
//   Component เห็นจริงๆ มีอะไรบ้าง เอาไว้แก้รอบถัดไปได้ตรงจุดที่สุด
function extractTripMapProps({ attrs }) {
    return { attrs };
}

registry.category("view_widgets").add("trip_map", {
    component: TripMapWidget,
    extractProps: extractTripMapProps,
});
