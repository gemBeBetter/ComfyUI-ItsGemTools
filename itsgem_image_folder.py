"""Image Folder (ItsGemTools) — 批量出图：选一个文件夹、勾选图片，逐张跑工作流。

复刻自 ComfyUI-AusBoss `nodes/node_image_folder.py` + `nodes/_image_folder_helpers.py`
(ComfyUI-AusBoss-main 2.20.5, MIT License, Copyright (c) 2026 ausboss)。
算法、widget 数量与顺序、输出插槽名与列表标记均与原版一致，工作流里把节点
`type` 从 `AUSBOSS_NODES_ImageFolder` 换成 `GemImageFolder` 即可原位替换。

与 AusBoss 原版唯一的差异（均为命名隔离，不改变行为）:
  * 映射键 `GemImageFolder` / 显示名 `Image Folder (ItsGemTools)`，不与原版撞键；
  * 服务路由前缀 `/itsgemtools/image_folder/*`（原版 `/ausboss/image_folder/*`）；
  * 给前端读的运行回执 key `itsgemtools_image_folder`（原版 `ausboss_image_folder`）；
  * 报错文案前缀 `Image Folder (ItsGemTools):`，同装两包时提示不混淆。

另外一条安全降级（真实启动下行为不变）: 原版在 import 时直接读
`PromptServer.instance`，没有服务器时（离线测试 / CI）会 AttributeError
把整个包带崩；这里先判空再注册路由。

AusBoss 原版代码零改动、保持只读。
"""

from __future__ import annotations

import functools
import hashlib
import inspect
import json
import os
import re
import threading
from collections import OrderedDict
from io import BytesIO
from pathlib import Path

import numpy as np
import torch
from PIL import Image, ImageOps

try:
    import folder_paths
except ImportError:  # offline tests
    folder_paths = None

# --- constants (identical to the upstream pack) --------------------------------
SOURCES = ("input", "output")
SORTS = ("name", "newest first", "oldest first")
RUNS = ("all in one run", "one per run")
AT_THE_END = ("stop the run", "start over")
# What the editor does with Picture once a run is queued. A run itself loads what Picture says.
AFTER_RUN = ("next", "stay", "random")
IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff")
# A folder this full is listed up to here; the gallery says so.
MAX_PICTURES = 5000
# Core Load Image hands out a 64 x 64 mask of zeros for a picture with no
# see-through part. The pack's mask nodes know that stand-in ("no mask").
NO_MASK_SIZE = (64, 64)

LABEL = "Image Folder (ItsGemTools)"
OUTSIDE = (
    "Image Folder reads only folders inside ComfyUI's input or output folder. "
    "Drop your folder on the node, or press Add, and its pictures are copied in."
)
NO_FOLDER = LABEL + ': there is no folder "{folder}" in ComfyUI\'s {source} folder.'
NO_PICTURES = LABEL + ': no pictures in "{folder}". Add some, or choose another folder.'
NONE_LEFT = LABEL + ": none of the picked pictures are in the folder anymore. Pick again on the node."
NONE_PICKED = LABEL + ": no picture is picked. Tick at least one on the node, or press All."
BAD_PICKS = LABEL + ": the list of picked pictures cannot be read. Pick again on the node."
THE_END = (
    LABEL + ": that was the last picture ({count} of {count}). "
    "Set Picture back to 1 to go through them again."
)

# What the frontend reads to show "7 of 42" on the node after a run.
UI_KEY = "itsgemtools_image_folder"
ROUTE_PREFIX = "/itsgemtools/image_folder"


# --- folder helpers ------------------------------------------------------------
def shown_folder(folder: str, source: str = "input") -> str:
    """The folder the way a message names it: the top folder has no name of its own."""
    text = clean_folder(folder)
    return text or f"the {source} folder"


