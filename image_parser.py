from __future__ import annotations

import re
import subprocess
import tempfile
from collections import Counter
from pathlib import Path

from PIL import Image, ImageOps

from models import ImageData, ImagePart


class ImageParseError(RuntimeError):
    pass


_PART_RE = re.compile(
    r"^\s*(?P<index>\d+)\s+"
    r"(?P<order>[A-Z0-9]+(?:-[A-Z0-9]+)*)\s+"
    r"(?P<drawing>[A-Z0-9]+(?:-[A-Z0-9]+)+[A-Z]?)\s+"
    r"T\s*(?P<thickness>\d+(?:[.,]\d+)?)\s+"
    r"(?P<base_quantity>\d+)\S*?\s+"
    r"(?P<bevel>[A-Z][A-Z0-9]*)\s*[X×]\s*(?P<split_quantity>\d+)\b",
    re.IGNORECASE,
)


def _prepare_crop(
    image: Image.Image,
    box: tuple[float, float, float, float],
    scale: int = 4,
    cutoff: int = 1,
) -> Image.Image:
    width, height = image.size
    absolute = (
        round(box[0] * width),
        round(box[1] * height),
        round(box[2] * width),
        round(box[3] * height),
    )
    crop = image.crop(absolute).convert("L")
    crop = ImageOps.autocontrast(crop, cutoff=cutoff)
    return crop.resize((crop.width * scale, crop.height * scale), Image.Resampling.LANCZOS)


def _tesseract(image: Image.Image, psm: int) -> str:
    with tempfile.NamedTemporaryFile(suffix=".png") as handle:
        image.save(handle.name)
        result = subprocess.run(
            ["tesseract", handle.name, "stdout", "-l", "eng", "--psm", str(psm)],
            check=False,
            capture_output=True,
            text=True,
        )
    if result.returncode != 0:
        raise ImageParseError(f"Tesseract 执行失败：{result.stderr.strip()}")
    return result.stdout


def _horizontal_rule_y(image: Image.Image) -> int:
    """Return the top edge of the FastCAM title block."""
    gray = image.convert("L")
    width, height = gray.size
    left, right = round(width * 0.02), round(width * 0.98)
    threshold = (right - left) * 0.80
    for y in range(round(height * 0.50), round(height * 0.86)):
        dark = sum(pixel < 140 for pixel in gray.crop((left, y, right, y + 1)).getdata())
        if dark >= threshold:
            return y
    raise ImageParseError("未能定位 FastCAM 标题栏")


def _ocr_variants(image: Image.Image, psms: tuple[int, ...]) -> list[str]:
    variants = [image, image.point(lambda value: 255 if value > 165 else 0)]
    return [_tesseract(variant, psm) for variant in variants for psm in psms]


def _parse_parts(text: str) -> tuple[ImagePart, ...]:
    found: dict[int, Counter[ImagePart]] = {}
    for raw_line in text.splitlines():
        line = re.sub(r"\s+", " ", raw_line.upper().replace("—", "-").replace("–", "-")).strip()
        match = _PART_RE.search(line)
        if not match:
            continue
        part = ImagePart(
            index=int(match.group("index")),
            order_no=match.group("order").upper(),
            drawing_no=match.group("drawing").upper(),
            thickness=float(match.group("thickness").replace(",", ".")),
            base_quantity=int(match.group("base_quantity")),
            bevel=match.group("bevel").upper(),
            split_quantity=int(match.group("split_quantity")),
        )
        found.setdefault(part.index, Counter())[part] += 1
    selected: list[ImagePart] = []
    for index, candidates in sorted(found.items()):
        best_count = max(candidates.values())
        best = [part for part, count in candidates.items() if count == best_count]
        if len(best) != 1:
            readable = [
                f"{part.order_no}/{part.drawing_no}/T{part.thickness:g}/{part.base_quantity}/{part.bevel}x{part.split_quantity}"
                for part in best
            ]
            raise ImageParseError(f"零件序号 {index} 存在无法消除的 OCR 冲突：{readable}")
        selected.append(best[0])
    parts = tuple(selected)
    if not parts:
        raise ImageParseError("未能从图片识别出零件索引")
    expected_indexes = list(range(1, len(parts) + 1))
    if [part.index for part in parts] != expected_indexes:
        raise ImageParseError(f"零件序号不连续：{[part.index for part in parts]}")
    return parts


