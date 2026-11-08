# ComfyUI-ItsGemTools

**剪映 / Photoshop 式调色面板 + 面部处理（美颜）自定义节点包，零 AI 模型、纯算法、实时预览。**

- **调色（Adjust）**：复现剪映「调节」与「效果」两套面板——色温 / 色调 / 饱和度 / 自然饱和度 / 亮度 / 对比度 / 高光 / 阴影 / 白色 / 黑色 / 光感 + 锐化 / 清晰 / 颗粒 / 褪色 / 暗角，共 18 个拖动条，色彩参数带**渐变色条**与白色指示线，拖动即自动刷新预览。
- **面部处理（Skin Beauty）**：复现剪映「美颜美体 → 皮肤管理」——磨皮 / 美白 / 匀肤 / 丰盈 / 祛斑祛痘 / 祛法令纹 / 祛黑眼圈 / 清晰，内置肤色检测，**只作用于皮肤**，可选 SAM3 面部 MASK 做**精准分区**，边缘自动羽化。
- 配套：图层合成（PNG 叠加 / 画中画）、掩码双向羽化、前后对比预览、Image Compare A/B 滑动对比、中英双语界面、淡紫主题。

全部为传统图像处理算法（torch 实现），**不加载任何大模型，实时出图**，适合在 4090 等任意 GPU/CPU 上流畅调参。

---

## 功能亮点

| 节点 | 对应剪映 / PS 功能 | 说明 |
|---|---|---|
| **Adjust Color & Effect** | 剪映「调节 + 效果」 / PS Camera Raw | 18 个拖动条一把调完：色彩 4 + 明度 7 + 光感 1 + 效果 5；色温/色调/饱和度/自然饱和度带彩色渐变轨道；`auto_refresh` 开启后 1 秒防抖自动刷新预览 |
| **Skin Beauty** | 剪映「美颜美体 → 皮肤管理」 | 磨皮、美白、匀肤、丰盈、祛斑祛痘、祛法令纹、祛黑眼圈、清晰；内置肤色检测默认只作用皮肤；可选 MASK 精准分区；`feather` 控制边缘羽化 |
| **Layer Composite** | PS 图层 / 剪映画中画 | 两张 PNG 叠加为一张；RGBA 透明通道自动识别；6 种混合模式；不透明度；等比居中或拉伸 |
| **maskfeather** | PS 蒙版羽化 | 按有符号欧氏距离对掩码双向羽化（向内/向外像素可分别控制），输出透明 PNG |
| **Set Image Alpha / Set Masked Alpha** | 图层透明度控制 | 整图或仅遮罩区域设定 Alpha |
| **Before/After Preview** | 剪映对比视图 | 原图与调色结果并排实时对比 |
| **Image Compare A/B** | 修图软件对比模式 | SLIDE 滑动逐帧揭示 A/B，A/B 一键切换，面板内嵌预览 |
| **Tiled Upscale / Tiled Upscale Stitch** | 分块放大（Qwen 编辑链） | 复刻 AusBoss 切块/拼回逻辑；Stitch 端新增 FFT 相位相关亚像素几何配准，消除 QwenImage 2.1 + 自训 LoRA 重绘导致的接缝偏移 / 重影；32 倍数对齐，纯 torch 零额外依赖 |
| **Color Calibrator** | 双图颜色校准 | 复刻 BlueBox `ColorCalibratorBlueBox`：自动对比两图检测变化区 → 遮罩外逐通道仿射校色 → 锚点对比度回压 → 遮罩外低频回贴（高频保留）或原图回贴；输出 image / mask / changed_ratio |
| **Image Crop By Mask** | 按遮罩自动裁切 | 复刻 KJNodes `ImageCropByMask`：按 mask 非零区外接框裁图，零 widget、与原版逐像素一致；空遮罩 / 混合尺寸 / 遮罩分辨率不符三条原版会崩的路径改为安全降级 |
| **Set / Get Image Variable** | 跨节点传递图片 | 全局图像变量：Set 写入（image 透传）、Get 按 key 读取，大型工作流免拉长线 |
| **Label** | 工作流注释 / 分区说明 | 浮动文字标签：双击就地改文案（回车或点外部生效），字号 / 字体 / 字色 / 对齐 / 背景（含 8 位 HEX 半透明）/ 内边距 / 圆角 / 角度全可调，支持 `\n` 换行 |

