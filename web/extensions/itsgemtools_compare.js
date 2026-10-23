import { api } from "../../../scripts/api.js";
import { app } from "../../../scripts/app.js";

// ============================================================
// ItsGemTools Image Compare A/B 面板
// 复刻 AusBoss 的 Image Compare A/B 🆎 节点交互（SLIDE 滑动对比 / A/B 切换），
// 所有依赖内联，不依赖任何第三方节点包。
// ============================================================

const NODE_NAME = "ItsGemImageCompare";
const PANEL_WIDGET = "itsgem_compare_panel";
const CAPTION_HEIGHT = 24;
const PANEL_MIN_HEIGHT = 144 + CAPTION_HEIGHT;
const WIDGET_FRAME = 20;
const BADGE_WIDTH = 22;
const BRAND = "#c4a6ff"; // 淡紫（本包统一主题色，区分 AusBoss 的青绿）
const CSS_ID = "itsgem-compare-ui-v1";

// 双语：优先取 i18n 扩展，未加载时回退英文
function T(zh, en) {
    return window.__ITSGEM_I18N ? window.__ITSGEM_I18N.t(zh, en) : en;
}

function responsivePreviewHeight(widthValue, minimum = 112, maximum = 420) {
    const width = Math.max(0, Number(widthValue) || 0);
    const scaled = Math.round(Math.max(0, width - 12) * 9 / 16);
    return Math.max(minimum, Math.min(maximum, scaled));
}
const DEFAULT_NODE_SIZE = [
    420,
    responsivePreviewHeight(420, 132, 520) + 12 + CAPTION_HEIGHT + 60,
];

// ---------- 纯逻辑（来自 compare.mjs，内联） ----------
const COMPARE_MODES = ["slide", "toggle"];
const LEGACY_MODES = { hold: "toggle" };

function normalizeCompareMode(value) {
    const mapped = LEGACY_MODES[value] ?? value;
    return COMPARE_MODES.includes(mapped) ? mapped : COMPARE_MODES[0];
}

function compareSizeLabel(refs) {
    const a = refs?.a;
    const b = refs?.b;
    if (!(a?.width > 0) || !(a?.height > 0)) return "";
    if (b?.width > 0 && b?.height > 0 && (b.width !== a.width || b.height !== a.height)) {
        return `A ${a.width}×${a.height} · B ${b.width}×${b.height}`;
    }
    return `${a.width}×${a.height}`;
}

function clipFraction(pointerX, rectLeft, rectWidth) {
    const width = Number(rectWidth);
    if (!Number.isFinite(width) || width <= 0) return 0;
    const fraction = (Number(pointerX) - Number(rectLeft)) / width;
    if (!Number.isFinite(fraction)) return 0;
    return Math.max(0, Math.min(1, fraction));
}

function slideFraction(pointerX, rectLeft, rectWidth, leaving = false) {
    const fraction = clipFraction(pointerX, rectLeft, rectWidth);
    if (leaving) return fraction < 0.5 ? 0 : 1;
    if (fraction <= 0.06) return 0;
    if (fraction >= 0.94) return 1;
    return fraction;
}

function compareClip(fraction) {
    const value = Number(fraction);
    const clamped = Number.isFinite(value) ? Math.max(0, Math.min(1, value)) : 0;
    return {
        clipPath: `inset(0 ${((1 - clamped) * 100).toFixed(2)}% 0 0)`,
        seamLeft: `${(clamped * 100).toFixed(2)}%`,
        seamVisible: clamped > 0 && clamped < 1,
    };
}

function findCompareImages(message) {
    const a = message?.a_images?.[0] ?? null;
    const b = message?.b_images?.[0] ?? null;
    return a?.filename && b?.filename ? { a, b } : null;
}

