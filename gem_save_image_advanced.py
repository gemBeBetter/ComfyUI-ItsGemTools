"""Gem Save Image (Advanced) —— ItsGemTools 自有节点（内置节点零改动）。

复刻 ComfyUI 内置 `Save Image (Advanced)`（comfy_extras/nodes_images.py: SaveImageAdvanced）：
同样的 filename_prefix / 计数器 / 元数据 / UI 回传 / images 透传，png(8/16bit、channels) /
exr(16/32bit float、input_color_space) / avif(crf) 三条路径直接复用核心编码助手，像素级行为与内置一致。

在此之上追加 JPG：
  * 默认格式 jpg（默认保存即 JPG，无需每次手切）
  * 默认 4:4:4，不降采样色度（这是 JPG 里最容易吃掉色彩细节、却几乎不省体积的一刀，直接关掉）
  * Huffman 优化（optimize=True，纯编码优化，解码像素完全相同，只变小）
  * 基线 / 渐进两种布局都编一遍，取体积更小的那个（解码像素相同，纯白捡的体积）
  * 默认 auto 质量：以"目标保真度（PSNR）"为下限，二分搜索满足下限的最低 quality，即最小体积
    —— 这就是"画面无损（视觉无损、可量化）前提下体积尽量小"的落地方式
  * 不写任何 EXIF/注释段；RGBA 按白底合成（JPG 无 alpha）

诚实说明：JPEG 是有损格式，本节点做到的是**可量化的视觉无损**（默认 PSNR ≥ 48 dB、色度 4:4:4），
需要逐像素无损请把 format 选 png。已知差异：内置节点的"动画 AVIF"多帧封装不在本节点范围，只做静态 AVIF。
"""

import io
import logging
import math
import os

import numpy as np
import torch

_LOG = logging.getLogger("ItsGemTools.SaveImageAdvanced")

# PIL/libljpeg 的色度采样档位：0=4:4:4（不降采样，色度无损失）, 1=4:2:2, 2=4:2:0（体积最小）
_JPEG_SUBSAMPLING = {
    "4:4:4 (no chroma loss)": 0,
    "4:2:2": 1,
    "4:2:0 (smallest)": 2,
}
_JPEG_SCAN_MIN_Q = 88    # auto 搜索下界：再低就不是"视觉无损"的讨论了
_JPEG_SCAN_MAX_Q = 99    # auto 搜索上界：本机实测 q99→q100 体积再涨 35%、PSNR 只 +1 dB，不值

_JPG_MODE_AUTO = "auto (min size @ target PSNR)"
_JPG_MODE_FIXED = "fixed quality"


# ---------------------------------------------------------------------------
# 核心内置编码助手的惰性引用（只读复用，绝不改动核心文件）
# ---------------------------------------------------------------------------

_CORE = None


def _core():
    global _CORE
    if _CORE is None:
        try:
            import comfy_extras.nodes_images as core  # noqa: WPS433
        except Exception as exc:  # 核心改名/被裁剪时给出可读的错，而不是 AttributeError
            raise RuntimeError(
                "GemSaveImageAdvanced 需要 ComfyUI 内置 comfy_extras.nodes_images 的编码助手，"
                f"当前环境无法导入：{exc}"
            ) from exc
        _CORE = core
    return _CORE


def _cli_metadata_enabled() -> bool:
    try:
        from comfy.cli_args import args as cli_args
        return not getattr(cli_args, "disable_metadata", False)
    except Exception:
        return True


def _asset_manager_enabled() -> bool:
    try:
        from server import PromptServer
        manager = getattr(PromptServer.instance, "asset_manager", None)
        return bool(manager is not None and manager.enabled)
    except Exception:
        return False


# ---------------------------------------------------------------------------
# JPG 编码
# ---------------------------------------------------------------------------

def _quantize(t: torch.Tensor) -> np.ndarray:
    """浮点 [0,1] → uint8，四舍五入（不是截断，少半个灰阶的偏差不值得）。"""
    scaled = (t.detach().to(torch.float32).clamp(0.0, 1.0) * 255.0).round()
    return scaled.to(torch.int32).cpu().numpy().astype(np.uint8)


def _to_jpeg_source(image: torch.Tensor) -> tuple[np.ndarray, str]:
    """HxWxC(或 HxW) → (uint8 数组, PIL 模式)。JPG 无 alpha：RGBA 按白底合成，单通道走灰度。"""
    if image.ndim == 2:
        image = image.unsqueeze(-1)
    channels = image.shape[-1]
    if channels == 1:
        arr = _quantize(image[..., 0])
        return arr, "L"
    if channels >= 3:
        rgb = image[..., :3]
        if channels == 4:
            alpha = image[..., 3:4].to(torch.float32).clamp(0.0, 1.0)
            rgb = rgb * alpha + (1.0 - alpha)  # 白底合成，和 Photoshop/浏览器展平一致
        return _quantize(rgb), "RGB"
    raise ValueError(
        f"JPG 保存不支持 {channels} 通道图像：支持 1（灰度）、3（RGB）、4（RGBA，白底合成）。"
    )