> **接口排序规范**：本包所有节点统一遵循——含 `image` 与 `mask` 输入时，**image 在上、mask 在下**；同时含 `foreground` / `background` 时，**foreground 在上、background 在下**。避免交叉连线，提升工作流可读性。

## 特色

- **纯算法零依赖模型**：磨皮、美白、调色全部用频率分离 / 肤色加权 / 亮度分区等传统算法实现，实时可调，不吃显存。
- **精准面部处理（可选 SAM3 分区）**：把 `SAM3_Detect` 输出的面部/皮肤 MASK 接进 `Skin Beauty` 的 `mask` 输入，即可只处理人脸区域，边缘自动羽化过渡，等效剪映「单人脸 + 皮肤管理」模式。
- **剪映式交互**：渐变色条、白色指示线、拖动条数值框分离布局、自动刷新防抖（1 秒停稳才刷）、淡紫主题外观。
- **中英双语**：节点标题与对比面板按钮跟随 ComfyUI 界面语言自动切换，也可节点右键菜单手动固定。

## 安装

### 方式一：ComfyUI Manager（已收录后）打开 ComfyUI Manager → 安装自定义节点 → 搜索 **ItsGemTools** → 安装。

### 方式二：git clone
```bash
cd ComfyUI/custom_nodes
git clone https://github.com/<你的GitHub名>/ComfyUI-ItsGemTools.git
```

### 方式三：手动
将 `ComfyUI-ItsGemTools` 文件夹放入 `ComfyUI/custom_nodes/`，确认结构：
```
ComfyUI/custom_nodes/ComfyUI-ItsGemTools/
├── __init__.py
├── nodes.py
└── web/extensions/
    ├── itsgemtools_auto_refresh.js
    ├── itsgemtools_compare.js
    ├── itsgemtools_appearance.js
    ├── itsgemtools_i18n.js
    └── itsgemtools_label.js
```

**然后重启 ComfyUI**（前端扩展需重启加载；若改过扩展文件建议浏览器 Ctrl+Shift+R 硬刷新）。

在节点搜索框搜索：`Adjust Color & Effect`、`Skin Beauty`、`Layer Composite`、`maskfeather`、`Before/After Preview`、`Image Compare A/B`、`Label`、`Color Calibrator`。

## 快速上手

### 1. 调色（剪映「调节 + 效果」）
```
Load Image → Adjust Color & Effect（色温=-10、对比度=20、清晰=15）→ Preview Image
```
拖任意参数，预览 1 秒停稳后自动刷新；色温/色调/饱和度/自然饱和度四行带渐变色条。

### 2. 美颜（剪映「皮肤管理」）
```
Load Image → Skin Beauty（磨皮=50、美白=30、祛黑眼圈=20）→ Preview Image
```
- 不加 MASK：自动检测肤色，只对皮肤区磨皮/美白。
- 加 SAM3 精准分区：
```
Load Image
 → CheckpointLoaderSimple(sam3.1_multiplex_fp16.safetensors)
 → CLIPTextEncode("face, skin.") → SAM3_Detect (MASK)
 → Skin Beauty 的 mask 输入
```

### 3. 图层合成（两张 PNG 叠一张）
```
Load Image(背景) + Load Image(透明PNG前景) → Layer Composite → Preview / Save
```
前景带 Alpha 通道自动识别；想用 MASK 当透明度就接 `foreground_mask`。

### 4. 前后对比
```
Load Image ─┬─> Adjust Color & Effect ─┬─> after ─┐
            └─> before ────────────────┴─> Before/After Preview -> Preview Image
```

## 节点详解

### Adjust Color & Effect（节点键 `Adjust`）
18 个拖动条，全部 0 基准、步进 1，中文提示悬停可见：

| 组 | 参数 |
|---|---|
| 色彩 | 色温、色调、饱和度、自然饱和度（带渐变色条） |
| 明度 | 亮度、对比度、高光、阴影、白色、黑色 |
| 光感 | 光感（剪映特色：通透感） |
| 效果 | 锐化、清晰、颗粒、褪色、暗角 |
| 开关 | auto_refresh（仅本节点自动刷新预览，不影响全局） |

