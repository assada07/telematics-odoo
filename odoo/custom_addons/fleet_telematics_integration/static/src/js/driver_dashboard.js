/** @odoo-module **/
// static/src/js/driver_dashboard.js
// Driver Dashboard OWL Component — FDD §12.6
//
// แสดง:
//   1) Scorecard รายพนักงาน (avatar + tier badge + คะแนนเฉลี่ย)
//   2) Trend กราฟคะแนนรายเดือน (วาดเองด้วย Canvas 2D — แก้ 2026-08-06:
//      เดิมใช้ Chart.js โหลดผ่าน CDN ภายนอก พังเงียบๆ ถ้าเครื่อง Odoo
//      ไม่มีอินเทอร์เน็ตออกนอก เปลี่ยนมาไม่พึ่ง library ภายนอกเลย)
//   3) Energy KPI (น้ำมัน, idle time, ระยะทาง, harsh events)
//   4) กดชื่อคนขับ → ดู Trip Log ของคนนั้น

import { Component, useState, onMounted, onWillUnmount, useRef } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

const TIER_COLOR_PALETTE = [
    { color: "#15803d", bg: "#dcfce7" },  // เขียว — อันดับ 1
    { color: "#1d4ed8", bg: "#dbeafe" },  // ฟ้า — อันดับ 2
    { color: "#d97706", bg: "#fef3c7" },  // ส้ม — อันดับ 3
    { color: "#b91c1c", bg: "#fee2e2" },  // แดง — อันดับ 4 เป็นต้นไป (รวม
                                           // "Below Minimum")
];

// แก้ 2026-08-04: Tier เปลี่ยนเป็นไดนามิกแล้ว ไม่ใช่แค่ A/B/C/D ตายตัว
// อีกต่อไป — สีเลยต้องกำหนดจาก "ลำดับ" ของ tier ไม่ใช่ชื่อ tier โดยตรง
function getTierStyle(tierName, tiers) {
    const idx = (tiers || []).findIndex(t => t.name === tierName);
    return idx >= 0 ? (TIER_COLOR_PALETTE[idx] || TIER_COLOR_PALETTE[TIER_COLOR_PALETTE.length - 1])
                     : TIER_COLOR_PALETTE[TIER_COLOR_PALETTE.length - 1];
}

function getTier(score, tiers) {
    // tiers: [{name, min_score, bonus_pct}, ...] เรียงจาก min_score มากไป
    // น้อยแล้ว (จาก search_read order="min_score desc")
    if (!tiers || !tiers.length) {
        // ไม่มี Scoring Config Active หรือไม่มี tier ตั้งไว้เลย → fallback
        // เป็น threshold มาตรฐาน (ตรงกับฝั่ง Python ใน telematics_log.py/
        // telematics_incentive.py)
        if (score >= 90) return "A";
        if (score >= 75) return "B";
        if (score >= 60) return "C";
        return "D";
    }
    for (const t of tiers) {
        if (score >= t.min_score) return t.name;
    }
    return "Below Minimum";
}

// แก้ตามที่ผู้ใช้ขอ 2026-08-06 (รอบ 2): เปลี่ยนจาก preset "เดือนนี้/เดือน
// ที่แล้ว/ทั้งหมด" เป็นเลือก "ปี + เดือน" ได้อิสระแทน — ย้อนหลังได้กี่ปี
// อิงตาม FDD หัวข้อ "Data Retention": trip summary เก็บย้อนหลัง 3 ปี
// (raw telemetry เก็บแค่ 90 วัน แต่ trip summary ที่ dashboard นี้ใช้อยู่
// เก็บ 3 ปี) — ให้เลือกปีได้ 3 ปีย้อนหลัง (ปีนี้ + 2 ปีก่อนหน้า) ตรงกับ
// ข้อมูลที่ยังมีอยู่จริงในระบบ เกินกว่านั้นข้อมูลไม่มีอยู่แล้วตามสเปค
const MONTH_NAMES_TH = [
    "มกราคม", "กุมภาพันธ์", "มีนาคม", "เมษายน", "พฤษภาคม", "มิถุนายน",
    "กรกฎาคม", "สิงหาคม", "กันยายน", "ตุลาคม", "พฤศจิกายน", "ธันวาคม",
];

