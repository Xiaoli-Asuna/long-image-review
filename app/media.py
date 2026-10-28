from __future__ import annotations
import base64
import hashlib
import io
import math
from pathlib import Path
import threading
import unicodedata
import uuid
import xml.etree.ElementTree as ET
import zipfile

from PIL import Image, ImageOps
import pypdfium2 as pdfium
from provider import ImageInput
from workbook import read_workbook

PDF_LOCK = threading.Lock()
Image.MAX_IMAGE_PIXELS = 80_000_000


def normalize(text):
    return "".join(c for c in unicodedata.normalize("NFKC", text) if not c.isspace())


def jpg(image, path, *, width=None):
    image = image.convert("RGBA")
    bg = Image.new("RGBA", image.size, "white")
    bg.alpha_composite(image)
    image = bg.convert("RGB")
    if width and image.width > width:
        image = image.resize((width, max(1, round(image.height * width / image.width))), Image.Resampling.LANCZOS)
    image.save(path, quality=94, subsampling=0)
    return image.size


def ingest(data_root: Path, *, name: str, raw: bytes, kind: str):
    if kind not in {"guide", "logo", "design", "copy"} or not raw or len(raw) > 32 * 1024 * 1024:
        raise ValueError("文件为空、超过 32 MB 或类型不正确。")
    suffix = Path(name).suffix.lower()
    if kind in {"logo", "design"} and suffix not in {".jpg", ".jpeg", ".png"}:
        raise ValueError("LOGO 和设计稿请使用 JPG 或 PNG。")
    allowed = {".jpg", ".jpeg", ".png", ".pdf", ".txt", ".md", ".docx"}
    if kind=="copy": allowed={".txt", ".md", ".docx", ".xlsx"}
    if suffix not in allowed:
        raise ValueError("支持 PDF、JPG、PNG、TXT、MD 和 DOCX。")
    asset_id = uuid.uuid4().hex
    folder = data_root / "assets" / asset_id
    folder.mkdir(parents=True)
    (folder / ("original" + suffix)).write_bytes(raw)
    meta = {"id": asset_id, "name": Path(name).name[:160], "kind": kind,
            "sha256": hashlib.sha256(raw).hexdigest(), "original": "original" + suffix,
            "size": len(raw), "pages": [], "text": ""}
    if suffix==".xlsx":
        meta["workbook"]=read_workbook(raw)
    elif suffix in {".jpg", ".jpeg", ".png"}:
        try:
            with Image.open(io.BytesIO(raw)) as source:
                if source.format not in {"PNG", "JPEG"}:
                    raise ValueError("图片实际格式必须为 PNG/JPEG。")
                if max(source.size) > 60000 or source.width * source.height > 80_000_000:
                    raise ValueError("图片尺寸过大，请分段导出。")
                source.load()
                image = ImageOps.exif_transpose(source)
                w, h = jpg(image, folder / "page-1.jpg")
                meta["pages"] = [{"id": asset_id + ":1", "page": 1, "file": "page-1.jpg", "width": w, "height": h, "text": ""}]
                jpg(image, folder / "thumb.jpg", width=320)
        except (OSError, Image.DecompressionBombError) as exc:
            raise ValueError("图片无法解码或尺寸超出限制。") from None
    elif suffix == ".pdf":
        if not raw.startswith(b"%PDF-"):
            raise ValueError("PDF 内容无效。")
        with PDF_LOCK:
            try:
                with pdfium.PdfDocument(raw) as pdf:
                    if not 1 <= len(pdf) <= 40:
                        raise ValueError("第一版支持单个 PDF 最多 40 页，请先拆分 guide。")
                    texts = []
                    for number in range(len(pdf)):
                        page = pdf[number]
                        width, height = page.get_size()
                        if not 1 <= width <= 20000 or not 1 <= height <= 20000:
                            raise ValueError("PDF 页面尺寸不受支持。")
                        text_page = page.get_textpage()
                        text = text_page.get_text_bounded()[:16000]
                        text_page.close()
                        bitmap = page.render(scale=min(2, 1400 / width, 2200 / height))
                        image = bitmap.to_pil().copy()
                        w, h = jpg(image, folder / f"page-{number+1}.jpg")
                        meta["pages"].append({"id": f"{asset_id}:{number+1}", "page": number+1,
                                              "file": f"page-{number+1}.jpg", "width": w, "height": h, "text": text})
                        texts.append(f"第{number+1}页\n{text}")
                        bitmap.close()
                        page.close()
                    meta["text"] = "\n\n".join(texts)
            except ValueError:
                raise
            except Exception:
                raise ValueError("无法读取 PDF；请确认文件未加密、未损坏。") from None
    else:
        if suffix == ".docx":
            try:
                with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                    item = archive.getinfo("word/document.xml")
                    if item.file_size > 4 * 1024 * 1024:
                        raise ValueError("DOCX 正文过大。")
                    root = ET.fromstring(archive.read(item))
                    meta["text"] = "\n".join("".join(p.itertext()) for p in root.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}p"))
            except Exception:
                raise ValueError("DOCX 无法读取，请改为粘贴文案或导出 TXT。") from None
        else:
            for encoding in ("utf-8-sig", "gb18030"):
                try:
                    meta["text"] = raw.decode(encoding)
                    break
                except UnicodeDecodeError:
                    pass
        if not meta["text"].strip() or len(meta["text"]) > 80000:
            raise ValueError("文字文件为空或超过 8 万字，请拆分后导入。")
    return meta


