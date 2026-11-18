import { api } from "../../../scripts/api.js";
import { app } from "../../../scripts/app.js";

// ============================================================
// ItsGemTools 批量出图 / Image Folder (ItsGemTools)
// 复刻 AusBoss `js/image_folder/index.js`（节点 AUSBOSS_NODES_ImageFolder）
// 及其依赖的 shared/{index, image_folder, panel_layout, widget_card_math}.mjs，
// 全部内联，不依赖任何第三方节点包；服务端走本包 /itsgemtools/image_folder/* 路由。
//
// 面板在卡片下方以瓦片墙展示文件夹里的图片：单击勾选，Shift + 单击批量勾选，
// 顶部计数条显示勾了多少张。勾选结果写进隐藏的 `pictures` widget，因此能被
// 保存 / 撤销 / API 调用。One per run 模式下，下一次要加载的那张图带白圈，
// 每次入队运行后 Picture 自动前进一格。
//
// 面板只问本包的服务器：图片列表、Browse 用的文件夹、以及图片的小缩略图。
// 服务器只读 ComfyUI 的 input / output 目录，别处的文件夹通过浏览器进来：
// 拖到节点上，或用 Add 选，图片由 ComfyUI 自己的上传接口复制进 input。
// ============================================================

const NODE_NAME = "GemImageFolder";
const PANEL_WIDGET = "itsgem_folder_panel";
const CSS_ID = "itsgem-image-folder-css-v1";
const BRAND = "#c4a6ff"; // 淡紫：本包统一主题色，区分 AusBoss 的青绿
const BRAND_RGB = "196,166,255";
const BRAND_INK = "#2a1b45";
const ROUTE_BASE = "/itsgemtools/image_folder";
const MIN_WIDTH = 320;
const MIN_HEIGHT = 204;
const WIDTH_PADDING = 15; // 前端给 DOM widget 留的左右内边距，最小宽度按它扣减
// 瓦片每次多渲染这么多：几千张的文件夹也不会拖垮面板
const SHOWN_STEP = 300;
const WATCHED = ["source", "folder", "subfolders", "sort", "pictures", "run", "position", "at_the_end"];
const PICTURE = /\.(png|jpe?g|webp|bmp|gif|tiff?)$/i;
// 拖进来的文件夹最多复制这么多张
const COPY_LIMIT = 3000;
// 到这个量级就算「很大的文件夹」，复制前先问一句
const BIG_COUNT = 300;
const BIG_BYTES = 1024 ** 3;

// 双语：优先取 i18n 扩展，未加载时回退英文
function T(zh, en) {
    return window.__ITSGEM_I18N ? window.__ITSGEM_I18N.t(zh, en) : en;
}

function ensureCss() {
    if (document.getElementById(CSS_ID)) return;
    const style = document.createElement("style");
    style.id = CSS_ID;
    style.textContent = `
.itsgem-if{box-sizing:border-box;overflow:hidden;position:relative;display:flex;flex-direction:column;gap:6px;width:100%;height:100%;padding:2px 6px 6px;color:#e2dcf5;font:11px/1.3 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;}
.itsgem-if-bar{flex:none;display:flex;align-items:center;gap:5px;height:20px;}
.itsgem-if-chip{box-sizing:border-box;height:20px;padding:0 8px;border:1px solid rgba(${BRAND_RGB},.5);border-radius:10px;background:rgba(${BRAND_RGB},.14);color:${BRAND};font:700 9.5px/18px "Segoe UI",sans-serif;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;min-width:0;}
.itsgem-if-chip.warn{border-color:#c98a2b;background:rgba(201,138,43,.16);color:#ffd9a0;}
.itsgem-if-gap{flex:1 1 auto;}
.itsgem-if-btn{flex:none;box-sizing:border-box;height:20px;padding:0 8px;border:1px solid #2c2a38;border-radius:5px;background:#14111c;color:#cdc3e6;font:700 9.5px/18px "Segoe UI",sans-serif;letter-spacing:.04em;text-transform:uppercase;cursor:pointer;}
.itsgem-if-btn:hover{color:#fff;border-color:${BRAND};}
.itsgem-if-btn[disabled]{opacity:.38;cursor:default;border-color:#2c2a38;color:#cdc3e6;}
.itsgem-if-grid{flex:1 1 auto;min-height:0;box-sizing:border-box;display:grid;grid-template-columns:repeat(auto-fill,minmax(74px,1fr));grid-auto-rows:max-content;align-content:start;gap:5px;padding:5px;overflow-y:auto;border:1px solid rgba(${BRAND_RGB},.22);border-radius:6px;background:#0d0b14;scrollbar-width:thin;}
.itsgem-if-cell{position:relative;box-sizing:border-box;aspect-ratio:1;padding:0;overflow:hidden;border:1px solid #262233;border-radius:5px;background:#1b1826;cursor:pointer;}
.itsgem-if-cell img{display:block;width:100%;height:100%;object-fit:cover;opacity:.34;transition:opacity .12s;}
.itsgem-if-cell.picked{border-color:rgba(${BRAND_RGB},.8);}
.itsgem-if-cell.picked img{opacity:1;}
.itsgem-if-cell:hover img{opacity:.8;}
.itsgem-if-cell.picked:hover img{opacity:1;}
.itsgem-if-cell.current{outline:2px solid #f2ecff;outline-offset:-2px;}
.itsgem-if-tick{position:absolute;top:3px;right:3px;display:none;width:15px;height:15px;border-radius:50%;background:${BRAND};color:${BRAND_INK};font:700 10px/15px "Segoe UI",sans-serif;text-align:center;}
.itsgem-if-cell.picked .itsgem-if-tick{display:block;}
.itsgem-if-num{position:absolute;left:3px;bottom:3px;display:none;min-width:8px;padding:0 4px;border-radius:7px;background:rgba(0,0,0,.72);color:#efe8ff;font:700 9px/14px "Segoe UI",sans-serif;text-align:center;}
.itsgem-if-cell.picked .itsgem-if-num{display:block;}
.itsgem-if-cell.done .itsgem-if-num{background:${BRAND};color:${BRAND_INK};}
.itsgem-if.dropping .itsgem-if-grid{border-color:${BRAND};box-shadow:inset 0 0 0 1px ${BRAND};background:#171326;}
.itsgem-if-menu{position:fixed;z-index:10000;box-sizing:border-box;min-width:170px;padding:4px;border:1px solid rgba(${BRAND_RGB},.5);border-radius:8px;background:#12101a;box-shadow:0 12px 32px rgba(0,0,0,.6);font:11.5px/1.3 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;}
.itsgem-if-ask{position:fixed;z-index:10001;box-sizing:border-box;width:300px;padding:12px;border:1px solid #c98a2b;border-radius:8px;background:#12101a;box-shadow:0 12px 32px rgba(0,0,0,.6);color:#e2dcf5;font:11.5px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;}
.itsgem-if-ask b{display:block;margin-bottom:6px;color:#ffd9a0;font-size:12.5px;}
.itsgem-if-ask .itsgem-if-browse-foot{padding:10px 0 0;border-top:0;}
.itsgem-if-pick{display:block;margin:12px auto 0;height:26px;padding:0 12px;border:1px solid ${BRAND};border-radius:6px;background:rgba(${BRAND_RGB},.16);color:#f2ecff;font:700 10.5px/24px "Segoe UI",sans-serif;letter-spacing:.03em;cursor:pointer;}
.itsgem-if-pick:hover{background:${BRAND};color:${BRAND_INK};}
.itsgem-if-cover{position:absolute;left:6px;right:6px;top:28px;bottom:6px;z-index:2;box-sizing:border-box;display:flex;flex-direction:column;justify-content:safe center;gap:6px;padding:12px 14px;overflow:auto;border:1px solid ${BRAND};border-radius:6px;background:#0d0b14;color:#e2dcf5;font-size:11.5px;line-height:1.45;text-align:center;}
.itsgem-if-cover[hidden]{display:none;}
.itsgem-if-cover b{color:#f7f3ff;font-size:12.5px;}
.itsgem-if-note{grid-column:1/-1;align-self:center;padding:22px 12px;color:#9a90b0;font-size:11.5px;line-height:1.45;text-align:center;}
.itsgem-if-more{grid-column:1/-1;height:24px;border:1px dashed #2c2a38;border-radius:5px;background:transparent;color:#9a90b0;font:600 10.5px/22px "Segoe UI",sans-serif;cursor:pointer;}
.itsgem-if-more:hover{color:#fff;border-color:${BRAND};}
.itsgem-if-browse{position:fixed;z-index:10000;box-sizing:border-box;display:flex;flex-direction:column;width:270px;max-height:330px;border:1px solid rgba(${BRAND_RGB},.5);border-radius:8px;background:#12101a;box-shadow:0 12px 32px rgba(0,0,0,.6);color:#e2dcf5;font:11.5px/1.3 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;}
.itsgem-if-browse-path{flex:none;padding:8px 10px 6px;border-bottom:1px solid #211d2e;color:#8a82a0;font:10.5px ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;overflow-wrap:anywhere;}
.itsgem-if-browse-path b{color:#f0ebfb;font-weight:600;}
.itsgem-if-browse-list{flex:1 1 auto;min-height:40px;overflow-y:auto;padding:4px;scrollbar-width:thin;}
.itsgem-if-browse-row{display:block;width:100%;box-sizing:border-box;padding:5px 8px;border:0;border-radius:5px;background:transparent;color:#e2dcf5;font:inherit;text-align:left;cursor:pointer;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}
.itsgem-if-browse-row:hover{background:rgba(${BRAND_RGB},.16);color:#fff;}
.itsgem-if-browse-row.up{color:#9a90b0;}
.itsgem-if-browse-row.bring{margin-top:4px;border-top:1px solid #211d2e;border-radius:0 0 5px 5px;color:${BRAND};font-weight:600;}
.itsgem-if-browse-none{padding:8px;color:#8a82a0;}
.itsgem-if-browse-foot{flex:none;display:flex;gap:6px;justify-content:flex-end;padding:6px 8px;border-top:1px solid #211d2e;}
.itsgem-if-browse-foot .use{background:${BRAND};border-color:${BRAND};color:${BRAND_INK};}
`;
    document.head.appendChild(style);
}

