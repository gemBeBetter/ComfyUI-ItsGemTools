import { app } from "../../../scripts/app.js";

// ============================================================
// ItsGemTools Label：浮动标签节点（复刻 rgthree Label 的功能）
//  - 纯前端虚拟节点（无后端类），不参与执行，仅画布装饰
//  - 文本 = 节点 Title，支持 "\n" 换行；可调字体/字号/颜色/对齐/背景/内边距/圆角/角度
//  - 背景与文字颜色支持 8 位 HEX（如 #FFFFFF88 = 半透明白）
//  - 双击进入就地文案编辑：回车或点击节点外区域提交生效，Esc 取消
//  - 命名 "Label (ItsGemTools)"：带品牌后缀唯一不撞名，搜索 "label" 时前缀命中且字母序靠前
// ============================================================

const LABEL_TYPE = "Label (ItsGemTools)";

// ------------------------------------------------------------
// 通用就地文案编辑（textarea 覆盖节点位置；回车/失焦提交、Esc 取消）
// 兼容两类节点：真实 ItsGemLabel 实例与新版前端 Vue 模式下的包装节点。
// ------------------------------------------------------------
function startGenericLabelEdit(node, canvas) {
    if (node._editor) return;
    if (!canvas?.canvas?.parentNode) {
        console.warn("[ItsGemTools.Label] edit: no canvas parent", canvas);
        return;
    }
    const editor = document.createElement("textarea");
    const container = canvas.canvas.parentNode;
    const rect = canvas.canvas.getBoundingClientRect();
    const scale = rect.width / (canvas.canvas.width || 1);
    const props = node.properties || {};
    const fontSize = Math.max(Number(props.fontSize) || 0, 12);
    const fontFamily = props.fontFamily || "Arial";
    const w = Math.max(node.size[0] * scale + 4, 80);
    const h = Math.max(node.size[1] * scale + 4, 24);

    editor.value = String(node.title ?? "").replace(/\\n/g, "\n");
    editor.wrap = "off";
    // 坐标换算：优先 convertCanvasToOffset（画布坐标→元素内像素偏移）；旧前端无此方法时兜底反向方法
    let px;
    if (typeof canvas.convertCanvasToOffset === "function") {
        px = canvas.convertCanvasToOffset([node.pos[0], node.pos[1]]);
    } else {
        px = canvas.convertOffsetToCanvas([node.pos[0], node.pos[1]]);
    }
    // 编辑框固定白底黑字：任何节点配色下都清晰可读，避免"白底白字"看不见
    editor.style.cssText = [
        "position:fixed",
        `left:${rect.left + px[0] * scale - 2}px`,
        `top:${rect.top + px[1] * scale - 2}px`,
        `width:${w}px`,
        `height:${h}px`,
        `font:${fontSize * scale}px ${fontFamily}`,
        "color:#111",
        "background:#fff",
        "border:1px dashed #8a63d2",
        "border-radius:4px",
        "outline:none",
        "padding:2px 4px",
        "z-index:9999",
        "resize:none",
        "box-sizing:border-box",
        "box-shadow:0 2px 10px rgba(0,0,0,0.4)",
    ].join(";");
    const commit = () => {
        if (!node._editor) return;
        node._editor = null;
        const next = editor.value.replace(/\n/g, "\\n").replace(/\n*$/, "");
        editor.remove();
        if (next !== node.title) {
            node.title = next;
            if (node.graph) {
                node.graph.setDirtyCanvas(true, true);
                node.graph.change?.();
            }
        }
    };
    const cancel = () => {
        if (!node._editor) return;
        node._editor = null;
        editor.remove();
    };
    editor.addEventListener("keydown", (e) => {
        if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            commit();
        } else if (e.key === "Escape") {
            e.preventDefault();
            cancel();
        }
    });
    editor.addEventListener("blur", commit);
    container.appendChild(editor);
    node._editor = editor;
    editor.focus();
    editor.select();
}

// 命中入口：真实实例走原型方法；Vue 包装等无 startInlineEdit 的节点走通用实现
function startItsGemLabelEdit(node, canvas) {
    if (typeof node.startInlineEdit === "function") {
        node.startInlineEdit(canvas);
        return;
    }
    startGenericLabelEdit(node, canvas);
}

