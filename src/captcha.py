# !/usr/bin/env python
# -*-coding:utf-8 -*-

"""
# File       : jy_click.py
# Time       ：2023/11/13 16:49
# Author     ：yujia
# version    ：python 3.6
# Description：
"""
import os
import warnings
from functools import lru_cache
from pathlib import Path
import numpy as np
from typing import List, Dict, Any, Optional
from PIL import Image, ImageDraw, ImageFont

from src.utils import ver_onnx
from src.utils import yolo_onnx
from src.utils import matchingMode


FONT_EXTENSIONS = {'.ttf', '.ttc', '.otf'}
FONT_NAME_KEYWORDS = [
    'noto sans cjk',
    'notosanscjk',
    'noto serif cjk',
    'notoserifcjk',
    'source han sans',
    'sourcehansans',
    'source han serif',
    'sourcehanserif',
    'pingfang',
    'hiragino sans gb',
    'microsoft yahei',
    'msyh',
    'simhei',
    'simsun',
    'deng',
    'songti',
    'stheiti',
    'heiti',
    'kaiti',
    'mingliu',
    'pmingliu',
    'wqy',
    'wenquanyi',
    'droidsansfallback',
    'ar pl',
    'uming',
    'ukai',
    'arial unicode',
    'sarasa',
    'lxgw',
]


def _clean_font_path(value: str) -> Optional[Path]:
    value = value.strip().strip('"').strip("'")
    if not value:
        return None
    return Path(value).expanduser()


def _iter_env_font_paths() -> List[Path]:
    paths = []
    for key in ('GLM_CODING_CJK_FONT', 'CAPTCHA_CJK_FONT'):
        value = os.getenv(key)
        if value:
            path = _clean_font_path(value)
            if path:
                paths.append(path)
    return paths


def _iter_font_dirs() -> List[Path]:
    dirs = []
    for key in ('GLM_CODING_FONT_DIR', 'CAPTCHA_FONT_DIR'):
        value = os.getenv(key)
        if value:
            path = _clean_font_path(value)
            if path:
                dirs.append(path)

    dirs.extend([
        # Linux and containers
        Path('/usr/share/fonts'),
        Path('/usr/local/share/fonts'),
        Path.home() / '.fonts',
        Path.home() / '.local/share/fonts',
        # macOS
        Path('/System/Library/Fonts'),
        Path('/System/Library/Fonts/Supplemental'),
        Path('/Library/Fonts'),
        Path.home() / 'Library/Fonts',
        # Windows
        Path(os.getenv('WINDIR', 'C:/Windows')) / 'Fonts',
        Path('C:/Windows/Fonts'),
    ])
    return dirs


def _font_priority(path: Path) -> int:
    name = path.name.lower().replace('-', ' ').replace('_', ' ')
    for index, keyword in enumerate(FONT_NAME_KEYWORDS):
        if keyword in name:
            return index
    return len(FONT_NAME_KEYWORDS)


def _iter_explicit_font_candidates() -> List[Path]:
    return [
        # Linux
        Path('/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc'),
        Path('/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.otf'),
        Path('/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc'),
        Path('/usr/share/fonts/truetype/noto/NotoSansCJKsc-Regular.otf'),
        Path('/usr/share/fonts/opentype/source-han-sans/SourceHanSansCN-Regular.otf'),
        Path('/usr/share/fonts/truetype/wqy/wqy-microhei.ttc'),
        # macOS
        Path('/System/Library/Fonts/PingFang.ttc'),
        Path('/System/Library/Fonts/STHeiti Light.ttc'),
        Path('/System/Library/Fonts/STHeiti Medium.ttc'),
        Path('/System/Library/Fonts/Supplemental/Songti.ttc'),
        Path('/System/Library/Fonts/Supplemental/Arial Unicode.ttf'),
        # Windows
        Path('C:/Windows/Fonts/msyh.ttc'),
        Path('C:/Windows/Fonts/msyh.ttf'),
        Path('C:/Windows/Fonts/simhei.ttf'),
        Path('C:/Windows/Fonts/simsun.ttc'),
        Path('C:/Windows/Fonts/Deng.ttf'),
        Path('C:/Windows/Fonts/NotoSansCJK-Regular.ttc'),
    ]


def _iter_scanned_font_candidates() -> List[Path]:
    seen = set()
    scanned = []
    for font_dir in _iter_font_dirs():
        if not font_dir.is_dir():
            continue
        try:
            for path in font_dir.rglob('*'):
                if path.suffix.lower() not in FONT_EXTENSIONS:
                    continue
                if _font_priority(path) >= len(FONT_NAME_KEYWORDS):
                    continue
                if path not in seen:
                    seen.add(path)
                    scanned.append(path)
        except OSError:
            continue

    return sorted(scanned, key=_font_priority)


