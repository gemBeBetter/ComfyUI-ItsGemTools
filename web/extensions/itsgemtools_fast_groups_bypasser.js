import { app } from "../../../scripts/app.js";

// ============================================================
// ItsGemTools · Fast Groups Bypasser（按组快速旁路）
// 复刻 rgthree-comfy 的 "Fast Groups Bypasser (rgthree)" 的思路
// （rgthree 为 MIT，本文件代码在本包内独立重写，rgthree 原版零改动，
//  类型名带 (ItsGemTools) 后缀，可与 rgthree 原节点同画布共存）。
//
// 核心机制（节点与"组"的关系是怎么建立的）：
//  1) 纯前端虚拟节点，没有任何输入，不参与执行（isVirtualNode = true）。
//  2) 关系不是靠连线，而是靠"几何位置"：定期枚举当前工作流的所有 Group，
//     再对每个组用"节点中心点是否落在组的矩形框内"算出该组包含哪些节点。
//  3) 每个匹配到的组 → 画一行开关；点一下就把该组内所有节点的 mode
//     在 0(启用) / 4(旁路) 之间翻转。状态的"读回"同样来自节点的真实 mode，
//     所以你在别处手动旁路/启用，这里会跟着变（450ms 轮询同步）。
//  4) 组的筛选/排序全部走 Properties：matchTitle(正则) / matchColors /
//     showAllGraphs / sort / customSortAlphabet / toggleRestriction。
// ============================================================

const NODE_TYPE = "Fast Groups Bypasser (ItsGemTools)";
const CATEGORY = "ItsGemTools";
const EXT_NAME = "ItsGemTools.FastGroupsBypasser";

// 本包主题色（与 itsgemtools_appearance.js 同一套淡紫）
const THEME = {
    rowBg: "#2c2540",
    rowBgOn: "#3a2f58",
    rowBgMixed: "#332a4c",
    rowLine: "#4d4170",
    rowLineOn: "#c4a6ff",
    text: "#e8e0ff",
    textDim: "#9a8fbb",
    knobOn: "#c4a6ff",
    knobOff: "#5b4f7a",
    nav: "#b9a7e6",
    navHover: "#e2d6ff",
    hint: "#8b81a8",
    fallbackGroup: "#8a63d2",
};

const ROW_H = 24;          // 每个开关行占的高度（与前端默认 widget 行高一致）
const ROW_PAD_X = 10;      // 行左右内缩
const MIN_W = 300;
const NAV_HIT_W = 22;      // 右侧"定位"箭头命中宽度

const P_TITLE = "matchTitle";
const P_COLORS = "matchColors";
const P_NAV = "showNav";
const P_ALL_GRAPHS = "showAllGraphs";
const P_SORT = "sort";
const P_ALPHA = "customSortAlphabet";
const P_RESTRICT = "toggleRestriction";

const MODE_ALWAYS = 0;
const MODE_MUTE = 2;
const MODE_BYPASS = 4;

// 纯装饰节点不参与旁路切换：否则标签/注释会被一起变灰看不见
const SKIP_TYPES = new Set(["Note", "MarkdownNote", "Label (ItsGemTools)", NODE_TYPE]);

const SORT_VALUES = ["position", "alphanumeric", "custom alphabet"];
const RESTRICT_VALUES = ["default", "max one", "always one"];

// ------------------------------------------------------------
// 小工具
// ------------------------------------------------------------
function roundRect(ctx, x, y, w, h, r) {
    const rr = Math.max(0, Math.min(r, h / 2, w / 2));
    ctx.beginPath();
    if (typeof ctx.roundRect === "function") {
        ctx.roundRect(x, y, w, h, rr);
        return;
    }
    ctx.moveTo(x + rr, y);
    ctx.lineTo(x + w - rr, y);
    ctx.arcTo(x + w, y, x + w, y + rr, rr);
    ctx.lineTo(x + w, y + h - rr);
    ctx.arcTo(x + w, y + h, x + w - rr, y + h, rr);
    ctx.lineTo(x + rr, y + h);
    ctx.arcTo(x, y + h, x, y + h - rr, rr);
    ctx.lineTo(x, y + rr);
    ctx.arcTo(x, y, x + rr, y, rr);
    ctx.closePath();
}