function getYearOptions() {
    const currentYear = new Date().getFullYear();
    // FDD: trip summary เก็บย้อนหลัง 3 ปี → ให้เลือกได้ปีนี้ + 2 ปีก่อนหน้า
    return [currentYear, currentYear - 1, currentYear - 2];
}

function getMonthOptions() {
    return MONTH_NAMES_TH.map((label, i) => ({ value: i + 1, label }));
}

function getPeriodRange(mode, year, month) {
    if (mode === "all_time") return null;
    const pad = (n) => String(n).padStart(2, "0");
    const fmt = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
    const from = new Date(year, month - 1, 1);
    const to   = new Date(year, month, 1);
    return { dateFrom: fmt(from), dateTo: fmt(to) };
}

async function odooRpc(route, params = {}) {
    const res = await fetch(route, {
        method: "POST",
        headers: {
            "Content-Type": "application/json",
            "X-Requested-With": "XMLHttpRequest",
        },
        body: JSON.stringify({ jsonrpc: "2.0", method: "call", params }),
    });
    const data = await res.json();
    if (data.error) throw new Error(data.error.data?.message || data.error.message);
    return data.result;
}

export class DriverDashboard extends Component {
    static template = "fleet_telematics_integration.DriverDashboard";

    setup() {
        this.chartRef      = useRef("trendChart");
        this.actionService = useService("action");
        this.state = useState({
            loading:        true,
            error:          null,
            drivers:        [],         // [{driver_id, name, avg_score, tier, trips, distance, fuel, idle, harsh}]
            selectedDriver: null,       // driver_id ที่เลือกดู trend
            trendData:      [],         // [{month, avg_score}]
            energyKPI:      null,       // {total_distance, total_fuel, total_idle, total_harsh}
            scoringConfig:  null,
            tiers:          [],  // [{name, min_score, bonus_pct}] จาก Scoring Config ที่ Active
            periodMode:     "ym",          // "ym" (ปี+เดือนที่เลือก) หรือ
                                            // "all_time" (ไม่กรองวันที่)
            selectedYear:   new Date().getFullYear(),
            selectedMonth:  new Date().getMonth() + 1,  // ค่าเริ่มต้น: เดือน
                                                          // ปัจจุบัน ตรงกับ
                                                          // รอบคำนวณ Incentive
        });
        // หมายเหตุ: ไม่มี this.chart อีกต่อไปแล้ว (เคยเก็บ instance ของ
        // Chart.js library ไว้ ตอนนี้วาดกราฟเองด้วย Canvas 2D ล้วนๆ ไม่ต้อง
        // เก็บ object อะไรไว้ระหว่าง render — ดู _renderChart())
        this._currentRange = null;  // ตั้งค่าจริงใน _loadDashboard() — เก็บ
                                     // ไว้ให้ onViewTrips() เอาไปกรองต่อ

        onMounted(async () => {
            await this._loadDashboard();

            // redraw กราฟเมื่อ resize หน้าต่าง (debounce กันเรียกถี่เกินไป)
            // — เพราะตอนนี้วาดเองด้วย Canvas ต้องคำนวณขนาดใหม่เองตอน resize
            // ไม่เหมือน Chart.js เดิมที่มี responsive:true จัดการให้อัตโนมัติ
            this._resizeTimer = null;
            this._onResize = () => {
                clearTimeout(this._resizeTimer);
                this._resizeTimer = setTimeout(() => this._renderChart(), 150);
            };
            window.addEventListener("resize", this._onResize);
        });

        onWillUnmount(() => {
            if (this._onResize) window.removeEventListener("resize", this._onResize);
            clearTimeout(this._resizeTimer);
        });
    }

