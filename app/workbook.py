"""Read XLSX text locally. Never execute formulas or follow workbook links."""
import io
import posixpath
import re
import xml.etree.ElementTree as ET
import zipfile

NS="{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
REL="{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"


def read_workbook(raw):
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            if len(z.infolist())>2000 or sum(i.file_size for i in z.infolist())>128*1024*1024:
                raise ValueError("工作簿内容过大，请只保留本篇需求表。")
            def xml(name):
                i=z.getinfo(name)
                if i.file_size>12*1024*1024: raise ValueError("工作表或共享文本过大，请拆分。")
                return ET.fromstring(z.read(i))
            shared=[]
            if "xl/sharedStrings.xml" in z.namelist():
                shared=["".join(t.text or "" for t in s.iter(NS+"t")) for s in xml("xl/sharedStrings.xml")]
            book=xml("xl/workbook.xml")
            relationships={r.attrib["Id"]:r.attrib["Target"] for r in xml("xl/_rels/workbook.xml.rels") if r.attrib.get("TargetMode")!="External"}
            sheets=[]
            for s in book.find(NS+"sheets"):
                if len(sheets)>=20: raise ValueError("最多支持20个工作表，请拆分工作簿。")
                target=relationships.get(s.attrib.get(REL+"id"),"")
                name=posixpath.normpath(target.lstrip("/") if target.startswith("/") else "xl/"+target)
                if not name.startswith("xl/"): raise ValueError("工作簿内部路径无效。")
                tree=xml(name); cells=[]
                for c in tree.iter(NS+"c"):
                    addr=c.attrib.get("r","")
                    if not re.fullmatch(r"[A-Z]{1,3}[1-9][0-9]{0,5}",addr):continue
                    value=c.find(NS+"v");text=value.text or "" if value is not None else ""
                    if c.attrib.get("t")=="s":text=shared[int(text)]
                    elif c.attrib.get("t")=="inlineStr":text="".join(t.text or "" for t in c.iter(NS+"t"))
                    if not text.strip():continue
                    cells.append({"cell":addr,"text":text[:12000],"formula":c.find(NS+"f") is not None})
                    if len(cells)>3000: raise ValueError("单个工作表超过3000个非空单元格，请精简需求表。")
                headers={}
                for c in cells:
                    if int(re.search(r"\d+",c["cell"])[0])<=6 and c["text"].strip() in {"内容文案","文案","正文","版块标题","板块标题","标题","顺序","类型","参考","相关图文资料","备注","素材"}:
                        headers[re.match("[A-Z]+",c["cell"])[0]]=c["text"].strip()
                content_cols={k for k,v in headers.items() if v in {"内容文案","文案","正文","版块标题","板块标题","标题"}}
                for c in cells:
                    column=re.match("[A-Z]+",c["cell"])[0]
                    c["role"]="copy" if (column in content_cols if headers else column=="A") else "note"
                    if c["text"].strip() in set(headers.values()):c["role"]="header"
                    if c["text"].strip() in {"头图","尾部","尾图","底部","封面","首屏","结束页","KV","主KV"}:c["role"]="note"
                    if re.search(r"https?://",c["text"]):c["role"]="note"
                    c["id"]=str(len(sheets))+":"+c["cell"]
                    c["column_label"]=headers.get(column,"")
                sheets.append({"name":s.attrib["name"],"hidden":s.attrib.get("state","visible")!="visible","cells":cells})
            if not sheets or not any(s["cells"] for s in sheets):raise ValueError("没有读到工作表文字。")
            return {"sheets":sheets,"notes":["只读取单元格文字及公式缓存，不执行公式、不打开链接；内嵌图片/批注不作为定稿文案。","请只选择本次成稿对应工作表，逐项确认上版文案；括号说明、标题和设计备注可编辑或取消。"]}
    except ValueError:raise
    except Exception:raise ValueError("XLSX无法读取，请确认文件未加密且未损坏；旧版XLS请另存为XLSX。") from None