function fitText(ctx, text, maxWidth) {
    const str = String(text ?? "");
    if (!(maxWidth > 0)) return str;
    if (ctx.measureText(str).width <= maxWidth) return str;
    let out = str;
    while (out.length > 1 && ctx.measureText(out + "…").width > maxWidth) {
        out = out.slice(0, -1);
    }
    return out + "…";
}

// ComfyUI 颜色名（red / pale_blue …）与 #abc 简写 → 统一成 #rrggbb
function normColor(color) {
    if (!color) return "";
    let c = String(color).trim();
    if (!c) return "";
    try {
        const named = LGraphCanvas?.node_colors?.[c.toLocaleLowerCase()];
        if (named) c = named.groupcolor || named.color || c;
    } catch (e) { /* 忽略 */ }
    c = c.replace("#", "").toLocaleLowerCase();
    if (c.length === 3) c = c.replace(/(.)(.)(.)/, "$1$1$2$2$3$3");
    return `#${c}`;
}

function markDirty(deep) {
    try {
        app.canvas?.setDirty?.(true, !!deep);
    } catch (e) { /* 忽略 */ }
}

// ------------------------------------------------------------
// 节点包围盒 / 组内节点
// 关系建立的关键：只有"节点中心点落在组矩形内"的节点，才算这个组的成员。
// ------------------------------------------------------------
function makeBoundsCache() {
    const cache = new WeakMap();
    return function bounds(node) {
        if (!node) return null;
        if (cache.has(node)) return cache.get(node);
        let b = null;
        try {
            if (typeof node.getBounding === "function") b = node.getBounding();
        } catch (e) { b = null; }
        if (!b || !(b[2] > 0) || !(b[3] > 0)) {
            b = [node.pos?.[0] ?? 0, node.pos?.[1] ?? 0, node.size?.[0] ?? 0, node.size?.[1] ?? 0];
        }
        cache.set(node, b);
        return b;
    };
}

function groupRect(group) {
    if (!group) return null;
    try {
        const b = group.bounding;
        if (b && b[2] > 0 && b[3] > 0) return b;
    } catch (e) { /* 忽略 */ }
    const p = group._pos || group.pos || [0, 0];
    const s = group._size || group.size || [0, 0];
    if (!(s[0] > 0) || !(s[1] > 0)) return null;
    return [p[0], p[1], s[0], s[1]];
}

function groupNodes(group, self, bounds, out) {
    const r = groupRect(group);
    if (!r) return out || [];
    const list = (typeof group.graph?.getNodes === "function" ? group.graph.getNodes() : group.graph?._nodes) ||
        app.graph?._nodes || [];
    const result = out || [];
    for (const n of list) {
        if (!n || n === self) continue;
        if (SKIP_TYPES.has(n.type)) continue;
        const b = bounds(n);
        if (!b) continue;
        const cx = b[0] + b[2] * 0.5;
        const cy = b[1] + b[3] * 0.5;
        if (cx < r[0] || cx >= r[0] + r[2] || cy < r[1] || cy >= r[1] + r[3]) continue;
        result.push(n);
    }
    return result;
}

// 改 mode，并递归进子图节点（与 rgthree 的 changeModeOfNodes 等价）
function setModes(nodes, mode, seen) {
    const visited = seen || new Set();
    for (const n of nodes) {
        if (!n || visited.has(n)) continue;
        visited.add(n);
        try { n.mode = mode; } catch (e) { /* 忽略 */ }
        try {
            if (typeof n.isSubgraphNode === "function" && n.subgraph) {
                setModes(n.subgraph._nodes || [], mode, visited);
            }
        } catch (e) { /* 忽略 */ }
    }
}

