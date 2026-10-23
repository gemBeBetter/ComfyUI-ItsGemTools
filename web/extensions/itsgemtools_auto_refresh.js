import { app } from "../../../scripts/app.js";

// ============================================================
// ItsGemTools 前端增强：仅对本工具条(AdjustColor/AdjustEffect)生效
//  1) 参数变化 -> 自动刷新预览（仅这两个节点，不影响其他节点/全局设置）
//  2) 色温/色调/饱和度/自然饱和度 滑块带彩色渐变条（位置对应数值）
//
// 实现要点（2026-10-23 修复）：
//  - 新版前端(1.55.18)中，registerExtension 的 nodeCreated/init 钩子
//    对已存在节点不一定触发，导致包装从未安装。
//  - 因此用全局轮询补装：每 300ms 扫描图内节点，未安装的补装，
//    同时做值变化检测（双重兜底：callback/onChange + 轮询）。
// ============================================================

const REFRESH_TYPES = new Set(["AdjustColor", "AdjustEffect", "Adjust", "SkinBeauty", "LayerComposite"]);

// 渐变定义：位置 0~1 -> 颜色（参考剪映调节面板的轨道配色）
const GRADIENTS = {
    // 色温：蓝(冷) -> 灰(中) -> 暖黄(暖)
    color_temperature: { stops: [[0, "#2563eb"], [0.5, "#e2e8f0"], [1, "#f59e0b"]] },
    // 色调：绿 -> 灰 -> 品红
    tint: { stops: [[0, "#16a34a"], [0.5, "#e2e8f0"], [1, "#db2777"]] },
    // 饱和度：灰 -> 紫
    saturation: { stops: [[0, "#94a3b8"], [0.5, "#d8b4fe"], [1, "#7c3aed"]] },
    // 自然饱和度：灰 -> 天蓝 -> 靛蓝（更柔和）
    vibrance: { stops: [[0, "#94a3b8"], [0.5, "#7dd3fc"], [1, "#3b82f6"]] },
};

function isAutoRefreshOn(node) {
    const w = node && node.widgets ? node.widgets.find((x) => x.name === "auto_refresh") : null;
    return !w || w.value !== false;
}

// ---------------- 自动刷新（官方签名 queuePrompt(0, batchCount, {intent})） ----------------
// 防抖 1000ms：用户调整值后，1 秒内值不再变化才触发一次刷新（避免拖动时高频连刷）。
let refreshTimer = null;
function scheduleRefresh() {
    if (refreshTimer) clearTimeout(refreshTimer);
    refreshTimer = setTimeout(async () => {
        try {
            if (app && typeof app.queuePrompt === "function") {
                await app.queuePrompt(0, 1, { intent: { trigger_source: "itsgemtools_auto_refresh" } });
                console.debug("[ItsGemTools] auto refresh queued");
            }
        } catch (e) {
            console.warn("[ItsGemTools] auto refresh failed:", e);
        }
    }, 1000);
}