    async _loadDashboard() {
        this.state.loading = true;
        this.state.error   = null;
        try {
            // 1) ดึง Scoring Config สำหรับ tier thresholds
            // แก้บั๊ก 2026-08-04: Tier เปลี่ยนเป็นไดนามิกแล้ว (tier_ids
            // แทน tier_a_min_score/tier_b_min_score/tier_c_min_score ที่ไม่
            // มี field พวกนี้บน model อีกต่อไป) — ต้องดึง tier_ids (list
            // ของ id) ก่อน แล้วค่อยดึงรายละเอียดแต่ละ tier อีกครั้งจาก
            // fleet.telematics.scoring.tier เรียงจาก min_score มากไปน้อย
            const cfgResult = await odooRpc("/web/dataset/call_kw", {
                model:  "fleet.telematics.scoring.config",
                method: "search_read",
                args:   [[["is_active", "=", true]]],
                kwargs: {
                    fields:  ["tier_ids"],
                    limit:   1,
                },
            });
            const activeCfg = cfgResult?.[0] || null;
            let tiers = [];
            if (activeCfg && activeCfg.tier_ids && activeCfg.tier_ids.length) {
                tiers = await odooRpc("/web/dataset/call_kw", {
                    model:  "fleet.telematics.scoring.tier",
                    method: "search_read",
                    args:   [[["id", "in", activeCfg.tier_ids]]],
                    kwargs: {
                        fields:  ["name", "min_score", "bonus_pct"],
                        order:   "min_score desc",
                    },
                });
            }
            this.state.scoringConfig = activeCfg;
            this.state.tiers = tiers;

            // 2) ดึง aggregate ต่อคนขับ — กรองตามปี/เดือนที่เลือก (ถ้าไม่ใช่
            // "all_time") — เก็บ range ไว้ที่ this._currentRange ด้วย เพื่อ
            // ให้ onViewTrips() เอาไปกรองหน้า Trip Logs ต่อได้ (แก้บั๊กที่
            // เคยกด "ดู Trip Logs →" แล้วไม่กรองตามเดือนที่เลือกไว้เลย)
            const range = getPeriodRange(this.state.periodMode, this.state.selectedYear, this.state.selectedMonth);
            this._currentRange = range;
            const domain = [["state", "=", "synced"]];
            if (range) {
                domain.push(["trip_start", ">=", range.dateFrom]);
                domain.push(["trip_start", "<", range.dateTo]);
            }
            const logs = await odooRpc("/web/dataset/call_kw", {
                model:  "fleet.telematics.log",
                method: "read_group",
                args:   [domain, ["driver_id", "driver_score:avg", "distance_km:sum", "fuel_used_est:sum", "idle_min:sum", "harsh_brake_count:sum", "harsh_accel_count:sum", "harsh_corner_count:sum"], ["driver_id"]],
                kwargs: { orderby: "driver_score desc" },
            });

            this.state.drivers = (logs || [])
                .filter(r => r.driver_id)
                .map(r => ({
                    driver_id:   r.driver_id[0],
                    name:        r.driver_id[1],
                    avg_score:   Math.round((r.driver_score || 0) * 100) / 100,
                    trips:       r.driver_id_count || 0,
                    distance:    Math.round(r.distance_km || 0),
                    fuel:        Math.round((r.fuel_used_est || 0) * 10) / 10,
                    idle:        Math.round(r.idle_min || 0),
                    harsh:       (r.harsh_brake_count || 0) + (r.harsh_accel_count || 0) + (r.harsh_corner_count || 0),
                    tier:        getTier(r.driver_score || 0, this.state.tiers),
                }));

            // 3) Energy KPI รวมทั้งฟลีท
            const total = this.state.drivers.reduce((acc, d) => ({
                distance: acc.distance + d.distance,
                fuel:     acc.fuel     + d.fuel,
                idle:     acc.idle     + d.idle,
                harsh:    acc.harsh    + d.harsh,
            }), { distance: 0, fuel: 0, idle: 0, harsh: 0 });
            this.state.energyKPI = total;

            // 4) โหลด trend ของคนแรก
            if (this.state.drivers.length > 0) {
                await this._loadTrend(this.state.drivers[0].driver_id);
            }

        } catch (e) {
            this.state.error = "โหลดข้อมูลไม่สำเร็จ: " + (e.message || e);
        } finally {
            this.state.loading = false;
        }
    }