function compareBadges(fraction, stageWidth, badgeWidth = BADGE_WIDTH, gap = 5) {
    const width = Number(stageWidth);
    const value = Number(fraction);
    if (!Number.isFinite(width) || width <= 0) return { a: null, b: null };
    const clamped = Number.isFinite(value) ? Math.max(0, Math.min(1, value)) : 0;
    const centred = Math.round((width - badgeWidth) / 2);
    if (clamped <= 0) return { a: null, b: centred };
    if (clamped >= 1) return { a: centred, b: null };
    const seam = clamped * width;
    const a = Math.round(seam - gap - badgeWidth);
    const b = Math.round(seam + gap);
    return { a: a >= 4 ? a : null, b: b + badgeWidth <= width - 4 ? b : null };
}

// ---------- 小工具 ----------
function outputKey(node, root = null) {
    if (!node || node.id === null || node.id === undefined) return null;
    const graph = node.graph;
    return graph && root && graph !== root && graph.id ? `${graph.id}:${node.id}` : String(node.id);
}

function chainCallback(proto, hook, fn) {
    const orig = proto[hook];
    proto[hook] = function (...args) {
        const r = orig ? orig.apply(this, args) : undefined;
        fn.apply(this, args);
        return r;
    };
}

function mediaViewQuery(metaValue, fallbackType = "output") {
    const meta = typeof metaValue === "string" ? { filename: metaValue, type: fallbackType } : (metaValue || {});
    return new URLSearchParams({
        filename: String(meta.filename || ""),
        subfolder: String(meta.subfolder || ""),
        type: String(meta.type || fallbackType),
        t: String(Date.now()),
    }).toString();
}

function makeToolButton(label, title) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "itsgem-compare-tool";
    button.textContent = label;
    button.title = title;
    return button;
}

// ---------- 面板高度/宽度联动（复刻 AusBoss panel_layout.mjs 的关键机制） ----------
// 前端 _arrangeWidgets 的三分支：computeSize 存在 -> 固定高度（不随节点变）；
// computeLayoutSize 存在 -> 加入 distributeSpace 剩余高度分配（随节点 resize 增长）。
// 所以必须 delete widget.computeSize，改用 computeLayoutSize 声明最小尺寸，
// 让预览区（flex:1 的 stage）吃掉节点多出来的高度。
function fillNodeHeight(widget, { minWidth = 0, minHeight = 0, minNodeSize } = {}) {
    if (!widget) return widget;
    delete widget.computeSize;
    widget.computeLayoutSize = () => ({
        minWidth: Math.max(0, minWidth),
        minHeight,
    });
    widget.options ??= {};
    if (minNodeSize) widget.options.minNodeSize = minNodeSize;
    return widget;
}

// DOM widget 的 width 永远跟随节点宽度（不让前端种下的固定宽度盖过节点）。
function keepDomWidgetWidthAuto(widget) {
    if (!widget) return widget;
    try {
        Object.defineProperty(widget, "width", {
            get() { return undefined; },
            set() { /* 忽略种下的固定宽度 */ },
            configurable: true,
        });
    } catch (e) { /* 忽略 */ }
    return widget;
}

