from __future__ import annotations

import re
import subprocess
import tempfile
from collections import Counter
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageOps

from models import ImageData, ImagePart


class ImageParseError(RuntimeError):
    pass


_PART_RE = re.compile(
    r"^\s*(?P<index>\d+)\s+"
    r"(?P<order>[A-Z0-9]+(?:-[A-Z0-9]+)*)\s+"
    # 图号既可能是 1001-01-08 / 0501-03.1，也可能是 2310 这类不带连字符的短号。
    # 对不带连字符的形式要求至少 2 个字符且至少包含 1 个数字，避免把单个 OCR 噪声字母当图号。
    r"(?P<drawing>(?:[A-Z0-9.]+(?:-[A-Z0-9.]+)+[A-Z]?|(?=[A-Z0-9.]*\d)[A-Z0-9.]{2,}))\s+"
    # FastCAM 小字 OCR 常把 T60 4J / T60 1J 粘成 T604J / T601J。
    # 以 J 作为基础件数结束标记，因此允许厚度与件数之间无空格，仍不会把 604 当厚度。
    r"T\s*(?P<thickness>\d+(?:[.,]\d+)?)\s*"
    r"(?P<base_quantity>\d+)\s*J\s*"
    r"(?P<bevel>[A-Z][A-Z0-9]*)\s*[X×]\s*(?P<split_quantity>\d+)\b",
    re.IGNORECASE,
)

_NAMED_PART_RE = re.compile(
    r"^\s*(?P<index>\d+)\s+"
    r"(?P<drawing>[\u3400-\u9fff](?:\s*[\u3400-\u9fff])*)\s*"
    r"T\s*(?P<thickness>\d+(?:[.,]\d+)?)\s*"
    r"(?P<base_quantity>\d+)\s*J\s*"
    r"(?P<bevel>[A-Z][A-Z0-9]*)\s*[X×]\s*(?P<split_quantity>\d+)\b",
    re.IGNORECASE,
)

_UNNAMED_PART_RE = re.compile(
    r"^\s*(?P<index>\d+)\s+.*?\s*T\s*(?P<thickness>\d+(?:[.,]\d+)?)\s*"
    r"(?P<base_quantity>\d+)\s*J\s*"
    r"(?P<bevel>[A-Z][A-Z0-9]*)\s*[X×]\s*(?P<split_quantity>\d+)\b",
    re.IGNORECASE,
)

# 真实 #2333 中“吊耳”在一组稳定中文 OCR 中被识别成“帅耳”。
# 只保留经过真实样本确认的精确别名；最终仍必须通过基础表唯一匹配。
_DRAWING_OCR_ALIASES = {"帅耳": "吊耳"}

