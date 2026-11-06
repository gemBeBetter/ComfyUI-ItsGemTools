import math
import os
import random
import string
import sys
from pathlib import Path

import numpy as np
import torch
from PIL import Image

try:
    import folder_paths
except Exception:
    folder_paths = None

try:
    from scipy.ndimage import distance_transform_edt
except Exception as e:
    distance_transform_edt = None
    _SCIPY_IMPORT_ERROR = e
else:
    _SCIPY_IMPORT_ERROR = None


def _smoothstep(x, edge0, edge1):
    """Smooth Hermite interpolation, clamped to [0, 1]."""
    t = ((x - edge0) / (edge1 - edge0)).clamp(0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def _gaussian_kernel(size, sigma):
    """Normalized 2D Gaussian kernel as a conv2d weight (1,1,size,size)."""
    coords = torch.arange(size, dtype=torch.float32) - (size - 1) / 2.0
    g = torch.exp(-(coords ** 2) / (2.0 * sigma ** 2))
    g = g / g.sum()
    kernel = torch.outer(g, g).unsqueeze(0).unsqueeze(0)
    return kernel


def _blur_rgb(x_bhwc, sigma, ksize):
    """Gaussian-blur each RGB channel independently (BHWC in/out)."""
    b, h, w, c = x_bhwc.shape
    k = _gaussian_kernel(ksize, sigma).expand(c, 1, ksize, ksize).to(x_bhwc.device)
    return torch.nn.functional.conv2d(
        x_bhwc.permute(0, 3, 1, 2), k, groups=c, padding=ksize // 2,
    ).permute(0, 2, 3, 1)


class MaskFeather:
    """
    Creates a contour-aware, bidirectional feather from a segmentation MASK.
    Signed distance is positive inside the selected region and negative outside.
    The output mask is a smoothstep transition from 0 to 1 across the chosen
    inner and outer feather widths.
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                # 接口排序规范：image 在上、mask 在下，防止交叉连线、提升工作流可读性
                "image": ("IMAGE",),
                "mask": ("MASK",),
                "inner_feather_px": ("INT", {
                    "default": 2, "min": 0, "max": 128, "step": 1,
                    "tooltip": "Feather distance inside the original selection, in pixels."
                }),
                "outer_feather_px": ("INT", {
                    "default": 6, "min": 0, "max": 128, "step": 1,
                    "tooltip": "Feather distance outside the original selection, in pixels."
                }),
                "threshold": ("FLOAT", {
                    "default": 0.5, "min": 0.01, "max": 0.99, "step": 0.01,
                    "tooltip": "Threshold used to convert the incoming mask into a binary contour."
                }),
            },
        }

    RETURN_TYPES = ("IMAGE", "MASK")
    # 输出端排序规范：image（rgba_image）在上、mask（feathered_mask）在下
    RETURN_NAMES = ("rgba_image", "feathered_mask")
    FUNCTION = "feather"
    CATEGORY = "masking/feather"
    DESCRIPTION = (
        "Contour-aware inner/outer feather using a signed Euclidean distance field. "
        "Optionally attaches the feathered mask as alpha to an input image."
    )

    def feather(self, mask, inner_feather_px, outer_feather_px, threshold=0.5, image=None):
        if distance_transform_edt is None:
            raise RuntimeError(
                "MaskFeather requires scipy.ndimage.distance_transform_edt. "
                f"Import error: {_SCIPY_IMPORT_ERROR}"
            )

        # ComfyUI MASK tensors are [B,H,W]. Convert safely to CPU float32.
        masks = mask.detach().to(device="cpu", dtype=torch.float32).numpy()
        if masks.ndim == 2:
            masks = masks[None, ...]
        if masks.ndim != 3:
            raise ValueError(f"Expected MASK shape [B,H,W] or [H,W], got {masks.shape}")

        inner = int(inner_feather_px)
        outer = int(outer_feather_px)
        threshold = float(threshold)
        results = []

        for src in masks:
            binary = src >= threshold

            # Handle degenerate masks without calling EDT on empty/full regions.
            if not binary.any():
                results.append(np.zeros_like(src, dtype=np.float32))
                continue
            if binary.all():
                results.append(np.ones_like(src, dtype=np.float32))
                continue

            # Pixel-center signed distance: positive inside, negative outside.
            # Padding ensures the outside distance is well-defined at image borders.
            padded = np.pad(binary, 1, mode="constant", constant_values=False)
            inside_dist = distance_transform_edt(padded)[1:-1, 1:-1]
            outside_dist = distance_transform_edt(~padded)[1:-1, 1:-1]
            signed = inside_dist - outside_dist

            # Preserve a crisp contour when both widths are zero.
            if inner == 0 and outer == 0:
                alpha = binary.astype(np.float32)
            else:
                # Transition runs from -outer outside to +inner inside.
                # If one side is zero, the transition terminates at the contour.
                lo = -float(outer)
                hi = float(inner)
                if hi <= lo:
                    alpha = binary.astype(np.float32)
                else:
                    t = np.clip((signed - lo) / (hi - lo), 0.0, 1.0)
                    # Smoothstep gives zero slope at both ends.
                    alpha = t * t * (3.0 - 2.0 * t)
                # When a width is zero, keep the transition on the nonzero side
                # while retaining a stable edge at the contour.
                if outer == 0:
                    alpha = np.where(signed < 0, 0.0, alpha)
                if inner == 0:
                    alpha = np.where(signed > 0, 1.0, alpha)

            results.append(np.clip(alpha, 0.0, 1.0).astype(np.float32))

        out_mask = torch.from_numpy(np.stack(results, axis=0))

        # If no image is connected, return a grayscale preview IMAGE as output 2.
        # If image is connected, output RGBA (Comfy IMAGE tensor with 4 channels).
        if image is None:
            preview = out_mask.unsqueeze(-1).repeat(1, 1, 1, 3)
            rgba = preview
        else:
            imgs = image.detach().to(device="cpu", dtype=torch.float32)
            if imgs.ndim != 4:
                raise ValueError(f"Expected IMAGE shape [B,H,W,C], got {tuple(imgs.shape)}")
            if imgs.shape[-1] < 3:
                raise ValueError("Input IMAGE must have at least 3 channels (RGB).")

            if imgs.shape[0] == 1 and out_mask.shape[0] > 1:
                imgs = imgs.repeat(out_mask.shape[0], 1, 1, 1)
            elif out_mask.shape[0] == 1 and imgs.shape[0] > 1:
                out_mask = out_mask.repeat(imgs.shape[0], 1, 1)
            elif imgs.shape[0] != out_mask.shape[0]:
                raise ValueError(
                    f"Batch mismatch: image batch={imgs.shape[0]}, mask batch={out_mask.shape[0]}"
                )

            if imgs.shape[1] != out_mask.shape[1] or imgs.shape[2] != out_mask.shape[2]:
                raise ValueError(
                    "Image and MASK dimensions differ. Resize/align the MASK to the image first. "
                    f"Image={tuple(imgs.shape[1:3])}, MASK={tuple(out_mask.shape[1:3])}"
                )

            rgb = imgs[..., :3].clamp(0.0, 1.0)
            rgba = torch.cat([rgb, out_mask.unsqueeze(-1)], dim=-1)

        # Return tensors on the same device as the incoming mask/image for ComfyUI.
        target_device = image.device if image is not None else mask.device
        return (rgba.to(target_device), out_mask.to(target_device))


class SetImageAlpha:
    """
    Writes a constant alpha value into the input image and outputs it as RGBA.
    RGB channels are preserved; the alpha channel is filled with the given value,
    which controls the image's transparency (1.0 = fully opaque, 0.0 = fully transparent).
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "image": ("IMAGE",),
                "alpha": ("FLOAT", {
                    "default": 1.0, "min": 0.0, "max": 1.0, "step": 0.01,
                    "tooltip": "Alpha value written into the image's alpha channel. 1.0 = fully opaque, 0.0 = fully transparent."
                }),
            }
        }

    RETURN_TYPES = ("IMAGE", "FLOAT")
    RETURN_NAMES = ("rgba_image", "alpha_value")
    FUNCTION = "set_alpha"
    CATEGORY = "image/alpha"
    DESCRIPTION = (
        "Writes a constant alpha into the image's alpha channel and returns the image as RGBA. "
        "The alpha value is also passed through as a FLOAT output for downstream nodes."
    )

    def set_alpha(self, image, alpha):
        imgs = image.detach().to(dtype=torch.float32)
        if imgs.ndim != 4:
            raise ValueError(f"Expected IMAGE shape [B,H,W,C], got {tuple(imgs.shape)}")
        if imgs.shape[-1] < 3:
            raise ValueError("Input IMAGE must have at least 3 channels (RGB).")

        a = torch.full(
            imgs.shape[:3] + (1,),
            float(alpha),
            dtype=torch.float32,
            device=imgs.device,
        )
        rgba = torch.cat([imgs[..., :3].clamp(0.0, 1.0), a], dim=-1)
        return (rgba.to(device=image.device, dtype=image.dtype), float(alpha))


class SetMaskedAlpha:
    """
    Sets a constant alpha value only inside the masked region of an image.
    Pixels where mask >= threshold get `alpha`; all other pixels get `outside_alpha`.
    Returns the RGBA image, the applied alpha channel as a MASK, and the alpha value.
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "image": ("IMAGE",),
                "mask": ("MASK",),
                "alpha": ("FLOAT", {
                    "default": 1.0, "min": 0.0, "max": 1.0, "step": 0.01,
                    "tooltip": "Alpha value applied inside the masked region. 1.0 = fully opaque, 0.0 = fully transparent."
                }),
                "outside_alpha": ("FLOAT", {
                    "default": 0.0, "min": 0.0, "max": 1.0, "step": 0.01,
                    "tooltip": "Alpha value applied outside the masked region. 0.0 = fully transparent (default)."
                }),
                "threshold": ("FLOAT", {
                    "default": 0.5, "min": 0.0, "max": 1.0, "step": 0.01,
                    "tooltip": "Mask values >= threshold are treated as 'selected'."
                }),
            }
        }

    RETURN_TYPES = ("IMAGE", "MASK", "FLOAT")
    RETURN_NAMES = ("rgba_image", "mask_alpha", "alpha_value")
    FUNCTION = "set_masked_alpha"
    CATEGORY = "image/alpha"
    DESCRIPTION = (
        "Writes a constant alpha into the image only inside the masked region. "
        "Outside the mask it is set to outside_alpha (default 0.0 = fully transparent). "
        "The applied alpha channel is also returned as a MASK and the value as FLOAT."
    )

    def set_masked_alpha(self, image, mask, alpha, outside_alpha=0.0, threshold=0.5):
        imgs = image.detach().to(device="cpu", dtype=torch.float32)
        if imgs.ndim != 4:
            raise ValueError(f"Expected IMAGE shape [B,H,W,C], got {tuple(imgs.shape)}")
        if imgs.shape[-1] < 3:
            raise ValueError("Input IMAGE must have at least 3 channels (RGB).")

        m = mask.detach().to(device="cpu", dtype=torch.float32)
        if m.ndim == 2:
            m = m[None, ...]
        if m.ndim != 3:
            raise ValueError(f"Expected MASK shape [B,H,W] or [H,W], got {m.shape}")

        if imgs.shape[0] == 1 and m.shape[0] > 1:
            imgs = imgs.repeat(m.shape[0], 1, 1, 1)
        elif m.shape[0] == 1 and imgs.shape[0] > 1:
            m = m.repeat(imgs.shape[0], 1, 1)
        elif imgs.shape[0] != m.shape[0]:
            raise ValueError(
                f"Batch mismatch: image batch={imgs.shape[0]}, mask batch={m.shape[0]}"
            )

        if imgs.shape[1] != m.shape[1] or imgs.shape[2] != m.shape[2]:
            raise ValueError(
                "Image and MASK dimensions differ. Resize/align the MASK to the image first. "
                f"Image={tuple(imgs.shape[1:3])}, MASK={tuple(m.shape[1:3])}"
            )

        inside = m >= float(threshold)
        a_channel = torch.where(
            inside,
            torch.full_like(m, float(alpha)),
            torch.full_like(m, float(outside_alpha)),
        )
        rgba = torch.cat([imgs[..., :3].clamp(0.0, 1.0), a_channel.unsqueeze(-1)], dim=-1)
        return (
            rgba.to(device=image.device, dtype=image.dtype),
            a_channel.to(device=mask.device, dtype=mask.dtype),
            float(alpha),
        )


class AdjustColor:
    """
    Reproduces the tone / color adjustment panel found in JianYing (剪映) and
    Photoshop (Camera Raw). Every parameter is a slider with a sensible range
    and a Chinese tooltip describing what it does.
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "image": ("IMAGE",),
                # ---- 色彩 ----
                "color_temperature": ("FLOAT", {
                    "default": 0, "min": -100, "max": 100, "step": 1,
                    "tooltip": "色温：负值偏冷（蓝），正值偏暖（黄），0 为原样。"
                }),
                "tint": ("FLOAT", {
                    "default": 0, "min": -100, "max": 100, "step": 1,
                    "tooltip": "色调：负值偏绿，正值偏品红，0 为原样。"
                }),
                "saturation": ("FLOAT", {
                    "default": 0, "min": -100, "max": 100, "step": 1,
                    "tooltip": "饱和度：负值降低色彩鲜艳度（-100 变为灰度），正值提高，0 为原样。"
                }),
                "vibrance": ("FLOAT", {
                    "default": 0, "min": -100, "max": 100, "step": 1,
                    "tooltip": "自然饱和度：智能增强，优先提高低饱和区域并保护肤色，0 为原样。"
                }),
                # ---- 明度 ----
                "brightness": ("FLOAT", {
                    "default": 0, "min": -100, "max": 100, "step": 1,
                    "tooltip": "亮度：负值变暗，正值变亮，0 为原样。"
                }),
                "contrast": ("FLOAT", {
                    "default": 0, "min": -100, "max": 100, "step": 1,
                    "tooltip": "对比度：负值降低明暗反差（-100 接近均匀灰），正值增强，0 为原样。"
                }),
                "highlights": ("FLOAT", {
                    "default": 0, "min": -100, "max": 100, "step": 1,
                    "tooltip": "高光：只调整画面亮部区域，负值压暗高光，正值提亮，0 为原样。"
                }),
                "shadows": ("FLOAT", {
                    "default": 0, "min": -100, "max": 100, "step": 1,
                    "tooltip": "阴影：只调整画面暗部区域，负值压暗暗部，正值提亮，0 为原样。"
                }),
                "whites": ("FLOAT", {
                    "default": 0, "min": -100, "max": 100, "step": 1,
                    "tooltip": "白色：调整最亮一档（白场），负值收窄高光，正值提亮，0 为原样。"
                }),
                "blacks": ("FLOAT", {
                    "default": 0, "min": -100, "max": 100, "step": 1,
                    "tooltip": "黑色：调整最暗一档（黑场），负值加深暗部，正值提亮，0 为原样。"
                }),
                "light_feel": ("FLOAT", {
                    "default": 0, "min": 0, "max": 100, "step": 1,
                    "tooltip": "光感（剪映特色）：提升画面整体通透感与光线氛围，0 关闭。"
                }),
                "auto_refresh": ("BOOLEAN", {
                    "default": True,
                    "tooltip": "自动刷新：开启后拖动本节点参数，预览自动更新（仅本节点生效，不影响全局自动队列）。"
                }),
            }
        }

    RETURN_TYPES = ("IMAGE",)
    RETURN_NAMES = ("image",)
    FUNCTION = "adjust"
    CATEGORY = "image/color"
    DESCRIPTION = (
        "调色：复现剪映/PS 的色调调整面板（色温、色调、饱和度、自然饱和度、"
        "亮度、对比度、高光、阴影、白色、黑色、光感）。全部参数为拖动条，"
        "输出调色后的图片，可接内置 Preview Image 实时预览。"
    )

    def adjust(
        self, image,
        color_temperature=0, tint=0, saturation=0, vibrance=0,
        brightness=0, contrast=0, highlights=0, shadows=0,
        whites=0, blacks=0, light_feel=0, auto_refresh=True,
    ):
        imgs = image.detach().to(device="cpu", dtype=torch.float32)
        if imgs.ndim != 4:
            raise ValueError(f"Expected IMAGE shape [B,H,W,C], got {tuple(imgs.shape)}")
        if imgs.shape[-1] < 3:
            raise ValueError("Input IMAGE must have at least 3 channels (RGB).")

        rgb = imgs[..., :3].clamp(0.0, 1.0)

        # 1) 色温：R/B 通道反向增益（正=暖，负=冷），G 轻微跟随防偏色
        t = float(color_temperature) / 100.0
        if t != 0:
            rgb = torch.stack([
                rgb[..., 0] * (1.0 + 0.25 * t),
                rgb[..., 1] * (1.0 - 0.06 * t),
                rgb[..., 2] * (1.0 - 0.25 * t),
            ], dim=-1)

        # 2) 色调：绿↔品红（负=绿，正=品红）
        tv = float(tint) / 100.0
        if tv != 0:
            rgb = torch.stack([
                rgb[..., 0] * (1.0 + 0.20 * tv),
                rgb[..., 1] * (1.0 - 0.20 * tv),
                rgb[..., 2] * (1.0 + 0.20 * tv),
            ], dim=-1)

        # 3) 饱和度：线性去饱和/增饱和
        sat = float(saturation) / 100.0
        if sat != 0:
            gray = rgb.mean(dim=-1, keepdim=True)
            rgb = gray + (rgb - gray) * (1.0 + sat)

        # 4) 自然饱和度：按像素饱和度加权，低饱和区增益大、肤色区受保护
        vib = float(vibrance) / 100.0
        if vib != 0:
            maxc, _ = torch.max(rgb, dim=-1, keepdim=True)
            minc, _ = torch.min(rgb, dim=-1, keepdim=True)
            denom = maxc + 1e-6
            per_pix_sat = (maxc - minc) / denom
            r = rgb[..., 0:1]
            g = rgb[..., 1:2]
            bch = rgb[..., 2:3]
            skin = ((r > g) & (g > bch) & ((r - bch) > 0.1)).to(rgb.dtype)
            weight = (1.0 - per_pix_sat) * (1.0 - 0.6 * skin)
            gray = rgb.mean(dim=-1, keepdim=True)
            rgb = rgb + (rgb - gray) * (vib * weight)

        # 5) 亮度：线性偏移
        b = float(brightness) / 100.0
        if b != 0:
            rgb = rgb + b * 0.5

        # 6) 对比度：围绕 0.5 缩放
        c = float(contrast) / 100.0
        if c != 0:
            rgb = (rgb - 0.5) * (1.0 + c) + 0.5

        # 7) 高光 / 阴影 / 白色 / 黑色：按亮度分区映射
        h = float(highlights) / 100.0
        s = float(shadows) / 100.0
        w = float(whites) / 100.0
        k = float(blacks) / 100.0
        if h != 0 or s != 0 or w != 0 or k != 0:
            lum = rgb.mean(dim=-1, keepdim=True)
            hi = _smoothstep(lum, 0.5, 1.0)       # 高光权重：亮部
            sh = 1.0 - _smoothstep(lum, 0.0, 0.5)  # 阴影权重：暗部
            wh = _smoothstep(lum, 0.65, 1.0)      # 白场权重：最亮一档
            bk = 1.0 - _smoothstep(lum, 0.0, 0.35)  # 黑场权重：最暗一档
            delta = h * 0.5 * hi + s * 0.5 * sh + w * 0.35 * wh + k * 0.35 * bk
            rgb = rgb + delta

        # 8) 光感：伽马提亮暗部 + 轻微整体增益（通透感）
        g = float(light_feel) / 100.0
        if g > 0:
            strength = g * 0.35
            rgb = rgb ** (1.0 / (1.0 + strength * 0.6))
            rgb = rgb * (1.0 + strength * 0.3)

        out = rgb.clamp(0.0, 1.0)
        return (out.to(device=image.device, dtype=image.dtype),)