// ---------- CSS ----------
function ensureCompareCss() {
    if (document.getElementById(CSS_ID)) return;
    const style = document.createElement("style");
    style.id = CSS_ID;
    style.textContent = `
.itsgem-compare-root{box-sizing:border-box;width:100%;height:100%;display:flex;flex-direction:column;gap:6px;padding:2px 6px 6px;color:#d8eeee;font:11px/1.35 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;overflow:hidden;}
.itsgem-compare-stage{position:relative;flex:1 1 auto;min-height:112px;overflow:hidden;border:1px solid rgba(196,166,255,.38);border-radius:6px;background:#000;box-shadow:inset 0 0 0 1px rgba(255,255,255,.025);touch-action:none;}
.itsgem-compare-stage img{position:absolute;inset:0;width:100%;height:100%;object-fit:contain;user-select:none;-webkit-user-drag:none;}
.itsgem-compare-stage.is-empty img{visibility:hidden;}
.itsgem-compare-seam{position:absolute;top:0;bottom:0;z-index:2;width:1px;margin-left:-0.5px;background:${BRAND};box-shadow:0 0 4px rgba(196,166,255,.55);opacity:0;pointer-events:none;}
.itsgem-compare-status{position:absolute;left:7px;top:7px;z-index:3;max-width:calc(100% - 112px);padding:3px 6px;border-radius:4px;background:rgba(0,0,0,.7);color:#b8d3d1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;pointer-events:none;backdrop-filter:blur(4px);}
.itsgem-compare-status:empty{display:none;}
.itsgem-compare-stage.is-empty .itsgem-compare-status{left:50%;top:50%;max-width:82%;transform:translate(-50%,-50%);color:#78908e;text-align:center;white-space:normal;}
.itsgem-compare-footer{display:flex;align-items:center;gap:8px;flex:none;height:24px;}
.itsgem-compare-tools{display:flex;gap:4px;flex:none;opacity:.9;}
.itsgem-compare-identity{position:absolute;left:0;right:0;top:7px;height:22px;z-index:3;pointer-events:none;}
.itsgem-compare-identity span{position:absolute;top:0;width:${BADGE_WIDTH}px;height:22px;border-radius:4px;background:rgba(0,0,0,.7);color:#d8eeee;font-weight:600;text-align:center;line-height:22px;}
.itsgem-compare-identity span[hidden]{display:none;}
.itsgem-compare-stage.is-empty .itsgem-compare-identity{display:none;}
.itsgem-compare-tools:hover{opacity:1;}
.itsgem-compare-caption{flex:1;min-width:0;height:${CAPTION_HEIGHT}px;overflow:hidden;color:#8ba3a1;font-size:10px;line-height:${CAPTION_HEIGHT}px;text-align:center;white-space:nowrap;text-overflow:ellipsis;}
.itsgem-compare-tool{box-sizing:border-box;height:22px;min-width:24px;padding:0 6px;border:1px solid rgba(196,166,255,.55);border-radius:4px;background:rgba(0,0,0,.66);color:#e0d9f5;font:700 9px/20px "Segoe UI",sans-serif;cursor:pointer;}
.itsgem-compare-tool:hover{border-color:${BRAND};color:#fff;background:rgba(80,55,150,.78);}
.itsgem-compare-tool.active{border-color:${BRAND};color:${BRAND};box-shadow:0 0 7px rgba(196,166,255,.3);}
`;
    document.head.appendChild(style);
}

// ---------- 面板 ----------
function getMode(node) {
    node.properties ??= {};
    node.properties.itsgem_compare_mode = normalizeCompareMode(node.properties.itsgem_compare_mode);
    return node.properties.itsgem_compare_mode;
}

function refreshLang(state) {
    if (!state) return;
    state.status.textContent = state.emptyText ?? "";
    state.slideButton.textContent = T("滑动", "SLIDE");
    state.slideButton.title = T("滑动：分割线跟随指针移动，逐步揭示 B 图", "Slide: divider follows the pointer to reveal image B");
    state.aButton.title = T("锁定预览 A（关闭滑动）", "Lock preview A (slide off)");
    state.bButton.title = T("锁定预览 B（关闭滑动）", "Lock preview B (slide off)");
    state.identity.title = state.seamVisible
        ? T("A 在分割线左侧 · B 在右侧", "A left of the seam · B right")
        : T(`显示 ${state.fraction === 0 ? "B" : "A"}`, `Showing ${state.fraction === 0 ? "B" : "A"}`);
}

function applyClip(state) {
    const { clipPath, seamLeft, seamVisible } = compareClip(state.fraction);
    state.imageA.style.clipPath = clipPath;
    state.seam.style.left = seamLeft;
    state.seam.style.opacity = seamVisible ? "1" : "0";
    state.seamVisible = seamVisible;
    const places = compareBadges(state.fraction, state.stage.clientWidth || 0);
    for (const [badge, left] of [[state.identityLeft, places.a], [state.identityRight, places.b]]) {
        badge.hidden = left == null;
        if (left != null) badge.style.left = `${left}px`;
    }
    refreshLang(state);
}

function setEmpty(state, text) {
    state.stage.classList.add("is-empty");
    state.emptyText = text;
    state.status.textContent = text;
    state.caption.textContent = "";
}