// ------------------------------------------------------------
// 枚举/筛选/排序组
// ------------------------------------------------------------
function collectGroups(node) {
    const props = node?.properties || {};
    const all = [];
    try {
        const main = app.graph;
        if (main?._groups?.length) all.push(...main._groups);
        const subs = main?.subgraphs;
        if (subs && typeof subs.values === "function") {
            for (const sg of subs.values()) {
                if (sg?.groups?.length) all.push(...sg.groups);
            }
        } else if (subs && typeof subs[Symbol.iterator] === "function") {
            for (const item of subs) {
                const sg = Array.isArray(item) ? item[1] : item;
                if (sg?.groups?.length) all.push(...sg.groups);
            }
        }
    } catch (e) { /* 忽略：拿不到子图就只列主图 */ }

    let groups = all.filter((g) => g && typeof g.title === "string");

    // 只看当前画布所在的图
    if (props[P_ALL_GRAPHS] === false) {
        let current = app.graph;
        try {
            const viaCanvas = app.canvas?.getCurrentGraph?.();
            if (viaCanvas) current = viaCanvas;
        } catch (e) { /* 忽略 */ }
        groups = groups.filter((g) => !g.graph || g.graph === current);
    }

    // 标题过滤（支持正则；写错正则就当不过滤，不炸节点）
    const pattern = String(props[P_TITLE] ?? "").trim();
    if (pattern) {
        let re = null;
        try { re = new RegExp(pattern, "i"); } catch (e) { re = null; }
        if (re) groups = groups.filter((g) => re.test(g.title));
    }

    // 颜色过滤（逗号分隔，支持 ComfyUI 颜色名与 #rrggbb）
    const colors = String(props[P_COLORS] ?? "")
        .split(",")
        .map((c) => normColor(c))
        .filter(Boolean);
    if (colors.length) {
        groups = groups.filter((g) => colors.includes(normColor(g.color)));
    }

    // 排序
    const sort = String(props[P_SORT] || "position");
    if (sort === "alphanumeric") {
        groups.sort((a, b) => a.title.localeCompare(b.title));
    } else if (sort === "custom alphabet") {
        const raw = String(props[P_ALPHA] ?? "").replace(/\n/g, "").trim();
        const alphabet = raw
            ? (raw.includes(",") ? raw.toLocaleLowerCase().split(",") : raw.toLocaleLowerCase().split(""))
            : [];
        if (alphabet.length) groups.sort(customAlphabetSort(alphabet));
        else groups.sort((a, b) => a.title.localeCompare(b.title));
    } else {
        groups.sort((a, b) => {
            const ra = groupRect(a) || [0, 0, 0, 0];
            const rb = groupRect(b) || [0, 0, 0, 0];
            const ay = Math.floor(ra[1] / 30);
            const by = Math.floor(rb[1] / 30);
            if (ay === by) return Math.floor(ra[0] / 30) - Math.floor(rb[0] / 30);
            return ay - by;
        });
    }
    return groups;
}

function customAlphabetSort(alphabet) {
    return (a, b) => {
        const idx = (title) => {
            const t = title.toLocaleLowerCase();
            for (let i = 0; i < alphabet.length; i++) {
                if (alphabet[i] && t.startsWith(alphabet[i])) return i;
            }
            return -1;
        };
        const ia = idx(a.title);
        const ib = idx(b.title);
        if (ia > -1 && ib > -1) return ia === ib ? a.title.localeCompare(b.title) : ia - ib;
        if (ia > -1) return -1;
        if (ib > -1) return 1;
        return a.title.localeCompare(b.title);
    };
}

// 点箭头：把画布移到该组，并按组大小缩放（与 rgthree 的行为一致）
function navigateTo(group) {
    const canvas = app.canvas;
    const r = groupRect(group);
    if (!canvas || !r) return;
    try { canvas.centerOnNode?.(group); } catch (e) { /* 忽略 */ }
    try {
        const cw = canvas.canvas?.width || 0;
        const ch = canvas.canvas?.height || 0;
        if (!cw || !ch) return;
        const current = canvas.ds?.scale || 1;
        const zoom = Math.max(0.05, Math.min(current, cw / r[2] - 0.02, ch / r[3] - 0.02, 1.5));
        if (typeof canvas.setZoom === "function") {
            canvas.setZoom(zoom, [cw / 2, ch / 2]);
        } else if (canvas.ds) {
            canvas.ds.scale = zoom;
            canvas.ds.offset[0] = r[0] + r[2] / 2 - cw / (2 * zoom);
            canvas.ds.offset[1] = r[1] + r[3] / 2 - ch / (2 * zoom);
        }
    } catch (e) { /* 忽略 */ }
    markDirty(true);
}

// ------------------------------------------------------------
// 一行开关：一个组 = 一个自定义 widget（纯 canvas 绘制）
// ------------------------------------------------------------
class GemGroupRow {
    constructor(group, owner) {
        this.type = "custom";
        this.name = "GEM_GROUP_ROW";
        this.options = {};
        this.group = group;
        this.owner = owner;
        this.label = group?.title || "";
        this.toggled = false;   // 组内是否还有"启用"的节点
        this.mixed = false;     // 组内既有启用又有旁路
        this.count = 0;
        this.y = 0;
        this.last_y = 0;
    }