// ---------------- 色条绘制（剪映式细轨道：标签 ｜ 渐变轨道 ｜ 数值框） ----------------
// 轨道画在行的标签与右侧数值框之间：高度 7px、垂直居中，白色指示线随值移动。
// labelW 由调用方传入（所有行统一的最大标签宽 + 10px 边距），保证：
//  - 色条永不与文字重叠；
//  - 各行轨道左缘对齐（付费软件式整齐）。
function drawBar(ctx, spec, rowWidth, y, rowHeight, widget, labelW) {
    try {
        const rowH = (rowHeight || 24);
        const x0 = (labelW || 120);
        const valueZone = 46; // 右侧数值框宽度 + 边距
        // 轨道宽度压缩：不超过行宽的一半，硬上限 120px（短轨道，不逼近文字与数值框）
        const maxBarW = Math.min((rowWidth || 200) * 0.5, 120);
        const w = Math.max(Math.min((rowWidth || 200) - x0 - valueZone, maxBarW), 6);
        const barH = 4; // 轨道高度更细
        const barY = y + Math.max(0, Math.round((rowH - barH) / 2));
        if (w <= 6) return;

        const grad = ctx.createLinearGradient(x0, 0, x0 + w, 0);
        for (const [pos, color] of spec.stops) grad.addColorStop(pos, color);
        ctx.save();
        ctx.globalAlpha = 0.9;
        ctx.fillStyle = grad;
        ctx.beginPath();
        if (typeof ctx.roundRect === "function") ctx.roundRect(x0, barY, w, barH, 2);
        else ctx.rect(x0, barY, w, barH);
        ctx.fill();
        ctx.restore();

        // 当前值指示线（拖动条）：位置对应数值；高度固定 9px，不随轨道变细而缩水
        let min = -100, max = 100;
        try {
            if (widget && widget.options) {
                if (typeof widget.options.min === "number") min = widget.options.min;
                if (typeof widget.options.max === "number") max = widget.options.max;
            }
        } catch (e) { /* 忽略 */ }
        const v = widget && typeof widget.value === "number" ? widget.value : 0;
        const t = (v - min) / (max - min || 1);
        const cx = x0 + Math.max(0, Math.min(1, t)) * w;
        ctx.save();
        ctx.strokeStyle = "#ffffff";
        ctx.lineWidth = 2;
        ctx.shadowColor = "rgba(0,0,0,0.7)";
        ctx.shadowBlur = 2;
        ctx.beginPath();
        ctx.moveTo(cx, barY - 2.5);
        ctx.lineTo(cx, barY + 6.5);
        ctx.stroke();
        ctx.restore();
    } catch (e) { /* 绘制失败不影响主功能 */ }
}

// ---------------- 色条：原型级 patch（仅对 AdjustColor 节点生效） ----------------
// 实例级包装在多扩展/重启/动态 import 场景下容易发生 orig 链断裂（实测全白屏），
// 改为直接包一层 LGraphNode.prototype.drawWidgets：
//  - 只在本节点 type 是 AdjustColor 时先画渐变轨道，然后走原绘制链；
//  - 原型 patch 无论页面加载/重载/import 多少次都只装一次（__itsgemtools_bar 标记）；
//  - 每个节点绘制时从 this.widgets 动态匹配 GRADIENTS，与 widgets 重建时序无关。
function installDrawWidgetsPatch() {
    const proto = globalThis.LiteGraph && globalThis.LiteGraph.LGraphNode
        ? globalThis.LiteGraph.LGraphNode.prototype
        : null;
    if (!proto || proto.__itsgemtools_bar) return;
    proto.__itsgemtools_bar = true;
    const origDraw = proto.drawWidgets;
    proto.drawWidgets = function (ctx, opts) {
        // 先走原绘制链（文本/滑块/背景），再画色条 —— 色条覆盖在背景之上，
        // 否则原实现的背景填充会盖掉先画的渐变（实测全像素为 0）。
        const ret = origDraw ? origDraw.call(this, ctx, opts) : undefined;
        if (this && (this.type === "AdjustColor" || this.type === "Adjust")) {
            try {
                const nw = this.renderingSize ? this.renderingSize[0] : (this.size ? this.size[0] : 200);
                const wws = (this.widgets || []);
                // 统一标签区宽度：所有渐变行中最大文字宽 + 10px 边距（轨道列左缘对齐）
                let maxLabelW = 0;
                const rows = [];
                for (const w of wws) {
                    if (!GRADIENTS[w && w.name]) continue;
                    rows.push(w);
                    try {
                        const m = ctx.measureText(w.label || w.name || "").width;
                        if (Number.isFinite(m)) maxLabelW = Math.max(maxLabelW, Math.ceil(m));
                    } catch (e) { /* 忽略 */ }
                }
                // 统一标签区宽度：最大文字宽 + 10px 边距；下限固定 150px（不依赖
                // 字体测量一致性，任何字体下轨道起点都让开标签文字，绝不再压字）
                const labelW = Math.max(maxLabelW + 10, 150);
                for (const w of rows) {
                    const y = typeof w.y === "number" ? w.y : (w.last_y || 0);
                    const ww = w.width || nw;
                    const hh = w.computedHeight || w.height || 24;
                    drawBar(ctx, GRADIENTS[w.name], ww, y, hh, w, labelW);
                }
            } catch (e) { /* 绘制失败不影响主功能 */ }
        }
        return ret;
    };
}