function setReady(state) {
    state.stage.classList.remove("is-empty");
    state.emptyText = "";
    state.status.textContent = "";
    state.caption.textContent = compareSizeLabel(state.refs);
}

function updateModeButtons(state) {
    const mode = getMode(state.node);
    state.slideButton.classList.toggle("active", mode === "slide");
    state.aButton.classList.toggle("active", mode === "toggle" && !state.showingB);
    state.bButton.classList.toggle("active", mode === "toggle" && state.showingB);
}

function applyToggle(state) {
    state.fraction = state.showingB ? 0 : 1;
    applyClip(state);
    updateModeButtons(state);
    state.node.setDirtyCanvas?.(true, true);
}

function loadPreviews(state, refs) {
    state.refs = refs;
    state.loaded = 0;
    setEmpty(state, T("加载预览…", "Loading previews…"));
    state.imageA.src = api.apiURL(`/view?${mediaViewQuery(refs.a, "temp")}`);
    state.imageB.src = api.apiURL(`/view?${mediaViewQuery(refs.b, "temp")}`);
}

function buildPanel(node) {
    if (node.__itsgemCompare) return node.__itsgemCompare;
    ensureCompareCss();

    const root = document.createElement("div");
    root.className = "itsgem-compare-root";
    const stage = document.createElement("div");
    stage.className = "itsgem-compare-stage is-empty";
    const imageA = document.createElement("img");
    const imageB = document.createElement("img");
    const seam = document.createElement("div");
    seam.className = "itsgem-compare-seam";
    const status = document.createElement("div");
    status.className = "itsgem-compare-status";
    status.textContent = T("运行以加载 A/B 预览", "Run to load A/B preview");
    const identity = document.createElement("div");
    identity.className = "itsgem-compare-identity";
    const identityLeft = document.createElement("span");
    const identityRight = document.createElement("span");
    identityLeft.textContent = "A";
    identityRight.textContent = "B";
    identity.append(identityLeft, identityRight);
    const tools = document.createElement("div");
    tools.className = "itsgem-compare-tools";
    const slideButton = makeToolButton(T("滑动", "SLIDE"), T("滑动：分割线跟随指针移动，逐步揭示 B 图", "Slide: divider follows the pointer to reveal image B"));
    const aButton = makeToolButton("A", T("锁定预览 A（关闭滑动）", "Lock preview A (slide off)"));
    const bButton = makeToolButton("B", T("锁定预览 B（关闭滑动）", "Lock preview B (slide off)"));
    tools.append(slideButton, aButton, bButton);
    stage.append(imageB, imageA, seam, status, identity);
    const caption = document.createElement("div");
    caption.className = "itsgem-compare-caption";
    const footer = document.createElement("div");
    footer.className = "itsgem-compare-footer";
    footer.append(caption, tools);
    root.append(stage, footer);

    const widget = node.addDOMWidget(PANEL_WIDGET, "itsgem_compare", root, {
        serialize: false,
        hideOnZoom: false,
        getMinHeight: () => PANEL_MIN_HEIGHT + WIDGET_FRAME,
    });
    // 预览区随节点外框联动（复刻 AusBoss fillNodeHeight）：
    // 删掉固定 computeSize，改用 computeLayoutSize 加入剩余高度分配，
    // 节点拉高时 flex:1 的 stage 自动吃掉多出来的高度。
    fillNodeHeight(widget, {
        minWidth: 0,
        minHeight: PANEL_MIN_HEIGHT + WIDGET_FRAME,
        minNodeSize: [320, 220],
    });
    keepDomWidgetWidthAuto(widget);

    const abort = new AbortController();
    const state = node.__itsgemCompare = {
        node, root, stage, imageA, imageB, seam, status, caption, identity, identityLeft, identityRight,
        slideButton, aButton, bButton,
        widget, abort, refs: null, fraction: 1, loaded: 0, showingB: false, seamVisible: false,
    };
    applyClip(state);
    updateModeButtons(state);

    const signal = abort.signal;
    window.addEventListener("itsgem-lang-change", () => refreshLang(state), { signal });
    const resized = new ResizeObserver(() => applyClip(state));
    resized.observe(stage);
    signal.addEventListener("abort", () => resized.disconnect());

    slideButton.addEventListener("click", (event) => {
        event.preventDefault();
        event.stopPropagation();
        node.properties.itsgem_compare_mode = normalizeCompareMode("slide");
        state.showingB = false;
        state.fraction = 1;
        applyClip(state);
        updateModeButtons(state);
        node.setDirtyCanvas?.(true, true);
    }, { signal });

    for (const [button, showingB] of [[aButton, false], [bButton, true]]) {
        button.addEventListener("click", (event) => {
            event.preventDefault();
            event.stopPropagation();
            node.properties.itsgem_compare_mode = "toggle";
            node.properties.itsgem_compare_side = showingB ? "B" : "A";
            state.showingB = showingB;
            applyToggle(state);
        }, { signal });
    }

    stage.addEventListener("pointermove", (event) => {
        if (tools.contains(event.target)) return;
        if (!state.refs || getMode(node) !== "slide") return;
        const rect = stage.getBoundingClientRect();
        state.fraction = slideFraction(event.clientX, rect.left, rect.width);
        applyClip(state);
    }, { signal });

    stage.addEventListener("pointerleave", (event) => {
        if (!state.refs || getMode(node) !== "slide") return;
        const rect = stage.getBoundingClientRect();
        state.fraction = slideFraction(event.clientX, rect.left, rect.width, true);
        applyClip(state);
    }, { signal });

    const onImageLoad = () => {
        state.loaded += 1;
        if (state.loaded >= 2) {
            setReady(state);
            node.setDirtyCanvas?.(true, true);
        }
    };
    imageA.addEventListener("load", onImageLoad, { signal });
    imageB.addEventListener("load", onImageLoad, { signal });
    const onImageError = () => setEmpty(state, "A/B 对比预览加载失败");
    imageA.addEventListener("error", onImageError, { signal });
    imageB.addEventListener("error", onImageError, { signal });

    return state;
}