@lru_cache(maxsize=1)
def _get_cjk_font_path() -> Optional[str]:
    """Find a CJK font on Linux, macOS, or Windows for rendered text matching."""
    for path in _iter_env_font_paths():
        if path.is_file():
            return str(path)

    for candidate in _iter_explicit_font_candidates():
        if candidate.is_file():
            return str(candidate)

    for candidate in _iter_scanned_font_candidates():
        if candidate.is_file():
            return str(candidate)

    return None


def _render_char(char: str, size: int = 48) -> np.ndarray:
    """将单个字符渲染为白底黑字图片，返回 BGR numpy 数组"""
    img = Image.new('RGB', (size, size), (255, 255, 255))
    draw = ImageDraw.Draw(img)
    font_path = _get_cjk_font_path()
    try:
        if not font_path:
            raise OSError("No CJK font found")
        font = ImageFont.truetype(font_path, size - 8)
    except OSError:
        warnings.warn(
            "No usable CJK font found; falling back to Pillow default font. "
            "Set GLM_CODING_CJK_FONT or CAPTCHA_CJK_FONT to a Chinese-capable .ttf/.ttc/.otf file.",
            RuntimeWarning,
            stacklevel=2,
        )
        font = ImageFont.load_default()
    bbox = draw.textbbox((0, 0), char, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text(((size - tw) / 2, (size - th) / 2 - bbox[1]), char, fill=(0, 0, 0), font=font)
    return np.array(img)[:, :, ::-1]  # RGB -> BGR


class TextSelectCaptcha(object):
    def __init__(self, per_path: str = 'pre_model_v7.onnx', yolo_path: str = 'best_v3.onnx') -> None:
        save_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'model'))
        per_path = os.path.join(save_path, per_path)
        yolo_path = os.path.join(save_path, yolo_path)
        self.yolo = yolo_onnx.YOLO(yolo_path)
        self.pre = ver_onnx.PreONNX(per_path)

    def detection(self, image_path: str) -> List[List[float]]:
        img = matchingMode.open_image(image_path)
        data = self.yolo.inference(img)
        return data

    def run(self, image_path: str, click_text: Optional[str] = None) -> List[List[float]]:
        img = matchingMode.open_image(image_path)
        data = self.yolo.inference(img)
        print(data)
        target_boxes = [item[:4] for item in data if len(item) >= 6 and item[5] == 2]
        char_boxes = [item[:4] for item in data if len(item) >= 6 and item[5] == 0]
        char_boxes.sort(key=lambda box: box[0])
        print(f"检测到 {len(target_boxes)} 个目标，{len(char_boxes)} 个字符")
        if not char_boxes:
            return []
        chars = [img[int(box[1]):int(box[3]), int(box[0]):int(box[2])] for box in char_boxes]
        if target_boxes:
            img_targets = [img[int(box[1]):int(box[3]), int(box[0]):int(box[2])] for box in target_boxes]
            slys = self.pre.reason_all_batch(chars, img_targets)
        elif click_text:
            chars_to_click = [c for c in click_text.replace(' ', '') if c.strip()]
            img_targets = [_render_char(c) for c in chars_to_click]
            slys = self.pre.reason_all_batch(chars, img_targets)
            print("渲染匹配分数:")
            for i, c in enumerate(chars_to_click):
                col_scores = [slys[j][i] for j in range(len(slys))]
                scores = sorted(enumerate(col_scores), key=lambda x: -x[1])
                print(f"  '{c}' -> 最佳: char{scores[0][0]}({scores[0][1]:.3f}), 次佳: char{scores[1][0]}({scores[1][1]:.3f})" if len(scores) >= 2 else f"  '{c}' -> char{scores[0][0]}({scores[0][1]:.3f})")
        else:
            return char_boxes
        sorted_result = matchingMode.find_overall_index_fast(slys)
        # 按 target 顺序排列（target_index 越小 → 越先点击）
        sorted_result.sort(key=lambda x: x[1])
        if click_text:
            chars_to_click = [c for c in click_text.replace(' ', '') if c.strip()]
            print(chars_to_click)
            sorted_result = sorted_result[:len(chars_to_click)]
        result = [char_boxes[i] for i, _ in sorted_result]
        return result

    def run_dict(self, image_path: str, click_text: Optional[str] = None) -> Dict[str, Any]:
        img = matchingMode.open_image(image_path)
        h, w, _ = img.shape
        result = self.run(image_path, click_text=click_text)
        return {
            "imgW": w,
            "imgH": h,
            "point": [{"x_rel": (x1 + x2) / 2, "y_rel": (y1 + y2) / 2} for x1, y1, x2, y2 in result],
            "corp": [{"x1": x1, "y1": y1, "x2": x2, "y2": y2} for x1, y1, x2, y2 in result],
        }


if __name__ == '__main__':
    from src.drawing import drow_img
    cap = TextSelectCaptcha()
    image_path = r"../docs/res.jpg"
    result = cap.run(image_path)
    print(result)
    drow_img(image_path, result)
    print(cap.run_dict(image_path))