def crop_logo(data_root, asset, page, box, label, usage, evidence):
    box=image_box(box)
    with Image.open(data_root/"assets"/asset["id"]/page["file"]) as image:
        w,h=image.size
        bounds=(math.floor(box[0]*w),math.floor(box[1]*h),math.ceil(box[2]*w),math.ceil(box[3]*h))
        if bounds[2]-bounds[0]<8 or bounds[3]-bounds[1]<8:raise ValueError("LOGO定位范围过小，未采用此解析结果。")
        buf=io.BytesIO();image.crop(bounds).save(buf,format="PNG")
    result=ingest(data_root,name=label[:80]+".png",raw=buf.getvalue(),kind="logo")
    result["derived_from"]={"asset_id":asset["id"],"page":page["page"],"box":box,"label":asset["name"]+f' · 第{page["page"]}页',"usage":usage,"evidence":evidence[:1000]}
    return result


def page_image(data_root, asset, page):
    return ImageInput(page["id"], "image/jpeg", (data_root / "assets" / asset["id"] / page["file"]).read_bytes())


def prepare_design(data_root, asset, job_id):
    folder = data_root / "jobs" / job_id
    folder.mkdir(parents=True, exist_ok=True)
    with Image.open(data_root / "assets" / asset["id"] / "page-1.jpg") as image:
        w, h = image.size
        scaled_w = min(w, 1500)
        scaled_h = max(1, round(h * scaled_w / w))
        step, height = 1400, 1600
        count = max(1, math.ceil(max(0, scaled_h - height) / step) + 1)
        if count > 24:
            raise ValueError("此长图将超过 24 个识别切片，请按章节分段上传后分别审核。")
        working = image.resize((scaled_w, scaled_h), Image.Resampling.LANCZOS) if scaled_w != w else image.copy()
        overview = working.copy()
        overview.thumbnail((1200, 1800), Image.Resampling.LANCZOS)
        jpg(overview, folder / "overview.jpg")
        tiles = []
        for i in range(count):
            top = i * step
            bottom = min(top + height, scaled_h)
            path = folder / f"tile-{i+1}.jpg"
            jpg(working.crop((0, top, scaled_w, bottom)), path)
            tiles.append({"id": f"tile-{i+1}", "file": path.name, "top": top / scaled_h,
                          "bottom": bottom / scaled_h, "own_bottom": min((top + step) / scaled_h, 1) if i+1 < count else 1,
                          "width": scaled_w, "height": bottom-top})
    return {"width": w, "height": h, "tiles": tiles, "scaled_width": scaled_w,
            "overview": "overview.jpg", "resized": w != scaled_w}


def image_box(value):
    if not isinstance(value, list) or len(value) != 4 or not all(type(x) in (int, float) and math.isfinite(x) and 0 <= x <= 1 for x in value):
        raise ValueError("模型返回了无效的位置坐标。")
    if value[0] >= value[2] or value[1] >= value[3]:
        raise ValueError("模型返回了空的位置范围。")
    return value


def original_box(box, tile):
    box = image_box(box)
    return [box[0], tile["top"] + box[1] * (tile["bottom"]-tile["top"]),
            box[2], tile["top"] + box[3] * (tile["bottom"]-tile["top"])]