旧节点 `Adjust Color` / `Adjust Effect` 保留兼容，历史工作流不受影响。

### Skin Beauty（节点键 `SkinBeauty`）
| 参数 | 效果 |
|---|---|
| 磨皮 | 频率分离平滑皮肤高频细节（瑕疵/毛孔） |
| 美白 | 肤色区提亮，高光保护不过曝 |
| 匀肤 | 肤色不均匀区域向平均肤色统一 |
| 丰盈 | 柔焦 + 中暗区高光提升，饱满光泽 |
| 祛斑祛痘 | 局部平滑细节密集区（斑点/痘印） |
| 祛法令纹 | 脸部暗部宽幅平滑 |
| 祛黑眼圈 | 眼部暗区提亮 |
| 清晰 | 局部对比度增强（通透） |
| 羽化 | 区域边缘过渡柔和度（默认 20） |
| 输入 | `image` 必填；`mask` 可选（SAM3 等任意 MASK） |

区域权重 = **肤色检测 ∩ 外部 MASK**，再整体羽化 → 等效「单人脸 + 皮肤管理」。

### Layer Composite（节点键 `LayerComposite`）
- 前景 Alpha 优先级：4 通道 RGBA → 可选 `foreground_mask` → 不透明
- 混合模式：normal / multiply / screen / overlay / soft_light / hard_light
- `opacity` 不透明度 0–100；`fit`：stretch 铺满 / contain 等比居中

### maskfeather（节点键 `maskfeather`，旧名 SAM3DistanceFeather）
对 MASK 按阈值二值化后，以有符号欧氏距离向内外双向羽化（`inner_feather_px` / `outer_feather_px`），输出 4 通道透明 PNG 与羽化后 MASK。输出端顺序：`rgba_image`（IMAGE）在上、`feathered_mask`（MASK）在下。

### Before/After Preview
`before` + `after` 两图并排（可选间隔），缩放到同一尺寸实时对比。

### Image Compare A/B（节点键 `ItsGemImageCompare`，显示名 Image Compare A/B）
面板内嵌预览：SLIDE 滑动分割线逐步揭示 B 图，A/B 一键切换；尺寸标注、状态提示；模式随工作流保存。不覆盖 ComfyUI 内置 Compare Images 节点。
- **预览区不吞滚轮**：鼠标停在预览上滚动时，事件转交画布处理，行为与把鼠标放在画布空白处完全一致——跟随设置 `Comfy.Canvas.MouseWheelScroll`（`zoom` 滚轮缩放 / `panning` 滚轮平移），Ctrl/⌘ 强制缩放，Shift 横向，缩放锚点在指针处。

### Set Image Variable / Get Image Variable（节点键 `SetImageVar` / `GetImageVar`）
全局图像变量（进程内存储），方便大型工作流跨节点传递图片、避免拉长线：
```
Set Image Variable（key=my_image，image=任意 IMAGE）→ 串在流程中
... 任意位置放 Get Image Variable（key=my_image）→ 输出刚才存入的 image
```
- Set：`key` 变量名 + 可选 `image`（不连 image 也可，仅声明变量名）；连 image 时原样透传输出。Set 标记为输出节点（OUTPUT_NODE）：ComfyUI 只执行"输出可达集"，若不标记，未连线的 Set 会被当作孤立节点跳过执行、变量永远为空
- Get：`key` 变量名，输出存入的 IMAGE；**零等待、无依赖**——Set 是执行根（OUTPUT_NODE），同一运行内必然先于 Get 执行并写入；节点已禁止输出缓存（IS_CHANGED），每次运行都重新读取变量。仅当单独运行/部分执行且 Set 未执行时，输出 1×1 占位黑图并在节点上提示，流程不中断
- 存储位于内存，重启 ComfyUI 后清空