def clean_folder(folder) -> str:
    """A folder name with forward slashes and no stray separators. Raises when
    it points anywhere but below the root."""
    text = str(folder or "").strip().replace("\\", "/")
    if text.startswith("/") or text.startswith("~") or re.match(r"^[A-Za-z]:", text):
        raise ValueError(OUTSIDE)
    parts = [part for part in text.split("/") if part not in ("", ".")]
    if any(part == ".." for part in parts):
        raise ValueError(OUTSIDE)
    return "/".join(parts)


def looks_full(folder) -> bool:
    """A whole path, typed or pasted, and not a folder name below the root."""
    text = str(folder or "").strip().strip('"').replace("\\", "/")
    return text.startswith("/") or text.startswith("~") or bool(re.match(r"^[A-Za-z]:", text))


def place_inside(folder, roots: dict):
    """A whole path as (source, folder below it), when it lies inside one of
    ComfyUI's own folders. People paste the path their file manager shows.

    This compares text only. A path that is outside those folders is never
    looked up on disk, and one on another machine is refused from its text."""
    text = str(folder or "").strip().strip('"').replace("\\", "/")
    if not looks_full(text) or text.startswith("//"):
        return None
    full = os.path.normpath(os.path.expanduser(text))
    for source, root in roots.items():
        for base in {os.path.normpath(str(root)), os.path.realpath(str(root))}:
            if full == base:
                return source, ""
            if full.startswith(base.rstrip(os.sep) + os.sep):
                return source, full[len(base):].strip(os.sep).replace(os.sep, "/")
    return None


def safe_folder(root, folder, source: str = "input") -> Path:
    """The folder as a real path below `root`. A link that leads out of the
    root is refused like any other way out."""
    relative = clean_folder(folder)
    top = Path(root).resolve()
    target = top.joinpath(*relative.split("/")).resolve() if relative else top
    if target != top and top not in target.parents:
        raise ValueError(OUTSIDE)
    if not target.is_dir():
        raise ValueError(NO_FOLDER.format(folder=relative, source=source))
    return target


def natural_key(name: str):
    """photo_2 before photo_10, upper and lower case together."""
    return [int(part) if part.isdigit() else part.casefold() for part in re.split(r"(\d+)", str(name))]


def list_folders(root, folder, source: str = "input") -> list[str]:
    """The folders directly inside `folder`, for the Browse list."""
    base = safe_folder(root, folder, source)
    names = []
    for entry in base.iterdir():
        if entry.name.startswith(".") or entry.is_symlink() or not entry.is_dir():
            continue
        names.append(entry.name)
    return sorted(names, key=natural_key)


def list_pictures(root, folder, subfolders: bool = False, sort: str = "name", source: str = "input",
                  limit: int = MAX_PICTURES) -> list[dict]:
    """Every picture in the folder: {"name": path below the folder, "path",
    "mtime", "size"}, in the chosen order. Hidden files, links that lead out
    of the root and anything that is not a picture are left out."""
    base = safe_folder(root, folder, source)
    top = Path(root).resolve()
    found: list[dict] = []
    if subfolders:
        walker = os.walk(base, followlinks=False)
    else:
        walker = [(str(base), [], sorted(os.listdir(base)))]
    for directory, folders, files in walker:
        folders[:] = sorted(name for name in folders if not name.startswith("."))
        for name in files:
            if name.startswith(".") or not name.lower().endswith(IMAGE_EXTENSIONS):
                continue
            path = Path(directory) / name
            try:
                real = path.resolve()
                if top not in real.parents or not real.is_file():
                    continue
                stat = real.stat()
            except OSError:
                continue
            found.append({
                "name": path.relative_to(base).as_posix(),
                "path": real,
                "mtime": float(stat.st_mtime),
                "size": int(stat.st_size),
            })
            if len(found) >= limit:
                break
        if len(found) >= limit:
            break
    if sort == "newest first":
        found.sort(key=lambda item: (-item["mtime"], natural_key(item["name"])))
    elif sort == "oldest first":
        found.sort(key=lambda item: (item["mtime"], natural_key(item["name"])))
    else:
        found.sort(key=lambda item: natural_key(item["name"]))
    return found