class BeforeAfterPreview:
    """
    Real-time preview helper: places the 'before' and 'after' images side by side
    into a single image. Wire Adjust Color's output into 'after', keep the original
    in 'before', and connect the result to ComfyUI's Preview Image — every slider
    move updates the comparison instantly.
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "before": ("IMAGE",),
                "after": ("IMAGE",),
                "gap_px": ("INT", {
                    "default": 8, "min": 0, "max": 64, "step": 1,
                    "tooltip": "原图与调色图之间的分隔条宽度（像素），0 表示不分隔。"
                }),
            }
        }

    RETURN_TYPES = ("IMAGE",)
    RETURN_NAMES = ("preview",)
    FUNCTION = "combine"
    CATEGORY = "image/color"
    OUTPUT_NODE = True
    DESCRIPTION = (
        "实时预览：把原图与调色结果左右并排拼成一张图，接到 Preview Image 即可"
        "边拖参数边看前后对比。两张图尺寸不同时会自动把 after 缩放到 before 的尺寸。"
    )

    def combine(self, before, after, gap_px=8):
        import torch.nn.functional as F

        a = before.detach().to(device="cpu", dtype=torch.float32)[..., :3]
        b = after.detach().to(device="cpu", dtype=torch.float32)[..., :3]

        if a.shape[0] == 1 and b.shape[0] > 1:
            a = a.repeat(b.shape[0], 1, 1, 1)
        elif b.shape[0] == 1 and a.shape[0] > 1:
            b = b.repeat(a.shape[0], 1, 1, 1)
        elif a.shape[0] != b.shape[0]:
            raise ValueError(
                f"Batch mismatch: before={a.shape[0]}, after={b.shape[0]}"
            )

        if a.shape[1:3] != b.shape[1:3]:
            # 把 after 缩放到 before 尺寸（等比拉伸）
            b = F.interpolate(
                b.permute(0, 3, 1, 2),
                size=(int(a.shape[1]), int(a.shape[2])),
                mode="bilinear",
                align_corners=False,
            ).permute(0, 2, 3, 1)

        gap = int(gap_px)
        if gap > 0:
            gap_img = torch.full(
                (a.shape[0], a.shape[1], gap, a.shape[3]), 0.5
            )
            out = torch.cat([a, gap_img, b], dim=2)
        else:
            out = torch.cat([a, b], dim=2)
        return (out.to(device=before.device, dtype=before.dtype),)


class AdjustEffect:
    """
    效果调整器：复现剪映/Photoshop 的「效果」面板（锐化、清晰、颗粒、褪色、暗角）。
    全部参数为拖动条，带中文标注说明，可接 Before/After Preview 实时对比。
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "image": ("IMAGE",),
                "sharpen": ("FLOAT", {
                    "default": 0, "min": 0, "max": 100, "step": 1,
                    "tooltip": "锐化：增强边缘细节清晰度，0 关闭。"
                }),
                "clarity": ("FLOAT", {
                    "default": 0, "min": 0, "max": 100, "step": 1,
                    "tooltip": "清晰：增强局部对比度（类似 PS 清晰度/剪映清晰），让画面更通透，0 关闭。"
                }),
                "grain": ("FLOAT", {
                    "default": 0, "min": 0, "max": 100, "step": 1,
                    "tooltip": "颗粒：叠加胶片颗粒噪点，暗部更明显，0 关闭。"
                }),
                "fade": ("FLOAT", {
                    "default": 0, "min": 0, "max": 100, "step": 1,
                    "tooltip": "褪色：整体降低对比并向灰色偏移（低饱和雾感），0 关闭。"
                }),
                "vignette": ("FLOAT", {
                    "default": 0, "min": -100, "max": 100, "step": 1,
                    "tooltip": "暗角：正值四周压暗、突出中心；负值四周提亮（亮角），0 关闭。"
                }),
                "auto_refresh": ("BOOLEAN", {
                    "default": True,
                    "tooltip": "自动刷新：开启后拖动本节点参数，预览自动更新（仅本节点生效，不影响全局自动队列）。"
                }),
            }
        }

    RETURN_TYPES = ("IMAGE",)
    RETURN_NAMES = ("image",)
    FUNCTION = "apply"
    CATEGORY = "image/effect"
    DESCRIPTION = (
        "效果：复现剪映/PS 的效果面板（锐化、清晰、颗粒、褪色、暗角）。"
        "全部参数为拖动条，auto_refresh 开启后参数变化自动刷新预览（仅本节点）。"
    )

    def apply(self, image, sharpen=0, clarity=0, grain=0, fade=0, vignette=0, auto_refresh=True):
        import torch.nn.functional as F

        imgs = image.detach().to(device="cpu", dtype=torch.float32)
        if imgs.ndim != 4:
            raise ValueError(f"Expected IMAGE shape [B,H,W,C], got {tuple(imgs.shape)}")
        if imgs.shape[-1] < 3:
            raise ValueError("Input IMAGE must have at least 3 channels (RGB).")

        rgb = imgs[..., :3].clamp(0.0, 1.0)
        B, H, W = rgb.shape[0], int(rgb.shape[1]), int(rgb.shape[2])

        # 1) 清晰（大核 unsharp：中低频局部对比，类似 PS 清晰度）
        cl = float(clarity) / 100.0
        if cl != 0:
            lum4 = rgb.mean(dim=-1, keepdim=True).permute(0, 3, 1, 2)  # (B,1,H,W)
            blur = F.conv2d(lum4, _gaussian_kernel(7, 1.2), padding=3)
            rgb = rgb + (lum4 - blur).permute(0, 2, 3, 1) * (cl * 0.6)

        # 2) 锐化（小核 unsharp：边缘细节）
        sh = float(sharpen) / 100.0
        if sh != 0:
            lum4 = rgb.mean(dim=-1, keepdim=True).permute(0, 3, 1, 2)
            blur = F.conv2d(lum4, _gaussian_kernel(3, 0.8), padding=1)
            rgb = rgb + (lum4 - blur).permute(0, 2, 3, 1) * (sh * 1.2)

        # 3) 颗粒：单通道亮度噪声，暗部更明显
        gr = float(grain) / 100.0
        if gr != 0:
            noise = torch.randn(B, H, W, 1)
            lum_n = rgb.mean(dim=-1, keepdim=True)
            noise = noise * (1.2 - lum_n * 0.8)
            rgb = rgb + noise * (gr * 0.12)

        # 4) 褪色：向中灰偏移、降低对比
        fd = float(fade) / 100.0
        if fd != 0:
            rgb = rgb * (1.0 - fd) + 0.5 * fd

        # 5) 暗角：径向因子（正=压暗四周，负=提亮四周）
        vg = float(vignette) / 100.0
        if vg != 0:
            yy, xx = torch.meshgrid(
                torch.linspace(0.0, 1.0, H),
                torch.linspace(0.0, 1.0, W),
                indexing="ij",
            )
            d2 = ((xx - 0.5) ** 2 + (yy - 0.5) ** 2) * 2.0  # 角落=1
            d2 = d2.to(rgb.device).to(rgb.dtype)
            if vg > 0:
                factor = 1.0 - vg * 0.7 * d2
            else:
                factor = 1.0 + (-vg) * 0.5 * d2
            rgb = rgb * factor.unsqueeze(-1)

        return (rgb.clamp(0.0, 1.0).to(device=image.device, dtype=image.dtype),)