### Label（节点键 `Label (ItsGemTools)`）
浮动文字标签，给工作流分区、写说明用；**渲染与编辑全在前端完成**（后端仅注册空实现类让后端认识类型，不参与任何执行逻辑），文本 = 节点标题：
- **改文案**：双击节点 → 就地弹出输入框，改完**按回车或点击节点外区域**立即生效，Esc 取消；标题支持 `\n` 字面序列换行
- **改样式**：右键 → Properties：字号 / 字体 / 字色 / 对齐（left / center / right）/ 背景色 / 内边距 / 圆角 / 角度（度）
- **半透明**：颜色支持 8 位 HEX——`#FFFFFF` 不透明白，`#FFFFFF88` 半透明白、`#00000066` 半透明黑（背景与文字均支持）
- **固定**：右键 → Pin 把标签钉在画布上并让点击穿透，再次右键取消
- **命名**：`Label (ItsGemTools)` 带品牌后缀，与 rgthree 等第三方 `Label (…)` 互不冲突、不会被合并覆盖；搜索框输入 `label` 即可前缀命中

### Tiled Upscale / Tiled Upscale Stitch（节点键 `GemTiledUpscale` / `GemTiledUpscaleStitch`）
复刻自 ComfyUI-AusBoss 的 `Tiled Upscale 🆎` / `Tiled Upscale Stitch 🆎`（切块 / 拼回逻辑与原版一致），并在 **Stitch 端新增 tile 级亚像素几何配准**，专治 QwenImage 2.1 + 自训 LoRA 重绘导致的接缝偏移 / 重影：
- **用法**：`Tiled Upscale (ItsGemTools)` 切 tile（megapixels / tile_megapixels / overlap / multiple / max_growth / enforce_qwen_tile_limit，32 倍数对齐）→ 采样器逐 tile 重绘 → `Tiled Upscale Stitch (ItsGemTools)` 拼回。与原 AusBoss 节点**成对替换**（stitcher 互相不兼容）。
- **对齐机制**：每个重绘 tile 与**已拼入的邻接 tile 卡通输出**做 **FFT 相位相关**（纯 torch，无 cv2 依赖），估计亚像素平移并归位后再混合（左/上邻居各测一次取峰更高者，诊断输出 `shift=(…) via left|up`）；**全分辨率估计**（长边超 2048 才降采样），避免小缩略图把大 tile 的几像素位移抹成亚像素噪声——这是大图偏移"修了又犯"的隐藏来源。
  - **为什么不拿源照片当配准参考**：Qwen 卡通化后与照片特征域差异太大，相位相关峰只有 0.02~0.03（阈值 0.10），全部 `skipped`、接缝零修正——正是重影 / 偏移的根源。参考改为卡通输出后同域对齐，峰显著提升、能真正测出相邻相对错位。
  - **配准参考用原始内容而非拼接快照**：窄带混合会让先写邻居在共享区 mask 让出（权重≈0），快照共享区内容≈0 会再次导致配准失败；配准参考单独维护"未乘 mask 的原始重绘累加"，共享区始终保留邻居完整内容。