    async _loadTrend(driverId) {
        this.state.selectedDriver = driverId;
        try {
            const trend = await odooRpc("/web/dataset/call_kw", {
                model:  "fleet.telematics.log",
                method: "read_group",
                args:   [
                    [["driver_id", "=", driverId], ["state", "=", "synced"]],
                    ["driver_score:avg", "trip_start"],
                    ["trip_start:month"],
                ],
                kwargs: { orderby: "trip_start asc", limit: 12 },
            });
            this.state.trendData = (trend || []).map(r => ({
                month: r.trip_start,
                score: Math.round((r.driver_score || 0) * 10) / 10,
            }));
            this._renderChart();
        } catch (e) {
            this.state.trendData = [];
        }
    }

    _renderChart() {
        if (!this.chartRef.el) return;
        const canvas = this.chartRef.el;
        const ctx = canvas.getContext("2d");
        if (!ctx) return;

        // แก้บั๊ก 2026-08-06: เดิมโหลด Chart.js จาก CDN ภายนอก
        // (cdn.jsdelivr.net) ตอน runtime — ถ้าเครื่องที่รัน Odoo ไม่มี
        // อินเทอร์เน็ตออกนอก (พบได้บ่อยมากกับเครื่อง dev/on-premise ในองค์กร)
        // สคริปต์จะโหลดไม่สำเร็จแบบเงียบๆ ไม่มี error ให้เห็นเลย เพราะไม่มี
        // s.onerror ดักไว้ — กราฟเลยว่างเปล่าตลอดไปโดยไม่รู้สาเหตุ (เจอจริง
        // จากภาพหน้าจอที่ผู้ใช้ส่งมา) เปลี่ยนมาวาดกราฟเองด้วย Canvas 2D API
        // ล้วนๆ แทน ไม่พึ่ง library ภายนอกเลยสักตัว รับประกันว่าทำงานได้
        // แน่นอนไม่ว่าเครื่องจะต่อเน็ตได้หรือไม่ก็ตาม
        const dpr = window.devicePixelRatio || 1;
        const rect = canvas.parentElement.getBoundingClientRect();
        const width  = Math.max(rect.width, 280);
        const height = 240;
        canvas.width  = width * dpr;
        canvas.height = height * dpr;
        canvas.style.width  = width + "px";
        canvas.style.height = height + "px";
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        ctx.clearRect(0, 0, width, height);

        const data = this.state.trendData;
        const pad  = { top: 16, right: 16, bottom: 30, left: 36 };
        const plotW = width - pad.left - pad.right;
        const plotH = height - pad.top - pad.bottom;

        // ── แกน Y (0-100) + เส้น gridline แนวนอน ──────────────────────
        ctx.strokeStyle = "#e5e7eb";
        ctx.fillStyle   = "#9ca3af";
        ctx.font        = "11px sans-serif";
        ctx.textAlign   = "right";
        ctx.textBaseline = "middle";
        const ySteps = [0, 25, 50, 75, 100];
        for (const yVal of ySteps) {
            const y = pad.top + plotH * (1 - yVal / 100);
            ctx.beginPath();
            ctx.moveTo(pad.left, y);
            ctx.lineTo(pad.left + plotW, y);
            ctx.stroke();
            ctx.fillText(String(yVal), pad.left - 8, y);
        }

        if (!data.length) {
            ctx.textAlign = "center";
            ctx.fillText("ไม่มีข้อมูล trend", width / 2, height / 2);
            return;
        }

        // ── จุดข้อมูลแต่ละเดือน ──────────────────────────────────────
        const n = data.length;
        const points = data.map((d, i) => ({
            x: pad.left + (n === 1 ? plotW / 2 : (plotW * i) / (n - 1)),
            y: pad.top + plotH * (1 - Math.min(d.score, 100) / 100),
            label: d.month,
            score: d.score,
        }));

        // ── พื้นที่ใต้เส้น (fill) ────────────────────────────────────
        ctx.beginPath();
        ctx.moveTo(points[0].x, pad.top + plotH);
        for (const p of points) ctx.lineTo(p.x, p.y);
        ctx.lineTo(points[points.length - 1].x, pad.top + plotH);
        ctx.closePath();
        ctx.fillStyle = "rgba(59,130,246,0.08)";
        ctx.fill();

        // ── เส้นกราฟ ────────────────────────────────────────────────
        ctx.beginPath();
        points.forEach((p, i) => (i === 0 ? ctx.moveTo(p.x, p.y) : ctx.lineTo(p.x, p.y)));
        ctx.strokeStyle = "#3b82f6";
        ctx.lineWidth   = 2;
        ctx.lineJoin    = "round";
        ctx.stroke();

        // ── จุด + label ค่าคะแนน + label เดือน ──────────────────────
        ctx.textBaseline = "alphabetic";
        for (const p of points) {
            ctx.beginPath();
            ctx.arc(p.x, p.y, 4, 0, Math.PI * 2);
            ctx.fillStyle = "#3b82f6";
            ctx.fill();
            ctx.strokeStyle = "#ffffff";
            ctx.lineWidth = 1.5;
            ctx.stroke();

            // ค่าคะแนนเหนือจุด
            ctx.fillStyle = "#1d4ed8";
            ctx.font = "bold 11px sans-serif";
            ctx.textAlign = "center";
            ctx.fillText(String(p.score), p.x, p.y - 10);

            // label เดือนใต้แกน X (ตัดให้สั้นถ้ามีหลายจุดเกินไปจนซ้อนกัน)
            const showLabel = n <= 8 || points.indexOf(p) % Math.ceil(n / 8) === 0;
            if (showLabel) {
                ctx.fillStyle = "#6b7280";
                ctx.font = "10px sans-serif";
                const shortLabel = (p.label || "").slice(0, 7);
                ctx.fillText(shortLabel, p.x, height - 8);
            }
        }
    }