def parse_picked(text):
    """The picked pictures from the widget: None means every picture."""
    raw = str(text or "").strip()
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except ValueError as error:
        raise ValueError(BAD_PICKS) from error
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(BAD_PICKS)
    names = [item.replace("\\", "/") for item in value]
    return list(dict.fromkeys(names))


def choose(pictures: list[dict], picked):
    """The picked pictures in the folder's order. Picked names are only ever
    compared with what the folder holds, never opened as paths."""
    if picked is None:
        return list(pictures)
    if not picked:
        raise ValueError(NONE_PICKED)
    wanted = set(picked)
    chosen = [item for item in pictures if item["name"] in wanted]
    if not chosen:
        raise ValueError(NONE_LEFT)
    return chosen


def which(count: int, position, run: str, at_the_end: str) -> list[int]:
    """Which of `count` picked pictures this run loads, counted from 0."""
    if run != "one per run":
        return list(range(count))
    place = max(1, int(position))
    if place > count:
        if at_the_end == "start over":
            return [(place - 1) % count]
        raise RuntimeError(THE_END.format(count=count))
    return [place - 1]


def output_name(name: str) -> str:
    """The name a result is saved under: the file name without its ending. A
    picture from a folder inside carries that folder in front, so two files
    with one name never land on each other."""
    stem = name.rsplit(".", 1)[0] if "." in name.rsplit("/", 1)[-1] else name
    return stem.replace("/", "_")


def _has_see_through(frame: Image.Image) -> bool:
    return frame.mode in ("RGBA", "LA", "PA") or (frame.mode == "P" and "transparency" in frame.info)


def load_picture(path):
    """(1 x H x W x 3 image, mask). The mask is white where the picture is
    see-through, as core Load Image has it, and the 64 x 64 stand-in when the
    picture has no see-through part."""
    with Image.open(path) as source:
        frame = ImageOps.exif_transpose(source)
        if frame.mode == "I":
            frame = frame.point(lambda value: value * (1 / 255))
        see_through = _has_see_through(frame)
        rgba = np.asarray(frame.convert("RGBA"), dtype=np.float32) / 255.0
    image = torch.from_numpy(rgba[..., :3].copy()).unsqueeze(0)
    if see_through:
        mask = (1.0 - torch.from_numpy(rgba[..., 3].copy())).unsqueeze(0)
    else:
        mask = torch.zeros((1, *NO_MASK_SIZE), dtype=torch.float32)
    return image, mask


def fingerprint(pictures: list[dict]) -> str:
    """Changes when a picked file is added, removed or rewritten."""
    digest = hashlib.sha1()
    for item in pictures:
        digest.update(f"{item['name']}|{item['mtime']}|{item['size']}\n".encode("utf-8"))
    return digest.hexdigest()


# --- thumbnails for the gallery -------------------------------------------------
_THUMBS: "OrderedDict[tuple, bytes]" = OrderedDict()
_THUMBS_LOCK = threading.Lock()
_THUMBS_BYTES = 24 * 1024 * 1024
_thumbs_size = 0
THUMB_BACK = (27, 35, 38)