    computeSize(width) {
        return [width, ROW_H];
    }

    // 状态完全由组内节点实时推导，不需要写进工作流
    serializeValue() {
        return null;
    }

    draw(ctx, node, width, posY, height) {
        // 前端传进来的是 renderingSize[0]，它可能比节点实际体型 size[0] 更宽
        // （画出来就是行条右侧捅到框外面），所以先夹到体型宽度内再留内边距。
        const bodyW = Number.isFinite(node?.size?.[0]) && node.size[0] > 0 ? node.size[0] : 0;
        let totalW = Number.isFinite(width) && width > 0 ? width : bodyW;
        if (bodyW > 0) totalW = Math.min(totalW, bodyW);
        const slot = Number.isFinite(height) && height > 0 ? height : ROW_H;
        const top = Number.isFinite(posY) ? posY : 0;
        const h = Math.max(16, Math.min(slot - 2, ROW_H - 2));
        const x = ROW_PAD_X;
        const w = Math.max(totalW - ROW_PAD_X * 2, 40);
        const y = top + Math.max(0, (slot - h) / 2);
        if (!(w > 20)) return;

        ctx.save();
        ctx.lineWidth = 1;

        // 行底：启用=亮紫、旁路=暗紫、混合=中间色
        roundRect(ctx, x, y, w, h, h / 2);
        ctx.fillStyle = this.toggled ? THEME.rowBgOn : (this.mixed ? THEME.rowBgMixed : THEME.rowBg);
        ctx.fill();
        ctx.strokeStyle = this.toggled ? THEME.rowLineOn : THEME.rowLine;
        ctx.stroke();

        // 左侧组色条：直接把"这一行对应画布上哪个颜色的框"告诉你
        const groupColor = normColor(this.group?.color) || THEME.fallbackGroup;
        roundRect(ctx, x + 5, y + h / 2 - 6, 3, 12, 1.5);
        ctx.fillStyle = groupColor;
        ctx.fill();

        let right = x + w - 8;

        // 定位箭头
        if (node?.properties?.[P_NAV] !== false) {
            const ax = right - 4;
            const ay = y + h / 2;
            ctx.strokeStyle = THEME.nav;
            ctx.lineWidth = 1.6;
            ctx.lineJoin = "round";
            ctx.lineCap = "round";
            ctx.beginPath();
            ctx.moveTo(ax - 5, ay - 4);
            ctx.lineTo(ax, ay);
            ctx.lineTo(ax - 5, ay + 4);
            ctx.stroke();
            right -= 14;
        }

        // 状态文字
        const stateText = this.mixed ? "部分关闭" : (this.toggled ? "启用" : "关闭");
        ctx.font = "11px Arial";
        ctx.textBaseline = "middle";
        ctx.textAlign = "right";
        ctx.fillStyle = this.toggled ? THEME.knobOn : THEME.textDim;
        ctx.fillText(stateText, right - 3, y + h / 2 + 0.5);
        right -= ctx.measureText(stateText).width + 8;

        // 圆点
        const dotR = 4.5;
        const dotX = right - dotR;
        const dotY = y + h / 2;
        if (this.toggled) {
            ctx.beginPath();
            ctx.arc(dotX, dotY, dotR, 0, Math.PI * 2);
            ctx.fillStyle = THEME.knobOn;
            ctx.fill();
        } else {
            ctx.beginPath();
            ctx.arc(dotX, dotY, dotR, 0, Math.PI * 2);
            ctx.strokeStyle = this.mixed ? THEME.knobOn : THEME.knobOff;
            ctx.lineWidth = 1.2;
            ctx.stroke();
        }
        right = dotX - dotR - 8;

        // 组名
        ctx.font = "12px Arial";
        ctx.textAlign = "left";
        ctx.fillStyle = THEME.text;
        const maxText = Math.max(right - (x + 15), 16);
        ctx.fillText(fitText(ctx, this.label || "(未命名组)", maxText), x + 15, y + h / 2 + 0.5);

        ctx.restore();
    }

