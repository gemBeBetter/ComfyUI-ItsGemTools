import os
import random
import string
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
    def IS_CHANGED(cls, key):
        # Get 的输入只有 key 这个 widget（每次运行不变），若不禁止缓存，
        # ComfyUI 会跳过执行、永远返回上一次的旧输出（变量更新后取不到新图）。
        # 返回递增计数器：每次执行都重新读取全局变量表。
        return next(_GET_CACHE_BUSTER)

    def get_var(self, key):
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
    "Label (ItsGemTools)": "Label (ItsGemTools)",
    # 空字符串让旧节点名在右键菜单中隐藏，仅作为工作流兼容别名。
    "SAM3DistanceFeather": "",
}