def thumbnail(path, edge: int = 160) -> bytes:
    """A small JPEG of the picture. A see-through part shows on the gallery's
    dark tile. Kept in a small cache keyed by the file's state."""
    global _thumbs_size
    edge = max(48, min(512, int(edge)))
    real = Path(path)
    stat = real.stat()
    key = (str(real), int(stat.st_mtime_ns), int(stat.st_size), edge)
    with _THUMBS_LOCK:
        hit = _THUMBS.get(key)
        if hit is not None:
            _THUMBS.move_to_end(key)
            return hit
    with Image.open(real) as source:
        source.draft("RGB", (edge * 2, edge * 2))        # a JPEG decodes small straight away
        frame = ImageOps.exif_transpose(source)
        if frame.mode == "I":
            frame = frame.point(lambda value: value * (1 / 255))
        if _has_see_through(frame):
            rgba = frame.convert("RGBA")
            rgba.thumbnail((edge, edge), Image.Resampling.LANCZOS)
            small = Image.new("RGB", rgba.size, THUMB_BACK)
            small.paste(rgba, (0, 0), rgba)
        else:
            small = frame.convert("RGB")
            small.thumbnail((edge, edge), Image.Resampling.LANCZOS)
    out = BytesIO()
    small.save(out, format="JPEG", quality=82)
    data = out.getvalue()
    with _THUMBS_LOCK:
        if key not in _THUMBS:
            _THUMBS[key] = data
            _thumbs_size += len(data)
            while _thumbs_size > _THUMBS_BYTES and len(_THUMBS) > 1:
                _, old = _THUMBS.popitem(last=False)
                _thumbs_size -= len(old)
    return data


def ignore_extra_inputs(validate):
    """Let a VALIDATE_INPUTS shrug off inputs it does not name."""
    signature = inspect.signature(validate)
    names = set(signature.parameters)

    @functools.wraps(validate)
    def wrapper(*args, **kwargs):
        return validate(*args, **{name: value for name, value in kwargs.items() if name in names})

    wrapper.__signature__ = signature
    return wrapper


# --- the node ------------------------------------------------------------------


def _root(source: str) -> Path:
    """ComfyUI's input or output folder. Nothing else is ever a root."""
    if source not in SOURCES:
        raise ValueError(f"Image Folder: From must be one of {SOURCES}, not '{source}'.")
    if folder_paths is None:
        raise RuntimeError("Image Folder needs ComfyUI's folders.")
    getter = folder_paths.get_output_directory if source == "output" else folder_paths.get_input_directory
    return Path(getter()).resolve()


def _settle(source, folder):
    """(source, folder below it). A whole path that lies inside ComfyUI's input
    or output folder is read as the folder it names; any other whole path is
    refused by clean_folder when the folder is opened."""
    source, folder = str(source), str(folder or "")
    if looks_full(folder):
        inside = place_inside(folder, {name: _root(name) for name in SOURCES})
        if inside:
            return inside
    return source, folder


def _picked(source, folder, subfolders, sort, pictures) -> list[dict]:
    source, folder = _settle(source, folder)
    listed = list_pictures(_root(str(source)), folder, bool(subfolders), str(sort), str(source))
    if not listed:
        raise ValueError(NO_PICTURES.format(folder=shown_folder(folder, str(source))))
    return choose(listed, parse_picked(pictures))