- **可靠度保护**：低纹理（纯色 / 均匀区）或位移超过 `max_align_shift` 时自动跳过配准，退化为原版行为，绝不错误配准；边界 8px 羽化过渡防图像最外缘撕裂。
- **全带对称渐变（`blend_band=0`，默认；抑制幽灵下巴）**：幻觉是单个 tile 在共享区随机生成、相邻 tile 不会在同一位置生成同样内容。全带渐变（与 AusBoss `_ramp` 一致）让共享区两 tile 权重 0↔1 横跨整个 overlap、和恒=1，**后写 tile 权重从 0 渐升，单 tile 幻觉被相邻正常内容平均稀释**（合成场景幻觉可见度 0.70→0.48；真实 AusBoss 成图幽灵区幻觉像素=0）。配准先对齐几何，全带平均的内容一致、无错位重影；配准即使 `skipped` 也退化为 AusBoss 实测干净的行为。全带路径不做 tone match，早期"共享区 gain<1 压暗亮毛衣"的问题从根源不存在。
- **窄带独占混合（`blend_band>0`，可选）**：相邻 tile 互相看不到对方输出、Qwen 各画一遍接缝区内容，若全带平均在个别场景出现宽重影带，可让 **overlap 主体由后写 tile 独占**（单侧内容，无双影），只在紧贴边界的 `blend_band` 像素内 0↔1 渐变，重影带从 overlap 宽缩到 2×band。**代价是后写 tile 在共享区的随机幻觉会被完整保留（幽灵下巴）**，故仅在全带确实重影且无幻觉时使用。
- **窄带 mask 缝隙修复（"黑色分隔线"真正根因）**：用户多轮实测黑线（over256 版 x=932 单列纯黑、横缝 y=1160/2320 暗行）在 edge fix 后仍未消除，最终定位根因在 `_gem_ramp_edge` 权重本身：旧 fall（先写 tile 让出区）的 body 0 区紧贴共享边界，而 rise（后写 tile）渐变从 0 开始——**边界处两个 tile 的 mask 都=0，`weight=0` → `total/weight.clamp_min(1e-6)=0` 黑缝**，resample 后成单列黑线。修复后 fall 渐变带紧贴共享边界（1→0），body 0 区移到内侧，与邻居 rise（0→1）精确对接，**overlap 区权重和恒=1，无 weight=0 位置**。
- **Qwen tile 边缘黑边填充（辅助防线）**：若 Qwen 重绘 tile 本身带边缘暗边伪影（实测 1~2px 整列黑边），拼接时逐边检测 tile 边缘 band（默认 8px）内**最暗列/行**均值是否比内侧带暗超 `_GEM_TILE_EDGE_DARK`（0.098，0-1 像素域），是则用内侧内容复制填充该边。**用"边缘带整体均值"检测会因黑边过窄被稀释而漏检**（1px 黑边在 8px 带中只拉低 ~0.07 < 阈值），必须按最暗列/行判定。
- **块间色调拉齐（"格子感"叠加因素，空间衰减）**：Qwen 各 tile 独立重绘的亮度/色调本身不齐（实测 tile 间中心亮度差可达 118.6~236.2）。拼接时对左/上已拼邻居共享区逐通道最小二乘 gain+bias，**通过空间衰减场只在接缝 band 附近对齐（接缝处=1、向 tile 内部衰减到 0，reach=max(band×2,96)）**，块间亮度/色调一致又不波及 tile 内部。不做整 tile 硬套：旧实现把暗共享区拟合的 gain<1 应用到整个 tile，会把下部亮白毛衣错误压暗（实测行2 均值 -28.4、越往下越暗）。窄带混合（`blend_band>0`）时执行；`blend_band=0` 旧全带兼容路径不做，保持旧行为。
- **Qwen 上限防呆（`enforce_qwen_tile_limit`，默认开）**：Qwen-Image 2.1 官方硬上限 2048×2048（4.19MP）。安全工作点对齐 AusBoss 切块口径（`tile_mp×1.125×1.03`，tile4≈4.6MP），本开关会在单 tile 超过安全线 **4.6MP** 时自动调低 `tile_megapixels` 重规划，并在 `report` 输出里写明"已自动降为 x（单块 y MP）"；自然 1×2 宽 tile（≤4.6MP）保留、无垂直缝切脸，小图不误钳，`tile_megapixels=8` 翻倍单块仍会被钳，关闭则完全按填的参数切。
- **Qwen 防呆同时收紧 tile 宽高比（上限 2.2 → 1.6）**：输入被缩成细长竖图时，网格可能选出 2.2:1 的超扁横条 tile，Qwen 在扁条上重绘构图保持差、内容变形，且扁条低频内容把配准峰压低导致全部 `skipped`（表现为"变形"）。开启防呆时强制 tile 接近方形（如 576×1024 → 8MP 从 5×1 扁条 2112×960 变为 2×3 网格 1184×1440），方图/横图/小图不受影响；关闭开关仍按通用 2.2 切。
- **成图严格等比（零比例误差）**：成图尺寸与 `before` 对照输出**像素级严格等比**于源图（`result_multiple=1` 默认），不再被 32 对齐取整带偏——如 576×1024 → 8MP 成图 2121×3771（误差 0.009%）。`redraw`/`tiles` 仍 32 对齐、tile 布局不变，Stitch 端负责把拼好的重绘层精确重采样到严格等比成图尺寸。
- **redraw 工作层取整对齐 AusBoss（`_gem_redraw_size`）**：工作层宽高各自独立就近 half-up 到 32（复刻 AusBoss `megapixel_size`），而非比例误差优先枚举，保证 tile 网格/boxes 与 AusBoss 完全一致（如 1088×1920→8MP 得 redraw 2144×3744、tile2 起点 1728、共享区 288），让斜向手臂/手套等结构在 tile 内有足够上下文，消除下半身斜向羽化模糊。该口径只影响内部工作层，最终仍 resample 到严格等比 result，成图零比例误差不变。
- **参数**：`align_mode`（translation 默认 / off 还原版行为）、`max_align_shift`（默认 64px，zoom / 重构图漂移大的工作流可调大）、`blend_band`（0=全带渐变默认、抑制幻觉；>0=窄带独占可选，带宽 32~96）、`keep_colors`（原版颜色保持，默认 1）。
- **兼容性**：只依赖 `torch` / `numpy` / `Pillow`（ComfyUI 自带），无新增依赖。