def _parse_parts_from_variants(texts: list[str]) -> tuple[ImagePart, ...]:
    candidates: list[tuple[ImagePart, ...]] = []
    for text in texts:
        try:
            candidates.append(_parse_parts(text))
        except ImageParseError:
            continue
    if not candidates:
        raise ImageParseError("未能从图片识别出连续的零件索引")
    return max(candidates, key=len)


def _parse_weight(text: str) -> float:
    # 小字号的 kg 在 FastCAM 图片中常被 Tesseract 识别为 ke/kq。
    normalized = text.upper().replace(",", ".")
    normalized = re.sub(r"(?<=\d)\s+(?=\d)", "", normalized)
    # FastCAM 的“.96”在低分辨率图片上会粘连成百分号。
    normalized = re.sub(r"(\d+\.)9%", r"\g<1>96", normalized)
    normalized = re.sub(r"(\d+\.)%", r"\g<1>96", normalized)
    matches = re.findall(r"(?<!\d)(\d{2,7}\.\d{1,3})\s*K\s*[EGOQZ]?(?![A-Z])", normalized)
    if not matches:
        raise ImageParseError("未能识别图片标注钢板重量")
    values = [float(value) for value in matches]
    return max(values)


def _parse_program(text: str) -> str:
    raw_matches = re.findall(r"\bN\s*([0-9ILSB]{2,})\b", text.upper())
    translation = str.maketrans({"I": "1", "L": "1", "S": "5", "B": "8"})
    matches = [value.translate(translation) for value in raw_matches]
    if not matches:
        raise ImageParseError("未能识别程序号")
    # 多个裁剪/版式识别的多数结果优先；相同票数时采用最后一次精细裁剪。
    counts = Counter(matches)
    best_count = max(counts.values())
    selected = next(value for value in reversed(matches) if counts[value] == best_count)
    return f"N{selected}"


def main_name_from_filename(filename: str) -> str:
    stem = Path(filename).stem
    if stem.startswith("完成_"):
        stem = stem[len("完成_") :]
    tokens = stem.strip().split()
    if not tokens:
        raise ImageParseError("图片文件名为空")
    return tokens[0]


def parse_image(path: Path, original_filename: str | None = None) -> ImageData:
    filename = original_filename or path.name
    with Image.open(path) as image:
        _, height = image.size
        title_top = _horizontal_rule_y(image) / height
        # Scale 4 preserves the narrow T/1 strokes in short order codes better
        # than the heavier enlargement used for the surrounding title block.
        part_crop = _prepare_crop(image, (0.005, title_top + 0.008, 0.62, min(0.90, title_top + 0.18)), 4, 0)
        weight_crop = _prepare_crop(image, (0.840, title_top + 0.002, 0.995, min(0.86, title_top + 0.075)), 8, 0)
        program_crop = _prepare_crop(image, (0.875, 0.90, 0.995, 0.995), 8, 0)
        bottom_crop = _prepare_crop(image, (0.00, title_top, 1.00, 1.00), 4)
        part_texts = _ocr_variants(part_crop, (6, 11)) + _ocr_variants(bottom_crop, (6,))
        texts = part_texts + _ocr_variants(weight_crop, (6, 7, 11, 13)) + _ocr_variants(program_crop, (6, 7, 11))
    ocr_text = "\n".join(texts)
    return ImageData(
        original_filename=filename,
        main_name=main_name_from_filename(filename),
        program_no=_parse_program(ocr_text),
        marked_weight_kg=_parse_weight(ocr_text),
        parts=_parse_parts_from_variants(part_texts),
        ocr_text=ocr_text,
    )