def _register_routes() -> None:
    """The gallery's three questions: which folders, which pictures, and a
    small copy of one. Every path is attacker-controlled; each is resolved
    below ComfyUI's input or output folder, and anything else answers 400."""
    try:
        from aiohttp import web
        from server import PromptServer
    except Exception:
        return
    # 原版在这里直接取 PromptServer.instance：ComfyUI 正常启动时它一定已经建好
    # （main.py 先建 server 再 init_extra_nodes），但离线/无服务器 import 本模块时
    # 会 AttributeError 把整个包带崩。这里安全降级，行为在真实启动下不变。
    if getattr(PromptServer, "instance", None) is None:
        return
    if getattr(PromptServer.instance, "_itsgem_image_folder_routes", False):
        return
    PromptServer.instance._itsgem_image_folder_routes = True
    routes = PromptServer.instance.routes

    def query(request, name: str) -> str:
        return str(request.rel_url.query.get(name, "")).strip()

    def refused(error: Exception):
        return web.json_response({"error": str(error)}, status=400)

    @routes.get(f"{ROUTE_PREFIX}/folders")
    async def folders(request):
        source = query(request, "source") or "input"
        try:
            source, folder = _settle(source, query(request, "folder"))
            shown = clean_folder(folder)
            names = list_folders(_root(source), shown, source)
        except (ValueError, RuntimeError) as error:
            return refused(error)
        return web.json_response({"source": source, "folder": shown, "folders": names})

    @routes.get(f"{ROUTE_PREFIX}/list")
    async def pictures(request):
        source = query(request, "source") or "input"
        try:
            # A pasted whole path inside ComfyUI's folders comes back as the
            # folder it names, so the node can show it that way.
            source, folder = _settle(source, query(request, "folder"))
            shown = clean_folder(folder)
            listed = list_pictures(
                _root(source), shown, query(request, "subfolders") == "1", query(request, "sort") or "name", source
            )
        except (ValueError, RuntimeError) as error:
            return refused(error)
        return web.json_response({
            "source": source,
            "folder": shown,
            "full": len(listed) >= MAX_PICTURES,
            "pictures": [{"name": item["name"], "v": int(item["mtime"])} for item in listed],
        })

    @routes.get(f"{ROUTE_PREFIX}/thumb")
    async def thumb(request):
        source = query(request, "source") or "input"
        name = query(request, "name").replace("\\", "/")
        try:
            root = _root(source)
            base = clean_folder(query(request, "folder"))
            # The name is a path below the folder; it goes through the same door.
            relative = clean_folder(f"{base}/{name}" if base else name)
            path = root.joinpath(*relative.split("/")).resolve()
            if root not in path.parents or not path.is_file() or not path.name.lower().endswith(IMAGE_EXTENSIONS):
                return web.json_response({"error": "no such picture"}, status=404)
            try:
                edge = int(query(request, "size") or 160)
            except ValueError:
                edge = 160
            data = thumbnail(path, edge)
        except (ValueError, RuntimeError) as error:
            return refused(error)
        except Exception:
            return web.json_response({"error": "this picture cannot be shown"}, status=415)
        return web.Response(body=data, content_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400"})


_register_routes()


class GemImageFolder:
    """批量出图 / Image Folder (ItsGemTools), 复刻 AUSBOSS_NODES_ImageFolder."""

    CATEGORY = "ItsGemTools/image"
    DESCRIPTION = (
        "批量出图：选一个文件夹、勾选图片，每张图各跑一遍工作流。"
        "All in one run 一次 Run 跑完所有勾选的图片；One per run 每次 Run 只加载下一张，"
        "某张失败只跳过它自己。输出每张图的 mask、可直接接 Save Image 文件名的文件名、"
        "序号与总数。文件夹范围为 ComfyUI 的 input / output 目录。\n"
        "Pick a folder, tick the pictures you want, and run the workflow on "
        "each of them. All in one run does every picked picture with one "
        "press of Run. One per run loads one picture each run and steps to "
        "the next by itself, so one that fails only skips itself. It gives "
        "each picture's mask, its file name for saving the result under the "
        "same name, its number and how many there are."
    )
    SEARCH_ALIASES = [
        "load folder", "load from folder", "load images", "batch load", "directory",
        "image list", "gallery", "dataset", "load image batch", "batch load images",
        "批量出图", "批量加载图片",
    ]

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "source": (
                    list(SOURCES),
                    {
                        "default": SOURCES[0],
                        "tooltip": "Which of ComfyUI's folders to look in: input (pictures you put there) or output (pictures you made).",
                    },
                ),
                "folder": (
                    "STRING",
                    {
                        "default": "",
                        "multiline": False,
                        "tooltip": "The folder inside it. Empty is the input or output folder itself. Browse lists the folders there.",
                    },
                ),
                "subfolders": (
                    "BOOLEAN",
                    {"default": False, "tooltip": "Also take the pictures in the folders inside this one."},
                ),
                "sort": (
                    list(SORTS),
                    {"default": SORTS[0], "tooltip": "The order of the pictures: by name, or by when the file was last changed."},
                ),
                "pictures": (
                    "STRING",
                    {
                        "default": "",
                        "multiline": False,
                        "tooltip": "The picked pictures. Tick them on the node. Empty means every picture in the folder.",
                    },
                ),
                "run": (
                    list(RUNS),
                    {
                        "default": RUNS[0],
                        "tooltip": (
                            "All in one run: one press of Run does every picked picture. One per run: "
                            "each run loads one picture and steps on, so a picture that fails only skips itself."
                        ),
                    },
                ),
                "position": (
                    "INT",
                    {
                        "default": 1,
                        "min": 1,
                        "max": 1000000,
                        "step": 1,
                        "tooltip": "With one per run: which of the picked pictures the next run loads, counted from 1.",
                    },
                ),
                "after_run": (
                    list(AFTER_RUN),
                    {
                        "default": AFTER_RUN[0],
                        "tooltip": (
                            "With one per run: next moves Picture on by one each time a run is queued, so "
                            "queuing several runs walks the folder. Stay keeps loading the same picture. "
                            "Random jumps to any of the picked ones."
                        ),
                    },
                ),
                "at_the_end": (
                    list(AT_THE_END),
                    {
                        "default": AT_THE_END[0],
                        "tooltip": "With one per run, after the last picture: stop the run with a message, or go back to the first and keep going.",
                    },
                ),
            },
        }

    RETURN_TYPES = ("IMAGE", "MASK", "STRING", "INT", "INT")
    RETURN_NAMES = ("image", "mask", "filename", "index", "count")
    OUTPUT_IS_LIST = (True, True, True, True, False)
    OUTPUT_TOOLTIPS = (
        "Each picked picture, one at a time. What comes after runs once for each.",
        "White where the picture is see-through. A picture with no see-through part gives the same empty mask core Load Image does.",
        "The picture's file name without its ending. Wire it to Save Image's filename and the result keeps the name.",
        "The picture's number among the picked ones, counted from 1.",
        "How many pictures are picked.",
    )
    FUNCTION = "load"

    @classmethod
    def IS_CHANGED(cls, source, folder, subfolders, sort, pictures, run, position, after_run, at_the_end):
        try:
            chosen = _picked(source, folder, subfolders, sort, pictures)
        except Exception as error:  # noqa: BLE001 - a changed error is a change too
            return f"error: {error}"
        return f"{fingerprint(chosen)}|{run}|{position if run == 'one per run' else ''}|{at_the_end}"

    @classmethod
    @ignore_extra_inputs
    def VALIDATE_INPUTS(cls, source, folder):
        if source is None or folder is None:      # wired in: only known when the run gets there
            return True
        try:
            source, folder = _settle(source, folder)
            if not list_pictures(_root(str(source)), folder, True, "name", str(source), limit=1):
                return NO_PICTURES.format(folder=shown_folder(folder, str(source)))
        except (ValueError, RuntimeError) as error:
            return str(error)
        return True

    def load(self, source, folder, subfolders, sort, pictures, run, position, after_run, at_the_end):
        # after_run is the editor's: it moves Picture on after a run is queued. A run loads what Picture says.
        chosen = _picked(source, folder, subfolders, sort, pictures)
        count = len(chosen)
        images, masks, names, numbers = [], [], [], []
        for place in which(count, position, str(run), str(at_the_end)):
            item = chosen[place]
            image, mask = load_picture(item["path"])
            images.append(image)
            masks.append(mask)
            names.append(output_name(item["name"]))
            numbers.append(place + 1)
        note = [{"count": count, "loaded": numbers[:64], "names": [chosen[n - 1]["name"] for n in numbers[:64]]}]
        return {"ui": {UI_KEY: note}, "result": (images, masks, names, numbers, count)}


NODE_CLASS_MAPPINGS = {"GemImageFolder": GemImageFolder}
NODE_DISPLAY_NAME_MAPPINGS = {"GemImageFolder": "Image Folder (ItsGemTools)"}