    mouse(event, pos, node) {
        if (!event || event.type !== "pointerdown") return false;
        const width = node?.size?.[0] || 0;
        const showNav = node?.properties?.[P_NAV] !== false;
        if (showNav && pos && pos[0] >= width - ROW_PAD_X - NAV_HIT_W) {
            navigateTo(this.group);
            return true;
        }
        changeGroupMode(node, this, null, false);
        return true;
    }
}

function rowWidgets(node) {
    return (node?.widgets || []).filter((w) => w instanceof GemGroupRow);
}

// 翻转/设定一个组：调用前先读"组内还有没有启用的节点"，决定往哪个方向切
function changeGroupMode(node, row, force, skipOthers) {
    if (!row?.group || !node) return;
    const props = node.properties || {};
    const restriction = String(props[P_RESTRICT] || "default");
    const rows = rowWidgets(node);

    let want = force == null ? !row.toggled : !!force;

    if (!skipOthers) {
        if (want && restriction.includes(" one")) {
            // max one / always one：开启本组前先关掉别的组
            for (const r of rows) {
                if (r !== row && r.toggled) changeGroupMode(node, r, false, true);
            }
        } else if (!want && restriction === "always one") {
            // always one：不允许把最后一组关掉
            const isLastOne = rows.every((r) => r === row || !r.toggled);
            if (isLastOne) want = true;
        }
    }

    const nodes = groupNodes(row.group, node, makeBoundsCache());
    setModes(nodes, want ? MODE_ALWAYS : MODE_BYPASS);

    row.toggled = want;
    row.mixed = false;
    markDirty(false);
}

// ------------------------------------------------------------
// 节点本体
// ------------------------------------------------------------
class GemFastGroupsBypasser extends LGraphNode {
    constructor(title) {
        super(title ?? NODE_TYPE);
        this.comfyClass = NODE_TYPE;
        this.isVirtualNode = true;       // 纯前端节点：不进 prompt，不参与执行
        this.serialize_widgets = false;  // 开关状态由节点真实 mode 推导，无需存盘
        this.color = "#8a63d2";          // 标题栏：本包淡紫
        this.bgcolor = "#241f38";        // 主体：深紫黑
        this.properties = Object.assign({
            [P_COLORS]: "",
            [P_TITLE]: "",
            [P_NAV]: true,
            [P_ALL_GRAPHS]: true,
            [P_SORT]: "position",
            [P_ALPHA]: "",
            [P_RESTRICT]: "default",
        }, this.properties || {});
        this.addOutput("OPT_CONNECTION", "*");
        this.size = [MIN_W + 40, 60];
        this._rows = new Map();   // group -> row
        this._rowCount = -1;
        this._syncing = false;
    }

    onRemoved() {
        this._rows?.clear?.();
        super.onRemoved?.();
    }