### Color Calibrator（节点键 `GemColorCalibrator`，显示名 Color Calibrator (ItsGemTools)）
复刻 BlueBox `ColorCalibratorBlueBox`（zhoutingxi/ComfyUI-BlueBox，MIT）——**算法思路按原版实现，代码在本包内重写；原版节点零改动，两节点键名/显示名互不冲突、可同画布共存**。典型场景：`image_original` = 原图、`image_edited` = AI 改图（换脸 / 重绘 / 调色），把改图区域的颜色"校准回"原图观感，同时保留编辑内容：
- **自动遮罩**（不接 `mask` 时）：逐像素比较两图，`threshold` 判定变化区 → 开运算（`open_k`，去孤立噪点）→ 闭运算（`close_k`，填内部小洞）→ 膨胀（`expand`，外扩包含边缘过渡）。`mask` 已接时跳过自动检测，直接用给定遮罩。
- **校色**：用遮罩外（可靠区，严格排除变化区）逐通道加权统计估计 **scale + shift 仿射**（scale 钳制 0.5~2.0），`strength` 控制校正强度（0 = 完全不改，1 = 全量校正）。
- **对比度回压**：遮罩外前后对比估计对比度拉伸倍数 `a`（钳制 0.3~3.0），围绕编辑图自身黑场把整图跨度缩回 `/a`——找回原图对比度又不钉死到原图绝对范围。
- **`outside_mode`**：
  - `edited`（默认）：高斯金字塔 3 层低频融合——遮罩外低频回贴原图、高频保留编辑图（改图纹理不丢，只换底色 / 环境光）。
  - `original`：遮罩外严格回贴原图（无过渡细节）。
- **输出**：`image` 校正图（RGBA 跟随输入）、`mask` 检测/使用的遮罩、`changed_ratio` 变化区占比。节点自带 512px 上限的结果 + 遮罩双预览。
- **依赖**：torch / numpy / PIL；`expand` 超过 32px 时优先用 scipy 半分辨率 EDT 加速，**scipy 缺失自动退化为 GPU max_pool**（结果一致，仅大半径略慢），不影响使用。
- **命名**：`Color Calibrator (ItsGemTools)` 带品牌后缀，与 BlueBox 的 `Color Calibrator (BlueBox)` 互不冲突、不会被合并覆盖；搜索框输入 `color calibrator` 即可前缀命中。

### Image Crop By Mask（节点键 `GemImageCropByMask`，显示名 Image Crop By Mask (ItsGemTools)）
复刻 KJNodes `ImageCropByMask`（comfyui-kjnodes / `nodes/image_nodes.py`）——**算法按原版实现，代码在本包内重写；kjnodes 原版零改动，两个节点键名/显示名互不冲突、可同画布共存**。典型场景：`SAM3_Detect` 出人脸遮罩 → `GrowMask` 扩边 → 本节点裁出人脸图 → 再送 `CenterCropImages` 对齐尺寸进 LoRA 数据集。
- **算法**：与原版逐行一致——遮罩先 `round()` 再判 `>0`（等价 0.5 硬边阈值），逐张取非零区的 `rows/cols` 首末索引作为外接框，用该框裁出对应图；batch 项数多于遮罩项数时沿用原版的"末项复用"（`min(b, mask.shape[0]-1)`）。
- **接口**：只有 `image` + `mask` 两个输入、`image` 一个输出，**零 widget**，因此工作流里可把 `ImageCropByMask` 原位换成 `GemImageCropByMask`（连线、尺寸、位置都不用动）；RGBA 输入会原样透传第 4 通道。
- **三条安全降级（原版会直接报错，这里不报错并打印告警）**：
  - **空遮罩（全 0）**：原版在 `torch.where(...)[0][[0,-1]]` 上抛 IndexError；本节点退化为返回**整帧**，整条链路不断（batch 里混一张空遮罩也不会连累其他帧）。
  - **batch 内各帧外接框尺寸不同**：原版 `torch.cat` 尺寸不一致直接崩；本节点把每张裁图**居中补零**到 batch 内最大尺寸后拼接，输出统一尺寸。
  - **mask 分辨率与图不一致**：原版会越界索引出错；本节点用**最近邻**把遮罩缩放到图尺寸后继续（硬边不插值，避免糊边扩框）。