class Adjust:
    """
    调色 + 效果 合并节点（一键完成剪映式「调节 + 效果」两套面板）：
    色彩（色温/色调/饱和度/自然饱和度）+ 明度（亮度/对比度/高光/阴影/白色/黑色/光感）
    + 效果（锐化/清晰/颗粒/褪色/暗角）。全部参数为拖动条，auto_refresh 自动刷新预览。
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "image": ("IMAGE",),
                # ---- 色彩 ----
                "color_temperature": ("FLOAT", {
                    "default": 0, "min": -100, "max": 100, "step": 1,
                    "tooltip": "色温：负值偏冷（蓝），正值偏暖（黄），0 为原样。"
                }),
                "tint": ("FLOAT", {
                    "default": 0, "min": -100, "max": 100, "step": 1,
                    "tooltip": "色调：负值偏绿，正值偏品红，0 为原样。"
                }),
                "saturation": ("FLOAT", {
                    "default": 0, "min": -100, "max": 100, "step": 1,
                    "tooltip": "饱和度：负值降低色彩鲜艳度（-100 变为灰度），正值提高，0 为原样。"
                }),
                "vibrance": ("FLOAT", {
                    "default": 0, "min": -100, "max": 100, "step": 1,
                    "tooltip": "自然饱和度：智能增强，优先提高低饱和区域并保护肤色，0 为原样。"
                }),
                # ---- 明度 ----
                "brightness": ("FLOAT", {
                    "default": 0, "min": -100, "max": 100, "step": 1,
                    "tooltip": "亮度：负值变暗，正值变亮，0 为原样。"
                }),
                "contrast": ("FLOAT", {
                    "default": 0, "min": -100, "max": 100, "step": 1,
                    "tooltip": "对比度：负值降低明暗反差（-100 接近均匀灰），正值增强，0 为原样。"
                }),
                "highlights": ("FLOAT", {
                    "default": 0, "min": -100, "max": 100, "step": 1,
                    "tooltip": "高光：只调整画面亮部区域，负值压暗高光，正值提亮，0 为原样。"
                }),
                "shadows": ("FLOAT", {
                    "default": 0, "min": -100, "max": 100, "step": 1,
                    "tooltip": "阴影：只调整画面暗部区域，负值压暗暗部，正值提亮，0 为原样。"
                }),
                "whites": ("FLOAT", {
                    "default": 0, "min": -100, "max": 100, "step": 1,
                    "tooltip": "白色：调整最亮一档（白场），负值收窄高光，正值提亮，0 为原样。"
                }),
                "blacks": ("FLOAT", {
                    "default": 0, "min": -100, "max": 100, "step": 1,
                    "tooltip": "黑色：调整最暗一档（黑场），负值加深暗部，正值提亮，0 为原样。"
                }),
                "light_feel": ("FLOAT", {
                    "default": 0, "min": 0, "max": 100, "step": 1,
                    "tooltip": "光感（剪映特色）：提升画面整体通透感与光线氛围，0 关闭。"
                }),
                # ---- 效果 ----
                "sharpen": ("FLOAT", {
                    "default": 0, "min": 0, "max": 100, "step": 1,
                    "tooltip": "锐化：增强边缘细节清晰度，0 关闭。"
                }),
                "clarity": ("FLOAT", {
                    "default": 0, "min": 0, "max": 100, "step": 1,
                    "tooltip": "清晰：增强局部对比度（类似 PS 清晰度/剪映清晰），让画面更通透，0 关闭。"
                }),
                "grain": ("FLOAT", {
                    "default": 0, "min": 0, "max": 100, "step": 1,
                    "tooltip": "颗粒：叠加胶片颗粒噪点，暗部更明显，0 关闭。"
                }),
                "fade": ("FLOAT", {
                    "default": 0, "min": 0, "max": 100, "step": 1,
                    "tooltip": "褪色：整体降低对比并向灰色偏移（低饱和雾感），0 关闭。"
                }),
                "vignette": ("FLOAT", {
                    "default": 0, "min": -100, "max": 100, "step": 1,
                    "tooltip": "暗角：正值四周压暗、突出中心；负值四周提亮（亮角），0 关闭。"
                }),
                "auto_refresh": ("BOOLEAN", {
                    "default": True,
                    "tooltip": "自动刷新：开启后拖动本节点参数，预览自动更新（仅本节点生效，不影响全局自动队列）。"
                }),
            }
        }

    RETURN_TYPES = ("IMAGE",)
    RETURN_NAMES = ("image",)
    FUNCTION = "adjust"
    CATEGORY = "image/color"
    DESCRIPTION = (
        "调色 + 效果 合并节点：复现剪映「调节 + 效果」两套面板于一个节点"
        "（色温/色调/饱和度/自然饱和度/亮度/对比度/高光/阴影/白色/黑色/光感"
        "+ 锐化/清晰/颗粒/褪色/暗角）。auto_refresh 开启后参数变化自动刷新预览。"
    )

    def adjust(
        self, image,
        color_temperature=0, tint=0, saturation=0, vibrance=0,
        brightness=0, contrast=0, highlights=0, shadows=0,
        whites=0, blacks=0, light_feel=0,
        sharpen=0, clarity=0, grain=0, fade=0, vignette=0,
        auto_refresh=True,
    ):
        import torch.nn.functional as F

        imgs = image.detach().to(device="cpu", dtype=torch.float32)
        if imgs.ndim != 4:
            raise ValueError(f"Expected IMAGE shape [B,H,W,C], got {tuple(imgs.shape)}")
        if imgs.shape[-1] < 3:
            raise ValueError("Input IMAGE must have at least 3 channels (RGB).")

        rgb = imgs[..., :3].clamp(0.0, 1.0)
        B, H, W = rgb.shape[0], int(rgb.shape[1]), int(rgb.shape[2])

        # ================= 调色（与 AdjustColor 一致） =================
        # 1) 色温：R/B 通道反向增益（正=暖，负=冷），G 轻微跟随防偏色
        t = float(color_temperature) / 100.0
        if t != 0:
            rgb = torch.stack([
                rgb[..., 0] * (1.0 + 0.25 * t),
                rgb[..., 1] * (1.0 - 0.06 * t),
                rgb[..., 2] * (1.0 - 0.25 * t),
            ], dim=-1)

        # 2) 色调：绿↔品红（负=绿，正=品红）
        tv = float(tint) / 100.0
        if tv != 0:
            rgb = torch.stack([
                rgb[..., 0] * (1.0 + 0.20 * tv),
                rgb[..., 1] * (1.0 - 0.20 * tv),
                rgb[..., 2] * (1.0 + 0.20 * tv),
            ], dim=-1)

        # 3) 饱和度：线性去饱和/增饱和
        sat = float(saturation) / 100.0
        if sat != 0:
            gray = rgb.mean(dim=-1, keepdim=True)
            rgb = gray + (rgb - gray) * (1.0 + sat)

        # 4) 自然饱和度：按像素饱和度加权，低饱和区增益大、肤色区受保护
        vib = float(vibrance) / 100.0
        if vib != 0:
            maxc, _ = torch.max(rgb, dim=-1, keepdim=True)
            minc, _ = torch.min(rgb, dim=-1, keepdim=True)
            denom = maxc + 1e-6
            per_pix_sat = (maxc - minc) / denom
            r = rgb[..., 0:1]
            g = rgb[..., 1:2]
            bch = rgb[..., 2:3]
            skin = ((r > g) & (g > bch) & ((r - bch) > 0.1)).to(rgb.dtype)
            weight = (1.0 - per_pix_sat) * (1.0 - 0.6 * skin)
            gray = rgb.mean(dim=-1, keepdim=True)
            rgb = rgb + (rgb - gray) * (vib * weight)

        # 5) 亮度：线性偏移
        b = float(brightness) / 100.0
        if b != 0:
            rgb = rgb + b * 0.5

        # 6) 对比度：围绕 0.5 缩放
        c = float(contrast) / 100.0
        if c != 0:
            rgb = (rgb - 0.5) * (1.0 + c) + 0.5

        # 7) 高光 / 阴影 / 白色 / 黑色：按亮度分区映射
        h = float(highlights) / 100.0
        s = float(shadows) / 100.0
        w = float(whites) / 100.0
        k = float(blacks) / 100.0
        if h != 0 or s != 0 or w != 0 or k != 0:
            lum = rgb.mean(dim=-1, keepdim=True)
            hi = _smoothstep(lum, 0.5, 1.0)       # 高光权重：亮部
            sh = 1.0 - _smoothstep(lum, 0.0, 0.5)  # 阴影权重：暗部
            wh = _smoothstep(lum, 0.65, 1.0)      # 白场权重：最亮一档
            bk = 1.0 - _smoothstep(lum, 0.0, 0.35)  # 黑场权重：最暗一档
            delta = h * 0.5 * hi + s * 0.5 * sh + w * 0.35 * wh + k * 0.35 * bk
            rgb = rgb + delta

        # 8) 光感：伽马提亮暗部 + 轻微整体增益（通透感）
        g = float(light_feel) / 100.0
        if g > 0:
            strength = g * 0.35
            rgb = rgb ** (1.0 / (1.0 + strength * 0.6))
            rgb = rgb * (1.0 + strength * 0.3)

        # ================= 效果（与 AdjustEffect 一致） =================
        # 9) 清晰（大核 unsharp：中低频局部对比）
        cl = float(clarity) / 100.0
        if cl != 0:
            lum4 = rgb.mean(dim=-1, keepdim=True).permute(0, 3, 1, 2)
            blur = F.conv2d(lum4, _gaussian_kernel(7, 1.2), padding=3)
            rgb = rgb + (lum4 - blur).permute(0, 2, 3, 1) * (cl * 0.6)

        # 10) 锐化（小核 unsharp：边缘细节）
        sh2 = float(sharpen) / 100.0
        if sh2 != 0:
            lum4 = rgb.mean(dim=-1, keepdim=True).permute(0, 3, 1, 2)
            blur = F.conv2d(lum4, _gaussian_kernel(3, 0.8), padding=1)
            rgb = rgb + (lum4 - blur).permute(0, 2, 3, 1) * (sh2 * 1.2)

        # 11) 颗粒：单通道亮度噪声，暗部更明显
        gr = float(grain) / 100.0
        if gr != 0:
            noise = torch.randn(B, H, W, 1)
            lum_n = rgb.mean(dim=-1, keepdim=True)
            noise = noise * (1.2 - lum_n * 0.8)
            rgb = rgb + noise * (gr * 0.12)

        # 12) 褪色：向中灰偏移、降低对比
        fd = float(fade) / 100.0
        if fd != 0:
            rgb = rgb * (1.0 - fd) + 0.5 * fd

        # 13) 暗角：径向因子（正=压暗四周，负=提亮四周）
        vg = float(vignette) / 100.0
        if vg != 0:
            yy, xx = torch.meshgrid(
                torch.linspace(0.0, 1.0, H),
                torch.linspace(0.0, 1.0, W),
                indexing="ij",
            )
            d2 = ((xx - 0.5) ** 2 + (yy - 0.5) ** 2) * 2.0  # 角落=1
            d2 = d2.to(rgb.device).to(rgb.dtype)
            if vg > 0:
                factor = 1.0 - vg * 0.7 * d2
            else:
                factor = 1.0 + (-vg) * 0.5 * d2
            rgb = rgb * factor.unsqueeze(-1)

        out = rgb.clamp(0.0, 1.0)
        return (out.to(device=image.device, dtype=image.dtype),)


class SkinBeauty:
    """
    美颜（皮肤管理）：复现剪映「美颜美体 → 皮肤管理」面板。
    磨皮 / 美白 / 匀肤 / 丰盈 / 祛斑祛痘 / 祛法令纹 / 祛黑眼圈 / 清晰。

    - 内置肤色检测：所有效果默认只作用于皮肤区域（r>g>b、非过亮过暗）。
    - 可选 mask 输入（如 SAM3_Detect 输出的面部/皮肤 mask）：
      传入后效果区域 = 肤色区 ∩ mask，实现「精准分区」美颜（类似剪映单人脸模式）。
    - 未接 mask 时自动检测肤色全局处理。
    - feather 控制区域边缘羽化过渡（0 关闭，默认 20 柔和过渡）。
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "image": ("IMAGE",),
                "smooth": ("FLOAT", {
                    "default": 0, "min": 0, "max": 100, "step": 1,
                    "tooltip": "磨皮：平滑皮肤高频细节（瑕疵、毛孔），0 关闭。"
                }),
                "whiten": ("FLOAT", {
                    "default": 0, "min": 0, "max": 100, "step": 1,
                    "tooltip": "美白：提亮肤色，同时保护高光不过曝，0 关闭。"
                }),
                "unify": ("FLOAT", {
                    "default": 0, "min": 0, "max": 100, "step": 1,
                    "tooltip": "匀肤：将肤色不均匀区域向平均肤色统一（去红/去黄斑驳），0 关闭。"
                }),
                "plump": ("FLOAT", {
                    "default": 0, "min": 0, "max": 100, "step": 1,
                    "tooltip": "丰盈：轻微柔焦 + 高光提升，让皮肤饱满有光泽，0 关闭。"
                }),
                "blemish": ("FLOAT", {
                    "default": 0, "min": 0, "max": 100, "step": 1,
                    "tooltip": "祛斑祛痘：局部平滑细节密集区域（斑点、痘印），0 关闭。"
                }),
                "wrinkle": ("FLOAT", {
                    "default": 0, "min": 0, "max": 100, "step": 1,
                    "tooltip": "祛法令纹：在脸部暗部区域做宽幅平滑（法令纹/细纹），0 关闭。"
                }),
                "dark_circle": ("FLOAT", {
                    "default": 0, "min": 0, "max": 100, "step": 1,
                    "tooltip": "祛黑眼圈：提亮眼部下方暗区，0 关闭。"
                }),
                "clarity": ("FLOAT", {
                    "default": 0, "min": 0, "max": 100, "step": 1,
                    "tooltip": "清晰：增强局部对比度，让皮肤纹理更通透，0 关闭。"
                }),
                "feather": ("FLOAT", {
                    "default": 20, "min": 0, "max": 100, "step": 1,
                    "tooltip": "羽化：美颜区域边缘的过渡柔和度，0 为硬边，越高过渡越柔和。"
                }),
                "auto_refresh": ("BOOLEAN", {
                    "default": True,
                    "tooltip": "自动刷新：开启后拖动本节点参数，预览自动更新（仅本节点生效，不影响全局自动队列）。"
                }),
            },
            "optional": {
                "mask": ("MASK",),
            },
        }

    RETURN_TYPES = ("IMAGE",)
    RETURN_NAMES = ("image",)
    FUNCTION = "beauty"
    CATEGORY = "image/beauty"
    DESCRIPTION = (
        "美颜（皮肤管理）：复现剪映「美颜美体 → 皮肤管理」面板（磨皮、美白、匀肤、"
        "丰盈、祛斑祛痘、祛法令纹、祛黑眼圈、清晰）。内置肤色检测，默认只作用于皮肤；"
        "可选 mask 输入（如 SAM3_Detect 的 MASK）实现精准分区美颜；feather 控制边缘羽化。"
    )

    @staticmethod
    def _gaussian_2d(sigma, ksize):
        """Return a 2D kernel tensor (1,1,k,k) for conv2d."""
        coords = torch.arange(ksize, dtype=torch.float32) - (ksize - 1) / 2.0
        g = torch.exp(-(coords ** 2) / (2.0 * sigma * sigma))
        g = g / g.sum()
        return torch.outer(g, g).unsqueeze(0).unsqueeze(0)

    @classmethod
    def _resize_mask(cls, mask, H, W, device, dtype):
        """Mask (N,H,W)/(H,W) -> (1,H,W,1), resized to image size, feather-blurred."""
        m = mask.float()
        if m.ndim == 2:
            m = m.unsqueeze(0)
        if m.ndim == 3:
            m = m.unsqueeze(0)  # (1,N,H,W)
        if m.shape[2] != H or m.shape[3] != W:
            m = torch.nn.functional.interpolate(
                m, size=(H, W), mode="bilinear", align_corners=False,
            )
        m = m[0, 0].clamp(0.0, 1.0).to(device=device).to(dtype=dtype)
        return m.unsqueeze(0).unsqueeze(-1)  # (1,H,W,1)

    @staticmethod
    def _feather(region, feather, H, W):
        """Gaussian-feather the region weights. feather 0..100."""
        f = float(feather) / 100.0
        if f <= 0:
            return region
        # sigma 与图像尺寸挂钩：小图也需要过渡带，大图过渡带成比例加宽。
        sigma = 2.0 + f * (0.04 + 0.10 * (max(H, W) / 1024.0))
        ksize = int(round(sigma * 3)) * 2 + 1
        region4 = region.permute(0, 3, 1, 2)
        region4 = torch.nn.functional.conv2d(
            region4, SkinBeauty._gaussian_2d(sigma, ksize).to(region4.device),
            padding=ksize // 2,
        )
        return region4.permute(0, 2, 3, 1).clamp(0.0, 1.0)

    @staticmethod
    def _skin_mask(rgb):
        """Per-pixel skin probability (0..1): warm reddish pixels, not too dark/bright."""
        r = rgb[..., 0:1]
        g = rgb[..., 1:2]
        b = rgb[..., 2:3]
        lum = rgb.mean(dim=-1, keepdim=True)
        warm = (r > g) & (g > b) & ((r - b) > 0.04)
        skin = warm.to(rgb.dtype) * (1.0 - torch.clamp((lum - 0.75) * 4.0, 0.0, 1.0))
        skin = skin * (1.0 - torch.clamp((0.18 - lum) * 6.0, 0.0, 1.0))
        # Blur to soften the hard r>g>b boundary.
        skin4 = skin.permute(0, 3, 1, 2)
        skin4 = torch.nn.functional.conv2d(skin4, _gaussian_kernel(11, 2.2), padding=5)
        return skin4.permute(0, 2, 3, 1).clamp(0.0, 1.0)

    def beauty(self, image, smooth=0, whiten=0, unify=0, plump=0, blemish=0,
               wrinkle=0, dark_circle=0, clarity=0, feather=20, auto_refresh=True,
               mask=None):
        import torch.nn.functional as F

        imgs = image.detach().to(device="cpu", dtype=torch.float32)
        if imgs.ndim != 4:
            raise ValueError(f"Expected IMAGE shape [B,H,W,C], got {tuple(imgs.shape)}")
        if imgs.shape[-1] < 3:
            raise ValueError("Input IMAGE must have at least 3 channels (RGB).")

        rgb = imgs[..., :3].clamp(0.0, 1.0)
        B, H, W = rgb.shape[0], int(rgb.shape[1]), int(rgb.shape[2])
        device = image.device

        # 区域权重 = 肤色区 ∩ 可选外部 mask（SAM3 等），最后整体羽化过渡
        region = self._skin_mask(rgb)                       # (B,H,W,1)
        if mask is not None:
            ext = self._resize_mask(mask, H, W, device, rgb.dtype)  # (1,H,W,1)
            region = region * ext
        region = self._feather(region, feather, H, W)

        out = rgb

        # 1) 磨皮：频率分离平滑（大核模糊低频保留，高频按皮肤区衰减）
        sm = float(smooth) / 100.0
        if sm > 0:
            base = _blur_rgb(out, 4.0, 21)
            out = out - (out - base) * (sm * 0.9) * region

        # 2) 美白：肤色区提亮，高光处减弱
        wt = float(whiten) / 100.0
        if wt > 0:
            lum = out.mean(dim=-1, keepdim=True)
            gain = 1.0 + wt * 0.22 * (1.0 - torch.clamp((lum - 0.7) * 2.5, 0.0, 1.0))
            out = out + out * (gain - 1.0) * region

        # 3) 匀肤：肤色区向平均肤色统一
        uf = float(unify) / 100.0
        if uf > 0:
            wsum = region.sum(dim=(1, 2, 3), keepdim=True) + 1e-6
            avg = (out * region).sum(dim=(1, 2, 3), keepdim=True) / wsum
            out = out + (avg - out) * (uf * 0.5) * region

        # 4) 丰盈：轻微柔焦 + 高光提升（皮肤饱满感）
        pl = float(plump) / 100.0
        if pl > 0:
            soft = _blur_rgb(out, 2.0, 9)
            lum = out.mean(dim=-1, keepdim=True)
            lift = torch.clamp((0.55 - lum) * 1.2, 0.0, 1.0)  # 中暗区高光微提
            out = out + (soft - out) * (pl * 0.35) * region
            out = out + out * (pl * 0.10) * lift * region

        # 5) 祛斑祛痘：细节密集处局部平滑（小核中频衰减）
        bm = float(blemish) / 100.0
        if bm > 0:
            blur = _blur_rgb(out, 1.5, 5)
            detail = (out - blur).abs().mean(dim=-1, keepdim=True)
            spot = torch.clamp((detail - 0.03) * 12.0, 0.0, 1.0)  # 细节越强越平滑
            out = out + (blur - out) * (bm * 0.9) * spot * region

        # 6) 祛法令纹：脸部暗部宽幅平滑
        wr = float(wrinkle) / 100.0
        if wr > 0:
            wide = _blur_rgb(out, 3.2, 15)
            lum = out.mean(dim=-1, keepdim=True)
            dark = 1.0 - torch.clamp((lum - 0.12) * 6.0, 0.0, 1.0)  # 暗部权重
            out = out + (wide - out) * (wr * 0.8) * dark * region

        # 7) 祛黑眼圈：眼部暗区提亮
        dc = float(dark_circle) / 100.0
        if dc > 0:
            lum = out.mean(dim=-1, keepdim=True)
            dark = torch.clamp((0.40 - lum) * 4.0, 0.0, 1.0)
            lift = dc * 0.18 * dark * region
            out = out + lum * lift * 0.5 * (out / (lum + 1e-6))

        # 8) 清晰：局部对比度增强（unsharp）
        cl = float(clarity) / 100.0
        if cl != 0:
            lum4 = out.mean(dim=-1, keepdim=True).permute(0, 3, 1, 2)
            blur = F.conv2d(lum4, _gaussian_kernel(7, 1.2), padding=3)
            out = out + (lum4 - blur).permute(0, 2, 3, 1) * (cl * 0.5)

        final = out.clamp(0.0, 1.0)
        return (final.to(device=image.device, dtype=image.dtype),)


class LayerComposite:
    """
    图层合成：将一张前景图（带透明通道的 PNG / 可选 mask）叠加到背景图上，
    类似 PS 图层面板 / 剪映画中画。支持混合模式、不透明度与缩放适配。

    - 前景 alpha 优先级：前景 4 通道（RGBA PNG）→ 可选 foreground_mask → 不透明
    - 混合模式：normal / multiply / screen / overlay / soft_light / hard_light
    - fit：stretch 拉伸铺满背景；contain 等比缩放居中（透明区露出背景）
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                # 接口排序规范：foreground 在上、background 在下
                "foreground": ("IMAGE",),
                "background": ("IMAGE",),
                "blend_mode": (["normal", "multiply", "screen", "overlay", "soft_light", "hard_light"], {
                    "default": "normal",
                    "tooltip": "混合模式：normal 正常叠放；multiply 正片叠底（变暗）；screen 滤色（变亮）；overlay 叠加；soft_light 柔光；hard_light 强光。"
                }),
                "opacity": ("FLOAT", {
                    "default": 100, "min": 0, "max": 100, "step": 1,
                    "tooltip": "前景不透明度：0 完全透明，100 完全不透明。"
                }),
                "fit": (["stretch", "contain"], {
                    "default": "stretch",
                    "tooltip": "前景适配：stretch 拉伸铺满背景；contain 等比缩放居中（透明区露出背景）。"
                }),
                "auto_refresh": ("BOOLEAN", {
                    "default": True,
                    "tooltip": "自动刷新：开启后拖动本节点参数，预览自动更新（仅本节点生效，不影响全局自动队列）。"
                }),
            },
            "optional": {
                "foreground_mask": ("MASK",),
            },
        }

    RETURN_TYPES = ("IMAGE",)
    RETURN_NAMES = ("image",)
    FUNCTION = "composite"
    CATEGORY = "image/composite"
    DESCRIPTION = (
        "图层合成：将前景图（透明 PNG / mask）叠加到背景图上（PS 图层 / 剪映画中画）。"
        "支持混合模式、不透明度、缩放适配与自动刷新。"
    )

    @staticmethod
    def _blend(bg, fg, mode):
        """Per-pixel blend of fg over bg, each (..., 3) in [0,1]."""
        if mode == "multiply":
            return bg * fg
        if mode == "screen":
            return 1.0 - (1.0 - bg) * (1.0 - fg)
        if mode == "overlay":
            return torch.where(
                bg < 0.5,
                2.0 * bg * fg,
                1.0 - 2.0 * (1.0 - bg) * (1.0 - fg),
            )
        if mode == "soft_light":
            return (1.0 - 2.0 * bg) * fg * fg + 2.0 * bg * fg
        if mode == "hard_light":
            return torch.where(
                fg < 0.5,
                2.0 * bg * fg,
                1.0 - 2.0 * (1.0 - bg) * (1.0 - fg),
            )
        return fg  # normal

    def composite(self, background, foreground, blend_mode="normal", opacity=100,
                  fit="stretch", auto_refresh=True, foreground_mask=None):
        import torch.nn.functional as F

        bg = background.detach().to(device="cpu", dtype=torch.float32)
        fg = foreground.detach().to(device="cpu", dtype=torch.float32)
        if bg.ndim != 4 or fg.ndim != 4:
            raise ValueError("Expected both IMAGE inputs of shape [B,H,W,C].")

        B, BH, BW, C = bg.shape
        fg_rgb = fg[..., :3] if fg.shape[-1] >= 3 else fg
        fg_alpha = fg[..., 3:4] if fg.shape[-1] >= 4 else None

        if fit == "contain":
            # 等比缩放至短边贴合背景，居中，四周透明
            fh, fw = int(fg.shape[1]), int(fg.shape[2])
            scale = min(BH / fh, BW / fw)
            nh, nw = max(1, int(round(fh * scale))), max(1, int(round(fw * scale)))
            if (nh, nw) != (fh, fw):
                fg_rgb = F.interpolate(
                    fg_rgb.permute(0, 3, 1, 2), size=(nh, nw),
                    mode="bilinear", align_corners=False,
                ).permute(0, 2, 3, 1)
                if fg_alpha is not None:
                    fg_alpha = F.interpolate(
                        fg_alpha.permute(0, 3, 1, 2), size=(nh, nw),
                        mode="bilinear", align_corners=False,
                    ).permute(0, 2, 3, 1)
            # 零填充到背景尺寸（居中）
            pad_top = (BH - nh) // 2
            pad_bottom = BH - nh - pad_top
            pad_left = (BW - nw) // 2
            pad_right = BW - nw - pad_left
            fg_rgb = F.pad(fg_rgb, (0, 0, pad_left, pad_right, pad_top, pad_bottom))
            if fg_alpha is not None:
                fg_alpha = F.pad(fg_alpha, (0, 0, pad_left, pad_right, pad_top, pad_bottom))
            elif foreground_mask is not None:
                pass  # mask handled below (will resize to BHxBW directly)
        else:
            # stretch：直接缩放到背景尺寸
            if (int(fg_rgb.shape[1]), int(fg_rgb.shape[2])) != (BH, BW):
                fg_rgb = F.interpolate(
                    fg_rgb.permute(0, 3, 1, 2), size=(BH, BW),
                    mode="bilinear", align_corners=False,
                ).permute(0, 2, 3, 1)
            if fg_alpha is not None and (int(fg_alpha.shape[1]), int(fg_alpha.shape[2])) != (BH, BW):
                fg_alpha = F.interpolate(
                    fg_alpha.permute(0, 3, 1, 2), size=(BH, BW),
                    mode="bilinear", align_corners=False,
                ).permute(0, 2, 3, 1)

        # 外部 mask 作为 alpha 覆盖（优先于内置？内置 alpha 与 mask 相乘更稳）
        if foreground_mask is not None:
            m = foreground_mask.float()
            if m.ndim == 2:
                m = m.unsqueeze(0)
            if m.ndim == 3:
                m = m.unsqueeze(0)
            m = m.permute(0, 1, 2, 3) if m.ndim == 4 else m
            m = F.interpolate(m, size=(BH, BW), mode="bilinear", align_corners=False)
            m = m[0, 0].clamp(0.0, 1.0).unsqueeze(0).unsqueeze(-1)  # (1,BH,BW,1)
            fg_alpha = m if fg_alpha is None else fg_alpha * m

        if fg_alpha is None:
            fg_alpha = torch.ones(B, BH, BW, 1)

        alpha = fg_alpha.clamp(0.0, 1.0) * (float(opacity) / 100.0)
        bg_rgb = bg[..., :3] if bg.shape[-1] >= 3 else bg

        # 需要 alpha 逐 batch 扩展（fg 是单图时 alpha 为 1 张）
        if alpha.shape[0] != B:
            alpha = alpha.expand(B, BH, BW, 1).contiguous()
        if fg_rgb.shape[0] != B:
            fg_rgb = fg_rgb.expand(B, BH, BW, 3).contiguous()

        blended = self._blend(bg_rgb.clamp(0.0, 1.0), fg_rgb.clamp(0.0, 1.0), blend_mode)
        result = blended * alpha + bg_rgb.clamp(0.0, 1.0) * (1.0 - alpha)
        out = result.clamp(0.0, 1.0)
        return (out.to(device=background.device, dtype=background.dtype),)


def _temp_prefix(node: str) -> str:
    """A temp filename prefix unique to one node instance.

    Two copies of the same node in one workflow write previews at the same
    moment; without a per-instance prefix the second would overwrite the
    first and both panels would show the same picture.
    """
    return f"itsgem_{node}_" + "".join(random.choices(string.ascii_lowercase, k=5))


def _first_frame_to_pil(image, source: str = "Preview"):
    """First frame of a BHWC float batch as a PIL image, ready to save."""
    if not isinstance(image, torch.Tensor) or image.ndim != 4:
        raise ValueError(f"{source} expected a BHWC IMAGE batch.")
    if int(image.shape[0]) < 1:
        raise ValueError(f"{source} received an empty IMAGE batch.")
    frame = image[0].detach().cpu().float().clamp(0.0, 1.0).mul(255.0).round().to(torch.uint8)
    array = frame.numpy()
    if array.shape[-1] == 1:
        array = array[..., 0]
    elif array.shape[-1] > 3:
        # Drop alpha: the panel composites onto its own dark stage, so a
        # transparent preview would read as a hole rather than as coverage.
        array = array[..., :3]
    return Image.fromarray(array)


def _save_temp_preview(image, prefix: str, source: str = "Preview") -> dict:
    """Write one preview PNG to the temp folder; returns its ui image entry."""
    if folder_paths is None:
        raise RuntimeError(f"{source} requires ComfyUI's folder_paths at runtime.")
    preview = _first_frame_to_pil(image, source)
    full_output_folder, filename, counter, subfolder, _prefix = (
        folder_paths.get_save_image_path(
            prefix,
            folder_paths.get_temp_directory(),
            preview.width,
            preview.height,
        )
    )
    file = f"{filename}_{counter:05}_.png"
    preview.save(Path(full_output_folder) / file, compress_level=4)
    return {
        "filename": file,
        "subfolder": subfolder,
        "type": "temp",
        "width": preview.width,
        "height": preview.height,
    }


class ImageCompare:
    """
    Image Compare A/B：同一个面板上对比两张图。
    横向滑动面板可把 B 图逐步揭示到指针位置；或点 A/B 按钮整面切换。
    image_a 原样透传，节点可串在流程图中间。
    """

    CATEGORY = "ItsGemTools/Image"

    def __init__(self):
        self._prefix = _temp_prefix("compare")

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "image_a": (
                    "IMAGE",
                    {
                        "tooltip": (
                            "基准图（第一批次）。其首帧作为预览 A，整个批次原样从输出透传。"
                        ),
                    },
                ),
                "image_b": (
                    "IMAGE",
                    {
                        "tooltip": (
                            "对比图（第二批次）。其首帧作为预览 B，通过滑动面板或 A/B 切换揭示。"
                        ),
                    },
                ),
            }
        }

    RETURN_TYPES = ("IMAGE",)
    RETURN_NAMES = ("image_a",)
    OUTPUT_TOOLTIPS = ("image_a 原样透传，下游节点可继续处理，同时面板持续对比。",)
    OUTPUT_NODE = True
    FUNCTION = "compare"

    def compare(self, image_a, image_b):
        return {
            "ui": {
                "a_images": [_save_temp_preview(image_a, self._prefix, "Compare")],
                "b_images": [_save_temp_preview(image_b, self._prefix, "Compare")],
            },
            "result": (image_a,),
        }


# 全局图像变量表（进程内存储）：Set Image Variable 写入，Get Image Variable 读取。
_IMAGE_STORE = {}
_MISSING = object()  # 哨兵：区分"从未 Set 过"与"Set 过但未连接图像（值为 None）"
_PLACEHOLDER_IMAGE = None
_GET_CACHE_BUSTER = iter(range(1 << 63))  # Get 的 IS_CHANGED 计数器：禁止 ComfyUI 缓存输出


def _placeholder():
    """1x1 黑色占位图：变量未就绪时输出，避免 ComfyUI 因 None 输出中断流程。"""
    global _PLACEHOLDER_IMAGE
    if _PLACEHOLDER_IMAGE is None:
        _PLACEHOLDER_IMAGE = torch.zeros((1, 1, 1, 3), dtype=torch.float32)
    return _PLACEHOLDER_IMAGE


class SetImageVar:
    """
    图像变量（set）：把 image 存入全局变量表，键由 key 指定。
    image 可选：未连接时仅声明变量名（值为空），不报错；
    连接时 image 原样透传输出，可串在流程中任意位置。
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "key": ("STRING", {
                    "default": "my_image",
                    "multiline": False,
                    "tooltip": "变量名：后续 Get Image Variable 用相同 key 读取。"
                }),
            },
            "optional": {
                "image": ("IMAGE",),
            },
        }

    RETURN_TYPES = ("IMAGE",)
    RETURN_NAMES = ("image",)
    FUNCTION = "set_var"
    CATEGORY = "image/utils"
    # OUTPUT_NODE：ComfyUI 只执行"输出节点可达集"（从 OUTPUT_NODE 沿输入依赖递归构建）。
    # Set 的输出若不连线到任何输出节点，会被当成孤立节点跳过执行，导致变量表永远为空。
    # 标记为 OUTPUT_NODE 后 Set 成为执行根（必执行），且同批就绪时优先于 Get 执行。
    OUTPUT_NODE = True
    DESCRIPTION = (
        "图像变量（set）：把 image 存入全局变量表（键由 key 指定）。image 可不连接（仅声明变量名），"
        "连接时原样透传输出，方便在大型工作流中跨节点传递图片。"
    )

    @classmethod
    def IS_CHANGED(cls, key, image=None):
        # Set 的输入（image + key）每次运行不变时，ComfyUI 默认会缓存跳过执行，
        # 导致进程重启后变量表从未写入、Get 永远读不到。返回递增计数器强制每次执行。
        return next(_GET_CACHE_BUSTER)

    def set_var(self, key, image=None):
        key = str(key).strip()
        if image is None:
            _IMAGE_STORE[key] = None
            return (_placeholder(), {
                "ui": {"info": f"[ItsGemTools] Set Image Variable '{key}': 未连接 image，仅声明变量。"}
            })
        _IMAGE_STORE[key] = image
        return (image,)


