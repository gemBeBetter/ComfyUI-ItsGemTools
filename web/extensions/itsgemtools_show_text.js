import { app } from "../../../scripts/app.js";
import { ComfyWidgets } from "../../../scripts/widgets.js";

// ============================================================
// ItsGemTools Show Text：在节点本体上展示上游 STRING（报告/识别文本等）。
//  - 后端 GemShowText 把文本放进 ui.text，本扩展在 onExecuted 时填入只读多行 widget
//  - 复刻 pysssss ShowText 的渲染做法，不依赖 pysssss / AusBoss 第三方包
//  - 文本同时由后端 result 原样向下游透传
// ============================================================

const SHOW_TYPE = "GemShowText";

app.registerExtension({
  name: "ItsGemTools.ShowText",
  async beforeRegisterNodeDef(nodeType, nodeData) {
    if (nodeData?.name !== SHOW_TYPE) return;

    function populate(text) {
      if (this.widgets) {
        // 旧前端可能存在被转成 widget 的隐藏输入槽，保留它，清掉其后自渲染的 widget
        const isConvertedWidget = +!!this.inputs?.[0]?.widget;
        for (let i = isConvertedWidget; i < this.widgets.length; i++) {
          this.widgets[i].onRemove?.();
        }
        this.widgets.length = isConvertedWidget;
      }

      const v = [...(text || [])];
      if (!v[0]) v.shift();
      for (let list of v) {
        // 强制按数组处理（个别版本里 list 可能不是数组）
        if (!(list instanceof Array)) list = [list];
        for (const l of list) {
          const w = ComfyWidgets["STRING"](
            this,
            "text_" + (this.widgets?.length ?? 0),
            ["STRING", { multiline: true }],
            app,
          ).widget;
          w.inputEl.readOnly = true;
          w.inputEl.style.opacity = 0.6;
          w.value = l;
        }
      }

      requestAnimationFrame(() => {
        const sz = this.computeSize();
        if (sz[0] < this.size[0]) sz[0] = this.size[0];
        if (sz[1] < this.size[1]) sz[1] = this.size[1];
        this.onResize?.(sz);
        app.graph.setDirtyCanvas(true, false);
      });
    }

    // 节点执行完成：后端 ui.text 送到这里，渲染成只读 widget
    const onExecuted = nodeType.prototype.onExecuted;
    nodeType.prototype.onExecuted = function (message) {
      onExecuted?.apply(this, arguments);
      populate.call(this, message.text);
    };

    // 保存原始 widgets_values，供加载历史工作流时回显
    const VALUES = Symbol();
    const configure = nodeType.prototype.configure;
    nodeType.prototype.configure = function () {
      this[VALUES] = arguments[0]?.widgets_values;
      return configure?.apply(this, arguments);
    };

    const onConfigure = nodeType.prototype.onConfigure;
    nodeType.prototype.onConfigure = function () {
      onConfigure?.apply(this, arguments);
      const widgets_values = this[VALUES];
      if (widgets_values?.length) {
        requestAnimationFrame(() => {
          populate.call(
            this,
            widgets_values.slice(+(widgets_values.length > 1 && this.inputs?.[0]?.widget)),
          );
        });
      }
    };
  },
});