- **兼容性**：只依赖 `torch`，ComfyUI 自带，无新增依赖。

## 中英双语

- 节点标题与 Image Compare 面板按钮跟随 ComfyUI 界面语言自动切换（Settings → Language 选简体中文即显示中文）。
- 也可在**节点右键菜单 → 语言 / Language** 手动固定中文或 English。

## 依赖说明

**结论：本包没有"必须额外安装"的依赖。** 全部节点只依赖 ComfyUI 自带的 `torch`、`numpy`、`Pillow`，直接放进 `custom_nodes` 即可运行；唯一可选的 `scipy` 只服务于 maskfeather 一个节点，缺失时该节点自动降级，其余节点完全不受影响。

### 逐节点依赖

| 节点 | 依赖 | 说明 |
|---|---|---|
| Adjust Color & Effect | torch / numpy | 纯 tensor 算法，ComfyUI 自带 |
| Skin Beauty（美颜） | torch / numpy | 内置肤色检测，ComfyUI 自带 |
| Layer Composite | torch | 混合模式逐通道实现，ComfyUI 自带 |
| maskfeather（羽化） | torch / **scipy（可选）** | 使用 `scipy.ndimage.distance_transform_edt` 计算有符号欧氏距离；**未装 scipy 时该节点报错并提示，其他节点不受影响** |
| Set Image Alpha / Set Masked Alpha | torch | ComfyUI 自带 |
| Color Calibrator | torch / numpy / **scipy（可选）** | `expand` > 32px 时用 scipy 半分辨率 EDT 加速膨胀；**未装 scipy 时自动退化为 GPU max_pool，结果一致、仅大半径略慢** |
| Image Crop By Mask | torch | 纯索引裁切，ComfyUI 自带 |
| Before/After Preview | torch | ComfyUI 自带 |
| Image Compare A/B | 前端内联 | 面板预览纯浏览器 JS，零依赖 |
| Label | 前端内联 | 前端画布自绘、双击编辑；后端仅注册空实现类，不参与执行 |

### 前端依赖

无。5 个前端扩展（自动刷新 / 色条、对比面板、外观主题、双语、Label 标签）全部内联自包含，**不安装任何 npm 包**。

### scipy 需要装吗？

- 如果你**用不到 maskfeather**（羽化）节点 → **不需要装**。
- 如果你**要用 maskfeather** → 用**该 ComfyUI 实例自己的 Python**安装，不要用系统 Python：

```bash
# Windows（ComfyUI Desktop，请把路径换成你实际运行的实例）
C:\...\ComfyUI\.venv\Scripts\python.exe -m pip install scipy

# 或手动在 ComfyUI 目录下激活虚拟环境后：
# .venv\Scripts\activate
# python -m pip install scipy
```

```bash
# Linux / macOS（venv 或 conda 环境同理）
/path/to/ComfyUI/venv/bin/python -m pip install scipy
```

装完**重启 ComfyUI**。不装 scipy 也能正常启动和加载本包所有其他节点。

### 实测环境（开发自测通过）

- Python 3.13 / torch 2.12.1+cu130 / numpy 2.5.1 / scipy 1.18.0（Windows，ComfyUI Desktop）
- 前端：ComfyUI 0.42.x / comfyui-frontend-package 1.55.x

## 兼容性

- 测试环境：ComfyUI 0.42.x / 前端 1.55.x，Windows / ComfyUI Desktop。
- 纯 PyTorch 实现，理论上支持任意平台与任意 GPU/CPU。

## 更新日志

版本历史与变更说明见 [CHANGELOG.md](CHANGELOG.md)。

## 许可证

MIT License（见 [LICENSE](LICENSE)）。
