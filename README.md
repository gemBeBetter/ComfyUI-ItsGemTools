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
| **Set / Get Image Variable** | 跨节点传递图片 | 全局图像变量：Set 写入（image 透传）、Get 按 key 读取，大型工作流免拉长线 |

> **接口排序规范**：本包所有节点统一遵循——含 `image` 与 `mask` 输入时，**image 在上、mask 在下**；同时含 `foreground` / `background` 时，**foreground 在上、background 在下**。避免交叉连线，提升工作流可读性。

## 特色

- **纯算法零依赖模型**：磨皮、美白、调色全部用频率分离 / 肤色加权 / 亮度分区等传统算法实现，实时可调，不吃显存。
- **精准面部处理（可选 SAM3 分区）**：把 `SAM3_Detect` 输出的面部/皮肤 MASK 接进 `Skin Beauty` 的 `mask` 输入，即可只处理人脸区域，边缘自动羽化过渡，等效剪映「单人脸 + 皮肤管理」模式。
- **剪映式交互**：渐变色条、白色指示线、拖动条数值框分离布局、自动刷新防抖（1 秒停稳才刷）、淡紫主题外观。
- **中英双语**：节点标题与对比面板按钮跟随 ComfyUI 界面语言自动切换，也可节点右键菜单手动固定。

## 安装

### 方式一：ComfyUI Manager（已收录后）
打开 ComfyUI Manager → 安装自定义节点 → 搜索 **ItsGemTools** → 安装。

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
    └── itsgemtools_i18n.js
```

**然后重启 ComfyUI**（前端扩展需重启加载；若改过扩展文件建议浏览器 Ctrl+Shift+R 硬刷新）。

在节点搜索框搜索：`Adjust Color & Effect`、`Skin Beauty`、`Layer Composite`、`maskfeather`、`Before/After Preview`、`Image Compare A/B`。

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

### Set Image Variable / Get Image Variable（节点键 `SetImageVar` / `GetImageVar`）
全局图像变量（进程内存储），方便大型工作流跨节点传递图片、避免拉长线：
```
Set Image Variable（key=my_image，image=任意 IMAGE）→ 串在流程中
... 任意位置放 Get Image Variable（key=my_image）→ 输出刚才存入的 image
```
- Set：`key` 变量名 + 可选 `image`（不连 image 也可，仅声明变量名）；连 image 时原样透传输出。Set 标记为输出节点（OUTPUT_NODE）：ComfyUI 只执行"输出可达集"，若不标记，未连线的 Set 会被当作孤立节点跳过执行、变量永远为空
- Get：`key` 变量名，输出存入的 IMAGE；**零等待、无依赖**——Set 是执行根（OUTPUT_NODE），同一运行内必然先于 Get 执行并写入；节点已禁止输出缓存（IS_CHANGED），每次运行都重新读取变量。仅当单独运行/部分执行且 Set 未执行时，输出 1×1 占位黑图并在节点上提示，流程不中断
- 存储位于内存，重启 ComfyUI 后清空

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
| Before/After Preview | torch | ComfyUI 自带 |
| Image Compare A/B | 前端内联 | 面板预览纯浏览器 JS，零依赖 |

### 前端依赖

无。4 个前端扩展（自动刷新 / 色条、对比面板、外观主题、双语）全部内联自包含，**不安装任何 npm 包**。

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

## 许可证

MIT License（见 [LICENSE](LICENSE)）。