class GetImageVar:
    """
    图像变量（get）：按 key 从全局变量表读取 image。
    变量未就绪时不报错中断：先短暂等待 Set 写入（并行时序兜底），
    仍未就绪则输出 1x1 占位黑图并在面板提示原因，流程继续。
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "key": ("STRING", {
                    "default": "my_image",
                    "multiline": False,
                    "tooltip": "变量名：与 Set Image Variable 的 key 一致。"
                }),
            },
            "optional": {
                # var_ref：前端扩展在 prompt 层自动把同 key Set 的输出隐形链接到此输入，
                # 仅用于让 ComfyUI 调度器保证"Set 先执行、Get 后读取"（与直接连线行为一致）。
                # 本节点忽略其值；输入口由前端隐藏，画布上不可见。
                "var_ref": ("IMAGE",),
            },
        }

    RETURN_TYPES = ("IMAGE",)
    RETURN_NAMES = ("image",)
    FUNCTION = "get_var"
    CATEGORY = "image/utils"
    DESCRIPTION = (
        "图像变量（get）：按 key 读取此前 Set Image Variable 存入的 image。"
        "变量未就绪时输出占位黑图并提示，不会中断工作流。"
    )

    @classmethod
    def IS_CHANGED(cls, key, var_ref=None):
        # Get 的输入只有 key 这个 widget（每次运行不变），若不禁止缓存，
        # ComfyUI 会跳过执行、永远返回上一次的旧输出（变量更新后取不到新图）。
        # 返回递增计数器：每次执行都重新读取全局变量表。
        return next(_GET_CACHE_BUSTER)

    def get_var(self, key, var_ref=None):
        key = str(key).strip()
        img = _IMAGE_STORE.get(key, _MISSING)
        # 无需等待：Set 是 OUTPUT_NODE（执行根），同一运行内必然先于 Get 执行并写入。
        # Set 未执行仅发生在"部分执行/单独运行 Get"时，此时输出占位并提示，不中断流程。
        if img is _MISSING:
            return (_placeholder(), {
                "ui": {"info": f"[ItsGemTools] 变量 '{key}' 尚未由 Set Image Variable 写入，已输出占位黑图。"
                               "（请先放置并执行一次 Set Image Variable(key={key})）"}
            })
        if img is None:
            return (_placeholder(), {
                "ui": {"info": f"[ItsGemTools] 变量 '{key}' 已声明但未连接 image，已输出占位黑图。"}
            })
        return (img,)


class GemLabel:
    """
    纯前端标注节点（Label (ItsGemTools)）的后端空实现。
    实际渲染（透明浮动文字）与双击就地编辑全部由前端扩展
    (web/extensions/itsgemtools_label.js) 完成，本类不参与任何执行逻辑，
    仅用于让后端识别该节点类型，避免工作流加载 / 运行时出现
    "A node type is missing or unavailable" 报错。
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {}, "optional": {}}

    RETURN_TYPES = ()
    FUNCTION = "noop"
    CATEGORY = "ItsGemTools"

    def noop(self):
        return ()


# ============================================================================
# —— Tile Upscale（ItsGemTools 复刻 + 几何配准）——
# 复刻自 ComfyUI-AusBoss 的 Tiled Upscale / Tiled Upscale Stitch 两节点：
# 切块与拼回逻辑与原版完全一致；Stitch 端新增 tile 级亚像素几何配准。
# 背景：QwenImage 2.1 编辑模型 + 自训 LoRA 重绘单个 tile 时，构图常相对
# 源 tile 漂移数像素（尤其人脸特写）。原版只做颜色匹配、直接按 box 坐标
# 放回，接缝处即出现错位 / 重影 / 模糊。本节点用 FFT 相位相关估计每个
# 重绘 tile 相对源 tile 的平移，归位后再混合，从机制上根治偏移；低纹理
# 或位移超限时自动跳过配准，行为退化为原版，不会错误配准。
# 依赖：仅 torch / PIL / numpy（与包内其它节点一致），无 cv2 依赖。
# ============================================================================

# ---- 切块计划（复刻 AusBoss _tile_helpers，纯算术）----
_GEM_TILE_SLACK = 1.125      # tile 可略超 tile_megapixels（网格取整缓冲）
_GEM_SNAP_SLACK = 1.03       # 取整到 32 网格可能再加一点，但不拆图
_GEM_NEAR_ENOUGH = 0.9       # 重绘达到目标尺寸 90% 即按目标尺寸重绘
_GEM_MAX_TILE_ASPECT = 2.2   # 避免细长 tile（单 tile 保持原图形状除外）
# Qwen 防呆模式下的 tile 宽高比上限：收紧到 1.6，逼网格选接近方形的 tile。
# 实测超扁横条（如 2112x960 ≈ 2.2:1）喂给 Qwen 重绘构图保持差、内容变形；
# 竖图多列分块后 tile 落回方形（如 1216x1440），重绘与配准都稳。
_GEM_QWEN_MAX_TILE_ASPECT = 1.6
_GEM_MIN_VISIBLE_GROWTH = 1.6
_GEM_SHRINK_BELOW = 0.97
_GEM_MAX_TILES = 144
_GEM_STITCHER_KIND = "itsgem_tile_stitcher"
# Qwen-Image 2.1 官方硬上限是 2048x2048（4.19 MP）。对齐 AusBoss 的切块口径：
# 它的 tile 上限 = tile_megapixels x 1.125 x 1.03（tile4 即 4.635MP），不做额外的
# 3MP 钳制，因此自然 1x2 宽 tile 保留、无垂直缝切割脸（窄 tile 缺全局上下文才会
# 脑补第二双眼睛）。这里取 4.6MP：自然宽 tile（如 2464x1792=4.415MP）通过，
# 而 tile_megapixels=8 那种翻倍单块（8MP）仍被钳制。可能的小幅漂移由 Stitch 端
# tile-to-tile 配准（AusBoss 没有的安全网）纠正。
_GEM_SAFE_TILE_PX = 4_600_000


def _gem_round_half_up(value):
    return int(math.floor(value + 0.5))


def _gem_snap_to_multiple(value, multiple):
    """就近取整到 multiple 的倍数，且不小于一个 step（尺寸不会塌成 0）。"""
    step = max(1, int(multiple))
    return max(step, _gem_round_half_up(value / step) * step)


def _gem_megapixel_size(width, height, megapixels, multiple):
    """按 megapixels 等比换算尺寸，32 对齐时保持源纵横比（比例误差最小化）。

    旧实现宽高各自就近取整，两次取整会把纵横比带偏（实测 1088x1920 -> 8MP
    得出 2144x3744，比例漂移 +1.06%）；Qwen 参考图被横向拉宽后重绘内容跟着
    变形，纯平移配准救不回来——这是"怎么修都还有偏移"的隐藏来源。
    """
    multiple = max(1, int(multiple))
    w0, h0 = int(width), int(height)
    ratio = w0 / h0
    h_ideal = math.sqrt(float(megapixels) * 1e6 / ratio)
    w_ideal = h_ideal * ratio
    hs = sorted({
        max(multiple, int(math.floor(h_ideal / multiple)) * multiple),
        max(multiple, int(math.ceil(h_ideal / multiple)) * multiple),
    })
    ws = sorted({
        max(multiple, int(math.floor(w_ideal / multiple)) * multiple),
        max(multiple, int(math.ceil(w_ideal / multiple)) * multiple),
    })
    best = None
    for h in hs:
        for w in ws:
            if w <= 0 or h <= 0:
                continue
            ratio_err = abs(w / h - ratio) / ratio
            area_err = (w * h - float(megapixels) * 1e6) / (float(megapixels) * 1e6)
            # 比例误差优先；面积尽量接近目标且不低于目标（允许小幅超出）
            score = (ratio_err, max(0.0, -area_err), abs(area_err))
            if best is None or score < best[0]:
                best = (score, w, h)
    return best[1], best[2]


def _gem_redraw_size(width, height, megapixels, multiple):
    """工作层(redraw)尺寸：宽高各自独立就近 half-up 到 multiple，对齐 AusBoss megapixel_size。

    与 _gem_megapixel_size（比例误差优先枚举）不同：这里刻意复刻 AusBoss 的独立取整，
    保证 tile 网格/boxes 与 AusBoss 完全一致（实测 1088x1920 -> 8MP 得 2144x3744，
    tile2 起点 1728、共享区 288）。redraw 只是内部工作层，最终会 resample 到严格等比的
    result（result_multiple=1），所以这里不追求最小比例误差，也不影响成图零比例误差。
    """
    m = max(1, int(multiple))
    scale = math.sqrt(float(megapixels) * 1e6 / (int(width) * int(height)))
    return (
        _gem_snap_to_multiple(max(1, int(width) * scale), m),
        _gem_snap_to_multiple(max(1, int(height) * scale), m),
    )


def _gem_starts(length, tile, count):
    """count 个 tile 的起始坐标，均匀铺满 length（首尾精确对齐）。"""
    if count <= 1 or tile >= length:
        return [0]
    return [_gem_round_half_up(index * (length - tile) / (count - 1)) for index in range(count)]


def _gem_side(length, count, overlap, multiple):
    """count 列覆盖 length、相邻共享 overlap 时，单边 tile 长度（multiple 倍数）。"""
    if count <= 1:
        return length
    tile = math.ceil((length + (count - 1) * overlap) / count / multiple) * multiple
    return max(multiple, min(length, tile))


def _gem_grid(width, height, cap_pixels, overlap, multiple, max_aspect=_GEM_MAX_TILE_ASPECT):
    """最少数量、最接近正方形的 tile 网格；返回 (columns, rows, tile_w, tile_h)。"""
    for count in range(1, _GEM_MAX_TILES + 1):
        best = None
        for columns in range(1, count + 1):
            if count % columns:
                continue
            rows = count // columns
            tile_width = _gem_side(width, columns, overlap, multiple)
            tile_height = _gem_side(height, rows, overlap, multiple)
            if tile_width * tile_height > cap_pixels:
                continue
            aspect = max(tile_width / tile_height, tile_height / tile_width)
            if count > 1 and aspect > max_aspect:
                continue
            score = (abs(math.log(tile_width / tile_height)), columns)
            if best is None or score < best[0]:
                best = (score, columns, rows, tile_width, tile_height)
        if best is not None:
            return best[1:]
    raise ValueError(
        f"Tiled Upscale would cut this picture into more than {_GEM_MAX_TILES} tiles. "
        "Ask for fewer megapixels or a larger tile size."
    )


def _gem_plan_tiles(
    source_width, source_height, megapixels, tile_megapixels=2.0,
    overlap=128, multiple=32, max_growth=8.0,
    max_aspect=_GEM_MAX_TILE_ASPECT,
    result_multiple=1,
):
    """规划一次放大：返回 result / redraw / tile 尺寸、tile box 列表、实际 overlap。

    megapixels=0 保持原尺寸。redraw 不超过 max_growth × 原图 megapixels 且不小于
    一个 tile；redraw 与目标之间的差距由 Stitch 端普通放大补齐。
    max_aspect 限制单 tile 宽高比（count>1 时），Qwen 防呆模式默认收紧到 1.6。
    result_multiple：成图尺寸的对齐步长，默认 1 = 严格等比（像素级零比例误差，
    before 输出与源图长宽比完全一致）；redraw/tiles 仍按 multiple（默认 32）对齐，
    两者的差距由 Stitch 端补齐。
    """
    width, height = int(source_width), int(source_height)
    if width <= 0 or height <= 0:
        raise ValueError("Tiled Upscale needs a picture larger than 0x0.")
    multiple = max(1, int(multiple))
    result_multiple = max(1, int(result_multiple))
    overlap = max(0, int(overlap))
    tile_mp = float(tile_megapixels)
    if tile_mp <= 0:
        raise ValueError("Tiled Upscale needs a tile size above 0 megapixels.")
    growth = max(1.0, float(max_growth))
    source_mp = width * height / 1e6
    keep_size = float(megapixels) <= 0
    asked_mp = source_mp if keep_size else float(megapixels)

    if keep_size:
        result_width, result_height = width, height
    else:
        result_width, result_height = _gem_megapixel_size(width, height, asked_mp, result_multiple)

    limit_mp = max(tile_mp, growth * source_mp)
    limited = (not keep_size) and limit_mp < asked_mp * _GEM_NEAR_ENOUGH
    if keep_size:
        redraw_width = _gem_snap_to_multiple(width, multiple)
        redraw_height = _gem_snap_to_multiple(height, multiple)
    elif limited:
        redraw_width, redraw_height = _gem_redraw_size(width, height, limit_mp, multiple)
    else:
        redraw_width, redraw_height = _gem_redraw_size(width, height, asked_mp, multiple)

    columns, rows, tile_width, tile_height = _gem_grid(
        redraw_width, redraw_height,
        tile_mp * _GEM_TILE_SLACK * _GEM_SNAP_SLACK * 1e6, overlap, multiple,
        max_aspect,
    )
    xs = _gem_starts(redraw_width, tile_width, columns)
    ys = _gem_starts(redraw_height, tile_height, rows)
    boxes = [(x, y, x + tile_width, y + tile_height) for y in ys for x in xs]
    overlap_x = tile_width - (xs[1] - xs[0]) if len(xs) > 1 else 0
    overlap_y = tile_height - (ys[1] - ys[0]) if len(ys) > 1 else 0

    result_mp = result_width * result_height / 1e6
    growth_per_side = math.sqrt(result_mp / source_mp)
    shrunk = (not keep_size) and growth_per_side < _GEM_SHRINK_BELOW
    return {
        "source": (width, height),
        "source_megapixels": source_mp,
        "asked_megapixels": asked_mp,
        "keep_size": keep_size,
        "result": (result_width, result_height),
        "redraw": (redraw_width, redraw_height),
        "tile": (tile_width, tile_height),
        "columns": len(xs),
        "rows": len(ys),
        "boxes": boxes,
        "overlap": (max(0, overlap_x), max(0, overlap_y)),
        "enlarged": limited,
        "shrunk": shrunk,
        "too_close": (not keep_size)
        and (not shrunk)
        and source_mp >= 1.0
        and growth_per_side < _GEM_MIN_VISIBLE_GROWTH,
        "suggested_megapixels": math.ceil(source_mp * _GEM_MIN_VISIBLE_GROWTH**2 * 2) / 2,
    }