function el(tag, className = "", text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
}

// ---------- 节点上的 widget 读写 ----------
function widgetOf(node, name) {
    return node.widgets?.find((widget) => widget?.name === name) ?? null;
}

function settings(node) {
    const value = (name, fallback) => widgetOf(node, name)?.value ?? fallback;
    return {
        source: String(value("source", "input")),
        folder: String(value("folder", "") ?? "").trim(),
        subfolders: Boolean(value("subfolders", false)),
        sort: String(value("sort", "name")),
        pictures: String(value("pictures", "") ?? ""),
        run: String(value("run", "all in one run")),
        position: Number(value("position", 1)) || 1,
        afterRun: String(value("after_run", "next")),
        atTheEnd: String(value("at_the_end", "stop the run")),
    };
}

// 照 LiteGraph 自己的 widget 那样写值：设值 -> 跑 widget 回调 -> 通知节点
function commitWidgetValue(node, widget, value, canvas) {
    const previous = widget.value;
    widget.value = value;
    widget.callback?.(value, canvas, node);
    node.onWidgetChanged?.(widget.name, value, previous, widget);
}

function write(node, name, value) {
    const widget = widgetOf(node, name);
    if (!widget || widget.value === value) return false;
    commitWidgetValue(node, widget, value, app.canvas);
    return true;
}

function isLinked(node, name) {
    return Boolean(node.inputs?.some((input) => (input?.widget?.name ?? input?.name) === name && input.link != null));
}

// ---------- 勾选/计数/寻址（内联自 shared/image_folder.mjs） ----------
// "picked" 为 null 表示「全选」（widget 里是空文本，之后新加的图也算选中），
// 否则是勾选的文件名数组。
function parsePicked(text) {
    const raw = String(text ?? "").trim();
    if (!raw) return null;
    try {
        const value = JSON.parse(raw);
        if (!Array.isArray(value) || !value.every((item) => typeof item === "string")) return null;
        return [...new Set(value.map((item) => item.replaceAll("\\", "/")))];
    } catch {
        return null;
    }
}

function pickedNames(picked, names) {
    if (picked === null) return [...names];
    const wanted = new Set(picked);
    return names.filter((name) => wanted.has(name));
}

function isPicked(picked, name) {
    return picked === null || picked.includes(name);
}

// 全选的名单等于全部时，又回落成「全选」
function settlePicked(list, names) {
    const kept = pickedNames(list, names);
    return kept.length === names.length && names.length > 0 ? null : kept;
}

function togglePick(picked, names, name) {
    if (!names.includes(name)) return picked;
    const current = pickedNames(picked, names);
    const next = current.includes(name) ? current.filter((item) => item !== name) : [...current, name];
    return settlePicked(next, names);
}

// Shift 单击：从上一张到这张，整段一起勾/取消
function pickRange(picked, names, from, to, on) {
    const a = names.indexOf(from);
    const b = names.indexOf(to);
    if (a < 0 || b < 0) return picked;
    const span = new Set(names.slice(Math.min(a, b), Math.max(a, b) + 1));
    const current = pickedNames(picked, names);
    const next = on ? [...new Set([...current, ...span])] : current.filter((name) => !span.has(name));
    return settlePicked(next, names);
}

