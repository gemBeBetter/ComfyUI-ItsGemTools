# -*- coding: utf-8 -*-
"""QwenImage2.1 文字块重绘管线（ItsGemTools 隔离模块）。

在自有节点内直接实例化/调用 Comfy 现成节点类，逐块在内存里流转；**不经过
``/prompt`` 二次提交子工作流**，因此不会和后端串行调度形成死锁。模型 / CLIP / VAE
懒加载为单例，跨块复用。

复用的现成节点（均取自 Comfy 的 NODE_CLASS_MAPPINGS）：
  CLIPLoader / UNETLoader / VAELoader / GemTiledUpscale / TextEncodeQwenImage21 /
  KSampler / VAEDecode / GemTiledUpscaleStitch。
"""
import sys

import torch

# ----------------------------------------------------------------------------
# 锁定模型名与采样/切块口径（对齐流水线，勿改）
# ----------------------------------------------------------------------------
CLIP_NAME = "qwen3vl_8b_bf16.safetensors"
UNET_NAME = "qwen_image_2.1_int8_convrot.safetensors"
VAE_NAME = "qwen_image_2.1_vae_bf16.safetensors"

CAPTION_STRONG = (
    "Recover blurry, compression-artifacted printed text into sharp, clean, correct "
    "characters. Rebuild merged, broken or missing strokes with proper glyph shapes. "
    "Erase the faint blurred residual strokes, ghost outlines and old smudged glyph "
    "traces from the source; redraw every character cleanly from the specified text "
    "only, without carrying over blurred artifacts, doubled edges or leftover traces. "
    "Keep the background, portrait, colors, texture and layout untouched; restore only "
    "the text. Do not invent text that is not present.")

NEGATIVE = ("blurry smeared text, ghost outline, residual strokes, doubled edges, faded "
            "faint traces, compression noise, wrong characters, extra invented text, "
            "altered background, changed colors, distorted portrait")

# 追加到正向词末尾的逐字对齐指令；{inject} 占位会被大脑识出的目标文字替换。
# 留空（节点 widget 清空）则不追加该指令；擦除背景字时可改成纯擦除口径且不带 {inject}。
ALIGN_INSTRUCTION = (
    "The text in this crop must read exactly as follows, character for "
    "character: {inject}. Do not alter, rewrite or invent these characters.")

# GemTiledUpscale 锁定切块参数
TILE_CFG = dict(
    megapixels=3.145728,
    tile_megapixels=3.6,
    overlap=256,
    multiple=32,
    max_growth=64,
    enforce_qwen_tile_limit=True,
)


def _mappings():
    """取 Comfy 核心节点注册表（NODE_CLASS_MAPPINGS）。"""
    nodes_mod = sys.modules.get("nodes")
    return getattr(nodes_mod, "NODE_CLASS_MAPPINGS", {}) if nodes_mod else {}