// ---------------- 安装到单个节点 ----------------
function widgetsSignature(node) {
    const parts = [];
    for (const w of node.widgets || []) parts.push(String(w && w.value));
    return parts.join("|");
}

function setupNode(node) {
    if (!node || node.__itsgemtools_setup) return;
    if (!REFRESH_TYPES.has(node.type)) return;
    // widgets 未就绪时先不装（不设 flag），下轮轮询再试
    if (!node.widgets || node.widgets.length === 0) return;
    node.__itsgemtools_setup = true;
    console.log("[ItsGemTools] setup:", node.type, "widgets:", (node.widgets || []).map((w) => w.name).join(","));
    // 1) 色条走原型 patch（模块顶层 installDrawWidgetsPatch() 已执行，见文件底部）；
    //    这里顺手清掉可能残留的实例级包装（旧版本/其他路径装的），
    //    保证渲染时走我们的原型方法。
    if (node.type === "AdjustColor" || node.type === "Adjust") {
        if (Object.prototype.hasOwnProperty.call(node, "drawWidgets")) {
            delete node.drawWidgets;
        }
        if (Object.prototype.hasOwnProperty.call(node, "__itsgemtools_drawWidgetsOrig")) {
            delete node.__itsgemtools_drawWidgetsOrig;
        }
    }

    // 2) 回调触发（新/旧前端兼容层都会调用 widget.callback）
    for (const w of node.widgets || []) {
        const orig = w.callback;
        try {
            w.callback = function (value, canvas, n, pos, event) {
                if (typeof orig === "function") orig.call(this, value, canvas, n, pos, event);
                if (isAutoRefreshOn(n || node)) scheduleRefresh();
            };
        } catch (e) { /* 只读 widget 时忽略 */ }
        const origOnChange = w.onChange;
        if (typeof origOnChange === "function") {
            try {
                w.onChange = function (value, ...rest) {
                    if (typeof origOnChange === "function") origOnChange.call(this, value, ...rest);
                    if (isAutoRefreshOn(node)) scheduleRefresh();
                };
            } catch (e) { /* 忽略 */ }
        }
    }
}

// ---------------- 全局轮询补装 + 值变化检测 ----------------
// 修复点：新版前端不保证 nodeCreated 对已存在/新添加节点触发，
// 因此持续扫描图内节点：未安装 -> 补装；已安装 -> 值变化则刷新。
const watched = new Map(); // node -> 最近一次 widget 值签名

function scanGraph() {
    const g = app && app.graph;
    if (!g) return;
    const nodes = (typeof g.getNodes === "function" ? g.getNodes() : g._nodes) || [];

    // 清理已移除节点
    const alive = new Set(nodes);
    for (const key of watched.keys()) {
        if (!alive.has(key)) watched.delete(key);
    }

    for (const node of nodes) {
        try {
            if (!REFRESH_TYPES.has(node.type)) continue;
            if (!node.__itsgemtools_setup) setupNode(node);
            const sig = widgetsSignature(node);
            if (watched.has(node)) {
                if (watched.get(node) !== sig) {
                    watched.set(node, sig);
                    if (isAutoRefreshOn(node)) scheduleRefresh();
                }
            } else {
                // 首次见到：只记录基线，不触发刷新（避免打开工作流时误跑一次）
                watched.set(node, sig);
            }
        } catch (e) { /* 节点状态异常时跳过 */ }
    }
}

setInterval(scanGraph, 300);

// 原型级 patch：只在模块加载时执行一次（页面每次加载都执行，但内部有防重装标记）
installDrawWidgetsPatch();

// ---------------- 注册扩展 ----------------
app.registerExtension({
    name: "ItsGemTools.AutoRefresh",
    init() {
        console.log("[ItsGemTools] extension loaded, app available:", !!app);
        try {
            setTimeout(() => scanGraph(), 800);
        } catch (e) { console.warn("[ItsGemTools] init scan failed:", e); }
    },
    nodeCreated(node) { setupNode(node); },
});