function serializePicked(picked, names) {
    if (picked === null) return "";
    return JSON.stringify(pickedNames(picked, names));
}

function summary(picked, names) {
    const total = names.length;
    const count = pickedNames(picked, names).length;
    if (!total) return { count: 0, total: 0, text: T("没有图片", "no pictures") };
    if (picked === null || count === total) return { count: total, total, text: T(`全部 ${total} 张已选`, `all ${total} picked`) };
    if (!count) return { count: 0, total, text: T(`${total} 张全未选`, `none of ${total} picked`) };
    return { count, total, text: T(`已选 ${count} / ${total}`, `${count} of ${total} picked`) };
}

// 某张图在勾选名单里的序号（从 1 开始），没勾就是 0
function placeOf(picked, names, name) {
    return pickedNames(picked, names).indexOf(name) + 1;
}

// One per run：下一次运行要加载的那张，超界且「到底就停」时为 null
function nameAt(picked, names, position, atTheEnd) {
    const list = pickedNames(picked, names);
    if (!list.length) return null;
    const place = Math.max(1, Math.round(Number(position) || 1));
    if (place <= list.length) return list[place - 1];
    return atTheEnd === "start over" ? list[(place - 1) % list.length] : null;
}

// 入队一次后 Picture 前进到哪
function nextPosition(position, count, afterRun, atTheEnd = "stop the run", roll = Math.random()) {
    const place = Math.max(1, Math.round(Number(position) || 1));
    if (count < 1) return place;
    if (afterRun === "random") return Math.min(count, Math.floor(roll * count) + 1);
    if (afterRun !== "next") return place;
    if (atTheEnd === "start over") return (place % count) + 1;
    return Math.min(place + 1, count + 1);
}

// 瓦片上方的计数条；One per run 时还说下一次是哪张
function countLine(picked, names, { run, position, atTheEnd } = {}) {
    const line = summary(picked, names);
    if (run !== "one per run" || line.count < 1) return { text: line.text, warn: line.total > 0 && line.count === 0 };
    const place = Math.max(1, Math.round(Number(position) || 1));
    const head = line.count === line.total ? T(`全部 ${line.total} 张`, `all ${line.total}`) : T(`${line.count} / ${line.total} 张`, `${line.count} of ${line.total}`);
    if (place <= line.count) return { text: `${head} \u00b7 ${T(`下一张 ${place}`, `next ${place}`)}`, warn: false };
    if (atTheEnd === "start over") return { text: `${head} \u00b7 ${T(`下一张 ${((place - 1) % line.count) + 1}`, `next ${((place - 1) % line.count) + 1}`)}`, warn: false };
    return { text: `${head} \u00b7 ${T("全部跑完", "all done")}`, warn: true };
}

function parentOf(folder) {
    const parts = String(folder ?? "").split("/").filter(Boolean);
    parts.pop();
    return parts.join("/");
}

function childOf(folder, name) {
    return [...String(folder ?? "").split("/").filter(Boolean), name].join("/");
}

function query(params) {
    return Object.entries(params)
        .filter(([, value]) => value !== undefined && value !== null && value !== "")
        .map(([key, value]) => `${encodeURIComponent(key)}=${encodeURIComponent(String(value))}`)
        .join("&");
}

function listAddress({ source, folder, subfolders, sort }) {
    return `${ROUTE_BASE}/list?${query({ source, folder, subfolders: subfolders ? 1 : 0, sort })}`;
}

function foldersAddress({ source, folder }) {
    return `${ROUTE_BASE}/folders?${query({ source, folder })}`;
}

function thumbAddress({ source, folder, name, v, size = 160 }) {
    return `${ROUTE_BASE}/thumb?${query({ source, folder, name, size, v })}`;
}

// ---------- 前端通用小件（内联自 shared/index.mjs 等） ----------
// 别家节点包也在补同一批原型方法，永远 chain，不要直接赋值。
function chainCallback(proto, name, fn) {
    const prior = proto[name];
    proto[name] = function (...args) {
        const result = prior?.apply(this, args);
        fn.apply(this, args);
        return result;
    };
}

function chainHandler(proto, name, fn) {
    const prior = proto[name];
    proto[name] = function (...args) {
        const result = prior?.apply(this, args);
        const handled = fn.apply(this, args);
        return result || handled;
    };
}

// DOM widget 的 width 永远跟随节点宽度（别让前端种下的固定宽度盖过节点）
function keepDomWidgetWidthAuto(widget) {
    if (!widget) return widget;
    try {
        Object.defineProperty(widget, "width", {
            get() { return undefined; },
            set() { /* 忽略种下的固定宽度 */ },
            configurable: true,
        });
    } catch (e) { /* 冻结的 widget 保持原行为 */ }
    return widget;
}

// 面板吃掉节点多出来的高度（复刻 AusBoss panel_layout.mjs 的关键机制）：
// 前端 _arrangeWidgets 三分支——有 computeSize 就固定高度，有 computeLayoutSize
// 才参与剩余高度分配，所以必须删掉 computeSize。
function fillNodeHeight(widget, { minWidth = 0, minHeight = 0, minNodeSize, exactMinWidth = false } = {}) {
    if (!widget) return widget;
    delete widget.computeSize;
    widget.computeLayoutSize = () => ({
        minWidth: Math.max(0, minWidth - (exactMinWidth ? WIDTH_PADDING : 0)),
        minHeight,
    });
    widget.options ??= {};
    if (minNodeSize) widget.options.minNodeSize = minNodeSize;
    return widget;
}

// DOM 面板在 click 阶段写 widget 值，撤销/脏标记的快照却在 mouseup 阶段抓，
// 一次手势只提醒一次，工作流才会变「已修改」并进入撤销历史。
let changeTimer = null;
function notifyChange() {
    if (changeTimer) clearTimeout(changeTimer);
    changeTimer = setTimeout(() => {
        changeTimer = null;
        try {
            const workflow =
                app?.extensionManager?.workflow?.activeWorkflow ??
                app?.workflowManager?.activeWorkflow;
            const tracker = workflow?.changeTracker;
            if (!tracker) return;
            if (typeof tracker.captureCanvasState === "function") tracker.captureCanvasState();
            else tracker.checkState?.();
        } catch (e) { /* 尽力而为：写值本身已经成功 */ }
    }, 120);
}

function showToast({ detail, severity = "info", summary = "ItsGemTools", life = 5000 } = {}) {
    try {
        const toast = app?.extensionManager?.toast;
        if (typeof toast?.add === "function") {
            toast.add({ severity, summary, detail, life });
            return;
        }
    } catch (e) { /* toast 不可用就退回控制台 */ }
    const line = `[ItsGemTools] ${detail}`;
    if (severity === "error" || severity === "warn") console.warn(line);
    else console.log(line);
}