# Some FastNEST PDF writers emit the last row as one continuous text object,
# e.g. ``6YT71S-4500-07180501-02.1T1002JPx2``.  It is still a normal part
# row: the order ends with its four-digit suffix and the drawing begins with
# the following four-digit drawing group.
_COMPACT_PART_RE = re.compile(
    r"^\s*(?P<index>\d+)\s*"
    r"(?P<order>[A-Z0-9]+(?:-[A-Z0-9]+)*-\d{4})"
    r"(?P<drawing>\d{4}-\d{2}(?:\.\d+)?)"
    r"T\s*(?P<thickness>\d+(?:[.,]\d+)?)"
    r"(?P<base_quantity>\d+)J\s*(?P<bevel>[A-Z][A-Z0-9]*)\s*[X×]\s*(?P<split_quantity>\d+)\b",
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


@lru_cache(maxsize=1)
def _tesseract_languages() -> str:
    languages = "eng"
    available = subprocess.run(
        ["tesseract", "--list-langs"],
        check=False,
        capture_output=True,
        text=True,
    )
    if "chi_sim" in available.stdout.split():
        languages = "eng+chi_sim"
    return languages


def _tesseract(image: Image.Image, psm: int) -> str:
    with tempfile.NamedTemporaryFile(suffix=".png") as handle:
        image.save(handle.name)
        result = subprocess.run(
            ["tesseract", handle.name, "stdout", "-l", _tesseract_languages(), "--psm", str(psm)],
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
    # 有些 FastCAM 图的标题栏顶边会落在图片高度约 49% 处。
    # 从 40% 开始寻找首条横跨 80% 宽度的水平线，避免错过顶边后误抓标题栏底边。
    for y in range(round(height * 0.40), round(height * 0.86)):
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
        match = _PART_RE.search(line) or _COMPACT_PART_RE.search(line)
        if match:
            part = ImagePart(
                index=int(match.group("index")),
                order_no=match.group("order").upper(),
                drawing_no=match.group("drawing").upper(),
                thickness=float(match.group("thickness").replace(",", ".")),
                base_quantity=int(match.group("base_quantity")),
                bevel=match.group("bevel").upper(),
                split_quantity=int(match.group("split_quantity")),
            )
        else:
            named_match = _NAMED_PART_RE.search(line)
            if named_match:
                drawing_no = re.sub(r"\s+", "", named_match.group("drawing")).upper()
                drawing_no = _DRAWING_OCR_ALIASES.get(drawing_no, drawing_no)
                part = ImagePart(
                    index=int(named_match.group("index")),
                    order_no="",
                    drawing_no=drawing_no,
                    thickness=float(named_match.group("thickness").replace(",", ".")),
                    base_quantity=int(named_match.group("base_quantity")),
                    bevel=named_match.group("bevel").upper(),
                    split_quantity=int(named_match.group("split_quantity")),
                )
            else:
                unnamed_match = _UNNAMED_PART_RE.search(line)
                if not unnamed_match:
                    continue
                part = ImagePart(
                    index=int(unnamed_match.group("index")),
                    order_no="",
                    drawing_no="",
                    thickness=float(unnamed_match.group("thickness").replace(",", ".")),
                    base_quantity=int(unnamed_match.group("base_quantity")),
                    bevel=unnamed_match.group("bevel").upper(),
                    split_quantity=int(unnamed_match.group("split_quantity")),
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
    # 拆图的主键是“序号后的图号”。订单号、厚度、件数和坡口会再由模板严格
    # 核验；不能因 OCR 对这些辅助字段偶发抖动而丢弃一张图。
    longest = max(len(candidate) for candidate in candidates)
    complete = [candidate for candidate in candidates if len(candidate) == longest]
    # 同样长度时，优先保留识别出更多图号的候选；空图号只是 OCR 兜底，
    # 不应凭出现次数压过包含明确图号的候选。明确图号仍要经模板唯一匹配验证。
    most_identified = max(sum(bool(part.drawing_no) for part in candidate) for candidate in complete)
    complete = [
        candidate
        for candidate in complete
        if sum(bool(part.drawing_no) for part in candidate) == most_identified
    ]
    signatures = [tuple((part.index, part.drawing_no) for part in candidate) for candidate in complete]
    counts = Counter(signatures)
    best_count = max(counts.values())
    best = [signature for signature, count in counts.items() if count == best_count]
    if len(best) != 1:
        raise ImageParseError("零件图号在多次 OCR 中不一致，无法安全匹配模板")
    selected_signature = best[0]
    selected_candidates = [candidate for candidate in complete if tuple((part.index, part.drawing_no) for part in candidate) == selected_signature]
    # 在图号一致的候选中优先使用出现次数最高的完整行；模板匹配仍是最终校验。
    return Counter(selected_candidates).most_common(1)[0][0]


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


def _combine_ocr_segments(first: list[str], second: list[str]) -> list[str]:
    """Cross-combine FastCAM main-list and continuation OCR candidates."""
    combined: list[str] = []
    seen: set[str] = set()
    for left in first:
        if not left.strip():
            continue
        for right in second:
            if not right.strip():
                continue
            text = f"{left.rstrip()}\n{right.lstrip()}"
            if text in seen:
                continue
            seen.add(text)
            combined.append(text)
    return combined


def _ocr_page(path: Path) -> tuple[list[str], list[str]]:
    with Image.open(path) as image:
        _, height = image.size
        try:
            title_top = _horizontal_rule_y(image) / height
        except ImageParseError:
            # 标题栏不是业务校验字段；缺失时仍尝试从底部清单读取图号和重量。
            title_top = 0.68
        # Scale 4 preserves the narrow T/1 strokes in short order codes better
        # than the heavier enlargement used for the surrounding title block.
        main_end = min(0.90, title_top + 0.18)
        part_crop = _prepare_crop(image, (0.005, title_top + 0.008, 0.62, main_end), 4, 0)
        # FastCAM 在零件较多时会把后续序号放到标题栏下方的续表区域。
        # 主表和续表的最佳 OCR 预处理可能不是同一组，因此交叉组合所有候选，
        # 再沿用严格连续序号与图号一致性校验，避免因版式续表而漏图。
        continuation_crop = _prepare_crop(image, (0.005, main_end, 0.62, 1.00), 4, 0)
        weight_crop = _prepare_crop(image, (0.840, title_top + 0.002, 0.995, min(0.86, title_top + 0.075)), 8, 0)
        bottom_crop = _prepare_crop(image, (0.00, title_top, 1.00, 1.00), 4)
        main_texts = _ocr_variants(part_crop, (6, 11))
        continuation_texts = _ocr_variants(continuation_crop, (6, 11))
        combined_texts = _combine_ocr_segments(main_texts, continuation_texts)
        bottom_texts = _ocr_variants(bottom_crop, (6,))
        part_texts = combined_texts + main_texts + continuation_texts + bottom_texts
        texts = part_texts + _ocr_variants(weight_crop, (6, 7, 11, 13))
    return part_texts, texts


def _merge_page_parts(page_parts: list[tuple[ImagePart, ...]]) -> tuple[ImagePart, ...]:
    candidates: dict[int, Counter[ImagePart]] = {}
    for parts in page_parts:
        for part in parts:
            candidates.setdefault(part.index, Counter())[part] += 1
    if not candidates:
        raise ImageParseError("PDF 各页均未能识别出连续的零件索引")
    selected: list[ImagePart] = []
    for index, options in sorted(candidates.items()):
        highest = max(options.values())
        best = [part for part, count in options.items() if count == highest]
        if len(best) != 1:
            raise ImageParseError(f"跨页零件序号 {index} 存在无法消除的 OCR 冲突：{best}")
        selected.append(best[0])
    expected = list(range(1, len(selected) + 1))
    if [part.index for part in selected] != expected:
        raise ImageParseError(f"跨页零件序号不连续：{[part.index for part in selected]}")
    return tuple(selected)


def parse_images(
    paths: list[Path],
    original_filename: str | None = None,
    native_page_texts: list[str] | None = None,
) -> ImageData:
    if not paths:
        raise ImageParseError("没有可识别的图片页面")
    filename = original_filename or paths[0].name
    all_part_texts: list[str] = []
    all_texts: list[str] = []
    page_parts: list[tuple[ImagePart, ...]] = []
    # PDF selectable text is authoritative for its part list and marked
    # weight.  Rendered-page OCR remains a fallback for scanned PDFs.
    for native_text in native_page_texts or []:
        all_part_texts.append(native_text)
        all_texts.append(native_text)
        try:
            page_parts.append(_parse_parts(native_text))
        except ImageParseError:
            pass

    for path in paths:
        part_texts, texts = _ocr_page(path)
        all_part_texts.extend(part_texts)
        all_texts.extend(texts)
        try:
            page_parts.append(_parse_parts_from_variants(part_texts))
        except ImageParseError:
            # A PDF may include drawing-only pages.  The final merged result
            # remains strict: it must contain one continuous, unambiguous list.
            continue
    ocr_text = "\n".join(all_texts)
    return ImageData(
        original_filename=filename,
        main_name=main_name_from_filename(filename),
        # 程序号只保留为展示信息，不参与是否可拆图的判定。
        program_no=_parse_program(ocr_text) if re.search(r"\bN\s*[0-9ILSB]{2,}\b", ocr_text.upper()) else "",
        marked_weight_kg=_parse_weight(ocr_text),
        parts=_merge_page_parts(page_parts),
        ocr_text=ocr_text,
    )


def parse_image(path: Path, original_filename: str | None = None) -> ImageData:
    return parse_images([path], original_filename)