def _gem_plan_tiles_qwen_clamped(
    source_width, source_height, megapixels, tile_megapixels=2.0,
    overlap=128, multiple=32, max_growth=8.0,
    limit_px=_GEM_SAFE_TILE_PX,
    max_aspect=_GEM_QWEN_MAX_TILE_ASPECT,
    result_multiple=1,
):
    """防呆版切块规划：单 tile 面积超过安全工作点时自动调低 tile_megapixels。

    安全上限默认 4.6 MP：对齐 AusBoss 的切块口径（tile_mp x 1.125 x 1.03），
    保留自然 1x2 宽 tile、无垂直缝切脸；官方硬上限 4.19 MP，tile_megapixels=8
    那种翻倍单块仍会被钳。返回
    (plan, effective_tile_megapixels, clamped, orig_max_tile_px, new_max_tile_px)。
    clamped=False 表示原参数本就安全（小图上 tile_megapixels 大但实际单块没超，
    不误钳）；clamped=True 时 effective 为自动降到的安全值，report 会提示。
    max_aspect 默认 1.6：Qwen 防呆模式强制 tile 接近方形（超扁横条会构图变形）。
    result_multiple 默认 1：成图严格等比（零比例误差），redraw/tiles 仍 32 对齐。
    """
    plan = _gem_plan_tiles(
        source_width, source_height, megapixels, tile_megapixels,
        overlap, multiple, max_growth, max_aspect, result_multiple,
    )
    def max_tile_px(p):
        return max((b[2] - b[0]) * (b[3] - b[1]) for b in p["boxes"])
    orig_max = max_tile_px(plan)
    if orig_max <= limit_px:
        return plan, float(tile_megapixels), False, orig_max, orig_max
    effective = float(tile_megapixels)
    for _ in range(24):
        effective *= 0.8
        plan = _gem_plan_tiles(
            source_width, source_height, megapixels, effective,
            overlap, multiple, max_growth, max_aspect, result_multiple,
        )
        new_max = max_tile_px(plan)
        if new_max <= limit_px:
            return plan, effective, True, orig_max, new_max
    return plan, effective, True, orig_max, new_max


def _gem_megapixels_text(value):
    text = f"{value:.2f}".rstrip("0").rstrip(".")
    return f"{text} MP"


def _gem_describe_plan(plan):
    """报告这次放大做了什么，一两句话。"""
    source_width, source_height = plan["source"]
    result_width, result_height = plan["result"]
    redraw_width, redraw_height = plan["redraw"]
    tiles = len(plan["boxes"])
    tile_width, tile_height = plan["tile"]
    head = f"{source_width} x {source_height} px ({_gem_megapixels_text(plan['source_megapixels'])})"
    redraw = _gem_megapixels_text(redraw_width * redraw_height / 1e6)
    if tiles == 1:
        how = f"redrawn as one tile of {redraw_width} x {redraw_height} px"
    else:
        how = (
            f"redrawn at {redraw} in {tiles} tiles of "
            f"{tile_width} x {tile_height} px"
        )
    if plan["keep_size"]:
        return f"{head} keeps its size: {how}."
    text = f"{head} to {result_width} x {result_height} px ({_gem_megapixels_text(result_width * result_height / 1e6)}): {how}"
    if plan["enlarged"]:
        text += ", then enlarged the plain way. A picture this small has no more detail to redraw from."
    else:
        text += "."
    if plan.get("shrunk"):
        text += " The picture is larger than the size asked for, so it is made smaller first."
    if plan["too_close"]:
        text += (
            " That is close to the picture's own size, so expect little change: "
            f"ask for {_gem_megapixels_text(plan['suggested_megapixels'])} or more."
        )
    return text


def _gem_rgb(image, who):
    """BHWC float 批，恰好 3 通道。"""
    if not isinstance(image, torch.Tensor) or image.ndim != 4:
        raise ValueError(f"{who} expected a BHWC IMAGE batch.")
    image = image.float()
    if image.shape[-1] == 1:
        return image.expand(-1, -1, -1, 3)
    return image[..., :3]


def _gem_model_enlarge(upscale_model, image, width, height):
    """用 ComfyUI 自带 ImageUpscaleWithModel 逐次放大，直到覆盖目标尺寸。"""
    mappings = getattr(sys.modules.get("nodes"), "NODE_CLASS_MAPPINGS", None)
    runner = mappings.get("ImageUpscaleWithModel") if isinstance(mappings, dict) else None
    if runner is None or not hasattr(runner, "execute"):
        raise RuntimeError(
            "Tiled Upscale could not find ComfyUI's Upscale Image (using Model) node. "
            "Update ComfyUI, or unplug the upscale model: the picture is then enlarged the plain way."
        )
    for _ in range(4):
        if image.shape[2] >= width and image.shape[1] >= height:
            break
        before = image.shape[1:3]
        image = runner.execute(upscale_model=upscale_model, image=image).result[0]
        image = _gem_rgb(image, "Tiled Upscale (ItsGemTools)")
        if image.shape[1] <= before[0] or image.shape[2] <= before[1]:
            break  # 1x 模型：再放大也拿不到更多
    return image


# ---- 重采样（复刻 AusBoss _resize_helpers.resample_batch，PIL lanczos）----
_GEM_TORCH_MODES = {
    "bicubic": "bicubic",
    "bilinear": "bilinear",
    "nearest": "nearest-exact",
    "area": "area",
}


def _gem_lanczos_resize(batch, width, height):
    """逐通道 PIL lanczos 浮点重采样（不经 uint8 往返）。"""
    device = batch.device
    array = batch.detach().cpu().numpy().astype(np.float32, copy=False)
    frames = []
    for frame in array:
        channels = [
            np.asarray(
                Image.fromarray(
                    np.ascontiguousarray(frame[:, :, index]), mode="F"
                ).resize((width, height), Image.LANCZOS),
                dtype=np.float32,
            )
            for index in range(frame.shape[-1])
        ]
        frames.append(np.stack(channels, axis=-1))
    result = torch.from_numpy(np.stack(frames, axis=0))
    return result.clamp_(0.0, 1.0).to(device)


def _gem_resample_batch(batch, width, height, interpolation="lanczos"):
    """BHWC 浮点批重采样；尺寸已一致时原样返回（保持位级一致）。"""
    if not isinstance(batch, torch.Tensor) or batch.ndim != 4:
        raise ValueError("Tiled Upscale expected a BHWC batch to resample.")
    width, height = int(width), int(height)
    if width <= 0 or height <= 0:
        raise ValueError("Tiled Upscale cannot resample to a zero-sized output.")
    if batch.shape[2] == width and batch.shape[1] == height:
        return batch
    batch = batch.float()
    if interpolation == "lanczos":
        return _gem_lanczos_resize(batch, width, height)
    moved = batch.movedim(-1, 1).contiguous()
    kwargs = {}
    if interpolation in ("bilinear", "bicubic"):
        downscale = width < moved.shape[-1] or height < moved.shape[-2]
        kwargs = {"align_corners": False, "antialias": downscale}
    resized = torch.nn.functional.interpolate(
        moved, size=(height, width), mode=_GEM_TORCH_MODES[interpolation], **kwargs
    )
    if interpolation == "bicubic":
        resized = resized.clamp_(0.0, 1.0)
    return resized.movedim(1, -1).contiguous()


# ---- sRGB <-> CIELAB（复刻 AusBoss _color_helpers，纯 torch）----
_GEM_D65 = (0.95047, 1.0, 1.08883)
_GEM_DELTA = 6.0 / 29.0
_GEM_RGB_TO_XYZ = (
    (0.4124564, 0.3575761, 0.1804375),
    (0.2126729, 0.7151522, 0.0721750),
    (0.0193339, 0.1191920, 0.9503041),
)
_GEM_XYZ_TO_RGB = (
    (3.2404542, -1.5371385, -0.4985314),
    (-0.9692660, 1.8760108, 0.0415560),
    (0.0556434, -0.2040259, 1.0572252),
)


def _gem_matrix(rows, like):
    return torch.tensor(rows, dtype=like.dtype, device=like.device)


def _gem_rgb_to_lab(rgb):
    """BHWC sRGB[0,1] -> BHWC CIELAB（L∈[0,100]，a/b 有符号）。"""
    rgb = rgb.clamp(0.0, 1.0)
    linear = torch.where(rgb <= 0.04045, rgb / 12.92, ((rgb + 0.055) / 1.055) ** 2.4)
    xyz = linear @ _gem_matrix(_GEM_RGB_TO_XYZ, rgb).T
    scaled = xyz / _gem_matrix([_GEM_D65], rgb)
    f = torch.where(
        scaled > _GEM_DELTA**3,
        scaled.clamp_min(1e-12) ** (1.0 / 3.0),
        scaled / (3.0 * _GEM_DELTA**2) + 4.0 / 29.0,
    )
    fx, fy, fz = f.unbind(-1)
    return torch.stack([116.0 * fy - 16.0, 500.0 * (fx - fy), 200.0 * (fy - fz)], dim=-1)


def _gem_lab_to_rgb(lab):
    """BHWC CIELAB 回 BHWC sRGB，clamp 到 [0,1]。"""
    lightness, a, b = lab.unbind(-1)
    fy = (lightness + 16.0) / 116.0
    f = torch.stack([fy + a / 500.0, fy, fy - b / 200.0], dim=-1)
    scaled = torch.where(f > _GEM_DELTA, f**3, 3.0 * _GEM_DELTA**2 * (f - 4.0 / 29.0))
    xyz = scaled * _gem_matrix([_GEM_D65], lab)
    linear = (xyz @ _gem_matrix(_GEM_XYZ_TO_RGB, lab).T).clamp_min(0.0)
    srgb = torch.where(linear <= 0.0031308, linear * 12.92, 1.055 * linear ** (1.0 / 2.4) - 0.055)
    return srgb.clamp(0.0, 1.0)


# ---- 颜色保持（复刻 AusBoss node_tiled_upscale._keep_colors）----
_GEM_COLOR_CELLS = 32
_GEM_COLOR_FLOOR = 0.5
_GEM_COLOR_GAIN_MAX = 3.0


def _gem_averaged_lab(rgb):
    """小尺寸均值版上的 LAB：细节消失、颜色保留。"""
    height, width = int(rgb.shape[1]), int(rgb.shape[2])
    scale = _GEM_COLOR_CELLS / max(height, width)
    size = (max(2, min(height, round(height * scale))), max(2, min(width, round(width * scale))))
    small = torch.nn.functional.interpolate(rgb.movedim(-1, 1), size=size, mode="area")
    return _gem_rgb_to_lab(small.movedim(1, -1))


def _gem_keep_colors(tile, source, strength):
    """把重绘 tile 的颜色对齐到它来源的源 tile：LAB 每通道直线拟合 gain/offset。"""
    mine, wanted = _gem_averaged_lab(tile), _gem_averaged_lab(source)
    mean = mine.mean(dim=(1, 2), keepdim=True)
    wanted_mean = wanted.mean(dim=(1, 2), keepdim=True)
    variance = ((mine - mean) ** 2).mean(dim=(1, 2), keepdim=True)
    shared = ((mine - mean) * (wanted - wanted_mean)).mean(dim=(1, 2), keepdim=True)
    gain = ((shared + _GEM_COLOR_FLOOR**2) / (variance + _GEM_COLOR_FLOOR**2)).clamp(0.0, _GEM_COLOR_GAIN_MAX)
    matched = _gem_lab_to_rgb((_gem_rgb_to_lab(tile) - mean) * gain + wanted_mean)
    return tile + (matched - tile) * strength


def _gem_ramp(length, rise, fall):
    """沿一边的混合权重：起始 rise 像素上升、末尾 fall 像素下降、中间 1。"""
    weights = torch.ones(length, dtype=torch.float32)
    rise = max(0, min(int(rise), length))
    fall = max(0, min(int(fall), length))
    if rise:
        weights[:rise] = torch.arange(1, rise + 1, dtype=torch.float32) / (rise + 1)
    if fall:
        weights[length - fall:] *= torch.arange(fall, 0, -1, dtype=torch.float32) / (fall + 1)
    return weights


# ---- tile 边缘修复与 tile 间色调拉齐（消除"黑色分隔线格子"）----
# 实测 Qwen 重绘 tile 的左边缘（tile 内 x≈3-4px）自带黑边；窄带独占混合下该边
# 由后写 tile 显示，权重归一化后黑边全强度呈现 -> 竖缝右侧 3-4px 处出现贯穿全图的
# 黑线（over256 x=932 亮度 0.1、over512 x=803-804）。同时 Qwen 各 tile 独立重绘
# 导致 tile 间亮度/色调不齐，均匀背景上拼出"格子"感。两者都在拼接端修复。
_GEM_TILE_EDGE_FIX = 8         # 边缘黑边修复宽度（px）
_GEM_TILE_EDGE_DARK = 0.098    # 边缘比内侧暗超过该值（0..1 灰度域，≈25/255）视为黑边
_GEM_TONE_GAIN_MIN, _GEM_TONE_GAIN_MAX = 0.7, 1.3
_GEM_TONE_BIAS_MAX = 0.18      # 0..1 灰度域


def _gem_fix_tile_edge_band(tile, band=_GEM_TILE_EDGE_FIX, dark_gap=_GEM_TILE_EDGE_DARK):
    """逐边检测 tile 黑边：边缘 band 内**最暗列/行**均值比内侧 band 均值低超过 dark_gap 时，
    用内侧内容复制填充该边，消除 Qwen 重绘 tile 的边缘暗边伪影。

    注意：黑边实测是 1~2px 宽的整列暗带（如 over256 版 x=932 整列亮度 0.3/255），
    用"边缘 band 整体均值"检测会被稀释（8px 带含 1px 黑只低 ~0.07），永不触发；
    必须用"最暗列/行均值"才能抓到窄黑边。"""
    if band <= 0:
        return tile
    b = int(band)
    out = tile.clone()
    gray = tile.mean(dim=3, keepdim=True)
    specs = {
        "left":   ((slice(None), slice(None), slice(0, b), slice(None)),
                   (slice(None), slice(None), slice(b, 2 * b), slice(None)),
                   2),  # 沿宽轴（列）取最暗
        "right":  ((slice(None), slice(None), slice(-b, None), slice(None)),
                   (slice(None), slice(None), slice(-2 * b, -b), slice(None)),
                   2),
        "top":    ((slice(None), slice(0, b), slice(None), slice(None)),
                   (slice(None), slice(b, 2 * b), slice(None), slice(None)),
                   1),  # 沿高轴（行）取最暗
        "bottom": ((slice(None), slice(-b, None), slice(None), slice(None)),
                   (slice(None), slice(-2 * b, -b), slice(None), slice(None)),
                   1),
    }
    for side, (esl, isl, axis) in specs.items():
        i = gray[isl].mean()
        # 边缘带沿列（left/right）或行（top/bottom）取均值，再取最暗
        if axis == 2:
            e = gray[esl].mean(dim=(0, 1, 3)).min()
        else:
            e = gray[esl].mean(dim=(0, 2, 3)).min()
        if i - e > dark_gap:
            out[esl] = tile[isl]
    return out


def _gem_fit_gain_bias(a, b):
    """逐通道 y = gain*x + bias 最小二乘拟合，gain/bias 均在安全范围内 clamp。"""
    a = a.reshape(-1, 3).float()
    b = b.reshape(-1, 3).float()
    mean_a = a.mean(dim=0)
    mean_b = b.mean(dim=0)
    cov = ((a - mean_a) * (b - mean_b)).mean(dim=0)
    var = ((a - mean_a) ** 2).mean(dim=0) + 1e-6
    gain = (cov / var).clamp(_GEM_TONE_GAIN_MIN, _GEM_TONE_GAIN_MAX)
    bias = (mean_b - gain * mean_a).clamp(-_GEM_TONE_BIAS_MAX, _GEM_TONE_BIAS_MAX)
    return gain, bias