// ---------- 瓦片 ----------
function cellFor(state, picture) {
    const cell = el("button", "itsgem-if-cell");
    cell.type = "button";
    cell.dataset.name = picture.name;
    cell.title = picture.name;
    const img = el("img");
    img.alt = "";
    img.decoding = "async";
    img.draggable = false;
    img.dataset.src = api.apiURL(thumbAddress({ ...state.shown, name: picture.name, v: picture.v }));
    cell.append(img, el("span", "itsgem-if-tick", "✓"), el("span", "itsgem-if-num"));
    state.watcher?.observe(cell);
    return cell;
}

// 重建瓦片：文件夹或里面的东西变了
function rebuild(state) {
    state.watcher?.disconnect();
    state.grid.replaceChildren();
    state.cells = new Map();
    const hint = (text, offer) => {
        const note = el("div", "itsgem-if-note", text);
        if (offer) {
            const pick = el("button", "itsgem-if-pick", T("从电脑复制一个文件夹", "Copy a folder from your computer"));
            pick.type = "button";
            pick.title = T("打开文件选择框。该文件夹里的图片会被复制进 ComfyUI 的 input 目录。",
                "Opens your file explorer. The folder's pictures are copied into ComfyUI's input folder.");
            pick.addEventListener("click", () => chooseFolder(state));
            note.append(pick);
        }
        state.grid.append(note);
    };
    if (state.error) {
        hint(state.error, state.outside);
    } else if (!state.list.length) {
        hint(state.busy
            ? T("正在读取文件夹…", "Looking in the folder...")
            : T("这里还没有图片。把文件夹或图片拖到节点上，或把文件夹带进来：",
                "No pictures here yet. Drop a folder or pictures on the node, or bring a folder in:"),
            !state.busy);
    } else {
        for (const picture of state.list.slice(0, state.limit)) {
            const cell = cellFor(state, picture);
            state.cells.set(picture.name, cell);
            state.grid.append(cell);
        }
        const rest = state.list.length - state.limit;
        if (rest > 0) {
            const more = el("button", "itsgem-if-more",
                T(`再显示 ${Math.min(rest, SHOWN_STEP)} 张（还有 ${rest} 张）`, `Show ${Math.min(rest, SHOWN_STEP)} more of ${rest}`));
            more.type = "button";
            more.addEventListener("click", () => { state.limit += SHOWN_STEP; rebuild(state); });
            state.grid.append(more);
        }
    }
    paint(state);
}

// 把勾、序号、白圈、计数条对齐到当前状态
function paint(state) {
    const now = settings(state.node);
    const picked = parsePicked(now.pictures);
    const names = state.names;
    const next = now.run === "one per run" ? nameAt(picked, names, now.position, now.atTheEnd) : null;
    const order = new Map(pickedNames(picked, names).map((name, index) => [name, index + 1]));
    for (const [name, cell] of state.cells) {
        const place = order.get(name) ?? 0;
        cell.classList.toggle("picked", place > 0);
        cell.classList.toggle("current", name === next);
        cell.classList.toggle("done", state.done.has(name));
        cell.lastChild.textContent = place ? String(place) : "";
    }
    const line = state.copying
        ? { text: T(`正在复制 ${state.copying.done} / ${state.copying.total}`, `copying ${state.copying.done} of ${state.copying.total}`), warn: false }
        : state.error ? { text: state.outside ? T("不在 ComfyUI 目录内", "outside ComfyUI") : T("找不到文件夹", "folder not found"), warn: true }
            : state.busy ? { text: T("读取中…", "looking..."), warn: false } : countLine(picked, names, now);
    state.chip.textContent = line.text;
    state.chip.classList.toggle("warn", line.warn);
    state.chip.title = [
        line.text,
        state.full ? T("这个文件夹只列出了前 5000 张。", "Only the first 5000 pictures of this folder are listed.") : "",
        state.loadedLine,
    ].filter(Boolean).join("\n");
    state.add.disabled = false;
    state.add.title = T("从电脑添加图片或整个文件夹，会被复制进 ComfyUI 的 input 目录。也可以直接拖到节点上。",
        "Add pictures or a whole folder from your computer. They are copied into ComfyUI's input folder. You can also drop them on the node.");
}

// 问服务器这个文件夹里有什么
async function refresh(state) {
    if (!state.alive) return;
    const now = settings(state.node);
    const token = ++state.token;
    state.busy = true;
    const shown = { source: now.source, folder: now.folder, subfolders: now.subfolders, sort: now.sort };
    try {
        const response = await api.fetchApi(listAddress(shown));
        const data = await response.json().catch(() => ({}));
        if (token !== state.token || !state.alive) return;
        if (response.ok) {
            state.list = Array.isArray(data.pictures) ? data.pictures : [];
            state.full = Boolean(data.full);
            state.error = "";
            state.outside = false;
            // 粘贴的整条路径落在 ComfyUI 目录内：按它命名的文件夹显示
            if (typeof data.folder === "string" && (data.folder !== shown.folder || data.source !== shown.source)) {
                shown.source = data.source; shown.folder = data.folder;
                state.key = `${shown.source}|${shown.folder}|${shown.subfolders}|${shown.sort}`;
                state.restoring = true;
                write(state.node, "source", shown.source); write(state.node, "folder", shown.folder);
                state.restoring = false;
            }
        } else {
            state.list = [];
            state.full = false;
            state.error = String(data?.error ?? T("这个文件夹读不了。", "This folder cannot be read."))
                .replace(/^Image Folder \(ItsGemTools\):?\s*/, "");
            state.outside = /inside ComfyUI/.test(state.error);
            if (state.outside) {
                state.error = T("这个文件夹在 ComfyUI 目录之外，节点没法就地读取。把它拖到节点上，或在这里选择它，图片会被复制进 ComfyUI 的 input 目录。",
                    "This folder is outside ComfyUI, so the node cannot read it where it is. Drop the folder on the node, or choose it here, and its pictures are copied into ComfyUI's input folder.");
            }
        }
    } catch {
        if (token !== state.token || !state.alive) return;
        state.list = [];
        state.error = T("文件夹没加载出来，点刷新按钮再试一次。", "The folder did not load. Press the refresh button to try again.");
    }
    state.busy = false;
    state.shown = shown;
    state.names = state.list.map((picture) => picture.name);
    state.limit = SHOWN_STEP;
    rebuild(state);
}

function scheduleRefresh(state, delay = 160) {
    clearTimeout(state.timer);
    state.timer = setTimeout(() => refresh(state), delay);
}