// 画布坐标命中 Label：新前端（Vue 渲染模式）下节点不保证是 ItsGemLabel 实例，
// 因此不用 instanceof / getNodeOnPos（两者都可能失效），改为 type + 包围盒直接命中。
function findItsGemLabelAt(canvas, canvasX, canvasY) {
    if (!canvas?.graph) return null;
    const nodes = canvas.graph._nodes || [];
    for (let i = nodes.length - 1; i >= 0; i--) {
        const n = nodes[i];
        if (!n || !isGemLabelNode(n)) continue;
        if (canvasX >= n.pos[0] && canvasX <= n.pos[0] + (n.size[0] || 0) &&
            canvasY >= n.pos[1] && canvasY <= n.pos[1] + (n.size[1] || 0)) {
            return n;
        }
    }
    return null;
}

class ItsGemLabel extends LGraphNode {
    constructor(title) {
        super(title ?? "Label (ItsGemTools)");
        this.comfyClass = LABEL_TYPE;
        this.resizable = false;
        this.serialize_widgets = false;
        this.flags = this.flags || {};
        this.flags.allow_interaction = true;
        this.properties = {
            fontSize: 12,
            fontFamily: "Arial",
            fontColor: "#ffffff",
            textAlign: "left",
            backgroundColor: "transparent",
            padding: 0,
            borderRadius: 0,
            angle: 0,
        };
        this.color = "#fff0";
        this.bgcolor = "#fff0";
        this._editor = null;
    }