def _gem_tone_match(tile, placed, left, top, lefts, tops, band):
    """把当前 tile 的亮度/色调对齐到已拼入的邻接内容（左/上共享区分别做
    逐通道 gain+bias 拟合，按共享区像素数加权平均），消除 Qwen 各 tile
    独立重绘导致的块间亮度/色调不一致（格子感）。

    校正只在接缝附近生效，向 tile 内部平滑衰减（空间衰减场）：共享区往往只占
    tile 一小部分且内容（如暗背景）与 tile 主体（如亮白毛衣）差异很大，若把
    单一 gain/bias 硬套整个 tile，会把不相关的主体错误拉偏（实测行2 tile 用顶部
    暗区拟合出 gain<1，把下部亮毛衣压暗，差异与源亮度成正比、越往下越暗）。
    衰减场在接缝处=1（完全对齐、消除格子感），tile 内部=0（保持重绘原样）。"""
    th, tw = tile.shape[1], tile.shape[2]
    gains, biases, counts = [], [], []
    li = lefts.index(left)
    has_left = li > 0
    if has_left:
        share_x = tw - (left - lefts[li - 1])
        if share_x > 8:
            g, bs = _gem_fit_gain_bias(tile[:, :, :share_x, :],
                                       placed[:, top:top + th, left:left + share_x, :])
            gains.append(g)
            biases.append(bs)
            counts.append(share_x * th)
    ti = tops.index(top)
    has_up = ti > 0
    if has_up:
        share_y = th - (top - tops[ti - 1])
        if share_y > 8:
            g, bs = _gem_fit_gain_bias(tile[:, :share_y, :, :],
                                       placed[:, top:top + share_y, left:left + tw, :])
            gains.append(g)
            biases.append(bs)
            counts.append(share_y * tw)
    if not gains:
        return tile
    w = torch.tensor(counts, dtype=torch.float32, device=tile.device)
    w = (w / w.sum()).view(-1, 1)
    gain = sum(g * w[i] for i, g in enumerate(gains))
    bias = sum(bs * w[i] for i, bs in enumerate(biases))
    corrected = (tile * gain.view(1, 1, 1, 3) + bias.view(1, 1, 1, 3)).clamp(0.0, 1.0)

    # 空间衰减场：接缝边缘 1 → tile 内部 0。reach 取 band 的 2 倍且不少于 96px，
    # 保证接缝对齐平滑，又不波及 tile 内部主体。
    reach = max(int(band) * 2, 96)
    field = torch.zeros((th, tw), dtype=torch.float32, device=tile.device)
    if has_left:
        r = min(reach, tw)
        ramp = torch.linspace(1.0, 0.0, r, dtype=torch.float32, device=tile.device)
        field[:, :r] = torch.maximum(field[:, :r], ramp[None, :].expand(th, -1))
    if has_up:
        r = min(reach, th)
        ramp = torch.linspace(1.0, 0.0, r, dtype=torch.float32, device=tile.device)
        field[:r, :] = torch.maximum(field[:r, :], ramp[:, None].expand(-1, tw))
    field = field[None, :, :, None]
    return tile * (1.0 - field) + corrected * field


def _gem_ramp_edge(length, rise, fall, band):
    """窄带混合权重（消除宽重影带，配合 tile-to-tile 配准使用）。

    语义按行优先处理顺序定义：
    - rise 区（与"先写"的上/左邻居共享）：当前 tile 后写，独占主体——
      紧贴当前 tile 边界的 band 像素内从 0 渐入到 1，其余保持 1。
    - fall 区（与"后写"的下/右邻居共享）：当前 tile 先写，让出主体——
      共享区其余部分为 0，只在紧贴当前 tile 边界的 band 像素内从 1 渐出到 0。
    结果：overlap 区主体由后写的 tile 独占（单侧内容，无双影），两条 band 渐变带
    负责衔接；重影带宽度从 overlap 宽缩到 2×band。配准先把平移归位，剩余内容级
    差异落在 band 渐变带内，肉眼不可见。
    """
    weights = torch.ones(length, dtype=torch.float32)
    rise = max(0, min(int(rise), length))
    fall = max(0, min(int(fall), length))
    band = max(1, int(band))
    if rise:
        g = min(band, rise)
        weights[:g] = torch.linspace(0.0, 1.0, g, dtype=torch.float32)
    if fall:
        g = min(band, fall)
        # 渐变带紧贴 fall 区起点（共享边界），与邻居 rise 渐变对接：
        # 边界处先写 tile 权重=1、后写 tile=0，band 内两者之和恒=1，无 weight=0 黑缝。
        # body 0 区在渐变带之后（更靠近 tile 内侧），不再紧贴边界。
        weights[length - fall:length - fall + g] = torch.linspace(
            1.0, 0.0, g, dtype=torch.float32
        )
        if fall - g > 0:
            weights[length - fall + g:length] = 0.0
    return weights


def _gem_align_vs_placed(tile, placed, left, top, lefts, tops, max_shift):
    """tile-to-tile 配准：把当前重绘 tile 与"已拼入的邻接卡通输出"对齐。

    之前配准拿源照片当参考，Qwen 卡通重绘与照片特征域差异太大（实测 peak 只有
    0.02~0.03），全部 skipped、接缝零修正。这里改为卡通域内对齐：已拼入的
    左/上邻居本就是 Qwen 输出，相位相关峰显著更高，能真正测出平移分量。
    返回 (shift, peak) 或 None；左/上邻居各测一次，取峰更高者。
    """
    tile_h, tile_w = tile.shape[1], tile.shape[2]
    best = None
    # 左邻居（同行前一列）：物理共享区 = 当前 tile 左缘起 share_x 宽，垂直整段
    li = lefts.index(left)
    if li > 0:
        prev_left = lefts[li - 1]
        share_x = tile_w - (left - prev_left)
        if share_x > 0:
            ref = placed[:, top:top + tile_h, left:left + share_x, :]
            tgt = tile[:, :, :share_x, :]
            out = _gem_estimate_shift_with_meta(ref, tgt, max_shift)
            if out is not None and out[0] is not None:
                score = out[1].get("peak", 0.0)
                if best is None or score > best[1]:
                    best = (out[0], score, "left")
    # 上邻居（同列上一行）：物理共享区 = 当前 tile 顶部起 share_y 高，水平整段
    ti = tops.index(top)
    if ti > 0:
        prev_top = tops[ti - 1]
        share_y = tile_h - (top - prev_top)
        if share_y > 0:
            ref = placed[:, top:top + share_y, left:left + tile_w, :]
            tgt = tile[:, :share_y, :, :]
            out = _gem_estimate_shift_with_meta(ref, tgt, max_shift)
            if out is not None and out[0] is not None:
                score = out[1].get("peak", 0.0)
                if best is None or score > best[1]:
                    best = (out[0], score, "up")
    return best


def _gem_shared(starts, size, start):
    """该 tile 与前后邻居共享的像素数。"""
    index = starts.index(start)
    before = starts[index - 1] + size - start if index > 0 else 0
    after = start + size - starts[index + 1] if index + 1 < len(starts) else 0
    return max(0, before), max(0, after)


# ---- 几何配准（新增核心：FFT 相位相关亚像素平移）----
# QwenImage 2.1 编辑模型 + LoRA 重绘 tile 时内容可能相对源 tile 漂移数像素。
# 相位相关在频域估计平移，纯 torch 实现（无 cv2）；低纹理 / 位移超限时判为
# 不可靠返回 None，Stitch 退化为原版行为，不会错误配准。

def _gem_to_gray(tile):
    return tile[..., 0] * 0.299 + tile[..., 1] * 0.587 + tile[..., 2] * 0.114


def _gem_hann2d(height, width, device, dtype):
    """二维汉宁窗：压边缘、抑制 tile 边界截断导致的相位泄漏。"""
    hh = torch.hann_window(height, periodic=False, device=device, dtype=dtype)
    ww = torch.hann_window(width, periodic=False, device=device, dtype=dtype)
    return hh[:, None] * ww[None, :]


def _gem_subpixel_peak(axis, index):
    """沿一维抛物线亚像素：返回 [-0.5, 0.5] 的修正量。

    相位相关是循环的，所以峰落在索引 0 / 末尾时取循环邻居，三点才对称。
    """
    n = len(axis)
    left = axis[index - 1]            # Python 负索引自动取循环前邻
    right = axis[(index + 1) % n]
    denom = left - 2.0 * axis[index] + right
    if abs(float(denom)) < 1e-12:
        return 0.0
    delta = 0.5 * (left - right) / denom
    return max(-0.5, min(0.5, float(delta)))


_GEM_ALIGN_PEAK_RATIO = 2.5   # 峰值/RMS 低于此值 = 低纹理/重绘差异过大，不配准（峰高阈值 0.15 已把关噪声，峰比可放宽）
# 归一化峰高下限。实测四档：不相关噪声伪峰 ≈ 0.02；真实重绘 tile（Qwen 卡通化，
# 2144x1952）峰 ≈ 0.12~0.13 且 ratio 高达 234~258（信号极强，峰高只是被内容一致区摊低）；
# 真实大图（2144x3776 全图）微位移峰 ≈ 0.27。0.15 曾把真实重绘 tile 误杀，取 0.10 在
# 噪声与真实信号之间留足裕度；峰高不过线的 tile 说明重绘差异大到平移模型不适用，宁可不配。
_GEM_ALIGN_MIN_PEAK = 0.10
_GEM_ALIGN_MAX_EDGE = 2048    # 超过此长边才降采样；以内全分辨率估计（精度最高）


def _gem_estimate_shift(reference, target, max_shift):
    """估计 target 相对 reference 的内容位移 (dx, dy)（像素）；不可靠返回 None。"""
    out = _gem_estimate_shift_with_meta(reference, target, max_shift)
    return None if out is None else out[0]


def _gem_estimate_shift_with_meta(reference, target, max_shift):
    """同 _gem_estimate_shift，但返回 (shift, {peak, ratio})；不可靠时返回 None。

    diagnostics 需要知道"为什么 skipped"：真实重绘 tile 的相关峰可能明显低于
    合成测试（卡通化差异大），把实测 peak/ratio 带回诊断，才能精准定阈值而不用盲调。
    """
    h, w = int(reference.shape[1]), int(reference.shape[2])
    ref_gray = _gem_to_gray(reference[0:1]).mean(dim=0)   # (H, W)
    tgt_gray = _gem_to_gray(target[0:1]).mean(dim=0)
    scale = min(1.0, float(_GEM_ALIGN_MAX_EDGE) / max(h, w))
    est_h, est_w = max(1, int(round(h * scale))), max(1, int(round(w * scale)))
    if (est_h, est_w) != (h, w):
        # area 抗锯齿降采样，保留更多高频信息给相关
        ref_gray = torch.nn.functional.interpolate(
            ref_gray[None, None], size=(est_h, est_w), mode="area"
        )[0, 0]
        tgt_gray = torch.nn.functional.interpolate(
            tgt_gray[None, None], size=(est_h, est_w), mode="area"
        )[0, 0]
    window = _gem_hann2d(est_h, est_w, ref_gray.device, ref_gray.dtype)
    a = (ref_gray - ref_gray.mean()) * window
    b = (tgt_gray - tgt_gray.mean()) * window
    cross = torch.fft.rfft2(a) * torch.conj(torch.fft.rfft2(b))
    corr = torch.fft.irfft2(cross / (cross.abs() + 1e-8), s=(est_h, est_w))
    peak_val, peak_idx = corr.reshape(-1).max(0)
    py, px = divmod(int(peak_idx), est_w)
    dx = px + _gem_subpixel_peak(corr[py, :], px)
    dy = py + _gem_subpixel_peak(corr[:, px], py)
    if dx > est_w / 2.0:
        dx -= est_w
    if dy > est_h / 2.0:
        dy -= est_h
    meta = {"peak": float(peak_val), "ratio": float(peak_val) / (corr.std() + 1e-8)}
    # 可靠度：①归一化峰高下限（噪声伪峰仅 ~0.02，真实大图微位移 ~0.27，取 0.15 在两者之间）；
    # ②峰值相对相关面 RMS 的对比度。
    if float(peak_val) < _GEM_ALIGN_MIN_PEAK:
        return (None, meta)
    if meta["ratio"] < _GEM_ALIGN_PEAK_RATIO:
        return (None, meta)
    dx_full, dy_full = dx / scale, dy / scale
    if abs(dx_full) > max_shift or abs(dy_full) > max_shift:
        return (None, meta)
    return (dx_full, dy_full), meta


def _gem_apply_shift(img, dx, dy):
    """grid_sample 平移整张 BHWC tile；边界 border 复制填充。"""
    b, h, w, c = img.shape
    theta = torch.zeros((b, 2, 3), dtype=torch.float32, device=img.device)
    theta[:, 0, 0] = 1.0
    theta[:, 1, 1] = 1.0
    theta[:, 0, 2] = 2.0 * float(dx) / w
    theta[:, 1, 2] = 2.0 * float(dy) / h
    grid = torch.nn.functional.affine_grid(theta, (b, c, h, w), align_corners=False)
    moved = torch.nn.functional.grid_sample(
        img.permute(0, 3, 1, 2), grid, mode="bilinear", padding_mode="border", align_corners=False
    )
    return moved.permute(0, 2, 3, 1)


def _gem_blend_shifted(tile, dx, dy, edge):
    """平移后的 tile 与未平移版在边缘 edge 像素内平滑过渡。

    内部用配准结果（内容归位）；最外圈保持原 tile，避免图像最外缘（无邻居
    覆盖的边）因平移露出拉伸伪影而撕裂。overlap 混合区的权重本身在边界趋 0，
    与这里的过渡叠加后自然无痕。
    """
    moved = _gem_apply_shift(tile, dx, dy)
    b, h, w, c = tile.shape
    edge = max(1, int(edge))
    if 2 * edge < w:
        xw = torch.ones(w, dtype=tile.dtype, device=tile.device)
        xw[:edge] = torch.linspace(0.0, 1.0, edge, dtype=tile.dtype, device=tile.device)
        xw[-edge:] = torch.linspace(1.0, 0.0, edge, dtype=tile.dtype, device=tile.device)
    else:
        xw = torch.linspace(0.0, 1.0, w, dtype=tile.dtype, device=tile.device)
    if 2 * edge < h:
        yw = torch.ones(h, dtype=tile.dtype, device=tile.device)
        yw[:edge] = torch.linspace(0.0, 1.0, edge, dtype=tile.dtype, device=tile.device)
        yw[-edge:] = torch.linspace(1.0, 0.0, edge, dtype=tile.dtype, device=tile.device)
    else:
        yw = torch.linspace(0.0, 1.0, h, dtype=tile.dtype, device=tile.device)
    wmap = (yw[:, None] * xw[None, :]).clamp(0.0, 1.0)   # 内部 1、边缘 0
    return moved * wmap[None, :, :, None] + tile * (1.0 - wmap[None, :, :, None])


class GemTiledUpscale:
    """Tiled Upscale（ItsGemTools 复刻）：按 megapixels 设定成图尺寸并切成 tile。

    与 GemTiledUpscaleStitch 配对使用；Stitch 端自带亚像素几何配准，适配
    QwenImage 2.1 + 自训 LoRA 的重绘漂移。
    """

    CATEGORY = "ItsGemTools/image"
    DESCRIPTION = (
        "按 megapixels 设定成图尺寸，把图切成模型能一次重绘的 tile（32 对齐）。"
        "把 tiles 送进采样器重绘，再接 GemTiledUpscaleStitch 拼回。"
        "Stitch 端自带亚像素几何配准，适配 QwenImage 2.1 + 自训 LoRA。"
    )

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "image": ("IMAGE", {"tooltip": "要放大并切成 tile 的图。"}),
                "megapixels": ("FLOAT", {
                    "default": 4.0, "min": 0.0, "max": 64.0, "step": 0.05,
                    "tooltip": "成图尺寸（百万像素）。0 = 保持原尺寸。",
                }),
            },
            "optional": {
                "upscale_model": ("UPSCALE_MODEL", {
                    "tooltip": "可选：放大模型，先放大再切 tile；不接则普通 lanczos 放大。",
                }),
                "tile_megapixels": ("FLOAT", {
                    "default": 2.0, "min": 0.25, "max": 16.0, "step": 0.05,
                    "tooltip": "单 tile 最大重绘尺寸。Qwen Image 2.1 / Krea 2 用 2，SDXL 用 1。超过 2048x2048 上限时会被 enforce_qwen_tile_limit 自动调低。",
                }),
                "overlap": ("INT", {
                    "default": 128, "min": 0, "max": 1024, "step": 8,
                    "tooltip": "相邻 tile 共享的像素宽；拼缝在这条带上混合。",
                }),
                "multiple": ("INT", {
                    "default": 32, "min": 1, "max": 256, "step": 1,
                    "tooltip": "每个 tile 的宽高都是它的倍数。32 适配 Qwen Image 2.1。",
                }),
                "max_growth": ("FLOAT", {
                    "default": 8.0, "min": 1.0, "max": 64.0, "step": 0.5,
                    "tooltip": "小图重绘不超过自身 megapixels 的多少倍；超出部分普通放大。",
                }),
                "enforce_qwen_tile_limit": ("BOOLEAN", {
                    "default": True,
                    "tooltip": "防呆：单 tile 超过安全工作点（4.6MP，对齐 AusBoss 口径，官方硬上限 4.19MP）时，自动调低 tile_megapixels 并写入 report 提示；关闭则完全按填的参数切。",
                }),
            },
        }

    RETURN_TYPES = ("IMAGE", "ITSGEM_TILE_STITCHER", "IMAGE", "INT", "INT", "STRING")
    RETURN_NAMES = ("tiles", "stitcher", "before", "width", "height", "report")
    OUTPUT_IS_LIST = (True, False, False, False, False, False)
    OUTPUT_TOOLTIPS = (
        "The tiles, one after another. Nodes after this run once per tile.",
        "GemTiledUpscaleStitch 拼回所需的 stitcher。",
        "普通放大到成图尺寸的原图：重绘前的对照图。",
        "成图宽（像素）。",
        "成图高（像素）。",
        "这次放大做了什么，一两句话。",
    )
    FUNCTION = "split"

    def split(
        self,
        image,
        megapixels,
        upscale_model=None,
        tile_megapixels=2.0,
        overlap=128,
        multiple=32,
        max_growth=8.0,
        enforce_qwen_tile_limit=True,
    ):
        source = _gem_rgb(image, "Tiled Upscale (ItsGemTools)")
        height, width = int(source.shape[1]), int(source.shape[2])
        if enforce_qwen_tile_limit:
            plan, eff_tile_mp, clamped, orig_px, new_px = _gem_plan_tiles_qwen_clamped(
                width, height, megapixels, tile_megapixels, overlap, multiple, max_growth
            )
        else:
            plan = _gem_plan_tiles(
                width, height, megapixels, tile_megapixels, overlap, multiple, max_growth
            )
            eff_tile_mp, clamped, orig_px, new_px = tile_megapixels, False, 0, 0
        redraw_width, redraw_height = plan["redraw"]
        result_width, result_height = plan["result"]

        enlarged = source
        # 重绘尺寸比原图大 5% 以上才值得跑一次放大模型
        if upscale_model is not None and redraw_width * redraw_height > width * height * 1.05:
            enlarged = _gem_model_enlarge(upscale_model, source, redraw_width, redraw_height)
        base = _gem_resample_batch(enlarged, redraw_width, redraw_height, "lanczos")
        before = _gem_resample_batch(source, result_width, result_height, "lanczos")
        tiles = [
            base[:, top:bottom, left:right, :].contiguous()
            for left, top, right, bottom in plan["boxes"]
        ]
        stitcher = {"kind": _GEM_STITCHER_KIND, "version": 2, "plan": plan, "tiles": tiles}
        report = _gem_describe_plan(plan)
        if clamped:
            report += (
                f" | Qwen 上限防呆: tile_megapixels={tile_megapixels:g} 单块"
                f" {orig_px / 1e6:.3f}MP 超安全工作点 4.6MP（官方硬上限 4.19MP），已自动降为"
                f" {eff_tile_mp:g}（单块 {new_px / 1e6:.3f}MP）"
            )
        return (tiles, stitcher, before, result_width, result_height, report)