function setPicked(state, picked) {
    if (write(state.node, "pictures", serializePicked(picked, state.names))) notifyChange();
    paint(state);
}

// ---------- 文件夹正在进来时节点上说的话 ----------
function cover(state) {
    const copying = state.copying;
    const choose = [
        T("在文件选择框里选你的文件夹", "Choose your folder in the file explorer"),
        T("它里面的图片会被复制进 ComfyUI 的 input 目录。", "All its pictures are copied into your ComfyUI input folder."),
        T('浏览器可能先问一句「要上传文件到这个站点吗？」——那就是这次复制，站点就是你自己的 ComfyUI。',
            'Your browser may first ask "Upload files to this site?". That is this copy, and the site is your ComfyUI.'),
    ];
    const lines = copying
        ? [T(`正在复制 ${copying.done} / ${copying.total} 张图片`, `Copying ${copying.done} of ${copying.total} pictures`),
           T(`来自「${copying.name}」，落到 ComfyUI 的 input 目录（input/${copying.target}）。`,
             `from "${copying.name}" into your ComfyUI input folder (input/${copying.target}).`)]
        : state.choosing ? choose : null;
    state.cover.hidden = !lines;
    if (!lines) return;
    [...state.cover.children].forEach((line, index) => {
        line.textContent = lines[index] ?? "";
        line.hidden = !lines[index];
    });
}

function endChoosing(state) {
    state.choosing?.abort();
    state.choosing = null;
    cover(state);
}

// 打开文件选择框选一个文件夹，并在节点上说清楚接下来会发生什么
function chooseFolder(state) {
    endChoosing(state);
    const abort = new AbortController();
    state.choosing = abort;
    const opened = performance.now();
    const stop = () => { if (state.choosing === abort) endChoosing(state); };
    state.pickFolder.addEventListener("cancel", stop, { signal: abort.signal });
    // 不报「取消」的浏览器：文件框或站点提问打开时页面收不到点击/按键，
    // 所以下一个点击就意味着两者都关了。
    const later = () => { if (performance.now() - opened > 700) stop(); };
    window.addEventListener("pointerdown", later, { capture: true, signal: abort.signal });
    window.addEventListener("keydown", later, { capture: true, signal: abort.signal });
    cover(state);
    state.pickFolder.click();
}

// ---------- 添加图片 ----------
const isPicture = (file) => String(file?.type ?? "").startsWith("image/") || PICTURE.test(String(file?.name ?? ""));

async function upload(file, subfolder, overwrite) {
    const body = new FormData();
    body.append("image", file, file.name);
    body.append("type", "input");
    if (subfolder) body.append("subfolder", subfolder);
    if (overwrite) body.append("overwrite", "true");
    try {
        const response = await api.fetchApi("/upload/image", { method: "POST", body });
        if (!response.ok) return null;
        const data = await response.json();
        return data?.name ? String(data.name) : null;
    } catch {
        return null;
    }
}

// 零散图片进节点当前显示的文件夹
async function addFiles(state, files) {
    const now = settings(state.node);
    const images = [...(files ?? [])].filter(isPicture);
    if (!images.length) return false;
    if (now.source !== "input") {
        showToast({ severity: "warn", detail: T("只能往 input 目录加图片，先把 From 设成 input。", "Pictures can only be added to the input folder. Set From to input first.") });
        return true;
    }
    const added = [];
    let failed = 0;
    for (const file of images) {
        const name = await upload(file, now.folder, false);
        if (name) added.push(name); else failed += 1;
    }
    await refresh(state);
    // 手工勾选过的名单把新图也带上：它们刚被放进来就是要用的
    const picked = parsePicked(settings(state.node).pictures);
    if (picked !== null && added.length) setPicked(state, [...picked, ...added.filter((name) => state.names.includes(name))]);
    const where = now.folder ? `input/${now.folder}` : T("input 目录", "the input folder");
    if (added.length) showToast({ detail: T(`已添加 ${added.length} 张到 ${where}。`, `Added ${added.length} picture${added.length === 1 ? "" : "s"} to ${where}.`) });
    if (failed) showToast({ severity: "warn", detail: T(`${failed} 个文件没能添加。`, `${failed} file${failed === 1 ? "" : "s"} could not be added.`) });
    return true;
}