    // ---------- 绘制（功能等效 rgthree Label，MIT 参考，自行实现） ----------
    draw(ctx) {
        // 节点框的透明由 drawNode 拦截层在画框前设置（color/bgcolor = "transparent"），此处不再改写
        this.flags.allow_interaction = !this.flags.pinned;
        ctx.save();

        const fontSize = Math.max(Number(this.properties.fontSize) || 0, 1);
        const fontFamily = this.properties.fontFamily || "Arial";
        const fontColor = this.properties.fontColor || "#ffffff";
        const backgroundColor = this.properties.backgroundColor || "";
        const padding = Number(this.properties.padding) || 0;
        const textAlign = this.properties.textAlign || "left";
        const angle = parseInt(String(this.properties.angle || 0), 10) || 0;

        const raw = String(this.title ?? "").replace(/\\n/g, "\n").replace(/\n*$/, "");
        const lines = raw.split("\n");
        ctx.font = `${fontSize}px ${fontFamily}`;
        const maxWidth = Math.max(...lines.map((s) => ctx.measureText(s).width));
        const width = maxWidth + padding * 2;
        const height = fontSize * lines.length + padding * 2;
        this.size[0] = width;
        this.size[1] = height;

        if (angle) {
            const cx = width / 2;
            const cy = height / 2;
            ctx.translate(cx, cy);
            ctx.rotate((angle * Math.PI) / 180);
            ctx.translate(-cx, -cy);
        }
        if (backgroundColor) {
            const radius = Number(this.properties.borderRadius) || 0;
            ctx.beginPath();
            ctx.roundRect(0, 0, width, height, radius);
            ctx.fillStyle = backgroundColor;
            ctx.fill();
        }

        // 同色保护：字体色与背景色相同时自动反色，避免"白底白字"看不清
        let textColor = fontColor;
        if (backgroundColor && fontColor) {
            const norm = (c) => String(c).replace(/^#/, "").slice(0, 6).toLowerCase();
            if (norm(fontColor) === norm(backgroundColor)) {
                const r = parseInt(norm(fontColor).slice(0, 2), 16) || 0;
                const g = parseInt(norm(fontColor).slice(2, 4), 16) || 0;
                const b = parseInt(norm(fontColor).slice(4, 6), 16) || 0;
                textColor = (r + g + b) / 3 > 128 ? "#000000" : "#ffffff";
            }
        }

        ctx.textBaseline = "top";
        ctx.fillStyle = textColor;
        let textX = padding;
        if (textAlign === "center") {
            ctx.textAlign = "center";
            textX = width / 2;
        } else if (textAlign === "right") {
            ctx.textAlign = "right";
            textX = width - padding;
        } else {
            ctx.textAlign = "left";
        }
        let y = padding;
        for (const line of lines) {
            ctx.fillText(line || " ", textX, y);
            y += fontSize;
        }
        ctx.restore();
    }

    // ---------- 双击：进入就地文案编辑 ----------
    onDblClick(event, pos, canvas) {
        this.startInlineEdit(canvas || LGraphCanvas.active_canvas);
    }

    startInlineEdit(canvas) {
        startGenericLabelEdit(this, canvas);
    }

    // ---------- 属性面板（ComfyUI Properties 自动读取 @ 静态定义） ----------
    onShowCustomPanelInfo(panel) {
        panel?.querySelector?.('div.property[data-property="Mode"]')?.remove?.();
        panel?.querySelector?.('div.property[data-property="Color"]')?.remove?.();
    }

    getHelp() {
        return `
      <p>在画布上放置一段浮动文字标签，用于给工作流分区/做说明。纯前端节点，不参与执行。</p>
      <ul>
        <li><strong>文案</strong>：双击节点进入编辑，改完按回车或点击节点外区域即生效；Esc 取消。支持字面序列 \\n 换行。</li>
        <li><strong>样式</strong>：右键 → Properties 可调字号、字体、字色、对齐、背景色、内边距、圆角、角度。</li>
        <li><strong>半透明</strong>：颜色支持 8 位 HEX，如 #FFFFFF88 为半透明白，#00000066 为半透明黑。</li>
        <li><strong>Pin</strong>：右键 → Pin 可把标签固定在画布上，点击穿透；再次右键取消。</li>
      </ul>`;
    }
}

// 属性面板的类型定义（ComfyUI Properties 面板据此渲染输入控件）
ItsGemLabel["@fontSize"] = { type: "number" };
ItsGemLabel["@fontFamily"] = { type: "string" };
ItsGemLabel["@fontColor"] = { type: "string" };
ItsGemLabel["@textAlign"] = { type: "combo", values: ["left", "center", "right"] };
ItsGemLabel["@backgroundColor"] = { type: "string" };
ItsGemLabel["@padding"] = { type: "number" };
ItsGemLabel["@borderRadius"] = { type: "number" };
ItsGemLabel["@angle"] = { type: "number" };
ItsGemLabel.title = LABEL_TYPE;
ItsGemLabel.title_mode = LiteGraph.NO_TITLE;
ItsGemLabel.collapsable = false;
// SEO：归入品牌分类，节点库搜索/浏览时独立成组、排在第三方包（如 rgthree）之前
ItsGemLabel.category = "ItsGemTools";
ItsGemLabel._category = "ItsGemTools";

// 宽匹配识别 Label 节点：前端 1.55 在 Vue 渲染模式下节点的 type 可能被改为
// "frontend_only"（画布标注 "#90 frontend_only" 的来源），type 并不可靠；
// 对齐 rgthree 的做法改用 constructor / instanceof 识别，并保留 type / comfyClass 兜底。
function isGemLabelNode(n) {
    return !!n && (
        n.type === LABEL_TYPE ||
        n.comfyClass === LABEL_TYPE ||
        n.constructor === ItsGemLabel ||
        n instanceof ItsGemLabel
    );
}

// 关键：拦截节点框绘制（对齐 rgthree 的实现）。
// LiteGraph 画节点框发生在 node.draw 之前、且用 node.color/bgcolor —— 若只在 draw 内设色，
// 节点框早已用默认底色画完，backgroundColor=transparent 时仍会看到不透明的节点框。
// 因此在画框前把 color/bgcolor 设为 "transparent"，画框后再手动调用 node.draw 自绘文字/背景。
{
    const _origDrawNode = LGraphCanvas.prototype.drawNode;
    LGraphCanvas.prototype.drawNode = function (node, ctx) {
        if (isGemLabelNode(node)) {
            node.bgcolor = "transparent";
            node.color = "transparent";
            const v = _origDrawNode.apply(this, arguments);
            try {
                if (typeof node.draw === "function") node.draw(ctx);
            } catch (err) { /* 自绘失败不阻断画布 */ }
            return v;
        }
        return _origDrawNode.apply(this, arguments);
    };
}

// 兜底钩子：若前端事件流仍走 LiteGraph processMouseDown 路径，这里拦截"双击 Label"。
// 判断用 type（兼容 Vue 包装节点），命中即就地编辑并跳过默认行为。
{
    const _origProcessMouseDown = LGraphCanvas.prototype.processMouseDown;
    LGraphCanvas.prototype.processMouseDown = function (e) {
        try {
            const isDbl = this.click_count >= 2 ||
                (e && typeof e.detail === "number" && e.detail >= 2);
            if (isDbl && this.clicked_node && isGemLabelNode(this.clicked_node)) {
                startItsGemLabelEdit(this.clicked_node, this);
                return;
            }
        } catch (err) { /* 不阻断画布正常流程 */ }
        return _origProcessMouseDown.apply(this, arguments);
    };
}

// Pin（固定）后点击穿透：单击/拖拽时忽略 Label，双击仍可命中以便编辑
const _origGetNodeOnPos = LGraph.prototype.getNodeOnPos;
LGraph.prototype.getNodeOnPos = function (x, y, nodes_list) {
    if (nodes_list && nodes_list.length) {
        const isDblClick = LiteGraph.getTime() - (LGraphCanvas.active_canvas?.last_mouseclick || 0) < 300;
        if (!isDblClick) {
            nodes_list = nodes_list.filter((n) => !(isGemLabelNode(n) && n.flags?.pinned));
        }
    }
    return _origGetNodeOnPos.apply(this, [x, y, nodes_list]);
};

// 运行时初始化：注册双击监听。
// 放在 registerCustomNodes 内执行（该生命周期在前端 1.55 一定被调用；setup 可能不被调用）。
// 幂等：只初始化一次。
function initGemLabelRuntime() {
    if (window.__gemLabelInit) return;
    window.__gemLabelInit = true;
    // 双击检测第 1 层：浏览器原生 dblclick（用户手动双击时可靠；自动化合成可能间隔超标）
    // 捕获阶段先于任何前端监听器执行，命中 Label 双击即就地编辑并阻断默认面板行为。
    document.addEventListener("dblclick", (e) => {
        try {
            const canvas = app?.canvas;
            if (!canvas?.graph) return;
            const toCanvas = typeof app.clientPosToCanvasPos === "function"
                ? app.clientPosToCanvasPos([e.clientX, e.clientY])
                : null;
            const node = toCanvas
                ? findItsGemLabelAt(canvas, toCanvas[0], toCanvas[1])
                : null;
            if (node) {
                e.preventDefault();
                e.stopImmediatePropagation();
                startItsGemLabelEdit(node, canvas);
            }
        } catch (err) {
            console.error("[ItsGemTools.Label] dblclick handler:", err);
        }
    }, true);
    // 双击检测第 2 层：自研 pointerdown 双击判定（450ms 内两次 down 且位移 < 8px 视为双击）。
    // 覆盖自动化/键盘辅助场景下原生 dblclick 不触发、前端 CanvasPointer 判定超时的情形；
    // 命中后阻断事件，避免前端把这次按下当作单次点击/拖拽处理。
    let gemLastDown = null;
    document.addEventListener("pointerdown", (e) => {
        try {
            const now = performance.now();
            if (gemLastDown && now - gemLastDown.t < 450 &&
                Math.hypot(e.clientX - gemLastDown.x, e.clientY - gemLastDown.y) < 8) {
                gemLastDown = null;
                const canvas = app?.canvas;
                if (!canvas?.graph) return;
                const toCanvas = typeof app.clientPosToCanvasPos === "function"
                    ? app.clientPosToCanvasPos([e.clientX, e.clientY])
                    : null;
                const node = toCanvas
                    ? findItsGemLabelAt(canvas, toCanvas[0], toCanvas[1])
                    : null;
                if (node) {
                    e.preventDefault();
                    e.stopImmediatePropagation();
                    startItsGemLabelEdit(node, canvas);
                }
                return;
            }
            gemLastDown = { t: now, x: e.clientX, y: e.clientY };
        } catch (err) {
            console.error("[ItsGemTools.Label] pointerdown handler:", err);
        }
    }, true);
}

app.registerExtension({
    name: "ItsGemTools.Label",
    registerCustomNodes() {
        // 运行时初始化：双击监听 + 诊断探针（幂等）
        initGemLabelRuntime();
        // SEO：归入品牌分类（与 KJNodes / rgthree 同款机制），
        // 节点库分类树与搜索按分类聚合时独立成组、字母序排在第三方包之前
        ItsGemLabel.category = "ItsGemTools";
        ItsGemLabel._category = "ItsGemTools";
        LiteGraph.registerNodeType(LABEL_TYPE, ItsGemLabel);
    },
});