class GemTiledUpscaleStitch:
    """Tiled Upscale Stitch（ItsGemTools 复刻）：重绘 tile 拼回 + 几何配准。

    每个重绘 tile 先与源 tile 做 FFT 相位相关亚像素平移估计，归位后再混合，
    消除 QwenImage 2.1 + 自训 LoRA 重绘导致的接缝偏移 / 重影；低纹理或位移
    超限时自动跳过配准，行为与原版一致。
    """

    CATEGORY = "ItsGemTools/image"
    DESCRIPTION = (
        "把 GemTiledUpscale 的 tiles 重绘结果拼回成图。相比原版新增亚像素几何配准"
        "（align_mode=translation）：每个重绘 tile 先与源 tile 配准归位再混合，"
        "根治接缝偏移 / 重影，适配 QwenImage 2.1 + 自训 LoRA。"
    )
    INPUT_IS_LIST = True

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "tiles": ("IMAGE", {"tooltip": "重绘后的 tiles，按 GemTiledUpscale 给出的顺序。"}),
                "stitcher": ("ITSGEM_TILE_STITCHER", {"tooltip": "GemTiledUpscale 输出的 stitcher。"}),
            },
            "optional": {
                "keep_colors": ("FLOAT", {
                    "default": 1.0, "min": 0.0, "max": 1.0, "step": 0.05,
                    "tooltip": "1 保持原图颜色（每 tile 匹配源 tile 颜色）；0 关。",
                }),
                "align_mode": (["translation", "off"], {
                    "default": "translation",
                    "tooltip": "translation：重绘 tile 与源 tile 亚像素平移配准后再混合（默认，适配自训 LoRA）；off：原版行为。",
                }),
                "max_align_shift": ("INT", {
                    "default": 64, "min": 1, "max": 256, "step": 1,
                    "tooltip": "配准位移超过该像素数判为不可靠并跳过，防止错误配准。zoom/重构图漂移大的工作流可调大。",
                }),
                "blend_band": ("INT", {
                    "default": 0, "min": 0, "max": 256, "step": 8,
                    "tooltip": "接缝混合模式。0 = 全带对称渐变（默认、推荐）：共享区两 tile 权重 0↔1 横跨整个 overlap、权重和恒=1，用相邻 tile 平均天然稀释单 tile 随机幻觉（幽灵下巴），配准先对齐几何故无重影，与 AusBoss 一致。>0 = 窄带独占（可选）：后写 tile 独占 overlap 主体、仅边界 band 内衔接，仅在全带确实出现重影且无幻觉时使用，值为渐变带宽。",
                }),
            },
        }

    RETURN_TYPES = ("IMAGE", "STRING")
    RETURN_NAMES = ("image", "diagnostics")
    OUTPUT_IS_LIST = (True, False)
    OUTPUT_TOOLTIPS = (
        "拼回并调整到目标尺寸的成图。输入多张图时输出列表，各一张成图。",
        "逐 tile 配准诊断：每个 tile 的估计位移 / 跳过原因，排查偏移用。",
    )
    FUNCTION = "stitch"

    def stitch(self, tiles, stitcher, keep_colors=None, align_mode="translation", max_align_shift=64, blend_band=0):
        # INPUT_IS_LIST=True：ComfyUI 会把所有输入（含 widget 标量）包成 list，
        # 统一解包，避免 int(list) 报错、str(list)=="translation" 永远为 False 导致配准静默失效
        if isinstance(keep_colors, (list, tuple)):
            keep_colors = keep_colors[0] if keep_colors else None
        if isinstance(align_mode, (list, tuple)):
            align_mode = align_mode[0] if align_mode else "translation"
        if isinstance(max_align_shift, (list, tuple)):
            max_align_shift = max_align_shift[0] if max_align_shift else 24
        if isinstance(blend_band, (list, tuple)):
            blend_band = blend_band[0] if blend_band else 0
        # 多张图时 Tiled Upscale 每张跑一次：tiles 按图逐段到达，每个 stitcher 对应一段
        stitchers = list(stitcher) if isinstance(stitcher, (list, tuple)) else [stitcher]
        if not stitchers or any(
            not isinstance(item, dict) or item.get("kind") != _GEM_STITCHER_KIND for item in stitchers
        ):
            raise ValueError(
                "Tiled Upscale Stitch (ItsGemTools) needs the stitcher from Tiled Upscale (ItsGemTools). "
                "AusBoss 的 stitcher 不兼容：请把切块与拼回两个节点成对替换。"
            )
        strength = 1.0 if keep_colors is None else max(0.0, min(1.0, float(keep_colors)))
        do_align = align_mode == "translation"
        max_shift = max(1, int(max_align_shift))
        counts = [len(item["plan"]["boxes"]) for item in stitchers]
        expected = sum(counts)

        redrawn = [
            _gem_rgb(tile, "Tiled Upscale Stitch (ItsGemTools)")
            for tile in (tiles if isinstance(tiles, (list, tuple)) else [tiles])
        ]
        if len(redrawn) == 1 and expected > 1 and redrawn[0].shape[0] % expected == 0:
            # tiles 以单个 batch 到达：按切块时的顺序拆回
            per_tile = redrawn[0].shape[0] // expected
            redrawn = list(redrawn[0].split(per_tile, dim=0))
        if len(redrawn) != expected:
            has = f"{len(stitchers)} stitchers have" if len(stitchers) > 1 else "the stitcher has"
            raise ValueError(
                f"Tiled Upscale Stitch (ItsGemTools) got {len(redrawn)} tiles and {has} {expected}. "
                "Send every tile from Tiled Upscale (ItsGemTools) through the same nodes, in order."
            )

        pictures = []
        diagnostics = []
        start = 0
        for item, count in zip(stitchers, counts):
            picture, notes = _gem_join(
                redrawn[start:start + count], item, strength, do_align, max_shift, blend_band
            )
            pictures.append(picture)
            if notes:
                diagnostics.append(f"--- 图 {len(pictures)} ---")
                diagnostics.extend(notes)
            start += count
        return (pictures, "\n".join(diagnostics))


def _gem_join(redrawn, stitcher, strength, do_align, max_shift, blend_band=0):
    """一张图由重绘 tiles 拼回：颜色匹配 +（可选）几何配准 + 接缝混合。

    默认 blend_band=0 用全带对称渐变（_gem_ramp，与 AusBoss _ramp 一致）：共享区两
    tile 权重 0↔1 横跨整个 overlap、权重和恒=1，相邻 tile 平均天然稀释单 tile 随机
    幻觉（幽灵下巴）；配准先对齐几何，避免全带平均错位内容产生重影。
    blend_band>0 时改用窄带独占混合（_gem_ramp_edge）：overlap 主体由后写 tile 独占，
    仅在全带确实出现重影且无幻觉时作为可选。
    配准参考改为已拼入的邻接 tile 输出（卡通域内对齐），修复"照片 vs 卡通"域不匹配
    导致全部 skipped 的问题。
    """
    plan = stitcher["plan"]
    boxes = plan["boxes"]
    sources = stitcher["tiles"]
    redraw_width, redraw_height = plan["redraw"]
    result_width, result_height = plan["result"]
    tile_width, tile_height = plan["tile"]
    lefts = sorted({box[0] for box in boxes})
    tops = sorted({box[1] for box in boxes})
    batch = max(tile.shape[0] for tile in redrawn)
    total = torch.zeros((batch, redraw_height, redraw_width, 3), dtype=torch.float32)
    weight = torch.zeros((redraw_height, redraw_width), dtype=torch.float32)
    # 配准参考：未乘 mask 的原始重绘内容累加 + 覆盖计数。
    # 不能直接拿 total/weight 当参考——窄带混合里先写的邻居在共享区 mask 让出（权重=0），
    # 快照的共享区内容≈0，相位相关测不出位移（实测 peak 只有 0.01~0.02）。
    # 原始累加保证共享区保留邻居的完整内容，卡通域内才能对齐。
    raw_total = torch.zeros_like(total)
    coverage = torch.zeros((redraw_height, redraw_width), dtype=torch.float32)
    diagnostics = []
    band = max(0, int(blend_band))

    # 行优先处理：保证配准参考（已拼入的左/上邻居）与窄带独占方向一致
    order = sorted(range(len(boxes)), key=lambda i: (boxes[i][1], boxes[i][0]))
    for idx in order:
        left, top, right, bottom = boxes[idx]
        tile = _gem_resample_batch(redrawn[idx].cpu(), tile_width, tile_height, "lanczos")
        reference = sources[idx].cpu()
        if reference.shape[0] not in (1, tile.shape[0]):
            reference = reference[:1]
        if strength > 0.0:
            tile = _gem_keep_colors(tile, reference, strength)
        # Qwen 重绘 tile 边缘黑边修复（实测左缘 3-4px 黑边，拼接后成竖缝黑线）
        tile = _gem_fix_tile_edge_band(tile)
        note = None
        placed = raw_total / coverage.clamp_min(1e-6)[None, :, :, None]
        if do_align:
            # 已拼入的邻接卡通输出做参考（原始内容，非 mask 快照；首块无邻居，无配准）
            align_out = _gem_align_vs_placed(tile, placed, left, top, lefts, tops, max_shift)
            if align_out is not None:
                (dx, dy), _, via = align_out
                # 方向语义与旧版一致：估计的是"内容相对参考的位移"，归位取反
                tile = _gem_apply_shift(tile, -dx, -dy)
                note = f"shift=({dx:+.1f},{dy:+.1f}) via {via}"
            else:
                meta = None
                # 首块无邻居时不配准也不判 skipped，单独标注
                if idx == order[0]:
                    note = "n/a(无邻接，起点)"
                else:
                    lv = _gem_estimate_shift_with_meta(
                        placed[:, top:top + tile_height, left:left + tile_width, :], tile, max_shift
                    )
                    meta = lv[1] if lv is not None else None
                    note = "skipped(估计不可靠)"
                    if meta:
                        note = "skipped(peak={:.2f},ratio={:.0f})".format(
                            meta.get("peak", 0.0), meta.get("ratio", 0.0)
                        )
        # 配准归位后，窄带混合下用共享区统计做逐通道 gain+bias 拉齐（tone match），
        # 消除 Qwen 各 tile 独立重绘造成的块间亮度/色调不一致（"格子"感）。
        # band=0（旧全带渐变兼容路径）不做 tone match，保持旧行为。
        if band > 0:
            tile = _gem_tone_match(tile, placed, left, top, lefts, tops, band)
        share_left, share_right = _gem_shared(lefts, tile_width, left)
        share_top, share_bottom = _gem_shared(tops, tile_height, top)
        if band > 0:
            mask = (
                _gem_ramp_edge(tile_height, share_top, share_bottom, band)[:, None]
                * _gem_ramp_edge(tile_width, share_left, share_right, band)[None, :]
            )
        else:
            mask = (
                _gem_ramp(tile_height, share_top, share_bottom)[:, None]
                * _gem_ramp(tile_width, share_left, share_right)[None, :]
            )
        total[:, top:bottom, left:right, :] += tile * mask[None, :, :, None]
        weight[top:bottom, left:right] += mask
        raw_total[:, top:bottom, left:right, :] += tile
        coverage[top:bottom, left:right] += 1.0
        if note:
            diagnostics.append(
                f"tile@({left},{top}) {tile_width}x{tile_height}: {note}"
            )

    merged = total / weight.clamp_min(1e-6)[None, :, :, None]
    return (
        _gem_resample_batch(merged.clamp_(0.0, 1.0), result_width, result_height, "lanczos"),
        diagnostics,
    )


# ============================================================
# Color Calibrator (ItsGemTools) —— 复刻自 BlueBox 的 ColorCalibratorBlueBox
# 参考：zhoutingxi/ComfyUI-BlueBox（MIT）nodes/color_calibrator_bluebox.py，
# 仅按算法思路在本包内重写；原版节点保持零改动、互不冲突。
# 算法：自动对比两图检测变化区（阈值 + 开/闭运算 + 膨胀）→ 遮罩外逐通道
#       仿射校色 → 遮罩外锚点测对比度拉伸倍数并围绕编辑图黑场回压全图 →
#       遮罩外低频回贴（高频保留）或原图回贴，输出 image / mask / changed_ratio。
# 依赖：仅 torch / numpy / PIL（ComfyUI 自带）；scipy 缺失时大半径膨胀自动
#       退化为 GPU max_pool（结果一致，仅速度略慢）。
# ============================================================
F = torch.nn.functional

_GEM_CC_MIN_ANCHOR_PCT = 0.02
_GEM_CC_WAVELET_LEVELS = 3
_GEM_CC_WAVELET_SIGMA = 2.0
_GEM_CC_SCALE_CLAMP = (0.5, 2.0)
_GEM_CC_RANGE_CLAMP = (0.3, 3.0)          # 对比度回压倍数 a 的放宽区间
_GEM_CC_RANGE_CLIP_PCT = 0.005            # 测黑/白场的剪切百分比（抗噪）
_GEM_CC_RANGE_DOWNSCALE = 4               # 测范围在 1/4 分辨率上做
_GEM_CC_FEATHER_DOWNSCALE = 4
_GEM_CC_WAVELET_DOWNSCALE = 4             # 低频在 1/4 分辨率上算
_GEM_CC_PREVIEW_MAX_SIDE = 512            # 预览图最大边
_GEM_CC_EDT_MAX_K = 32                    # 超过此半径才考虑半分辨率 EDT


def _gem_cc_to_bhwc(t):
    if t.dim() == 3:
        return t.unsqueeze(0)
    return t


def _gem_cc_split_rgb_alpha(img):
    c = img.shape[-1]
    if c >= 4:
        return img[..., :3], img[..., 3:4]
    if c == 3:
        return img, None
    if c == 1:
        return img.expand(-1, -1, -1, 3), None
    rgb = img[..., :3]
    if rgb.shape[-1] < 3:
        pad = torch.zeros(*rgb.shape[:-1], 3 - rgb.shape[-1],
                          dtype=rgb.dtype, device=rgb.device)
        rgb = torch.cat([rgb, pad], dim=-1)
    return rgb, None


def _gem_cc_merge_rgb_alpha(rgb, alpha):
    if alpha is None:
        return rgb
    return torch.cat([rgb, alpha], dim=-1)


def _gem_cc_mask_to_bhw1(mask, b, h, w):
    if mask.dim() == 2:
        mask = mask.unsqueeze(0)
    if mask.dim() == 4:
        if mask.shape[-1] == 1:
            m = mask
        elif mask.shape[1] == 1:
            m = mask.permute(0, 2, 3, 1)
        else:
            m = mask[..., :1]
    else:
        m = mask.unsqueeze(-1)
    if m.shape[1] != h or m.shape[2] != w:
        m = F.interpolate(
            m.permute(0, 3, 1, 2), size=(h, w),
            mode="bilinear", align_corners=False,
        ).permute(0, 2, 3, 1)
    if m.shape[0] == 1 and b > 1:
        m = m.expand(b, -1, -1, -1)
    elif m.shape[0] != b:
        m = m[:1].expand(b, -1, -1, -1)
    return m.clamp(0.0, 1.0)