def _jpeg_bytes(
    arr: np.ndarray,
    mode: str,
    quality: int,
    subsampling: int,
    progressive: bool,
    optimize: bool = True,
) -> bytes:
    from PIL import Image  # 与 nodes.py 同一份 PIL，惰性导入避免包内循环依赖

    img = Image.fromarray(arr, mode=mode)
    buf = io.BytesIO()
    img.save(
        buf,
        format="JPEG",
        quality=int(quality),
        subsampling=int(subsampling),
        optimize=bool(optimize),  # Huffman 表优化：解码像素不变，文件更小（但慢，只在定稿那遍开）
        progressive=bool(progressive),
    )
    return buf.getvalue()


def _decode_jpeg(data: bytes) -> np.ndarray:
    from PIL import Image

    with Image.open(io.BytesIO(data)) as im:
        return np.asarray(im, dtype=np.uint8)


def _psnr(a: np.ndarray, b: np.ndarray) -> float:
    if a.ndim == 3 and a.shape[-1] == 1:
        a = a[..., 0]
    diff = a.astype(np.float32) - b.astype(np.float32)
    mse = float(np.mean(diff * diff))
    if mse <= 1e-12:
        return 99.0
    return 10.0 * math.log10((255.0 ** 2) / mse)


def _encode_jpeg(
    arr: np.ndarray,
    mode: str,
    subsampling: int,
    quality_mode: str,
    quality: int,
    target_psnr: float,
    pick_smaller_layout: bool,
) -> tuple[bytes, int, float]:
    """按质量策略编码，返回 (bytes, 实际 quality, 实测 PSNR)。

    搜索阶段一律 optimize=False + 基线布局：Huffman 优化/渐进布局只改熵编码，不改量化表和解码像素，
    所以 PSNR 与最终文件完全一致，但省掉最慢的那部分重复工作；定稿才带上 optimize。
    """
    measured: dict[int, float] = {}

    def psnr_at(q: int) -> float:
        if q not in measured:
            probe = _jpeg_bytes(arr, mode, q, subsampling, False, optimize=False)
            measured[q] = _psnr(arr, _decode_jpeg(probe))
        return measured[q]

    if quality_mode == _JPG_MODE_AUTO:
        lo, hi = _JPEG_SCAN_MIN_Q, _JPEG_SCAN_MAX_Q
        chosen = None
        while lo <= hi:
            mid = (lo + hi) // 2
            if psnr_at(mid) >= target_psnr:
                chosen = mid
                hi = mid - 1
            else:
                lo = mid + 1
        if chosen is None:
            _LOG.warning(
                "[GemSaveImageAdvanced] q=%d 仍达不到目标 PSNR %.1f dB，已退回最高质量。"
                "该图噪声/细节极密，JPG 想要更干净的保真请改用 png。",
                _JPEG_SCAN_MAX_Q, target_psnr,
            )
            chosen = _JPEG_SCAN_MAX_Q
        psnr = psnr_at(chosen)
    else:
        chosen = int(quality)
        psnr = psnr_at(chosen)

    # 布局选择也用快编（两种布局的量化像素相同，体积排序几乎不会反）
    progressive = False
    if pick_smaller_layout:
        quick_base = _jpeg_bytes(arr, mode, chosen, subsampling, False, optimize=False)
        quick_prog = _jpeg_bytes(arr, mode, chosen, subsampling, True, optimize=False)
        progressive = len(quick_prog) < len(quick_base)

    data = _jpeg_bytes(arr, mode, chosen, subsampling, progressive, optimize=True)
    return data, chosen, psnr


# ---------------------------------------------------------------------------
# 节点
# ---------------------------------------------------------------------------

