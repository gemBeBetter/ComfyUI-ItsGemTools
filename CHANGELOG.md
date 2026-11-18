# Changelog

本包所有重要变更记录于此。格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循[语义化版本](https://semver.org/lang/zh-CN/)。

## [Unreleased]

### 新增

- **Fast Groups Bypasser（节点键 `Fast Groups Bypasser (ItsGemTools)`，前端文件 `web/extensions/itsgemtools_fast_groups_bypasser.js`）**：复刻 rgthree-comfy 的 `Fast Groups Bypasser (rgthree)`——**机制等价、代码在本包内独立重写**（rgthree 原版零改动，类型名带 `(ItsGemTools)` 后缀，可与 rgthree 原节点同画布共存）。
  - **关系建立方式（与原版一致）**：纯前端虚拟节点（`isVirtualNode = true`，无输入、不进 prompt、不参与执行）；每 450ms 枚举工作流全部分组，对每个组按「**节点中心点是否落在组矩形内**」算出成员——**不靠连线**，组增删/改名/拖动自动跟上；`recomputeInsideNodes` 之类前端内部方法一概不依赖，只用 `group.bounding` + 节点包围盒自行判定。
  - **交互等价**：一行一组（行底=启用亮紫/旁路暗紫/混合中间色，左侧色条取组边框色），点击整组在 `0(启用) ↔ 4(旁路)` 翻转；右侧箭头定位并缩放该组；状态由组内节点真实 `mode` 读回（别处手动改会立刻显示为「混合」）；右键菜单「全部旁路 / 全部启用 / 全部翻转 / 重新扫描分组」；Properties 七项与原版同名同义（`matchTitle` 正则、`matchColors` 颜色名与 hex 归一化、`showNav`、`showAllGraphs`、`sort`、`customSortAlphabet`、`toggleRestriction` 的 `max one` / `always one`），子图节点 mode 递归切换（等价 `changeModeOfNodes`）。
  - **主题与归类**：标题栏 `#8a63d2` / 主体 `#241f38` / 描边 `#c4a6ff`（沿用本包 `itsgemtools_appearance.js`，节点已登记进 `NODE_TYPES`）；分类 `ItsGemTools`；i18n 中文名 `按组旁路开关`。
  - **一处主动改进**：组内 `Note` / `MarkdownNote` / `Label (ItsGemTools)` 与节点自身被排除在旁路集合外——否则整组旁路会把标签/备注一起变灰，看不见分区说明。
  - **后端配合**：`nodes.py` 新增 `GemFastGroupsBypasser` 空实现类并登记两张映射表（与 `GemLabel` 同款做法，纯前端节点也必须让后端认识类型，否则加载/排队报 missing node）。
  - **验证（真实后端 + 无浏览器逻辑测试台）**：① `py_compile` 通过、新增映射键写入两张表；② 临时实例（**端口 8199、独立 `--user-directory`，不动 8188 运行实例**）启动后 `GET /object_info/Fast%20Groups%20Bypasser%20(ItsGemTools)` **200**，`category` / `description` / `python_module=custom_nodes.ItsGemTools` 正确；`GET /extensions` 已挂载 `itsgemtools_fast_groups_bypasser.js`；验证实例用完**整树关闭、8199 已释放、无孤儿进程**；③ Node 逻辑测试台（把前端文件里的纯逻辑注入假 `LGraphNode`/`LiteGraph`/`app` 后跑）**14 项全过**：3 组识别与 position 排序、组内成员判定（自动排除 Label 与自身）、点击翻转（4↔0）、手动改单节点后显示「混合」、全部旁路/全部启用、`always one` 不允许关掉最后一组、`max one` 开 B 自动关 A、`matchTitle` 正则过滤、`matchColors` hex 归一化过滤、alphanumeric 排序、组删除同步删行、无匹配不抛错。测试台顺带抓出一个真实缺陷并已修：`refreshRows()` 无参调用（`loadedGraphNode` 路径）时包围盒缓存为 `undefined` 导致 `bounds is not a function`。
  - **实测待进行**：需**重启 ComfyUI（后端要重载 nodes.py）**后由用户确认：节点库搜索 `fast groups` 出现两个（ItsGemTools / rgthree）、点行开关能真正翻转组内节点 mode、箭头能定位，Nodes 2.0 与经典渲染下外观一致。

- **Save Image (Advanced+JPG)（节点键 `GemSaveImageAdvanced`，显示名 `Save Image (Advanced+JPG) (ItsGemTools)`）**：复刻 ComfyUI 内置 `Save Image (Advanced)`（`comfy_extras/nodes_images.py` / `SaveImageAdvanced`）——**编码逻辑只读复用、内置节点与 AusBoss 等公共组件零改动、键名/显示名互不冲突可同画布共存**；并追加 JPG，默认保存即 JPG（独立模块 `gem_save_image_advanced.py`，`nodes.py` 只加 import 与两条映射）。
  - **与内置节点逐项对齐**：同样的 `filename_prefix`（`%date:%` / `%batch_num%` / 子目录）、`folder_paths.get_save_image_path` 计数器、`ui.images` 回执、`images` 原样透传、`OUTPUT_NODE`、`--disable-metadata` 生效；png（8/16-bit + channels 的 auto/1/3/4 语义 + `inject_png_metadata`）、exr（`_to_scene_linear` + 16/32-bit float + `inject_exr_metadata` + asset_manager 预览）、avif（`_save_avif` + crf + `inject_avif_metadata`）三条路径直接调用核心编码助手，像素级行为与内置一致。
  - **JPG 默认档（"视觉无损前提下体积尽量小"的落地）**：① 4:4:4 不降采样色度（彩色/文字边缘不糊）；② `optimize=True` Huffman 优化（解码像素不变，只变小）；③ 基线与渐进两种布局各编一遍取更小者（解码像素相同，纯白捡体积）；④ **auto 质量**：以目标 PSNR（默认 47 dB）为下限，在 88–99 区间二分搜最低 quality（本机实测 q99→q100 体积再涨 35%、PSNR 仅 +1 dB，故上界收到 99）。搜索阶段一律 `optimize=False` 快编（量化表与解码像素和定稿完全一致），只在定稿那遍开优化，15 MP 图单张约 0.7–1.5 s。
  - **JPG 通道语义**：JPEG 无 alpha，RGBA 按白底合成（与浏览器 / Photoshop 展平一致），单通道走灰度；png 的 `channels` 语义不受影响。不写任何 EXIF/注释段。
  - **诚实边界**：JPEG 本身有损，这里做到的是**可量化的视觉无损**（PSNR 可查、可调），逐像素无损请把 `format` 切 png。内置节点的"动画 AVIF"多帧封装（`save_mode = animated`）未复刻，只做静态 AVIF。
  - **实测（本机真实 4K 级样图，ComfyUI-VOSR2 用例，4:4:4 + optimize）**：4K 中文小字图 16.4 MB（节点 PNG）→ 默认 JPG 5.41 MB（34.1%）、q97、PSNR 47.13 dB；建筑 12.4 MB → 2.93 MB（24.0%）、q94、47.18 dB；landscape 4.8 MB → 1.83 MB（38.5%）、q98、48.11 dB。对照 fixed q96 三图均为 PNG 的 29.6–30.6%（PSNR 46.2 / 48.4 / 46.1）；4:2:0 q92 可到 14.5–16.3% 但 PSNR 掉到 41.5–44.6（彩色边缘有损，仅作体积对照）。
  - **验证（真实后端跑通，不只离线）**：临时实例（端口 8199、独立 `--user-directory`，避免扰动 8188 上的运行实例）启动 12 s 后 `GET /object_info/GemSaveImageAdvanced` 200；`POST /prompt` 执行 `LoadImage → GemSaveImageAdvanced → PreviewImage` 返回 `status: success`，落盘 `out_00001.jpg`（PIL 可解、progressive、无 EXIF、PSNR 47.60 dB），下游 PreviewImage 正常收图（透传链路成立）；离线另验 png 元数据内嵌（`prompt` tEXt 命中）、exr、avif 三条路径。验证实例用完即整树关闭，8199 确认释放，8188 原实例未动。
  - **实测待进行**：重启 ComfyUI Desktop 后由用户确认 `merge_vae_fixed_v4`（ep10，节点 631 已换成该节点、默认 jpg）的控件展示与出图；该节点在工作流里仍是旁路（mode 4），取消旁路才真正写盘。

- **Image Folder（节点键 `GemImageFolder`，显示名 `Image Folder (ItsGemTools)`）**：复刻 AusBoss `Image Folder 🆎`（ComfyUI-AusBoss-main 2.20.5，MIT / `nodes/node_image_folder.py` + `nodes/_image_folder_helpers.py` + `js/image_folder/index.js` + `js/shared/*`）——**后端实现与前端交互按原版复刻、代码在本包内重写，AusBoss 原版零改动、键名/显示名互不冲突可同画布共存**。
  - **接口逐项对齐**：9 个 widget 同序同选项（`source` input/output、`folder`、`subfolders`、`sort` name / newest first / oldest first、`pictures` 勾选 JSON、`run` all in one run / one per run、`position`、`after_run` next / stay / random、`at_the_end` stop the run / start over），tooltip 原文照搬；5 个输出 `image` / `mask` / `filename` / `index` / `count`，`OUTPUT_IS_LIST=(True,True,True,True,False)`；`IS_CHANGED` 指纹格式、`VALIDATE_INPUTS` 语义、`which()` 到底语义、64×64 空 mask 约定、UI 回执 `{"count","loaded","names"}` 全部同原版。
  - **前端单文件内联**：本包 `WEB_DIRECTORY="./web"`，ComfyUI 只对 `web/extensions` 挂一次静态目录、且只自动加载 `**/*.js`（`.mjs` 只能被 import），所以把上游 `index.js` + `shared/{index,image_folder,panel_layout,widget_card_math,prompt_scope}.mjs` 全部内联成 `web/extensions/itsgemtools_image_folder.js`；改成本包淡紫 `#c4a6ff` 主题、文案走 `window.__ITSGEM_I18N` 并监听 `itsgem-lang-change` 重建。
  - **交互等价**：瓦片墙 + 单击勾选 + Shift 段选 + All / None / Add / ↻、计数条（`X of Y picked` / `next N` / `all done`）、`one per run` 白色 ring 与双击定位 Picture、`afterQueued` 自动推进 position、Browse 弹窗（只看 input/output 目录树）、Add 菜单（图片 / 整个文件夹，浏览器 `/upload/image` 三并发复制进 input、大文件夹确认框、同名覆盖写）、拖拽文件夹/图片、IntersectionObserver 懒加载缩略图、`fillNodeHeight`（删 `computeSize` 用 `computeLayoutSize` 吃满高度）、`onConfigure` 恢复工作流自带 folder/pick、`onExecuted` 记 done 与文件名行、`onRemoved` 清理。
  - **命名隔离**：路由 `/itsgemtools/image_folder/{folders,list,thumb}`、UI key `itsgemtools_image_folder`、报错前缀 `Image Folder (ItsGemTools):`、分类 `ItsGemTools/image`；i18n `NODE_NAMES` 加 `批量出图（文件夹）` / `Image Folder (ItsGemTools)`；`GemImageFolder` 已登记进 `itsgemtools_appearance.js` 的 `NODE_TYPES`，节点沿用本包淡紫外观（标题栏 `#8a63d2` / 主体 `#241f38` / 描边 `#c4a6ff`），不再保留工作流里存的 AusBoss 青绿 `#14424d`。
  - **一处安全降级（真实启动下行为不变）**：原版 import 时直接读 `PromptServer.instance`，无服务器（离线测试 / CI）时 AttributeError 会把整个包带崩；这里先判空再注册路由。
  - **验证（离线，以上游原文为参照）**：`py_compile` / `node --check` 通过；等价性脚本 **296 项全通过**——按文件路径加载上游 `_image_folder_helpers.py`、把上游 `node_image_folder.py` 装进合成包让相对 import 成立、两边用同一对临时 input/output 根，逐函数比对 `clean_folder` / `looks_full` / `place_inside` / `output_name` / `parse_picked` / `choose` / `which` / `fingerprint` / `thumbnail` / `natural_key` / `list_pictures`（四种排序 × 三种 limit）/ `list_folders`，逐像素比对 `load_picture`，逐条比对 `load()` 的 8 组真实跑（image/mask/names/numbers/count/UI note）、`IS_CHANGED`、`VALIDATE_INPUTS`（含空文件夹与多余参数）、三条错误路径，外加真 aiohttp 端到端跑通三条路由（list / folders / thumb 的 200、递归子目录、越界 400、缺图 404、缩略图 JPEG 头）、接口契约（widget 名/顺序/类型/选项）、映射与路由常量、工作流原位替换（widget 数与 `widgets_values_named`、输出插槽名与类型、只改 `type` 后仍合法）、`import custom_nodes.ItsGemTools` 注册检查。
  - **实测待进行**：需重启 ComfyUI 后由用户在 `restorV4_豆包切图_sam3_crop_flow` 工作流里把节点 10（`AUSBOSS_NODES_ImageFolder`）换成 `GemImageFolder` 跑一遍，确认相册面板与三条路由在真实前端（Nodes 2.0 / 经典渲染）下表现正常。

- **Image Crop By Mask（节点键 `GemImageCropByMask`，显示名 `Image Crop By Mask (ItsGemTools)`）**：复刻 KJNodes `ImageCropByMask`（comfyui-kjnodes / `nodes/image_nodes.py`）——算法按原版实现、代码在本包内重写，**kjnodes 原版零改动、键名/显示名互不冲突可同画布共存**。
  - **算法与原版逐行一致**：遮罩先 `round()` 再判 `>0`（等价 0.5 硬边阈值）→ 逐张取非零区 `rows/cols` 首末索引为外接框 → 用该框裁出对应图；batch 项数多于遮罩项数时沿用原版"末项复用"（`min(b, mask.shape[0]-1)`）。
  - **接口零 widget**：只有 `image` + `mask` 输入、`image` 输出，与 `豆包切脸` 工作流里的节点 127 完全一致，可**原位替换**（连线/尺寸/位置不动）；RGBA 输入透传第 4 通道。
  - **三条安全降级（原版会崩、这里只告警）**：① 空遮罩（全 0）原版在 `torch.where(...)[0][[0,-1]]` 抛 IndexError → 退化为返回整帧（batch 内混一张空遮罩也不连累其他帧）；② batch 内各帧外接框尺寸不同时原版 `torch.cat` 尺寸不一致崩 → **居中补零**到 batch 内最大尺寸后拼接；③ mask 分辨率与图不一致原版越界索引出错 → **最近邻**缩放到图尺寸（硬边不插值，避免糊边扩框）。
  - **验证**：`py_compile` 通过；离线等价性测试 **72 项全通过**——其中 60 项把原版 `crop()` 原文照抄作参照（40 组随机矩形遮罩 + 20 组软边遮罩 0.4/0.62 验证 round 阈值语义）逐像素 `torch.equal` 一致，另含 batch3 同尺寸/RGBA 透传、空遮罩整帧、混合外接框居中补零（左右补零 + 中间内容断言）、遮罩尺寸不符、接口契约（输入/返回/分类/映射键/显示名/不与 `ImageCropByMask` 撞键）、工作流实际尺寸（1152×864 遮罩 500×400 框 → 输出 1×500×400×3）。
  - **前端登记**：淡紫主题 `NODE_TYPES` 与 i18n 双语标题（`按遮罩裁切（图像）` / `Image Crop By Mask (ItsGemTools)`）已加入。
  - **实测待进行**：需重启 ComfyUI 后由用户在 `豆包切脸` 工作流里把节点 127 换成新节点跑一遍。

### 变更

- **Save Image (Advanced+JPG) 纳入本包淡紫主题（`web/extensions/itsgemtools_appearance.js`，2026-11-18）**：`GemSaveImageAdvanced` 之前没进 `NODE_TYPES` 白名单，所以一直是 ComfyUI 默认蓝灰外观。已补登记，跟随 `TITLE_COLOR #8a63d2` / `BODY_COLOR #241f38` / `BORDER_COLOR #c4a6ff`。存量节点由 400ms 轮询补装、新节点走 `nodeCreated`，刷新页面即生效（无需重启后端）。

- **按组开关：行状态文案「旁路 / 混合」改为「关闭 / 部分关闭」（前端，2026-11-18）**：`旁路`（Bypass）对非 ComfyUI 老手太抽象，改成与「启用」成对的「关闭」；组内一半启用一半关闭时显示「部分关闭」（原「混合」）。同步改：右键菜单「全部旁路」→「全部关闭」；内置 Help 里补一句「关闭(4) = ComfyUI 的 Bypass/旁路，节点变灰跳过」把术语还给老手；i18n 中文名「按组旁路开关」→「按组开关」。仅前端文件 `web/extensions/itsgemtools_fast_groups_bypasser.js` 与 `itsgemtools_i18n.js`，**刷新页面即生效，无需重启后端**。节点内部行为（`mode 0/4`）不变。

### 修复

- **Image Compare A/B 预览区不再吞掉鼠标滚轮（`web/extensions/itsgemtools_compare.js`，2026-11-09）**：鼠标停在对比预览上时滚轮/缩放完全失效（画布放到很大时尤其明显）。
  - **根因（读前端源码确认，不是猜）**：LiteGraph 的 `bindEvents()` 把 wheel 绑在 `<canvas>` 元素自身（`canvas.addEventListener("wheel", processMouseWheel)`），而 DOM widget 预览面板是叠在 canvas 之上的独立元素，滚轮事件根本不经过 canvas，`processMouseWheel` 永不触发；面板自身也没有任何滚轮处理，于是滚轮被静默吞掉。
  - **修复（照业界成熟做法，不自造算法）**：ComfyUI 自己的 DOM 控件 `bindMultilineTextareaWidget` 就是手动把原始 `wheel` 转交给画布——这里同样在面板 `root` 上加 `wheel` 监听（`passive:false`），命中即 `app.canvas.processMouseWheel(event)` 并 `stopPropagation`。因为直接复用画布同一入口，行为完全跟随 `Comfy.Canvas.MouseWheelScroll` 设置（`zoom` / `panning`）、Ctrl/⌘ 缩放、Shift 横向，缩放锚点仍在指针处。**只新增滚轮转发**：SLIDE 滑动、A/B 切换、面板尺寸/模式保存逻辑零改动。
  - **实测（真实前端，ComfyUI 0.44.0 / frontend 1.57.6，预览区中心 325,403）**：对照（同坐标但 `bubbles:false`，等价修复前的路径）scale 1.00000、offset 不变、`defaultPrevented=false`；修复后冒泡事件 scale 1→1.1、offset 改变、`defaultPrevented=true`；CDP `Input.dispatchMouseEvent(type="mouseWheel", modifiers=2)` 受信事件 scale 1.1→1.21；普通受信滚轮按当前 `zoom` 设置 1.21→1.1。`node --check` 通过；`/extensions/ItsGemTools/extensions/itsgemtools_compare.js` 服务端 200 且含新逻辑。

## [1.0.0] - 2026-11-06

### 新增

- **redraw 工作层取整对齐 AusBoss，消除 case2 下半身斜向模糊/重影**——case2（源图 1088×1920→8MP，tile4/ov256）成图（2129×3759）腰以下裙子/手/麦克风出现沿手臂/手套斜向轮廓的半透明羽化模糊。
  - **排查路径**：工作流确认该 case 用窄带 blend_band=48；A/B 实测全带（0）与窄带（48）右侧模糊都在、配准两次都 `skipped(peak=0.01)`，排除 band 混合与配准；所有几何操作（ramp/keep_colors/edge fix/resample）均为轴对齐或全局，无法产生斜向边界，判定模糊来自模型 tile2 重绘输出本身，且对 tile 几何边界敏感。
  - **决定性对照**：用 AusBoss 节点、同图同 seed 同参数（8MP/tile4/ov256）跑，AusBoss 干净。逐行对比定位到 redraw 尺寸口径差异：旧 `_gem_megapixel_size` 用"比例误差优先枚举"，该 case 选 2144×**3776**（tile2 起点 1760、共享区 256）；AusBoss 用宽高各自独立 half-up，得 2144×**3744**（tile2 起点 1728、共享区 288），手臂斜向结构在 tile2 里有更多上方上下文，避开重绘失败。
  - **修复（只改 ItsGemTools，AusBoss 零改动）**：新增 `_gem_redraw_size`（独立 half-up，复刻 AusBoss `megapixel_size`），plan 的 redraw 工作层改用它；**result 严格等比层（result_multiple=1）不动**，成图仍零比例误差（case2 实测 0.0016%），redraw 只是内部工作层、最后 resample 到 result。离线确认 case2/图1 的 redraw 与 boxes 均与 AusBoss 完全一致。
  - **验证**：py_compile 通过；核心回归 42/42；verify_fullband_align_shift / mask_gap / ghost_fix / edge_fix_unit 全 exit=0。**用户实测待进行**。
  - ——用户在 `qwen21_v3_人脸_发丝` 工作流（8MP/tile4/ov256）跑出正常眼睛上方半透明半睁的第二双眼（带矩形羽化暗块），决定性证据是**全带（blend_band=0）、进 Calibrator 之前的纯 Stitch 输出就有鬼影**，排除 Calibrator 与全/窄带差异，确认是单 tile 模型幻觉：
  - **几何根因**：该场景自然布局是 1×2 宽 tile（2464×1792=4.415MP，无垂直缝），但旧的 3MP 安全线把它钳制降成 2×2 窄 tile（1376×1792），**垂直缝（x≈1081）切到右眼**；窄 tile 只看得到半张脸、缺全局上下文，按"人像"语义脑补出第二双眼，且该位置在 tile 内部非共享区（全带 ramp 权重=1.000），拼接无法稀释。
  - **对齐 AusBoss**：AusBoss 的 tile 上限 = `tile_mp × TILE_SLACK(1.125) × SNAP_SLACK(1.03)`（tile4=4.635MP），不做额外面积钳制，自然宽 tile 保留、无垂直缝。把 `_GEM_SAFE_TILE_PX` 改为 4_600_000：自然宽 tile 4.415MP 通过、无垂直缝；`tile_megapixels=8` 翻倍单块（8.096MP）仍 >4.6 被钳。可能的小幅漂移由 Stitch 端 tile-to-tile 配准（AusBoss 没有的安全网）纠正。
  - **否决的弯路**：宽 tile 面积豁免（近方形宽 tile 实测系统性横向漂移 +1.6~2.9px，会让偏移回归，已回退）；overlap 256→384 让眼睛进入共享区、明显鬼影消失但仍有残留（幻觉结构在非共享区部分）；成图后局部 inpaint（矩形暗块边界不自然、通用误判风险）；FaceDetailer（用户早期淘汰）。
  - **验证**：py_compile 通过；split 离线输出 1×2、tile(2464,1792)、垂直缝=False；核心回归 42/42（2c 防呆断言按 4.6MP 口径更新、新增 tile8 翻倍仍被钳；第5节整链改用自然 1×2 多 tile 验证配准，off 误差 0.0328→translation 0.0001）；verify_fullband_align_shift / mask_gap / edge_fix / tone_spatial / ghost_fix 全通过。**用户实测待进行**。
- **Color Calibrator（节点键 `GemColorCalibrator`，显示名 `Color Calibrator (ItsGemTools)`）**：复刻 BlueBox `ColorCalibratorBlueBox`（zhoutingxi/ComfyUI-BlueBox，MIT）的双图颜色校准节点——
  - **算法对齐原版**：自动对比两图检测变化区（`threshold` 阈值 + `open_k`/`close_k` 开闭运算 + `expand` 膨胀）→ 遮罩外逐通道加权统计仿射校色（scale 钳 0.5~2.0、`strength` 控强度）→ 遮罩外锚点测对比度拉伸倍数（钳 0.3~3.0）围绕编辑图自身黑场回压全图 → `outside_mode=edited` 高斯金字塔 3 层低频融合（遮罩外低频回贴原图、高频保留编辑图）或 `original` 严格回贴；输出 `image` / `mask` / `changed_ratio`。
  - **独立命名不撞名**：显示名带 `(ItsGemTools)` 品牌后缀（搜索框 `color calibrator` 前缀命中），与 BlueBox 原节点键/名互不覆盖，可同画布共存、原版零改动。
  - **依赖策略同包**：仅 torch / numpy / PIL；`expand`>32px 的 EDT 加速优先 scipy，缺失自动退化为 GPU max_pool（结果一致仅略慢），不新增硬依赖。
  - **前端登记**：淡紫主题皮肤 NODE_TYPES 与 i18n 双语标题已加入。
  - **验证**：实例 venv 导入 + 合成双图端到端验证（自动遮罩、校色、对比度回压、两种 outside_mode、mask 直通、RGBA 透传）全通过。
- **幽灵下巴结构性根因确认：窄带独占混合保留单 tile 幻觉，默认混合改回全带对称渐变（`blend_band` 默认 48→0）**——在确认 LoRA/seed 均非根因（关 LoRA 幻觉反而最重 27996 像素、同 LoRA 不同 seed 差异巨大）后，逐行对照 AusBoss 源码定位到**混合机制这一结构性差异**：
  - **机制（`verify_narrow_hall_mechanism`）**：幻觉是单个 tile 在共享区随机生成、相邻 tile 不会在同一位置生成同样内容。**全带对称渐变（AusBoss `_ramp`，共享区两 tile 权重 0↔1 横跨整个 overlap、和恒=1）下，后写 tile 在共享区权重从 0 渐升，幻觉被相邻正常 tile 内容以高权重平均稀释**；而**窄带独占（`_gem_ramp_edge`）让后写 tile 在共享区主体 mask=1 独占，幻觉被完整保留**。合成 1D 场景（真值 0.70、相邻 0.20）：全带幻觉可见度 **0.481**、窄带 **0.699**，全带权重在 y40 仅 0.16；真实 AusBoss 成图 `qwen_00002`（1056×1888）幽灵区幻觉像素 **=0**。
  - **重影与幻觉是同一机制两面**：全带平均错位内容→重影但抑制单 tile 幻觉；窄带独占→无重影但保留幻觉。**修复 = 全带渐变 + 保留配准**：配准先把 tile 几何对齐（对齐后全带平均的内容一致、无错位重影），全带再平均稀释幻觉。配准即使 `skipped`，全带也退化为 AusBoss 实测干净的行为。
  - **明暗问题一并消解**：全带路径（`band=0`）不做 tone match，早期"用共享区 gain<1 硬套整 tile 压暗亮毛衣"的有害校正从根源不存在；全带平均天然平滑接缝色调（AusBoss 无 tone match 亦无明暗问题）。
  - **落地**：`blend_band` 默认 0（全带，推荐）；`>0` 窄带独占保留为可选（仅当全带确实出现重影且无幻觉时）。核心 42/42 + 全部 verify（含新增 `verify_narrow_hall_mechanism`、`verify_fullband_align_shift`）通过。
  - **偏移不回归（`verify_fullband_align_shift`）**：偏移修复分两层——几何防线（3MP/AR1.6/严格等比/切块）独立于 blend，配准在混合前、独立于 band 执行。相同平移漂移下主体条带误差：不配准 **0.0949**、全带+配准 **0.0058**、窄带+配准 **0.0056**（全带 5 块配准成功），全带与窄带校正力相当、均比 baseline 好约 16 倍。全带不引入位置偏移，仅在未对齐时可能产生重影（由配准消除）。

- **GemTiledUpscaleStitch "黑色分隔线/格子"根除：窄带 mask 缝隙修复（主因）+ Qwen tile 边缘黑边填充 + 块间色调拉齐**——用户多轮实测黑线（over256 版 x=932 单列纯黑亮度 0.3/255、横缝 y=1160/2320 暗行）在两轮修复后仍未消除，最终定位**真正根因是窄带 mask 权重缝隙，不是 tile 内容黑边**：
  - **mask 缝隙根因与修复（`_gem_ramp_edge`）**：旧 `_gem_ramp_edge` 的 fall（先写 tile 让出区）把 body 0 区紧贴共享边界（`weights[length-fall:length-band]=0`），而 rise（后写 tile）渐变从 0 开始（`weights[0]=0`）——**边界处两个 tile 的 mask 都=0，`weight=0` → `total/weight.clamp_min(1e-6)=0` 黑缝**，resample 后成单列黑线（横缝同理）。修复后 fall 渐变带紧贴共享边界（边界处权重=1，band 内 1→0），body 0 区移到渐变带内侧；与邻居 rise（0→1）精确对接，**overlap 区权重和恒=1，无 weight=0 位置**。这解释了前两版 edge fix 为何无效：mask=0 时 tile 内容根本不贡献。
  - **边缘黑边填充（`_gem_fix_tile_edge_band`，辅助防线）**：逐边检测 tile 边缘 band（默认 8px）内**最暗列/行**均值比内侧 band 低超阈值（`_GEM_TILE_EDGE_DARK=0.098`，0-1 像素域）时，用内侧内容复制填充该边，消除 Qwen tile 本身可能带的边缘暗边伪影。检测用"最暗列/行"而非"band 整体均值"：1~2px 窄黑边在 8px 带均值中会被稀释（只低 ~0.07 < 阈值），按最暗列/行才能抓到。已在 `_gem_join` 中 keep_colors 之后执行。
  - **块间色调拉齐（`_gem_tone_match` / `_gem_fit_gain_bias`，空间衰减）**：对左/上已拼邻居的共享区分别逐通道最小二乘 gain+bias（`_GEM_TONE_GAIN_MIN/_MAX=0.7/1.3`、`_GEM_TONE_BIAS_MAX=0.18`），按共享区像素数加权平均，消除 Qwen 各 tile 独立重绘造成的块间亮度/色调不一致（"格子感"叠加因素）。**校正通过空间衰减场只在接缝 band 附近生效（接缝处=1、向 tile 内部 linspace 衰减到 0，reach=max(band×2,96)），不做整 tile 硬套**——早期版本把单一 gain/bias 应用到整个 tile，实测行2 tile 用顶部暗背景共享区拟合出 gain<1，把下部亮白毛衣错误压暗（源毛衣 112→209 渐变，成图只到 165，差异与源亮度成正比、越往下越暗，均值 -28.4）。窄带混合（`blend_band>0`）时执行；`blend_band=0` 旧全带渐变兼容路径不做 tone match，保持旧行为。
  - **实测**：`verify_mask_gap` 水平/垂直 overlap 权重和恒=1（边界 [928..980] 全 1.000）；黑边修复 0 → 0.502（内侧 0.506）；接缝 band 色调差 0.100 → 0.025 且 tile 内部主体改动量=0（亮毛衣不再被压暗）；端到端横缝上下差 0.01；42/42 核心回归 + `verify_tone_spatial`（接缝对齐、内部亮主体 0.700 保持、衰减单调）+ `verify_ghost_fix`（窄带单侧相关 0.705 vs A -0.002，band=0 双影对照保持）+ `verify_placed_raw`（5/6 配准、条带 0.0037）+ `verify_edge_tone` + `verify_edge_fix_unit` 全部通过。

- **GemTiledUpscaleStitch 重影根治：tile-to-tile 卡通域配准 + 窄带独占混合（`blend_band` 新参数）**——消除 Qwen 相邻 tile 内容不一致导致的宽重影带：
  - **配准参考域修正**：旧配准拿源照片当参考，Qwen 卡通化后特征域差异太大（实测 peak 仅 0.02~0.03，阈值 0.10），全部 `skipped`、接缝零修正。现改为**已拼入的邻接 tile 卡通输出**做参考（`_gem_align_vs_placed`），左/上邻居各测一次取峰更高者，诊断输出 `shift=(…) via left|up`。
  - **参考污染修复**：窄带混合会让先写邻居在共享区 mask 让出（权重≈0），若直接拿拼接快照当配准参考，共享区内容≈0、配准必失败（实测 peak 0.01~0.02）。现配准参考改用**未乘 mask 的原始重绘内容累加 + 覆盖计数**，共享区保留邻居完整内容。
  - **窄带独占混合（`_gem_ramp_edge`）**：overlap 区主体由后写 tile 独占（单侧内容，无双影），仅在紧贴 tile 边界的 `blend_band`（默认 48）像素内 0↔1 渐变衔接；重影带宽度从 overlap 宽缩到 2×band。`blend_band=0` 还原旧的全带对称渐变行为。
  - **实测**：模拟相邻 tile 相对错位 2~3px 时 5/6 块配准成功、位移全部测准，接缝条带误差 0.1073 → 0.0052（20 倍改善）；42/42 回归通过。

- **Tiled Upscale / Tiled Upscale Stitch（节点键 `GemTiledUpscale` / `GemTiledUpscaleStitch`）**：复刻 ComfyUI-AusBoss 的切块 / 拼回两节点，Stitch 端新增 **tile 级亚像素几何配准**（FFT 相位相关，纯 torch 零新依赖）——
  - **根治接缝偏移**：QwenImage 2.1 编辑模型 + 自训 LoRA 重绘单 tile 时构图会相对源 tile 漂移数像素，原版只做颜色匹配、直接按 box 放回，接缝处即错位 / 重影。本节点先估计每个重绘 tile 相对源 tile 的平移、归位后再混合，实测对 2px 漂移的拼接误差降低 ~76%。
  - **大图配准可靠**：全分辨率相位相关（长边超 2048 才降采样），避免早期"512 缩略图"方案把大 tile 的真实位移抹成亚像素噪声导致估计失效——这是偏移"修了又犯"的隐藏来源。
  - **安全保护**：低纹理 / 位移超 `max_align_shift` 自动跳过配准（退化原版行为）；边界 8px 羽化过渡防图像最外缘撕裂；`align_mode=off` 可还原版行为。
  - **兼容**：与原 AusBoss 节点成对替换（stitcher 互不兼容，异源直接报错提示）；参数接口与原版一致（megapixels / tile_megapixels / overlap / multiple / max_growth / keep_colors）。

- **GemTiledUpscale Qwen 上限防呆（`enforce_qwen_tile_limit`，默认开）**：单 tile 面积超过安全工作点时自动调低 `tile_megapixels` 重规划，并在 `report` 输出明确提示。安全工作点初版为 3MP，后对齐 AusBoss 口径调整为 4.6MP（见顶部条目）。
  - **背景**：Qwen-Image 2.1 官方硬上限 2048×2048（4.19MP），但实测单块贴着上限（如 2144×1952≈4.185MP）时重绘构图仍系统性横向漂移（+1.6~+2.9px），且配准无法完全挽回——这是"调大 tile_megapixels 又复发"的兜底防线。
  - **行为**：如 1088×1920 → 8MP、tile_megapixels=4 时，自动降为 3.28（单块 2144×1344=2.88MP、3 块），成图尺寸不变；小图上参数虽大但实际单块不超限时不误钳；关闭开关则完全按填的参数切。

- **GemTiledUpscale Qwen 防呆模式收紧 tile 宽高比（`_GEM_QWEN_MAX_TILE_ASPECT=1.6`）**：开启 `enforce_qwen_tile_limit` 时，单 tile 宽高比上限从通用 2.2 收紧到 1.6，逼网格选择接近方形的 tile。
  - **背景**：输入经 1024 控制缩成细长竖图（如 576×1024）后，`max_growth` 较大时 redraw=result，网格枚举第一个可行解是单列超扁横条（实测 5×1，tile 2112×960≈2.2:1）。Qwen 在 2.2:1 横条上重绘构图保持差、内容变形（五官/手型），且扁条低频内容使 FFT 配准峰压低全部 `skipped`（peak 0.05~0.09），接缝无修正 → 表现为"变形"。
  - **行为**：同参数下 576×1024 → 8MP 从 5×1 扁条变为 2×3 网格（tile 1184×1440，AR 1.22，6 块），落回 Qwen 舒适区；方图/横图/小图/防呆钳场景（2144×1344 AR 1.60）均不受影响；关闭开关仍按通用 2.2 切（还原老行为）。

- **GemTiledUpscale 成图严格等比（`result_multiple=1` 默认）**：成图尺寸（`result` / `before` 输出 / Stitch 最终输出）改为**像素级严格等比**于源图，不再被 32 对齐取整带偏比例。
  - **背景**：用户以 `before` 输出（源图放大到成图尺寸的对照图）为检查点，发现 576×1024 → 8MP 旧成图 2112×3744 比例 0.5641 vs 源 0.5625，存在 +0.28% 的 32 对齐取整误差——Qwen 参考的就是这个比例，是"上游偏、后面重绘跟着偏"的最后一项来源。
  - **行为**：`redraw` / `tiles` 仍按 `multiple`（默认 32）对齐（tile 布局、切块完全不变），Stitch 端利用已有的"重绘尺寸 → 成图尺寸"补差机制，把拼好的 32 对齐层 lanczos 重采样到严格等比的成图尺寸。576×1024 → 8MP 新成图 **2121×3771**（比例误差 0.009%，亚像素级）；1088×1920 → **2129×3757**。keep_size（megapixels=0）保持原尺寸不受影响。

- **Label（节点键 `Label (ItsGemTools)`）**：浮动文字标签节点，复刻 rgthree Label 的全部视觉功能——
  - 文案 = 节点标题，支持 `\n` 字面序列换行；可调字号、字体、字色、对齐（左 / 中 / 右）、背景色、内边距、圆角、角度（度）。
  - 颜色支持 8 位 HEX 半透明（如 `#FFFFFF88` = 半透明白，背景与文字均支持）。
  - **双击进入就地文案编辑**：双击节点弹出输入框，改完按回车或点击节点外区域即生效，Esc 取消。
  - 纯前端虚拟节点，零依赖、不参与执行；支持右键 Pin 固定后点击穿透。
  - **命名 SEO**：`Label (ItsGemTools)` 带品牌后缀唯一不撞名（不同于 rgthree 的 `Label (rgthree)`），搜索框搜 "label" 时前缀命中且字母序靠前。

### 修复

- **Tiled Upscale Stitch 峰高阈值再次卡狠真实信号（实测 peak=0.13,ratio=258 仍被拒）**
  - 真实重绘 tile（2144×1952，Qwen 卡通化）实测：归一化峰高 **0.12~0.13**、峰比 **234~258**（信号极强，峰高只是被大 tile 内容一致区摊低），0.15 阈值差 0.02 全拒。**阈值校准到 0.10**（噪声伪峰 0.02 仍有 5 倍裕度，真实重绘 0.12~0.13 放行）。同时 diagnostics 的 `skipped` 现在带实测 `peak/ratio`，阈值校准不再靠猜。
  - 峰比门槛 4.0 → 2.5：峰高阈值已把关噪声，峰比可放宽。

- **Tiled Upscale Stitch 归一化峰高阈值过严，真实配准全部被拒（表现为"有偏移还是"）**
  - **根因**：上一版把 `_GEM_ALIGN_MIN_PEAK` 定在 0.3。实测三档数据：不相关噪声伪峰 ≈ **0.020**；真实整 tile 平移峰 ≈ **0.999**；但**全分辨率大图（2144×3776）真实微位移峰只有 ≈ 0.27**——大图上大部分区域内容一致、相位相关峰被摊低是天然现象。0.3 阈值把真实大图配准全判成 `skipped`，重绘漂移原样保留，用户实拍输出相对基准水平偏移 **2.2px** 全部漏配。
  - **修复**：阈值降至 **0.15**，在噪声（0.02）与真实大图信号（0.27）之间留足裕度；37 项回归全绿（噪声仍被拒、已知平移 0.999 仍通过）。用真实输出图对复测：全图配准成功估计 (2.19, -0.26)px。

- **Tiled Upscale 目标尺寸 32 对齐导致纵横比漂移（Qwen 参考图变形 → 重绘跟着偏移）**
  - **根因**：`_gem_megapixel_size` 宽高各自就近取整到 32 倍数，两次取整带偏比例。实测 1088×1920 → 8MP 得到 2144×3744，比例漂移 **+1.06%**——Qwen 看到的是被横向拉宽的参考图，重绘内容跟着变形，纯平移配准救不回来，表现为"怎么修都还有偏移"。
  - **修复**：改为比例优先的枚举取整（宽高各取上下 32 对齐共 4 组合，按比例误差最小 + 面积不低于目标选择）。修复后 1088×1920 → 8MP 得 2144×3776，比例误差 0.20%；新增 2b 回归（4 组尺寸场景，比例误差 < 0.5% 断言）。
- **Tiled Upscale Stitch 低纹理误配准（噪声伪峰被当成有效位移）**
  - **根因**：可靠度只判峰/RMS 对比度；两张独立低幅噪声图相位相关峰比可达 8（阈值 4.0 拦不住），把噪声当位移 → tile 被错误平移 → 更严重的偏移。
  - **修复**：加归一化峰高下限 `_GEM_ALIGN_MIN_PEAK=0.3`（有效平移峰接近 1.0，不相关噪声伪峰仅 0.1~0.3）。同时固定测试 4 项的随机种子，消除断言漂移。
- **Tiled Upscale Stitch 多 tile 时接缝出现重影**
  - **根因**：`_gem_blend_shifted` 为防单 tile 平移露出边缘伪影，在 tile 最外圈 edge px 内把"配准版"与"未配准版"混合；多 tile 时 tile 边缘正是接缝区，混合带里两版内容几何错位 dx px，直接把偏移原样混回接缝——表现为明显重影。而接缝区 overlap 权重本就趋 0，平移的 border 复制会被邻居权重压掉，该保护纯属多余。
  - **修复**：删除边缘混合，配准后直接用纯归位版（`_gem_apply_shift(tile, -dx, -dy)`）；单 tile 场景的最外缘伪影由 border 填充兜底，视觉影响远小于重影。
  - **配套**：Stitch 新增 **diagnostics STRING 输出**——逐 tile 报告估计位移 / 跳过原因（如 `tile@(x,y) WxH: shift=(+1.6,-0.8)`），排查偏移可直接读该输出判断配准是否生效。回归测试 33 项全部通过（含新诊断断言）。

- **Tiled Upscale Stitch 配准门槛过严导致强风格化 / 大位移重绘不配准（表现为"偏移太大"）**
  - **根因**：峰值/RMS 可靠度门槛 `_GEM_ALIGN_PEAK_RATIO=8.0` 在重绘风格化较强（对比度 / 色相变化、细节重绘）时把相关峰压钝，多数 tile 被判"低纹理"直接跳过配准；`max_align_shift=24` 对 zoom / 构图漂移大的工作流（如本包适配的 Qwen zoom 卡通化）也偏小，真实位移超限同样被跳过——两者都导致输出退化为原版"按 box 直接放回"的偏移行为。
  - **修复**：门槛放宽至 4.0（低纹理保护仍有效，纯噪声图虚警率不升）；`max_align_shift` 默认 24 → 64，示例工作流同步更新为 64。
  - **配套**：回归测试在门槛 4.0 下仍 32 项全绿（含低纹理保护 / 位移超限拒绝 / list 包参）。

- **Tiled Upscale Stitch 在真实 ComfyUI 报 `TypeError: int() argument must be ... not 'list'`**
  - **根因**：`GemTiledUpscaleStitch` 声明了 `INPUT_IS_LIST=True`，ComfyUI 执行时会把**所有**输入（包括 widget 标量 `keep_colors` / `align_mode` / `max_align_shift`）包成 list 传入；`stitch()` 里 `int(max_align_shift)` 对 list 调用直接抛 TypeError，且 `str(align_mode)=="translation"` 恒为 False（配准静默失效）。独立单元验证时传的是标量，未模拟 ComfyUI 的 list 包参方式，故 31 项全过却在真实运行环境暴露。
  - **修复**：`stitch()` 开头统一解包标量输入（keep_colors / align_mode / max_align_shift），再进入原有逻辑；配准与颜色保持均按预期生效。
  - **配套**：回归测试新增「5b. INPUT_IS_LIST 全 list 包参」项（用 ComfyUI 真实传参方式跑整链，验证不抛错且配准误差改善保留），现 32 项全部通过。

- **Label 双击编辑框定位（适配前端 1.55）**
  - **根因**：前端 1.55 重写了坐标系统——画布元素上 `ds` / `convertCanvasToOffset` / `convertOffsetToCanvas` 等坐标 API 全部移除，且画布被非等比拉伸显示，任何换算公式都会把编辑框算到屏幕外（实测 top 偏移 1700px+，表现为"双击没反应"）。
  - **修复**：编辑框改为**直接锚定双击点**（用户双击哪里，编辑框就出现在哪里，绝对可见）；坐标矩阵 / 比例换算降级为无双击点场景的兜底，并加 NaN 守卫保证编辑框必然出现。
  - **配套**：三层双击命中（浏览器 `dblclick` 捕获 / 自研 `pointerdown` 双击判定 / `processMouseDown` 拦截），双击后回车或点击节点外区域提交生效、Esc 取消。

- **含 Label 的工作流报 "A node type is missing or unavailable"**
  - **根因**：Label 为纯前端节点，后端（Python）不认识 `Label (ItsGemTools)` 类型；前端 frontend_only 特殊通道在本环境未兜住，加载 / 运行时后端校验 prompt 即报缺失节点。
  - **修复**：后端注册同键空实现类（`GemLabel`，`RETURN_TYPES=()`、`noop()` 无操作）——后端认识类型即不再报错；实际渲染与双击编辑仍完全由前端扩展完成，后端类不参与任何执行逻辑。

- **Label 误伤 Note / Markdown 等基础装饰节点**
  - **根因**：画布命中函数（`findItsGemLabelAt`）带"无输入输出的纯装饰节点"兜底分支——Note / Markdown 同样无输入输出，双击即被劫持弹 Label 编辑框。
  - **修复**：删除全部特征兜底，Label 识别收紧为 **type / comfyClass / 实例三种精确匹配**；`drawNode` 透明拦截、双击 / `pointerdown` / `processMouseDown` / Pin 穿透等所有入口统一走严格判定，与其它装饰节点彻底隔离。
  - **按需激活**：新增画布级 Label 存在性缓存（`gemGraphHasLabel`）——**画布上没有 Label 节点时所有双击 / 交互监听与拦截立即短路**（零开销、零干扰，不影响添加节点面板、Note / Markdown 等交互）；有 Label 时才全量生效，缓存为 false 时每 1s 惰性重算防漏算。

- **Set / Get Image Variable 传值延迟一轮（换图后 Get 仍读到旧图）**
  - **根因**：Set 与 Get 之间无连线时，ComfyUI 执行器优先执行"输出链可达"的节点——Get 处于 Save / 处理链上，被 `ux_friendly_pick_node` 优先挑中执行；而 Set 依赖链长、就绪晚，本轮尚未写入新值，导致 Get 读到上一轮的旧值（"延迟一轮"）。
  - **修复**：新增前端扩展 `itsgemtools_setget.js`，在 `app.graphToPrompt` 层把**同 key 的 Set 的 IMAGE 输出隐形链接到 Get 的 `var_ref` 可选输入**——画布上无任何连线，但执行器因此保证 Set 先执行、Get 后读取，与"直接连线"行为完全一致。`var_ref` 输入口由前端隐藏（nodeCreated + 轮询兜底），Get 忽略该值，纯执行序依赖。
  - **后端**：`GetImageVar` 增加 optional `var_ref`（IMAGE）输入（不参与读取逻辑）。

- **节点折叠（Collapse）后残留未收起的框线（全部节点）**
  - **根因**：外观主题扩展的 `onDrawForeground` 描边按 `node.size` 画完整圆角框；折叠后 size 高度未收缩，框线残留在标题栏下方。
  - **修复**：`onDrawForeground` 增加折叠判断（`collapsed` / `flags.collapsed`）——折叠时跳过自定义描边，与官方节点折叠行为对齐；展开后自动恢复描边。包内所有节点共用同一皮肤逻辑，一处修复全覆盖。

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