def _gem_cc_gaussian_blur_bhwc(rgb, sigma):
    if sigma <= 0:
        return rgb
    k = int(math.ceil(sigma * 3)) * 2 + 1
    xs = torch.arange(k, dtype=torch.float32, device=rgb.device) - k // 2
    g = torch.exp(-(xs ** 2) / (2 * sigma * sigma))
    g = g / g.sum()
    x = rgb.permute(0, 3, 1, 2)
    c = x.shape[1]
    gx = g.view(1, 1, 1, k).repeat(c, 1, 1, 1)
    x = F.conv2d(F.pad(x, (k // 2, k // 2, 0, 0), mode="replicate"), gx, groups=c)
    gy = g.view(1, 1, k, 1).repeat(c, 1, 1, 1)
    x = F.conv2d(F.pad(x, (0, 0, k // 2, k // 2), mode="replicate"), gy, groups=c)
    return x.permute(0, 2, 3, 1)


def _gem_cc_odd(k):
    """强制核大小为奇数，避免 max_pool2d 尺寸漂移。"""
    if k <= 1:
        return k
    return k if (k % 2 == 1) else k + 1


def _gem_cc_morph_open(mask_bhw1, k):
    if k <= 1:
        return mask_bhw1
    k = _gem_cc_odd(k)
    x = mask_bhw1.permute(0, 3, 1, 2)
    x = -F.max_pool2d(-x, kernel_size=k, stride=1, padding=k // 2)
    x = F.max_pool2d(x, kernel_size=k, stride=1, padding=k // 2)
    return x.permute(0, 2, 3, 1)


def _gem_cc_morph_close(mask_bhw1, k):
    if k <= 1:
        return mask_bhw1
    k = _gem_cc_odd(k)
    x = mask_bhw1.permute(0, 3, 1, 2)
    x = F.max_pool2d(x, kernel_size=k, stride=1, padding=k // 2)
    x = -F.max_pool2d(-x, kernel_size=k, stride=1, padding=k // 2)
    return x.permute(0, 2, 3, 1)


def _gem_cc_dilate_pool(mask_bhw1, k):
    """GPU 方形核膨胀，O(HW)，常数极小。k≤32 时与圆核肉眼无差。"""
    if k <= 0:
        return mask_bhw1
    k = _gem_cc_odd(k)
    x = mask_bhw1.permute(0, 3, 1, 2)
    x = F.max_pool2d(x, kernel_size=k, stride=1, padding=k // 2)
    return x.permute(0, 2, 3, 1)


def _gem_cc_dilate_edt_half(mask_bhw1, k, downscale=2):
    """半分辨率 EDT 再上采样，误差 <1px，速度 ×4；scipy 缺失时退化 max_pool。"""
    if k <= 0:
        return mask_bhw1
    B, H, W, _ = mask_bhw1.shape
    sh = max(H // downscale, 1)
    sw = max(W // downscale, 1)
    m = mask_bhw1.permute(0, 3, 1, 2)
    m_small = F.interpolate(m, size=(sh, sw), mode="bilinear", align_corners=False)
    k_small = max(k // downscale, 1)
    device = m_small.device
    outs = []
    for i in range(B):
        arr = m_small[i, 0].detach().cpu().numpy()
        fg = arr > 0.5
        if not fg.any():
            outs.append(torch.zeros_like(m_small[i, 0]))
            continue
        if distance_transform_edt is None:
            return _gem_cc_dilate_pool(mask_bhw1, k)
        dist = distance_transform_edt(~fg)
        outs.append(torch.from_numpy((dist <= k_small).astype(np.float32)))
    out = torch.stack(outs, dim=0).unsqueeze(1).to(device)
    out = F.interpolate(out, size=(H, W), mode="bilinear", align_corners=False)
    return out.permute(0, 2, 3, 1).clamp(0, 1)


def _gem_cc_dilate(mask_bhw1, k):
    """统一入口：小半径用 GPU max_pool，大半径用半分辨率 EDT。"""
    if k <= 0:
        return mask_bhw1
    if k <= _GEM_CC_EDT_MAX_K:
        return _gem_cc_dilate_pool(mask_bhw1, k)
    return _gem_cc_dilate_edt_half(mask_bhw1, k, downscale=2)


def _gem_cc_feather_mask_full(mask, radius):
    if radius <= 0:
        return mask
    k = radius * 2 + 1
    sigma = max(radius / 3.0, 0.5)
    xs = torch.arange(k, dtype=torch.float32, device=mask.device) - radius
    g = torch.exp(-(xs ** 2) / (2 * sigma * sigma))
    g = g / g.sum()
    x = mask.permute(0, 3, 1, 2)
    gx = g.view(1, 1, 1, k)
    x = F.conv2d(F.pad(x, (radius, radius, 0, 0), mode="replicate"), gx)
    gy = g.view(1, 1, k, 1)
    x = F.conv2d(F.pad(x, (0, 0, radius, radius), mode="replicate"), gy)
    return x.permute(0, 2, 3, 1).clamp(0.0, 1.0)


def _gem_cc_feather_mask_fast(mask_bhw1, radius, downscale=_GEM_CC_FEATHER_DOWNSCALE):
    if radius <= 0:
        return mask_bhw1
    B, H, W, _ = mask_bhw1.shape
    small_h = max(H // downscale, 1)
    small_w = max(W // downscale, 1)
    if small_h < 4 or small_w < 4:
        return _gem_cc_feather_mask_full(mask_bhw1, radius)
    m = mask_bhw1.permute(0, 3, 1, 2)
    m_small = F.interpolate(m, size=(small_h, small_w),
                            mode="bilinear", align_corners=False)
    small_radius = max(radius // downscale, 1)
    m_small = _gem_cc_feather_mask_full(m_small.permute(0, 2, 3, 1), small_radius)
    m_back = F.interpolate(
        m_small.permute(0, 3, 1, 2), size=(H, W),
        mode="bilinear", align_corners=False,
    )
    return m_back.permute(0, 2, 3, 1).clamp(0, 1)


def _gem_cc_gaussian_pyramid(rgb, levels, sigma):
    pyr = [rgb]
    for _ in range(levels):
        pyr.append(_gem_cc_gaussian_blur_bhwc(pyr[-1], sigma))
    low = pyr[-1]
    highs = [pyr[i] - pyr[i + 1] for i in range(len(pyr) - 1)]
    return low, highs


def _gem_cc_compute_affine_params(I0, I1, A):
    """
    A: (B,H,W,1) 前景权重（遮罩外=1，遮罩内=0）
    用加权均值/方差，等价于"只在前景上统计"，但完全不物化布尔索引。
    """
    device = I0.device
    scale = torch.ones(3, device=device)
    shift = torch.zeros(3, device=device)
    w = A.reshape(-1)                            # (N,)
    w_sum = w.sum()
    if w_sum.item() < 32:                        # 只同步一次
        return scale, shift
    inv_n = 1.0 / w_sum
    for c in range(3):
        s = I1[..., c].reshape(-1)
        d = I0[..., c].reshape(-1)
        mu1 = (w * s).sum() * inv_n
        mu0 = (w * d).sum() * inv_n
        var1 = (w * (s - mu1) ** 2).sum() * inv_n
        var0 = (w * (d - mu0) ** 2).sum() * inv_n
        sd1 = torch.sqrt(var1.clamp_min(1e-12))
        sd0 = torch.sqrt(var0.clamp_min(1e-12))
        if sd1.item() < 1e-4:                    # 只同步一次/通道
            scale[c] = 1.0
            shift[c] = mu0 - mu1
        else:
            sc = (sd0 / sd1).clamp(*_GEM_CC_SCALE_CLAMP)
            scale[c] = sc
            shift[c] = mu0 - sc * mu1
    return scale, shift


def _gem_cc_apply_affine(rgb, scale, shift):
    return (rgb * scale.view(1, 1, 1, 3) + shift.view(1, 1, 1, 3)).clamp(0, 1)


def _gem_cc_auto_detect_mask(I0, I1, threshold, expand, open_k, close_k):
    diff = (I0 - I1).abs().max(dim=-1, keepdim=True).values
    mask = (diff > threshold).float()
    if open_k > 1:
        mask = _gem_cc_morph_open(mask, open_k)
    if close_k > 1:
        mask = _gem_cc_morph_close(mask, close_k)
    if expand > 0:
        mask = _gem_cc_dilate(mask, expand)
    return mask.clamp(0, 1)


class GemColorCalibrator:
    """
    Color Calibrator (ItsGemTools)
    复刻 BlueBox ColorCalibratorBlueBox，一个节点内完成：
      1. 若 mask 未接 → 自动对比两图检测变化区作为遮罩
      2. 用遮罩外（严格排除遮罩内）逐通道仿射估计色差
      3. 用遮罩外前后对比估计对比度拉伸倍数，围绕编辑图自身黑场
         把整张编辑图的跨度回压（含遮罩内），找回原图对比度
      4. outside_mode == "edited"：遮罩外高斯金字塔融合（低频原图 + 高频编辑图）
         outside_mode == "original"：遮罩外严格回贴原图
    """
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "image_original": ("IMAGE",),
                "image_edited": ("IMAGE",),
                "threshold": ("FLOAT", {
                    "default": 0.08, "min": 0.0, "max": 1.0, "step": 0.01,
                }),
                "expand": ("INT", {
                    "default": 48, "min": 0, "max": 128, "step": 1,
                }),
                "open_k": ("INT", {
                    "default": 13, "min": 1, "max": 81, "step": 2,
                }),
                "close_k": ("INT", {
                    "default": 15, "min": 1, "max": 81, "step": 2,
                }),
                "strength": ("FLOAT", {
                    "default": 1.0, "min": 0.0, "max": 1.0, "step": 0.05,
                }),
                "outside_mode": (["edited", "original"], {"default": "edited"}),
                "feather": ("INT", {
                    "default": 24, "min": 0, "max": 128, "step": 1,
                }),
            },
            "optional": {
                "mask": ("MASK",),
            },
        }
    RETURN_TYPES = ("IMAGE", "MASK", "FLOAT")
    RETURN_NAMES = ("image", "mask", "changed_ratio")
    FUNCTION = "run"
    CATEGORY = "image/color"

    def run(self, image_original, image_edited,
            threshold, expand, open_k, close_k,
            strength, outside_mode, feather, mask=None):
        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        I0_raw = _gem_cc_to_bhwc(image_original).float().to(device)
        I1_raw = _gem_cc_to_bhwc(image_edited).float().to(device)
        I0_rgb, I0_alpha = _gem_cc_split_rgb_alpha(I0_raw)
        I1_rgb, I1_alpha = _gem_cc_split_rgb_alpha(I1_raw)
        B = max(I0_rgb.shape[0], I1_rgb.shape[0])
        if I0_rgb.shape[0] == 1 and B > 1:
            I0_rgb = I0_rgb.expand(B, -1, -1, -1)
            if I0_alpha is not None:
                I0_alpha = I0_alpha.expand(B, -1, -1, -1)
        if I1_rgb.shape[0] == 1 and B > 1:
            I1_rgb = I1_rgb.expand(B, -1, -1, -1)
            if I1_alpha is not None:
                I1_alpha = I1_alpha.expand(B, -1, -1, -1)
        H, W = I1_rgb.shape[1], I1_rgb.shape[2]
        if I0_rgb.shape[1] != H or I0_rgb.shape[2] != W:
            I0_rgb = F.interpolate(
                I0_rgb.permute(0, 3, 1, 2), size=(H, W),
                mode="bilinear", align_corners=False,
            ).permute(0, 2, 3, 1)
            if I0_alpha is not None:
                I0_alpha = F.interpolate(
                    I0_alpha.permute(0, 3, 1, 2), size=(H, W),
                    mode="nearest",
                ).permute(0, 2, 3, 1)
        I0 = I0_rgb
        I1 = I1_rgb
        if mask is None:
            M = _gem_cc_auto_detect_mask(I0, I1, threshold, expand, open_k, close_k)
            if M.shape[0] == 1 and B > 1:
                M = M.expand(B, -1, -1, -1)
        else:
            M = _gem_cc_mask_to_bhw1(mask.to(device), B, H, W)
        M_stat = (M > 0.5).float()
        A = 1.0 - M_stat
        anchor_ratio = A.mean().item()            # 只同步一次
        changed_ratio = M.mean().item()
        if anchor_ratio < _GEM_CC_MIN_ANCHOR_PCT:
            out_rgb = I1.clamp(0, 1)
            out_alpha = I1_alpha if I1_alpha is not None else I0_alpha
            return self._finish(out_rgb, out_alpha, M,
                                changed_ratio=float(changed_ratio),
                                preview_rgb=I0)

        scale, shift = _gem_cc_compute_affine_params(I0, I1, A)
        I1_corr = _gem_cc_apply_affine(I1, scale, shift)
        I1_corr = (I1 * (1.0 - strength) + I1_corr * strength).clamp(0, 1)
        if outside_mode == "edited":
            I1_ranged = self._restore_contrast_by_anchor(I0, I1_corr, A)
            out_rgb = self._wavelet_merge(I0, I1_ranged, M, feather)
        else:
            M_feather = _gem_cc_feather_mask_fast(M, feather) if feather > 0 else M
            I1_ranged = self._restore_contrast_by_anchor(I0, I1_corr, A)
            out_rgb = (M_feather * I1_ranged + (1.0 - M_feather) * I0).clamp(0, 1)
        out_alpha = I1_alpha if I1_alpha is not None else I0_alpha
        return self._finish(out_rgb, out_alpha, M,
                            changed_ratio=float(changed_ratio),
                            preview_rgb=I0)

    def _restore_contrast_by_anchor(self, I0, I1_corr, A):
        """
        用遮罩外（可靠区）对比 I0 与 I1_corr 的主体黑/白场，
        估计编辑图相对原图的对比度拉伸倍数 a，
        然后围绕 I1_corr 自身黑场把整张图的跨度缩回 /a。
        不分通道、无外部参数。围绕编辑图自身黑场而非原图黑场，
        避免遮罩内外原始范围不同时被错误钉到原图绝对范围。
        """
        B, H, W, _ = I0.shape
        ds = _GEM_CC_RANGE_DOWNSCALE
        sh, sw = max(H // ds, 1), max(W // ds, 1)

        def _down(x):
            return F.interpolate(
                x.permute(0, 3, 1, 2), size=(sh, sw),
                mode="bilinear", align_corners=False,
            ).permute(0, 2, 3, 1)

        I0_s = _down(I0)
        I1_s = _down(I1_corr)
        A_s = _down(A)                                  # (B,sh,sw,1)

        def _weighted_q(vals, w, q):
            order = torch.argsort(vals)
            v = vals[order]
            ww = w[order]
            cum = torch.cumsum(ww, 0)
            total = cum[-1]
            if total.item() <= 0:
                return None
            target = total * q
            idx = torch.searchsorted(cum, target).clamp(0, len(v) - 1)
            return v[idx]

        out = I1_corr.clone()
        clip_pct = _GEM_CC_RANGE_CLIP_PCT
        for bi in range(B):
            w = A_s[bi, ..., 0].reshape(-1)
            if w.sum().item() < 32:
                continue
            # 统一（不分通道）：用三通道均值灰度测范围，保证色调关系不变
            g0 = I0_s[bi].mean(dim=-1).reshape(-1)
            g1 = I1_s[bi].mean(dim=-1).reshape(-1)
            L0 = _weighted_q(g0, w, clip_pct)
            H0 = _weighted_q(g0, w, 1.0 - clip_pct)
            L1 = _weighted_q(g1, w, clip_pct)
            H1 = _weighted_q(g1, w, 1.0 - clip_pct)
            if L0 is None or L1 is None:
                continue
            span0 = H0 - L0
            span1 = H1 - L1
            if span0.item() < 1e-3 or span1.item() < 1e-3:
                continue
            a = (span1 / span0).clamp(*_GEM_CC_RANGE_CLAMP)
            # 围绕 I1_corr 自身黑场 L1 反转拉伸倍数，作用于全图（含遮罩内）
            out[bi] = ((I1_corr[bi] - L1) / a + L1).clamp(0, 1)
        return out

    def _wavelet_merge(self, I0, I1_corr, M, feather):
        """
        原算法恒等式：
            out = M*low1 + (1-M)*low0 + Σhighs1
                = I1_corr + (1-M)*(low0 - low1)      # 因为 Σhighs1 = I1_corr - low1
        因此只需在低分辨率上算 low0、low1 两个低频，再做 (low0-low1) 的融合。
        (low0-low1) 是低频差，降采样计算误差极小。
        """
        M_soft = _gem_cc_feather_mask_fast(M, feather) if feather > 0 else M
        B, H, W, _ = I0.shape
        ds = _GEM_CC_WAVELET_DOWNSCALE
        sh, sw = max(H // ds, 1), max(W // ds, 1)

        def _down(x):
            return F.interpolate(
                x.permute(0, 3, 1, 2), size=(sh, sw),
                mode="bilinear", align_corners=False,
            ).permute(0, 2, 3, 1)

        def _up(x):
            return F.interpolate(
                x.permute(0, 3, 1, 2), size=(H, W),
                mode="bilinear", align_corners=False,
            ).permute(0, 2, 3, 1)

        # 低频：1/4 分辨率上算 3 层高斯
        low0_s, _ = _gem_cc_gaussian_pyramid(_down(I0), _GEM_CC_WAVELET_LEVELS, _GEM_CC_WAVELET_SIGMA)
        low1_s, _ = _gem_cc_gaussian_pyramid(_down(I1_corr), _GEM_CC_WAVELET_LEVELS, _GEM_CC_WAVELET_SIGMA)
        M_soft_s = _down(M_soft)
        # (1-M) * (low0 - low1)，在 1/4 分辨率上融合，再上采样
        delta_s = (1.0 - M_soft_s) * (low0_s - low1_s)
        delta = _up(delta_s)
        # out = I1_corr + (1-M)*(low0 - low1)
        out = I1_corr + delta
        return out.clamp(0, 1)

    def _finish(self, out_rgb, out_alpha, M, changed_ratio, preview_rgb=None):
        # 输出与预览统一回 CPU（ComfyUI 期望 CPU IMAGE/MASK）
        out = _gem_cc_merge_rgb_alpha(out_rgb, out_alpha).cpu()
        M_cpu = M.cpu()
        preview_src = preview_rgb.cpu() if preview_rgb is not None else out
        preview = self._save_preview(preview_src, M_cpu)
        return {
            "ui": {"images": preview},
            "result": (out, M_cpu.squeeze(-1), float(changed_ratio)),
        }

    def _save_preview(self, preview_src, M):
        if folder_paths is None:
            return []
        try:
            prefix = _temp_prefix("cc")
            items = []
            # 结果图：最大边 ≤ 512，双线性缩放
            r = preview_src[0, ..., :3].unsqueeze(0)
            if max(r.shape[1], r.shape[2]) > _GEM_CC_PREVIEW_MAX_SIDE:
                scale = _GEM_CC_PREVIEW_MAX_SIDE / max(r.shape[1], r.shape[2])
                nh = max(1, int(round(r.shape[1] * scale)))
                nw = max(1, int(round(r.shape[2] * scale)))
                r = F.interpolate(
                    r.permute(0, 3, 1, 2), size=(nh, nw),
                    mode="bilinear", align_corners=False,
                ).permute(0, 2, 3, 1)
            items.append(_save_temp_preview(r, prefix + "_result", "Color Calibrator"))
            # 遮罩图：最大边 ≤ 512 + NEAREST（保留硬边）
            m = M[0, ..., 0].unsqueeze(0).unsqueeze(-1)
            if max(m.shape[1], m.shape[2]) > _GEM_CC_PREVIEW_MAX_SIDE:
                scale = _GEM_CC_PREVIEW_MAX_SIDE / max(m.shape[1], m.shape[2])
                nh = max(1, int(round(m.shape[1] * scale)))
                nw = max(1, int(round(m.shape[2] * scale)))
                m = F.interpolate(
                    m.permute(0, 3, 1, 2), size=(nh, nw),
                    mode="nearest",
                ).permute(0, 2, 3, 1)
            items.append(_save_temp_preview(m, prefix + "_mask", "Color Calibrator"))
            return items
        except Exception as e:
            print(f"[GemColorCalibrator] 预览保存失败: {e}")
            return []


NODE_CLASS_MAPPINGS = {
    "maskfeather": MaskFeather,
    "SetImageAlpha": SetImageAlpha,
    "SetMaskedAlpha": SetMaskedAlpha,
    "AdjustColor": AdjustColor,
    "AdjustEffect": AdjustEffect,
    "Adjust": Adjust,
    "SkinBeauty": SkinBeauty,
    "LayerComposite": LayerComposite,
    "BeforeAfterPreview": BeforeAfterPreview,
    # 键用 ItsGemImageCompare：避免覆盖 ComfyUI 内置同名 ImageCompare(Compare Images) 节点
    "ItsGemImageCompare": ImageCompare,
    "SetImageVar": SetImageVar,
    "GetImageVar": GetImageVar,
    # Tile Upscale 复刻 + 几何配准（QwenImage 2.1 + 自训 LoRA 适配）
    "GemTiledUpscale": GemTiledUpscale,
    "GemTiledUpscaleStitch": GemTiledUpscaleStitch,
    # Color Calibrator：复刻自 BlueBox ColorCalibratorBlueBox（独立命名，互不覆盖）
    "GemColorCalibrator": GemColorCalibrator,
    # 前端纯标注节点：后端空实现，保证后端认识该类型（不报 missing node）
    "Label (ItsGemTools)": GemLabel,
    # 旧名称保留映射（菜单中隐藏），保证历史保存的工作流仍能加载。
    "SAM3DistanceFeather": MaskFeather,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "maskfeather": "maskfeather",
    "SetImageAlpha": "Set Image Alpha",
    "SetMaskedAlpha": "Set Masked Alpha",
    "AdjustColor": "Adjust Color",
    "AdjustEffect": "Adjust Effect",
    "Adjust": "Adjust Color & Effect",
    "SkinBeauty": "Skin Beauty",
    "LayerComposite": "Layer Composite",
    "BeforeAfterPreview": "Before/After Preview",
    "ItsGemImageCompare": "Image Compare A/B",
    "SetImageVar": "Set Image Variable",
    "GetImageVar": "Get Image Variable",
    "GemTiledUpscale": "Tiled Upscale (ItsGemTools)",
    "GemTiledUpscaleStitch": "Tiled Upscale Stitch (ItsGemTools)",
    "GemColorCalibrator": "Color Calibrator (ItsGemTools)",
    "Label (ItsGemTools)": "Label (ItsGemTools)",
    # 空字符串让旧节点名在右键菜单中隐藏，仅作为工作流兼容别名。
    "SAM3DistanceFeather": "",
}