    onDriverClick(driverId) {
        this._loadTrend(driverId);
    }

    onYearChange(ev) {
        this.state.selectedYear = parseInt(ev.target.value, 10);
        this._loadDashboard();
    }

    onMonthChange(ev) {
        this.state.selectedMonth = parseInt(ev.target.value, 10);
        this._loadDashboard();
    }

    onPeriodModeChange(ev) {
        this.state.periodMode = ev.target.checked ? "all_time" : "ym";
        this._loadDashboard();
    }

    get yearOptions() {
        return getYearOptions();
    }

    get monthOptions() {
        return getMonthOptions();
    }

    get periodLabel() {
        if (this.state.periodMode === "all_time") return "ทั้งหมด (ตลอดชีพ)";
        const m = MONTH_NAMES_TH[this.state.selectedMonth - 1];
        return `${m} ${this.state.selectedYear}`;
    }

    // แก้บั๊ก 2026-08-06 (รอบ 2): เดิมปุ่ม "ดู Trip Logs →" เปิด Trip Logs
    // แบบกรองแค่ driver_id อย่างเดียว ไม่กรองตามช่วงเวลาที่เลือกไว้บน
    // Dashboard เลย (เลือก "สิงหาคม 2026" อยู่ แต่กดแล้วเห็นทริปทุกเดือน
    // ของคนนั้นปนกันหมด) — ใช้ this._currentRange (คำนวณไว้ตอน
    // _loadDashboard() ล่าสุด) มาเติมเป็นเงื่อนไข trip_start เพิ่มด้วย ให้
    // ตรงกับสิ่งที่กำลังดูอยู่บน Dashboard จริงๆ
    onViewTrips(driverId, ev) {
        ev.stopPropagation();
        const domain = [["driver_id", "=", driverId]];
        if (this._currentRange) {
            domain.push(["trip_start", ">=", this._currentRange.dateFrom]);
            domain.push(["trip_start", "<", this._currentRange.dateTo]);
        }
        this.actionService.doAction({
            type:      "ir.actions.act_window",
            name:      `Trip Logs — ${this.periodLabel}`,
            res_model: "fleet.telematics.log",
            views:     [[false, "list"], [false, "form"]],
            domain,
            context:   { search_default_driver_id: driverId },
        });
    }

    getTierConfig(tier) {
        return getTierStyle(tier, this.state.tiers);
    }
}

registry.category("actions").add("fleet_telematics_driver_dashboard", DriverDashboard);