    // 重建行列表 + 同步状态（每 450ms 由轮询调用，组增删/改名/排序都会自己跟上）
    refreshRows(bounds) {
        if (this._syncing) return;
        this._syncing = true;
        try {
            // 轮询会把同一轮的包围盒缓存传进来复用；单独调用（加载工作流后立刻扫一遍）就自己建一份
            const getBounds = typeof bounds === "function" ? bounds : makeBoundsCache();
            const groups = collectGroups(this);
            const rows = [];
            let stateChanged = false;
            for (const group of groups) {
                let row = this._rows.get(group);
                if (!row) {
                    row = new GemGroupRow(group, this);
                    this._rows.set(group, row);
                }
                const title = group.title || "";
                if (row.label !== title) row.label = title;

                const bucket = row._nodes || (row._nodes = []);
                bucket.length = 0;
                groupNodes(group, this, getBounds, bucket);
                let on = 0;
                let off = 0;
                for (const n of bucket) {
                    if (n.mode === MODE_ALWAYS) on += 1;
                    else if (n.mode === MODE_BYPASS || n.mode === MODE_MUTE) off += 1;
                }
                const toggled = on > 0;
                const mixed = on > 0 && off > 0;
                if (row.toggled !== toggled || row.mixed !== mixed || row.count !== bucket.length) {
                    stateChanged = true;
                }
                row.toggled = toggled;
                row.mixed = mixed;
                row.count = bucket.length;
                rows.push(row);
            }

            // 组被删了就把它那行也删掉
            for (const group of [...this._rows.keys()]) {
                if (!rows.includes(this._rows.get(group))) this._rows.delete(group);
            }

            // 原地改写 widgets 数组（保持前端持有的同一个数组对象）
            const widgets = this.widgets || (this.widgets = []);
            const listChanged = widgets.length !== rows.length || rows.some((w, i) => widgets[i] !== w);
            let relayout = listChanged;
            if (listChanged) {
                widgets.length = 0;
                for (const row of rows) widgets.push(row);
            }

            // 兜底排布：新前端是「读 widget.y 来画每一行」的，如果某个时机前端没跑
            // _arrangeWidgets（首次加载 / 渲染器差异），所有行的 y 都会是 0 → 全部叠在
            // 同一处，看起来就是"所有组挤在一起"。这里按 28px 步进自己排一遍；
            // 前端一旦 arrange 过（y>0），就交给它，不覆盖官方位置。
            if (rows.length && rows.every((r) => !(r.y > 0))) {
                let y0 = 46;
                for (const row of rows) {
                    row.computedHeight = ROW_H + 4;
                    row.y = y0;
                    row.last_y = y0;
                    y0 += ROW_H + 4;
                }
                relayout = true;
            }

            if (this._rowCount !== rows.length || relayout) {
                this._rowCount = rows.length;
                const computed = this.computeSize?.();
                const needW = Math.max(this.size?.[0] || 0, MIN_W, Math.ceil(computed?.[0] || 0));
                const needH = Math.max(Math.ceil(computed?.[1] || 0), 42 + rows.length * (ROW_H + 4));
                this.setSize?.([needW, needH]);
            }

            if (listChanged || stateChanged) markDirty(false);
        } catch (e) {
            console.warn("[ItsGemTools] Fast Groups Bypasser refresh:", e);
        } finally {
            this._syncing = false;
        }
    }

    // 没有任何组时给一句提示（此时没有行可画）
    onDrawForeground(ctx) {
        if ((this.widgets || []).length) return;
        if (this.collapsed) return;
        const w = this.size?.[0] || 0;
        const h = this.size?.[1] || 0;
        if (!(w > 0 && h > 0)) return;
        const pattern = String(this.properties?.[P_TITLE] ?? "").trim();
        const text = pattern ? `没有标题匹配 /${pattern}/ 的组` : "当前工作流里还没有分组";
        ctx.save();
        ctx.font = "12px Arial";
        ctx.fillStyle = THEME.hint;
        ctx.textAlign = "center";
        ctx.textBaseline = "middle";
        ctx.fillText(fitText(ctx, text, w - 24), w / 2, h / 2 + 4);
        ctx.restore();
    }

    // 批量操作（右键菜单里的三项）
    handleAction(action) {
        const rows = rowWidgets(this);
        if (!rows.length) {
            this.refreshRows();
            return;
        }
        const restriction = String(this.properties?.[P_RESTRICT] || "default");
        if (action === "bypass") {
            const alwaysOne = restriction === "always one";
            rows.forEach((row, i) => changeGroupMode(this, row, alwaysOne && i === 0 ? true : false, true));
        } else if (action === "enable") {
            const onlyOne = restriction.includes(" one");
            rows.forEach((row, i) => changeGroupMode(this, row, onlyOne && i > 0 ? false : true, true));
        } else {
            const onlyOne = restriction.includes(" one");
            let foundOne = false;
            for (const row of rows) {
                const next = !(onlyOne && foundOne) && !row.toggled;
                if (next) foundOne = true;
                changeGroupMode(this, row, next, true);
            }
            if (!foundOne && restriction === "always one") {
                changeGroupMode(this, rows[rows.length - 1], true, true);
            }
        }
        this.refreshRows();
    }

    getExtraMenuOptions(canvas, options) {
        super.getExtraMenuOptions?.apply(this, [canvas, options]);
        const target = Array.isArray(options) ? options : [];
        target.push(
            { content: "全部关闭（本节点所有组）", callback: () => this.handleAction("bypass") },
            { content: "全部启用（本节点所有组）", callback: () => this.handleAction("enable") },
            { content: "全部翻转（本节点所有组）", callback: () => this.handleAction("toggle") },
            { content: "重新扫描分组", callback: () => this.refreshRows() },
            null
        );
        return target;
    }