function folderName(name) {
    return String(name ?? "").replace(/[\\/:*?"<>|]+/g, "_").replace(/^\.+/, "").trim() || "pictures";
}

function sizeText(bytes) {
    if (bytes >= 1024 ** 3) return `${(bytes / 1024 ** 3).toFixed(1)} GB`;
    return `${Math.max(1, Math.round(bytes / 1024 ** 2))} MB`;
}

// 很大的文件夹：先说多大，让人自己决定
let openAsk = null;

function closeAsk(answer = false) {
    const ask = openAsk;
    openAsk = null;
    if (!ask) return;
    ask.abort.abort();
    ask.root.remove();
    ask.done(answer);
}

function askBig(state, name, pictures) {
    const bytes = pictures.reduce((sum, item) => sum + (Number(item.file?.size) || 0), 0);
    if (pictures.length <= BIG_COUNT && bytes <= BIG_BYTES) return Promise.resolve(true);
    closeAsk(false);
    return new Promise((done) => {
        const root = el("div", "itsgem-if-ask");
        const many = pictures.length.toLocaleString("en-US");
        root.append(el("b", "", T(`把 ${many} 张图片复制进 ComfyUI 的 input 目录？`, `Copy ${many} pictures into your ComfyUI input folder?`)));
        root.append(el("div", "", T(`「${name}」里有 ${many} 张图，共 ${sizeText(bytes)}。复制会再占一份磁盘空间，也要等一会儿。`,
            `"${name}" holds ${many} pictures, ${sizeText(bytes)}. The copies take that much disk space again, and it can take a while.`)));
        if (pictures.length > COPY_LIMIT) {
            root.append(el("div", "", T(`只会复制前 ${COPY_LIMIT.toLocaleString("en-US")} 张。`, `Only the first ${COPY_LIMIT.toLocaleString("en-US")} are copied.`)));
        }
        const foot = el("div", "itsgem-if-browse-foot");
        const cancel = el("button", "itsgem-if-btn", T("取消", "Cancel"));
        const go = el("button", "itsgem-if-btn use", T("复制", "Copy them"));
        cancel.type = "button"; go.type = "button";
        foot.append(cancel, go);
        root.append(foot);
        const abort = new AbortController();
        openAsk = { root, abort, done };
        cancel.addEventListener("click", () => closeAsk(false));
        go.addEventListener("click", () => closeAsk(true));
        document.body.append(root);
        const box = state.grid.getBoundingClientRect();
        root.style.left = `${Math.max(8, Math.min(window.innerWidth - 308, box.left + (box.width - 300) / 2))}px`;
        root.style.top = `${Math.max(8, Math.min(window.innerHeight - 190, box.top + 16))}px`;
        window.addEventListener("keydown", (event) => { if (event.key === "Escape") closeAsk(false); }, { capture: true, signal: abort.signal });
        root.addEventListener("wheel", (event) => event.stopPropagation(), { signal: abort.signal });
    });
}

// 整个文件夹从电脑进来：图片复制到 input/<它的名字>，里面的子文件夹也一起，
// 然后节点切过去。同一个文件夹再复制一次是覆盖，不会堆重复文件。
async function copyFolder(state, name, pictures) {
    if (!(await askBig(state, name, pictures)) || !state.alive) return;
    const target = folderName(name);
    const queue = pictures.slice(0, COPY_LIMIT);
    state.copying = { done: 0, total: queue.length, name, target };
    paint(state);
    cover(state);
    let copied = 0;
    const worker = async () => {
        for (;;) {
            const next = queue.shift();
            if (!next || !state.alive) return;
            if (await upload(next.file, next.folder ? `${target}/${next.folder}` : target, true)) copied += 1;
            state.copying.done += 1;
            paint(state);
            cover(state);
        }
    };
    await Promise.all([worker(), worker(), worker()]);
    const total = state.copying.total;
    state.copying = null;
    cover(state);
    if (!state.alive) return;
    const moved = write(state.node, "source", "input") | write(state.node, "folder", target);
    if (moved) notifyChange(); else await refresh(state);
    paint(state);
    showToast({ detail: T(`已把「${name}」的 ${copied} 张图复制进 ComfyUI 的 input 目录（input/${target}）。`,
        `Copied ${copied} picture${copied === 1 ? "" : "s"} from "${name}" into your ComfyUI input folder (input/${target}).`) });
    if (copied < total) showToast({ severity: "warn", detail: T(`${total - copied} 张没能复制。`, `${total - copied} could not be copied.`) });
    if (pictures.length > COPY_LIMIT) showToast({ severity: "warn", detail: T(`只复制了前 ${COPY_LIMIT} 张。`, `Only the first ${COPY_LIMIT} pictures were copied.`) });
}

// 拖进来的东西要在拖拽还在进行时读：浏览器只在那时给散落的文件夹
function dropped(event) {
    const folders = [];
    const files = [];
    for (const item of [...(event?.dataTransfer?.items ?? [])]) {
        if (item.kind !== "file") continue;
        const entry = item.webkitGetAsEntry?.();
        if (entry?.isDirectory) folders.push(entry);
        else { const file = item.getAsFile?.(); if (file) files.push(file); }
    }
    if (!folders.length && !files.length) files.push(...(event?.dataTransfer?.files ?? []));
    return { folders, files };
}

async function picturesIn(entry, below = "", found = []) {
    const reader = entry.createReader();
    for (;;) {
        const batch = await new Promise((resolve) => reader.readEntries(resolve, () => resolve([])));
        if (!batch.length) break;
        for (const child of batch) {
            if (found.length > COPY_LIMIT) return found;
            if (child.name.startsWith(".")) continue;
            if (child.isDirectory) await picturesIn(child, below ? `${below}/${child.name}` : child.name, found);
            else if (PICTURE.test(child.name)) {
                const file = await new Promise((resolve) => child.file(resolve, () => resolve(null)));
                if (file) found.push({ file, folder: below });
            }
        }
    }
    return found;
}

async function takeDrop(state, things) {
    for (const entry of things.folders) {
        const pictures = await picturesIn(entry);
        if (!pictures.length) {
            showToast({ severity: "warn", detail: T(`「${entry.name}」里没有图片。`, `No pictures in "${entry.name}".`) });
            continue;
        }
        await copyFolder(state, entry.name, pictures);
    }
    if (!things.folders.length) await addFiles(state, things.files);
}

// ---------- Add：图片，或一整个文件夹 ----------
let openMenu = null;

function closeMenu() {
    openMenu?.abort.abort();
    openMenu?.root.remove();
    openMenu = null;
}

function addMenu(state, anchor) {
    closeMenu();
    const root = el("div", "itsgem-if-menu");
    const abort = new AbortController();
    openMenu = { root, abort };
    const row = (text, title, run) => {
        const button = el("button", "itsgem-if-browse-row", text);
        button.type = "button"; button.title = title;
        button.addEventListener("click", () => { closeMenu(); run(); });
        root.append(button);
    };
    row(T("图片…", "Pictures..."),
        T("选图片，会被复制进节点当前显示的文件夹。", "Choose pictures. They are copied into the folder the node shows."),
        () => state.pickFiles.click());
    row(T("整个文件夹…", "A whole folder..."),
        T("选一个文件夹，里面的图片会被复制进 ComfyUI 的 input 目录（以该文件夹名建子目录）。",
            "Choose a folder. Its pictures are copied into ComfyUI's input folder under the folder's name."),
        () => chooseFolder(state));
    document.body.append(root);
    const box = anchor.getBoundingClientRect();
    root.style.left = `${Math.max(8, Math.min(window.innerWidth - 180, box.left))}px`;
    root.style.top = `${Math.max(8, Math.min(window.innerHeight - 80, box.bottom + 4))}px`;
    window.addEventListener("pointerdown", (event) => { if (!root.contains(event.target)) closeMenu(); }, { capture: true, signal: abort.signal });
    window.addEventListener("keydown", (event) => { if (event.key === "Escape") closeMenu(); }, { capture: true, signal: abort.signal });
}

// ---------- Browse：ComfyUI input / output 目录里的子文件夹 ----------
let openBrowse = null;

function closeBrowse() {
    openBrowse?.abort.abort();
    openBrowse?.root.remove();
    openBrowse = null;
}

async function browse(state, anchor) {
    closeBrowse();
    const now = settings(state.node);
    const root = el("div", "itsgem-if-browse");
    const path = el("div", "itsgem-if-browse-path");
    const list = el("div", "itsgem-if-browse-list");
    const foot = el("div", "itsgem-if-browse-foot");
    const cancel = el("button", "itsgem-if-btn", T("取消", "Cancel"));
    const use = el("button", "itsgem-if-btn use", T("用这个文件夹", "Use this folder"));
    cancel.type = "button"; use.type = "button";
    foot.append(cancel, use);
    root.append(path, list, foot);
    const abort = new AbortController();
    openBrowse = { root, abort };
    let at = now.folder;

    const show = async (folder) => {
        let data = null;
        try {
            const response = await api.fetchApi(foldersAddress({ source: now.source, folder }));
            if (response.ok) data = await response.json();
        } catch { /* 下面按空列表显示 */ }
        if (openBrowse?.root !== root) return;
        if (!data && folder) return show("");          // 文件夹没了：退回顶层
        at = data?.folder ?? "";
        path.replaceChildren(document.createTextNode(`${now.source} / `),
            el("b", "", at || T("（就是这个目录）", "(the folder itself)")));
        list.replaceChildren();
        if (at) {
            const up = el("button", "itsgem-if-browse-row up", T("↑ 上一层", "↑ up"));
            up.type = "button";
            up.addEventListener("click", () => show(parentOf(at)));
            list.append(up);
        }
        const folders = Array.isArray(data?.folders) ? data.folders : [];
        for (const name of folders) {
            const row = el("button", "itsgem-if-browse-row", name);
            row.type = "button"; row.title = name;
            row.addEventListener("click", () => show(childOf(at, name)));
            list.append(row);
        }
        if (!folders.length) list.append(el("div", "itsgem-if-browse-none", T("这个文件夹里没有子文件夹。", "No folders inside this one.")));
        // 还没进 ComfyUI 的文件夹：交给文件选择框搬进来
        const bring = el("button", "itsgem-if-browse-row bring", T("+ 从电脑复制一个文件夹…", "+ Copy a folder from your computer..."));
        bring.type = "button";
        bring.title = T("打开文件选择框。该文件夹里的图片会被复制进 ComfyUI 的 input 目录。",
            "Opens your file explorer. The folder's pictures are copied into ComfyUI's input folder.");
        bring.addEventListener("click", () => { closeBrowse(); chooseFolder(state); });
        list.append(bring);
    };

    use.addEventListener("click", () => {
        if (write(state.node, "folder", at)) notifyChange();
        closeBrowse();
    });
    cancel.addEventListener("click", closeBrowse);
    document.body.append(root);
    const box = (anchor ?? state.root).getBoundingClientRect();
    root.style.left = `${Math.max(8, Math.min(window.innerWidth - 278, box.left))}px`;
    root.style.top = `${Math.max(8, Math.min(window.innerHeight - 338, box.bottom + 4))}px`;
    window.addEventListener("pointerdown", (event) => { if (!root.contains(event.target)) closeBrowse(); }, { capture: true, signal: abort.signal });
    window.addEventListener("keydown", (event) => { if (event.key === "Escape") closeBrowse(); }, { capture: true, signal: abort.signal });
    root.addEventListener("wheel", (event) => event.stopPropagation(), { signal: abort.signal });
    await show(at);
}

// ---------- 面板 ----------
function buildPanel(node) {
    if (node.__itsgemFolder) return node.__itsgemFolder;
    ensureCss();
    const root = el("div", "itsgem-if");
    const bar = el("div", "itsgem-if-bar");
    const chip = el("span", "itsgem-if-chip", "");
    const all = el("button", "itsgem-if-btn", T("全选", "All"));
    const none = el("button", "itsgem-if-btn", T("清空", "None"));
    const add = el("button", "itsgem-if-btn", T("添加", "Add"));
    const reload = el("button", "itsgem-if-btn", "↻");
    all.title = T("勾选文件夹里的每一张，之后再加进来的也算。", "Pick every picture in the folder. Pictures added later are in too.");
    none.title = T("全部取消，再点你想用的那些。", "Pick none, then click the ones you want.");
    reload.title = T("重新读取文件夹。", "Look in the folder again.");
    for (const button of [all, none, add, reload]) button.type = "button";
    const picker = el("input");
    picker.type = "file"; picker.multiple = true; picker.accept = "image/*"; picker.style.display = "none";
    const folderPicker = el("input");
    folderPicker.type = "file"; folderPicker.webkitdirectory = true; folderPicker.multiple = true; folderPicker.style.display = "none";
    bar.append(chip, el("span", "itsgem-if-gap"), all, none, add, reload);
    const grid = el("div", "itsgem-if-grid");
    const note = el("div", "itsgem-if-cover");
    note.hidden = true;
    note.append(el("b"), el("div"), el("div"));
    root.append(bar, grid, note, picker, folderPicker);

    const widget = node.addDOMWidget(PANEL_WIDGET, "itsgem_folder", root, {
        serialize: false,
        hideOnZoom: false,
        getMinHeight: () => MIN_HEIGHT,
    });
    // 相册本身不是一个值：勾选名单存在 `pictures` widget 里
    widget.serialize = false;
    keepDomWidgetWidthAuto(widget);
    // 相册是文件夹的一扇窗：吃掉节点多余的高度
    fillNodeHeight(widget, { minWidth: MIN_WIDTH, minHeight: MIN_HEIGHT, minNodeSize: [MIN_WIDTH, 90], exactMinWidth: true });

    const abort = new AbortController();
    const state = node.__itsgemFolder = {
        node, root, widget, chip, grid, add, abort, cover: note, choosing: null,
        alive: true, list: [], names: [], cells: new Map(), done: new Set(), limit: SHOWN_STEP,
        shown: { source: "input", folder: "", subfolders: false, sort: "name" },
        token: 0, timer: null, busy: true, error: "", outside: false, full: false, loadedLine: "", key: "",
        copying: null, pickFiles: picker, pickFolder: folderPicker, dropTimer: null, restoring: false,
    };
    // 瓦片滚到附近才去取它的图
    state.watcher = typeof IntersectionObserver === "function"
        ? new IntersectionObserver((entries) => {
            for (const entry of entries) {
                if (!entry.isIntersecting) continue;
                const img = entry.target.firstChild;
                if (img?.dataset?.src) { img.src = img.dataset.src; delete img.dataset.src; }
                state.watcher.unobserve(entry.target);
            }
        }, { root: grid, rootMargin: "160px" })
        : null;

    const signal = abort.signal;
    grid.addEventListener("click", (event) => {
        const cell = event.target.closest?.(".itsgem-if-cell");
        if (!cell) return;
        const name = cell.dataset.name;
        const picked = parsePicked(settings(node).pictures);
        const on = !isPicked(picked, name);
        const next = event.shiftKey && state.last && state.last !== name
            ? pickRange(picked, state.names, state.last, name, on)
            : togglePick(picked, state.names, name);
        state.last = name;
        setPicked(state, next);
    }, { signal });
    // One per run：双击某张，把它设成下一次要加载的
    grid.addEventListener("dblclick", (event) => {
        const cell = event.target.closest?.(".itsgem-if-cell");
        if (!cell || settings(node).run !== "one per run") return;
        const name = cell.dataset.name;
        let picked = parsePicked(settings(node).pictures);
        if (!isPicked(picked, name)) { picked = togglePick(picked, state.names, name); setPicked(state, picked); }
        if (write(node, "position", placeOf(picked, state.names, name))) notifyChange();
        paint(state);
    }, { signal });
    all.addEventListener("click", () => setPicked(state, null), { signal });
    none.addEventListener("click", () => setPicked(state, []), { signal });
    reload.addEventListener("click", () => refresh(state), { signal });
    add.addEventListener("click", () => addMenu(state, add), { signal });
    picker.addEventListener("change", async () => { await addFiles(state, picker.files); picker.value = ""; }, { signal });
    // 浏览器自己的文件夹选择框：每个文件都带着它在那个文件夹下的路径
    folderPicker.addEventListener("change", async () => {
        endChoosing(state);
        const groups = new Map();
        for (const file of [...folderPicker.files]) {
            if (!PICTURE.test(file.name) || file.name.startsWith(".")) continue;
            const parts = String(file.webkitRelativePath || file.name).split("/");
            const top = parts.length > 1 ? parts[0] : "pictures";
            if (!groups.has(top)) groups.set(top, []);
            groups.get(top).push({ file, folder: parts.slice(1, -1).join("/") });
        }
        folderPicker.value = "";
        if (!groups.size) { showToast({ severity: "warn", detail: T("那个文件夹里没有图片。", "No pictures in that folder.") }); return; }
        for (const [name, pictures] of groups) await copyFolder(state, name, pictures);
    }, { signal });

    state.browse = (anchor) => browse(state, anchor);
    state.refresh = () => refresh(state);

    // 某个设置变了：重读文件夹，或者只挪一下标记
    for (const name of WATCHED) {
        const target = widgetOf(node, name);
        if (!target) continue;
        chainCallback(target, "callback", () => {
            if (!state.alive) return;
            const now = settings(node);
            const key = `${now.source}|${now.folder}|${now.subfolders}|${now.sort}`;
            if (key === state.key) { paint(state); return; }
            // 换了文件夹就是换了一批名字：在旧文件夹里做的勾选到了新地方没意义
            const moved = state.key && (now.source !== state.shown.source || now.folder !== state.shown.folder || now.subfolders !== state.shown.subfolders);
            state.key = key;
            if (moved && !state.restoring && now.pictures) write(node, "pictures", "");
            state.busy = true;
            paint(state);
            scheduleRefresh(state);
        });
    }

    // One per run：入队一次 Picture 前进一格；只跑别的节点的运行不动它
    const position = widgetOf(node, "position");
    if (position) {
        chainCallback(position, "afterQueued", (options) => {
            if (!state.alive || options?.isPartialExecution) return;
            const now = settings(node);
            if (now.run !== "one per run" || isLinked(node, "position")) return;
            const count = pickedNames(parsePicked(now.pictures), state.names).length;
            const next = nextPosition(now.position, count, now.afterRun, now.atTheEnd);
            if (next !== now.position) { position.value = next; position.callback?.(next); }
        });
    }

    // 拖到节点上的文件夹或图片会被复制进来
    const carriesFiles = (event) => [...(event?.dataTransfer?.types ?? [])].includes("Files");
    chainHandler(node, "onDragOver", (event) => {
        if (!carriesFiles(event)) return false;
        root.classList.add("dropping");
        clearTimeout(state.dropTimer);
        state.dropTimer = setTimeout(() => root.classList.remove("dropping"), 260);
        return true;
    });
    chainHandler(node, "onDragDrop", (event) => {
        root.classList.remove("dropping");
        const things = dropped(event);
        if (!things.folders.length && !things.files.some(isPicture)) return false;
        takeDrop(state, things);
        return true;
    });

    const now = settings(node);
    state.key = `${now.source}|${now.folder}|${now.subfolders}|${now.sort}`;
    rebuild(state);
    scheduleRefresh(state, 60);

    // 新节点开出来先留三行瓦片的高度；复制出来的节点在入图前就有尺寸，
    // 那种情况由下面的 onConfigure 记下来。
    chainCallback(node, "onAdded", () => {
        if (state.sized) return;
        state.sized = true;
        const floor = Number(node.computeSize?.()?.[1]);
        if (Number.isFinite(floor) && node.size?.[1] <= floor + 1) {
            node.setSize?.([Math.max(node.size[0], 340), floor + 96]);
        }
    });

    // 语言切换：重建瓦片与文案
    window.addEventListener("itsgem-lang-change", () => {
        if (!state.alive) return;
        all.textContent = T("全选", "All");
        none.textContent = T("清空", "None");
        add.textContent = T("添加", "Add");
        state.loadedLine = "";
        rebuild(state);
    }, { signal });
    return state;
}

app.registerExtension({
    name: "ItsGemTools.ImageFolder",
    beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData?.name !== NODE_NAME) return;
        chainCallback(nodeType.prototype, "onNodeCreated", function () {
            buildPanel(this);
        });
        chainCallback(nodeType.prototype, "onConfigure", function (info) {
            const state = buildPanel(this);
            // 自带尺寸的节点（含最小尺寸）保留自己的尺寸
            if (info?.size) state.sized = true;
            // 打开的工作流带着自己的文件夹和自己的勾选
            state.restoring = true;
            queueMicrotask(() => {
                const now = settings(this);
                state.key = `${now.source}|${now.folder}|${now.subfolders}|${now.sort}`;
                state.restoring = false;
                scheduleRefresh(state, 30);
            });
        });
        chainCallback(nodeType.prototype, "onExecuted", function (output) {
            const state = this.__itsgemFolder;
            const note = output?.itsgemtools_image_folder?.[0];
            if (!state?.alive || !note) return;
            for (const name of note.names ?? []) state.done.add(name);
            const loaded = note.loaded ?? [];
            state.loadedLine = loaded.length === 1
                ? T(`上一次运行加载了第 ${loaded[0]} 张，共 ${note.count} 张。`, `The last run loaded picture ${loaded[0]} of ${note.count}.`)
                : T(`上一次运行加载了 ${loaded.length} 张，共 ${note.count} 张。`, `The last run loaded ${loaded.length} of ${note.count} pictures.`);
            paint(state);
        });
        chainCallback(nodeType.prototype, "onRemoved", function () {
            const state = this.__itsgemFolder;
            if (!state) return;
            state.alive = false;
            clearTimeout(state.timer);
            state.watcher?.disconnect();
            state.abort.abort();
            state.choosing?.abort();
            clearTimeout(state.dropTimer);
            if (openBrowse) closeBrowse();
            if (openMenu) closeMenu();
            if (openAsk?.root && state.grid.isConnected === false) closeAsk(false);
            this.__itsgemFolder = null;
        });
    },
    nodeCreated(node) {
        if (node?.comfyClass === NODE_NAME || node?.type === NODE_NAME) buildPanel(node);
    },
});