function showStoredResult(node) {
    const state = node.__itsgemCompare;
    if (!state || state.refs) return;
    const refs = findCompareImages(app.nodeOutputs?.[outputKey(node, app.rootGraph ?? app.graph)]);
    if (refs) loadPreviews(state, refs);
}

app.registerExtension({
    name: "ItsGemTools.Compare",
    beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData?.name !== NODE_NAME) return;
        chainCallback(nodeType.prototype, "onNodeCreated", function () {
            buildPanel(this);
            this.setSize?.([
                Math.max(DEFAULT_NODE_SIZE[0], this.size?.[0] ?? 0),
                Math.max(DEFAULT_NODE_SIZE[1], this.size?.[1] ?? 0),
            ]);
        });
        chainCallback(nodeType.prototype, "onConfigure", function () {
            queueMicrotask(() => {
                const state = buildPanel(this);
                state.showingB = this.properties?.itsgem_compare_side === "B";
                if (getMode(this) === "toggle") applyToggle(state);
                else updateModeButtons(state);
            });
        });
        chainCallback(nodeType.prototype, "onExecuted", function (message) {
            const state = buildPanel(this);
            const refs = findCompareImages(message);
            if (refs) loadPreviews(state, refs);
            else setEmpty(state, "执行完成但没有对比预览");
            app.canvas?.setDirty?.(true, true);
        });
        chainCallback(nodeType.prototype, "onRemoved", function () {
            this.__itsgemCompare?.abort?.abort();
            this.__itsgemCompare = null;
        });
    },
    onNodeOutputsUpdated() {
        setTimeout(() => {
            for (const node of app.graph?._nodes ?? []) {
                if (node?.comfyClass === NODE_NAME) showStoredResult(node);
            }
        }, 0);
    },
    nodeCreated(node) {
        if (node?.comfyClass === NODE_NAME || node?.type === NODE_NAME) buildPanel(node);
    },
});
