# Changelog

本包所有重要变更记录于此。格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循[语义化版本](https://semver.org/lang/zh-CN/)。

## [Unreleased]

### 新增

- **Label（节点键 `Label (ItsGemTools)`）**：浮动文字标签节点，复刻 rgthree Label 的全部视觉功能——
  - 文案 = 节点标题，支持 `\n` 字面序列换行；可调字号、字体、字色、对齐（左 / 中 / 右）、背景色、内边距、圆角、角度（度）。
  - 颜色支持 8 位 HEX 半透明（如 `#FFFFFF88` = 半透明白，背景与文字均支持）。
  - **双击进入就地文案编辑**：双击节点弹出输入框，改完按回车或点击节点外区域即生效，Esc 取消。
  - 纯前端虚拟节点，零依赖、不参与执行；支持右键 Pin 固定后点击穿透。
  - **命名 SEO**：`Label (ItsGemTools)` 带品牌后缀唯一不撞名（不同于 rgthree 的 `Label (rgthree)`），搜索框搜 "label" 时前缀命中且字母序靠前。

### 修复

- **Label 双击编辑框定位（适配前端 1.55）**
  - **根因**：前端 1.55 重写了坐标系统——画布元素上 `ds` / `convertCanvasToOffset` / `convertOffsetToCanvas` 等坐标 API 全部移除，且画布被非等比拉伸显示，任何换算公式都会把编辑框算到屏幕外（实测 top 偏移 1700px+，表现为"双击没反应"）。
  - **修复**：编辑框改为**直接锚定双击点**（用户双击哪里，编辑框就出现在哪里，绝对可见）；坐标矩阵 / 比例换算降级为无双击点场景的兜底，并加 NaN 守卫保证编辑框必然出现。
  - **配套**：三层双击命中（浏览器 `dblclick` 捕获 / 自研 `pointerdown` 双击判定 / `processMouseDown` 拦截），双击后回车或点击节点外区域提交生效、Esc 取消。

- **Set / Get Image Variable 不连线时取不到图（本次核心修复）**
  - **根因**：ComfyUI 只执行"输出可达节点集"——执行列表从 `OUTPUT_NODE`（如 Preview / SaveImage）出发沿输入依赖反向构建。`Set Image Variable` 的输出若不连线到任何输出节点，会被调度器判定为孤立节点而**完全不执行**，导致全局变量表永远为空，`Get Image Variable` 只能输出 1×1 占位黑图。
  - **修复**：`SetImageVar` 标记为 `OUTPUT_NODE`（执行根）。Set 现在**必然执行**，且在同一批就绪节点中**优先于 Get** 执行（ComfyUI 对输出节点优先调度）。无需连线、无需等待、无依赖，行为与"直接连线"完全一致。
  - **附带**：Set / Get 均实现 `IS_CHANGED`（递增计数器），禁止 ComfyUI 输出缓存——每次运行都重新执行 / 重新读取变量，重启后首次运行即正确。
  - **验证**：真实运行环境实测，`Load → Set(key) → Get(key) → Save` 无连线工作流输出 3840×4608 真图，全链路通过。

### 变更

- README 更新 Set / Get Image Variable 机制说明（OUTPUT_NODE 执行根）。

## [1.0.0] - 2026-10-23

### 新增

- 12 个剪映 / Photoshop 复刻节点（首版发布，纯算法零模型）：
  - **Adjust Color & Effect**：18 滑块一把调完（色彩 4 + 明度 7 + 光感 1 + 效果 5），色温 / 色调 / 饱和度 / 自然饱和度带彩色渐变轨道。
  - **Skin Beauty**：8 项皮肤管理（磨皮 / 美白 / 匀肤 / 丰盈 / 祛斑祛痘 / 祛法令纹 / 祛黑眼圈 / 清晰），内置肤色检测，可选 SAM3 面部 MASK 精准分区 + 边缘羽化。
  - **Layer Composite**：两张 PNG 叠加，6 种混合模式，RGBA 自动识别，等比居中或拉伸。
  - **maskfeather**：按有符号欧氏距离对掩码双向羽化，输出透明 PNG。
  - **Set Image Alpha / Set Masked Alpha**：整图或仅遮罩区域设定 Alpha。
  - **Before/After Preview**：原图与调色结果并排实时对比。
  - **Image Compare A/B**：SLIDE 滑动逐帧对比，A/B 一键切换。
  - **Set / Get Image Variable**：全局图像变量，跨节点传图免拉长线。
- 前端体验：中英双语界面（跟随 ComfyUI 语言自动切换）、淡紫主题、1 秒防抖自动刷新预览。
- 发布：GitHub 公开仓库 + ComfyUI Manager 收录 PR（[Comfy-Org/ComfyUI-Manager#3409](https://github.com/Comfy-Org/ComfyUI-Manager/pull/3409)）。