class QwenRestorePipeline:
    """逐块重绘管线；model/clip/vae 单例懒加载，跨块复用。

    模型来源优先级：节点连线注入的 CLIP/MODEL/VAE > 按节点 widget 模型名内部加载。
    widget 模型名变化时自动清缓存重载。
    """

    def __init__(self):
        self._model = None
        self._clip = None
        self._vae = None
        self._ext_model = None
        self._ext_clip = None
        self._ext_vae = None
        self._names = (CLIP_NAME, UNET_NAME, VAE_NAME)
        self._loaded_names = None

    def configure(self, clip=None, model=None, vae=None,
                  clip_name=CLIP_NAME, unet_name=UNET_NAME, vae_name=VAE_NAME):
        """记录外部注入对象与回退模型名；名字变化时使内部缓存失效。"""
        self._ext_clip = clip
        self._ext_model = model
        self._ext_vae = vae
        names = (clip_name, unet_name, vae_name)
        if self._loaded_names is not None and self._loaded_names != names:
            self._model = self._clip = self._vae = None
            self._loaded_names = None
        self._names = names

    def _ensure_loaded(self):
        m = _mappings()
        # 外部注入对象优先；未注入的槽位走内部按名加载
        self._clip = self._ext_clip if self._ext_clip is not None else self._clip
        self._vae = self._ext_vae if self._ext_vae is not None else self._vae
        self._model = self._ext_model if self._ext_model is not None else self._model
        if self._model is not None and self._clip is not None and self._vae is not None:
            return
        missing = [name for name in ("CLIPLoader", "VAELoader", "UNETLoader")
                   if name not in m]
        if missing:
            raise RuntimeError(f"QwenRestorePipeline 缺少 Comfy 内置节点: {missing}")
        clip_name, unet_name, vae_name = self._names
        if self._clip is None:
            self._clip = m["CLIPLoader"]().load_clip(clip_name, "qwen_image")[0]
        if self._vae is None:
            self._vae = m["VAELoader"]().load_vae(vae_name)[0]
        if self._model is None:
            self._model = m["UNETLoader"]().load_unet(unet_name, "default")[0]
        self._loaded_names = self._names

    @staticmethod
    def _build_prompt(inject, positive=None, instruction=None):
        base = (positive or "").strip() or CAPTION_STRONG
        # None 回落内置对齐模板；显式空串表示关闭后缀。
        tmpl = ALIGN_INSTRUCTION if instruction is None else instruction
        tmpl = (tmpl or "").strip()
        if not tmpl:
            return base
        return base + "\n" + tmpl.replace("{inject}", str(inject))

    def restore_block(self, block_image, inject, seed, steps, cfg, denoise,
                      sampler_name="euler", scheduler="simple",
                      tile_megapixels=3.6, overlap=256,
                      keep_colors=1.0, max_align_shift=64, negative=None,
                      positive=None, instruction=None):
        """重绘单个文字块。

        Args:
            block_image: IMAGE 张量 [1, H, W, 3]，值域 0~1（已放大到目标块尺寸）。
            inject: 注入手端的目标文本。
            seed/steps/cfg/denoise: 采样参数。
            sampler_name/scheduler: 采样器与调度器（默认 euler / simple）。
            tile_megapixels/overlap: 切块单块像素上限与重叠 px。
            keep_colors: 缝合时保留原块色彩强度（0~1）。
            max_align_shift: 缝合配准最大平移 px。
            negative: 负向提示词；为空用内置 NEGATIVE。
        Returns:
            修好块的 IMAGE 张量 [1, h, w, 3]，值域 0~1。
        """
        self._ensure_loaded()
        m = _mappings()
        prompt = self._build_prompt(inject, positive, instruction)
        negative = negative or NEGATIVE
        tile_cfg = dict(TILE_CFG)
        tile_cfg["tile_megapixels"] = tile_megapixels
        tile_cfg["overlap"] = overlap

        # 1) 切块（tile 列表 + stitcher）
        ups = m["GemTiledUpscale"]()
        tiles, stitcher, _before, _w, _h, _rep = ups.split(
            block_image, upscale_model=None, **tile_cfg)

        encoder = m["TextEncodeQwenImage21"]
        sampler = m["KSampler"]()
        decoder = m["VAEDecode"]()

        # 2) 逐 tile 编码 → 采样 → 解码
        redrawn = []
        for tile in tiles:
            out = encoder.execute(
                clip=self._clip,
                prompt=prompt,
                negative_prompt=negative,
                vae=self._vae,
                resolution=0,
                images={"image_1": tile},
            )
            positive, negative_cond, latent = out.result[0], out.result[1], out.result[2]
            # KSampler.sample 返回单元素元组 (out,)；画布上框架自动解包，
            # 节点内直调必须手动取 [0]，否则 VAEDecode 对元组做 ["samples"] 索引报错。
            lat = sampler.sample(
                self._model, seed, steps, cfg, sampler_name, scheduler,
                positive, negative_cond, latent, denoise)[0]
            img = decoder.decode(self._vae, lat)[0]
            redrawn.append(img)

        # 3) 缝合回成图
        stitch = m["GemTiledUpscaleStitch"]()
        pictures, _diag = stitch.stitch(
            redrawn, stitcher, keep_colors=keep_colors, align_mode="translation",
            max_align_shift=max_align_shift, blend_band=0)
        if isinstance(pictures, (list, tuple)):
            pictures = pictures[0]
        return pictures
