import { app } from "../../../scripts/app.js";

// ============================================================
// ItsGemTools Set / Get Image Variable：执行序修正
// ------------------------------------------------------------
// 问题：Get 与 Set 之间没有连线时，ComfyUI 优先执行输出链上的节点（Get 在
// Save/处理链上，会被 ux_friendly_pick_node 优先挑中），而 Set 依赖链长、就绪晚，
// 导致本轮 Get 读到上一轮 Set 写入的旧值（"传值延迟一轮"）。
//
// 解决：在 prompt 层把同 key 的 Set 的 IMAGE 输出（槽 0）隐形链接到 Get 的
// var_ref 可选输入——画布上无任何连线，但执行器因此保证 Set 先执行、Get 后读取，
// 与"直接连线"的行为完全一致。
//
// 隐藏：Get 的 var_ref 输入口（IMAGE socket）对用户不可见——nodeCreated +
// 轮询兜底把它从画布 inputs 中移除；prompt 注入时再手动补回。
// ============================================================

function gemReadKey(node) {
    if (!node) return "";
    try {
        const w = node.widgets && node.widgets[0];
        const raw = w ? w.value : (node.widgets_values && node.widgets_values[0]);
        return String(raw == null ? "" : raw).trim();
    } catch (_) {
        return "";
    }
}

function gemInjectVarRefs(graph, p) {
    try {
        if (!graph || !p || !p.prompt) return;
        const nodes = graph._nodes || [];
        // 每个 key 取图中"最后添加"的 Set（后写入的覆盖先写入）
        const setByKey = {};
        for (const n of nodes) {
            if (n.type !== "SetImageVar") continue;
            const key = gemReadKey(n);
            if (key) setByKey[key] = String(n.id);
        }
        if (!Object.keys(setByKey).length) return;
        for (const n of nodes) {
            if (n.type !== "GetImageVar") continue;
            const key = gemReadKey(n);
            const setNodeId = setByKey[key];
            if (!setNodeId) continue;
            const gp = p.prompt[String(n.id)];
            if (!gp || !gp.inputs || gp.inputs.var_ref) continue;
            // 链接 Set 的 IMAGE 输出槽 0：只作执行序依赖，Get 忽略该值
            gp.inputs.var_ref = [setNodeId, 0];
        }
    } catch (_) { /* 注入失败不阻断运行 */ }
}

// 隐藏 Get 的 var_ref 输入口（画布干净）：从节点 inputs 移除，prompt 注入时手动补回。
// 注意：不依赖一次性标记——前端在加载工作流/重建节点时可能从 comfyClass 定义重建
// inputs（var_ref 会回来），因此每次轮询都重新检查并移除（Get 节点少，开销可忽略）。
function gemHideVarRefInput(node) {
    try {
        if (!node || node.type !== "GetImageVar" || !Array.isArray(node.inputs)) return;
        const idx = node.inputs.findIndex((i) => i.name === "var_ref");
        if (idx >= 0) node.inputs.splice(idx, 1);
    } catch (_) { /* 忽略 */ }
}

(function () {
    const origGraphToPrompt = app.graphToPrompt ? app.graphToPrompt.bind(app) : null;
    if (origGraphToPrompt) {
        app.graphToPrompt = async function (graph) {
            const p = await origGraphToPrompt(graph);
            gemInjectVarRefs(graph || this.rootGraph || app.rootGraph, p);
            return p;
        };
    }
})();

app.registerExtension({
    name: "ItsGemTools.SetGet",
    nodeCreated(node) {
        gemHideVarRefInput(node);
    },
});

// 轮询兜底：nodeCreated 对已存在节点不一定触发，补齐清理（幂等，Get 节点通常很少）
setInterval(() => {
    try {
        const g = app && (app.graph || app.rootGraph);
        const nodes = g && (typeof g.getNodes === "function" ? g.getNodes() : g._nodes) || [];
        for (const n of nodes) {
            if (n && n.type === "GetImageVar") gemHideVarRefInput(n);
        }
    } catch (_) { /* 忽略 */ }
}, 800);
