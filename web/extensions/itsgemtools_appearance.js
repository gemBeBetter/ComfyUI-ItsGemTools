import { app } from "../../../scripts/app.js";

// ============================================================
// ItsGemTools 外观：淡紫色主题（参考 AusBoss Appearance 的实现方式）
//  - 标题栏/主体着淡紫系（node.color / node.bgcolor）
//  - 节点边框淡紫（onDrawForeground 描边，Nodes 2.0 下走 DOM 时自动还原主题）
//  - 用轮询补装，不依赖 nodeCreated 钩子（新版前端钩子对已有节点不触发）
// 仅作用于本包 7 个节点，不影响其他节点。
// ============================================================

const NODE_TYPES = new Set([
    "maskfeather",
    "SetImageAlpha",
    "SetMaskedAlpha",
    "AdjustColor",
    "AdjustEffect",
    "Adjust",
    "SkinBeauty",
    "LayerComposite",
    "BeforeAfterPreview",
    "ItsGemImageCompare",
    "SetImageVar",
    "GetImageVar",
]);

const TITLE_COLOR = "#8a63d2"; // 标题栏：淡紫
const BODY_COLOR = "#241f38";  // 主体：深紫黑
const BORDER_COLOR = "#c4a6ff"; // 边框：淡紫

function installSkin(node) {
    if (node.__itsgemtools_skin) return;
    node.__itsgemtools_skin = true;

    // 1) 着色（参考 AusBoss applyScheme：赋值而不是 delete，保持 Vue 渲染的响应性）
    node.color = TITLE_COLOR;
    node.bgcolor = BODY_COLOR;

    // 2) 淡紫边框：在节点局部坐标画一个比节点略大的圆角矩形描边
    const orig = typeof node.onDrawForeground === "function" ? node.onDrawForeground : null;
    node.onDrawForeground = function (ctx, canvas) {
        try {
            // 折叠（Collapse）时跳过自定义描边：前端折叠只保留标题栏，
            // 若仍按 size 画整框会残留一条未收起的框线（与官方节点折叠行为对齐）。
            if (this.collapsed || this.flags?.collapsed) {
                return orig ? orig.call(this, ctx, canvas) : undefined;
            }
            const s = this.size || [0, 0];
            if (s[0] > 0 && s[1] > 0) {
                ctx.save();
                ctx.strokeStyle = BORDER_COLOR;
                ctx.lineWidth = 2;
                ctx.globalAlpha = 0.95;
                ctx.beginPath();
                if (typeof ctx.roundRect === "function") ctx.roundRect(-2, -2, s[0] + 4, s[1] + 4, 10);
                else ctx.rect(-2, -2, s[0] + 4, s[1] + 4);
                ctx.stroke();
                ctx.restore();
            }
        } catch (e) { /* 忽略 */ }
        return orig ? orig.call(this, ctx, canvas) : undefined;
    };
}

// 轮询补装：覆盖工作流加载、节点添加、任意创建路径
setInterval(() => {
    const g = app && app.graph;
    if (!g) return;
    const nodes = (typeof g.getNodes === "function" ? g.getNodes() : g._nodes) || [];
    for (const n of nodes) {
        try {
            if (NODE_TYPES.has(n.type) && !n.__itsgemtools_skin) installSkin(n);
        } catch (e) { /* 忽略 */ }
    }
}, 400);

app.registerExtension({
    name: "ItsGemTools.Appearance",
    nodeCreated(node) {
        if (node && NODE_TYPES.has(node.type)) installSkin(node);
    },
});
