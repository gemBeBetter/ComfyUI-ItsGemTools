import { app } from "../../../scripts/app.js";

// ============================================================
// ItsGemTools 双语（中/英）扩展
// - 节点标题：跟随 ComfyUI 界面语言自动切换，也可在节点右键菜单手动覆盖
// - 按钮等界面文字：通过 window.__ITSGEM_I18N.t(zh, en) 提供，
//   语言变化时派发 "itsgem-lang-change" 事件供其它扩展刷新
// ============================================================

const NODE_NAMES = {
    "maskfeather": { zh: "羽化遮罩", en: "maskfeather" },
    "SetImageAlpha": { zh: "设置图像透明度", en: "Set Image Alpha" },
    "SetMaskedAlpha": { zh: "蒙版透明度合成", en: "Set Masked Alpha" },
    "AdjustColor": { zh: "调色", en: "Adjust Color" },
    "AdjustEffect": { zh: "效果调整", en: "Adjust Effect" },
    "Adjust": { zh: "调色与效果", en: "Adjust Color & Effect" },
    "SkinBeauty": { zh: "美颜（皮肤管理）", en: "Skin Beauty" },
    "LayerComposite": { zh: "图层合成", en: "Layer Composite" },
    "BeforeAfterPreview": { zh: "前后对比预览", en: "Before/After Preview" },
    "ItsGemImageCompare": { zh: "图像对比 A/B", en: "Image Compare A/B" },
    "SetImageVar": { zh: "设置图像变量", en: "Set Image Variable" },
    "GetImageVar": { zh: "读取图像变量", en: "Get Image Variable" },
};

function detectLang() {
    // 1) 界面根元素 lang：ComfyUI 语言设置（Settings → Language）直接反映于此，最可靠
    try {
        const l = document.documentElement.lang;
        if (typeof l === "string" && l) return /^zh/i.test(l) ? "zh" : "en";
    } catch (e) { /* ignore */ }
    // 2) localStorage 兼容（个别版本设置会写这里）
    try {
        const v = window.localStorage?.getItem?.("Comfy_Locale");
        if (v) return /^zh/i.test(v) ? "zh" : "en";
    } catch (e) { /* ignore */ }
    // 3) 浏览器语言兜底
    const v = (typeof navigator !== "undefined" && navigator.language) || "en";
    return /^zh/i.test(String(v)) ? "zh" : "en";
}

let currentLang = detectLang();

window.__ITSGEM_I18N = {
    lang: () => currentLang,
    t: (zh, en) => (currentLang === "zh" ? zh : en),
};

function applyTitle(node) {
    const pair = NODE_NAMES[node?.type];
    if (!pair) return;
    const lang = node.__itsgem_lang || currentLang;
    const title = lang === "zh" ? pair.zh : pair.en;
    if (node.__itsgem_defaultTitle === undefined) node.__itsgem_defaultTitle = node.title;
    // 跟随环境且用户已改成自定义标题（非两套名称）→ 不覆盖
    if (!node.__itsgem_lang &&
        node.title !== node.__itsgem_defaultTitle &&
        node.title !== pair.zh && node.title !== pair.en) {
        return;
    }
    if (node.title === title) return;
    node.title = title;
    node.setDirtyCanvas?.(true, true);
}

function setNodeLang(node, lang) {
    node.__itsgem_lang = lang; // undefined = 跟随环境
    applyTitle(node);
}

function addLangMenu(node) {
    if (!node || typeof node.addMenuOptions !== "function" || node.__itsgem_i18n_menu) return;
    node.__itsgem_i18n_menu = true;
    try {
        node.addMenuOptions((options) => {
            options.push({
                content: "语言 / Language",
                submenu: {
                    title: "语言 / Language",
                    options: [
                        { content: "跟随环境 / Auto", callback: () => setNodeLang(node, undefined) },
                        { content: "中文", callback: () => setNodeLang(node, "zh") },
                        { content: "English", callback: () => setNodeLang(node, "en") },
                    ],
                },
            });
            return options;
        });
    } catch (e) { /* ignore */ }
}

function applyAll() {
    const graph = app.graph;
    if (!graph?._nodes) return;
    for (const node of graph._nodes) {
        if (NODE_NAMES[node?.type]) {
            applyTitle(node);
            addLangMenu(node);
        }
    }
}

let lastLang = currentLang;
function pollLang() {
    const now = detectLang();
    if (now !== lastLang) {
        lastLang = now;
        currentLang = now;
        applyAll();
        window.dispatchEvent(new CustomEvent("itsgem-lang-change", { detail: { lang: now } }));
    }
}
setInterval(pollLang, 700);

app.registerExtension({
    name: "ItsGemTools.i18n",
    async setup() {
        currentLang = detectLang();
        lastLang = currentLang;
        setTimeout(applyAll, 400);
        setInterval(applyAll, 2000); // 兜底：新加载/新建节点补装标题与菜单
    },
    nodeCreated(node) {
        if (NODE_NAMES[node?.type]) {
            addLangMenu(node);
            applyTitle(node);
        }
    },
});