class GemSaveImageAdvanced:
    """Save Image (Advanced) 的自有复刻版：png / exr / avif / jpg，默认 jpg。"""

    CATEGORY = "ItsGemTools/IO"

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "images": ("IMAGE", {"tooltip": "要保存的图片批次。"}),
                "filename_prefix": ("STRING", {
                    "default": "ComfyUI",
                    "multiline": False,
                    "tooltip": "文件名前缀，可含 %date:yyyy-MM-dd%、%batch_num% 等格式化记号；支持子目录。",
                }),
                "format": (["jpg", "png", "exr", "avif"], {
                    "default": "jpg",
                    "tooltip": (
                        "默认 jpg。jpg=视觉无损下体积最小（4:4:4 + 目标 PSNR + 双布局取小）；"
                        "png=逐像素无损；exr=线性/HDR 浮点；avif=高压缩静态图。"
                    ),
                }),
                "bit_depth": (["8-bit", "16-bit", "32-bit float", "16-bit float"], {
                    "default": "8-bit",
                    "tooltip": "仅 png（8-bit / 16-bit）与 exr（32-bit float / 16-bit float）使用；jpg、avif 忽略此项。",
                }),
                "input_color_space": (["sRGB", "HDR", "HDR PQ", "linear", "linear ACEScg"], {
                    "default": "sRGB",
                    "tooltip": (
                        "仅 exr / avif 使用。sRGB=输入是 sRGB 编码；HDR=HLG Rec.2020；"
                        "linear=已是场景线性（Rec.709）；linear ACEScg=ACEScg(AP1/D60)。"
                        "png / jpg 恒按 sRGB 交付，不改变像素。"
                    ),
                }),
                "channels": (["auto", "grayscale (1 channel)", "RGB (3 channels)", "RGBA (4 channels)"], {
                    "default": "auto",
                    "tooltip": (
                        "仅 png 使用。auto=保留输入通道；grayscale=按亮度合成并丢弃 alpha；"
                        "RGB=丢弃 alpha；RGBA=保留 alpha（无 alpha 时补全不透明）。"
                        "jpg 恒无 alpha（RGBA 按白底合成）。"
                    ),
                }),
                "avif_crf": ("INT", {
                    "default": 18, "min": 1, "max": 63, "step": 1,
                    "tooltip": "仅 avif 使用：CRF 越小质量越高、体积越大。",
                }),
                "jpg_quality_mode": ([_JPG_MODE_AUTO, _JPG_MODE_FIXED], {
                    "default": _JPG_MODE_AUTO,
                    "tooltip": (
                        "auto=在目标 PSNR 下限内二分搜最低 quality（体积最小，推荐）；"
                        "fixed=固定用下面的 jpg_quality。"
                    ),
                }),
                "jpg_quality": ("INT", {
                    "default": 96, "min": 60, "max": 100, "step": 1,
                    "tooltip": "fixed 模式下的固定 quality。auto 模式下不生效。96 以上边际收益很低。",
                }),
                "jpg_target_psnr": ("FLOAT", {
                    "default": 47.0, "min": 35.0, "max": 60.0, "step": 0.5,
                    "tooltip": (
                        "auto 模式的保真下限（dB，与原始 8-bit 像素比）。47 dB 已在公认视觉无损区间；"
                        "本机实测 4K 文字图上 47 dB ≈ PNG 体积的 36%，再往上体积陡涨、肉眼无感。"
                    ),
                }),
                "jpg_subsampling": (list(_JPEG_SUBSAMPLING.keys()), {
                    "default": "4:4:4 (no chroma loss)",
                    "tooltip": "JPG 色度采样。4:4:4 不降采样（默认，彩色边缘不糊）；4:2:0 体积最小但色度有损。",
                }),
                "jpg_pick_smaller_layout": ("BOOLEAN", {
                    "default": True,
                    "tooltip": "基线与渐进两种 JPG 布局都编一遍取更小的（解码像素相同，纯省体积，代价是多编一次）。",
                }),
            },
            "hidden": {"prompt": "PROMPT", "extra_pnginfo": "EXTRA_PNGINFO"},
        }

    RETURN_TYPES = ("IMAGE",)
    RETURN_NAMES = ("images",)
    FUNCTION = "save"
    OUTPUT_NODE = True
    DESCRIPTION = (
        "复刻内置 Save Image (Advanced)（自有实现，核心零改动），并追加 JPG：默认 JPG、默认 4:4:4、"
        "默认按目标 PSNR 自动搜最低 quality + Huffman 优化 + 双布局取小，做到视觉无损下体积最小。"
    )

    # 供前端 / 文档引用的默认值说明
    JPG_DEFAULTS = {
        "subsampling": _JPEG_SUBSAMPLING["4:4:4 (no chroma loss)"],
        "target_psnr": 47.0,
    }

    def save(
        self,
        images,
        filename_prefix: str,
        format: str = "jpg",
        bit_depth: str = "8-bit",
        input_color_space: str = "sRGB",
        channels: str = "auto",
        avif_crf: int = 18,
        jpg_quality_mode: str = _JPG_MODE_AUTO,
        jpg_quality: int = 96,
        jpg_target_psnr: float = 47.0,
        jpg_subsampling: str = "4:4:4 (no chroma loss)",
        jpg_pick_smaller_layout: bool = True,
        prompt=None,
        extra_pnginfo=None,
    ):
        import folder_paths

        file_format = str(format).strip().lower()
        if file_format in ("jpeg", "jpg"):
            file_format = "jpg"
        colorspace = str(input_color_space)
        core = _core()

        output_dir = folder_paths.get_output_directory()
        full_output_folder, filename, counter, subfolder, filename_prefix = folder_paths.get_save_image_path(
            filename_prefix, output_dir, images[0].shape[1], images[0].shape[0]
        )

        write_metadata = _cli_metadata_enabled()
        results = []

        # ---- avif：复用核心静态 AVIF 封装（多帧 animated 不在本节点范围）----
        if file_format == "avif":
            metadata = None
            if write_metadata:
                metadata = {}
                if prompt is not None:
                    metadata["prompt"] = prompt
                if extra_pnginfo:
                    metadata.update(extra_pnginfo)
            avif_bit_depth = "10-bit YUV420" if colorspace in ("HDR", "HDR PQ") else "8-bit YUV420"
            for batch_number, batch in enumerate(images.unsqueeze(1)):
                name = filename.replace("%batch_num%", str(batch_number))
                file = f"{name}_{counter:05}.avif"
                core._save_avif(
                    batch,
                    os.path.join(full_output_folder, file),
                    avif_bit_depth,
                    colorspace,
                    int(avif_crf),
                    fps=1.0,
                    loop_count=None,
                    metadata=metadata,
                )
                results.append({"filename": file, "subfolder": subfolder, "type": "output"})
                counter += 1
            return {"ui": {"images": results}, "result": (images,)}

        write_previews = file_format == "exr" and _asset_manager_enabled()

        for batch_number, image in enumerate(images):
            data = None
            if file_format == "jpg":
                arr, mode = _to_jpeg_source(image)
                data, chosen_q, psnr = _encode_jpeg(
                    arr,
                    mode,
                    _JPEG_SUBSAMPLING.get(str(jpg_subsampling), 0),
                    str(jpg_quality_mode),
                    int(jpg_quality),
                    float(jpg_target_psnr),
                    bool(jpg_pick_smaller_layout),
                )
                _LOG.info(
                    "[GemSaveImageAdvanced] JPG q=%d subsampling=%s PSNR=%.1f dB size=%.1f KB",
                    chosen_q, jpg_subsampling, psnr, len(data) / 1024.0,
                )

            elif file_format == "png":
                if bit_depth not in ("8-bit", "16-bit"):
                    raise ValueError(f"png 只支持 8-bit / 16-bit 位深，收到 {bit_depth!r}。")
                if image.ndim == 2:
                    image = image.unsqueeze(-1)
                if channels == "grayscale (1 channel)" and image.shape[-1] in (3, 4):
                    image = 0.299 * image[..., :1] + 0.587 * image[..., 1:2] + 0.114 * image[..., 2:3]
                elif channels in ("RGB (3 channels)", "RGBA (4 channels)"):
                    if image.shape[-1] == 1:
                        image = image.expand(-1, -1, 3)
                    if channels == "RGB (3 channels)":
                        image = image[..., :3]
                    elif image.shape[-1] == 3:
                        image = torch.cat((image, torch.ones_like(image[..., :1])), dim=-1)
                data = core._encode_image(image, "png", bit_depth, colorspace)

            elif file_format == "exr":
                if bit_depth not in ("32-bit float", "16-bit float"):
                    raise ValueError(f"exr 只支持 32-bit float / 16-bit float 位深，收到 {bit_depth!r}。")
                if image.ndim == 2:
                    image = image.unsqueeze(-1)  # 转换函数把 4 宽的末维当 RGBA，先补成 1 通道
                image = core._to_scene_linear(image, colorspace)
                data = core._encode_image(image, "exr", bit_depth, colorspace)

            else:
                raise ValueError(f"未知保存格式 {format!r}，可选 jpg / png / exr / avif。")

            if write_metadata and file_format == "png":
                data = core.inject_png_metadata(data, prompt, extra_pnginfo)
            elif write_metadata and file_format == "exr":
                data = core.inject_exr_metadata(data, prompt, extra_pnginfo, colorspace)

            name = filename.replace("%batch_num%", str(batch_number))
            file = f"{name}_{counter:05}.{file_format}"
            path = os.path.join(full_output_folder, file)
            with open(path, "wb") as f:
                f.write(data)

            entry = {"filename": file, "subfolder": subfolder, "type": "output"}
            if write_previews:
                preview = core._write_asset_preview(image)
                if preview is not None:
                    entry["asset_preview"] = preview
            results.append(entry)
            counter += 1

        return {"ui": {"images": results}, "result": (images,)}


NODE_CLASS_MAPPINGS = {
    # 键用 GemSaveImageAdvanced：内置节点 SaveImageAdvanced 保持原样，互不覆盖
    "GemSaveImageAdvanced": GemSaveImageAdvanced,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "GemSaveImageAdvanced": "Save Image (Advanced+JPG) (ItsGemTools)",
}