    getHelp() {
        return `
      <p><strong>按组开关（Fast Groups Bypasser）</strong>：没有输入、不参与执行的纯前端节点。它把当前工作流里的每个 Group
      变成一行开关，点一下就把该组内所有节点在「启用(0)」与「关闭(4，即 ComfyUI 的 Bypass/旁路，节点变灰跳过)」之间切换。</p>
      <ul>
        <li><strong>它和组是怎么建立关系的？</strong>不靠连线。节点每隔几百毫秒枚举一次工作流的全部分组，
        再对每个组判断<em>「哪些节点的中心点落在该组的矩形框里」</em>，这些节点就是这个组的成员。
        所以：把节点拖进组框内 = 归到这个开关管；拖出去 = 自动不管。</li>
        <li><strong>行 = 组</strong>：每行左侧的小色条就是该组在画布上的边框颜色，方便对号入座。</li>
        <li><strong>状态是读回来的</strong>：行显示「启用 / 关闭 / 部分关闭」来自组内节点的真实 mode。
        你手动关掉其中某个节点，这一行会立刻变成「部分关闭」，不需要点任何刷新。</li>
        <li><strong>右侧箭头</strong>：把画布定位并缩放到该组，快速找到它。</li>
        <li><strong>Properties（右键 → 属性）</strong>：
          <code>matchTitle</code> 只保留标题匹配的组（支持正则，如 <code>放大|缩小</code>）；
          <code>matchColors</code> 只保留指定颜色的组（逗号分隔，如 <code>#3f789e,red</code>）；
          <code>showAllGraphs</code> 是否包含子图里的组；
          <code>sort</code> 排序方式（position / alphanumeric / custom alphabet）；
          <code>customSortAlphabet</code> 自定义排序字母表；
          <code>toggleRestriction</code> 最多/始终只有一个组启用。
        </li>
        <li><strong>右键菜单</strong>：全部关闭 / 全部启用 / 全部翻转 / 重新扫描分组。</li>
      </ul>`;
    }
}

GemFastGroupsBypasser["@matchTitle"] = { type: "string" };
GemFastGroupsBypasser["@matchColors"] = { type: "string" };
GemFastGroupsBypasser["@showNav"] = { type: "boolean" };
GemFastGroupsBypasser["@showAllGraphs"] = { type: "boolean" };
GemFastGroupsBypasser["@sort"] = { type: "combo", values: SORT_VALUES };
GemFastGroupsBypasser["@customSortAlphabet"] = { type: "string" };
GemFastGroupsBypasser["@toggleRestriction"] = { type: "combo", values: RESTRICT_VALUES };
GemFastGroupsBypasser.title = NODE_TYPE;
GemFastGroupsBypasser.category = CATEGORY;
GemFastGroupsBypasser._category = CATEGORY;

// ------------------------------------------------------------
// 轮询：组增删/改名/拖动、以及别处手动改 mode，都会自动同步
// ------------------------------------------------------------
function tick() {
    const graph = app?.graph;
    if (!graph) return;
    const canvas = app.canvas;
    if (canvas?.isDragging || canvas?.selected_group_moving) return;
    const nodes = graph._nodes || [];
    if (!nodes.length) return;
    const bounds = makeBoundsCache();
    for (const node of nodes) {
        if (!node) continue;
        if (node.type !== NODE_TYPE && !(node instanceof GemFastGroupsBypasser)) continue;
        try {
            node.refreshRows?.(bounds);
        } catch (e) { /* 单个节点失败不影响其它 */ }
    }
}

function startTicker() {
    if (window.__itsgemFgbTimer) return;
    window.__itsgemFgbTimer = setInterval(tick, 450);
}

app.registerExtension({
    name: EXT_NAME,
    registerCustomNodes() {
        if (window.__itsgemFgbRegistered) return;
        window.__itsgemFgbRegistered = true;
        // SEO：归入品牌分类（与 Label 同一机制），节点库里单独成组
        GemFastGroupsBypasser.category = CATEGORY;
        GemFastGroupsBypasser._category = CATEGORY;
        LiteGraph.registerNodeType(NODE_TYPE, GemFastGroupsBypasser);
        startTicker();
    },
    // 工作流加载后：丢掉旧的行缓存，并立刻扫一遍
    loadedGraphNode(node) {
        if (node?.type !== NODE_TYPE) return;
        node._rows?.clear?.();
        node._rowCount = -1;
        setTimeout(() => {
            try { node.refreshRows?.(); } catch (e) { /* 忽略 */ }
        }, 80);
    },
});
