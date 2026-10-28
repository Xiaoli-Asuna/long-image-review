from __future__ import annotations
import difflib
import json
import time
from pathlib import Path

from provider import ImageInput, ProviderError, VisionJsonProvider
from media import normalize, page_image, prepare_design, image_box, original_box, crop_logo


def category(text):
    if any(x in text.lower() for x in ("logo", "标志", "标识")): return "logo"
    if any(x in text for x in ("颜色", "色值", "配色", "色号")): return "color"
    if any(x in text for x in ("字体", "字号", "字重")): return "typography"
    return "layout"


class Engine:
    def __init__(self, app, job, config):
        self.app, self.job, self.config = app, job, config
        self.project = job["snapshot"]
        self.provider = VisionJsonProvider(config)
        self.calls = []

    def progress(self, message):
        self.app.job_update(self.job["id"], progress=message)

    def ask(self, task, payload, images, schema):
        call_id = f'{self.job["id"]}-{len(self.calls)+1}'
        row = {"id": call_id, "task": task, "started": time.time(), "status": "requesting",
               "provider": self.config.provider, "model": self.config.model, "usage": None,
               "image_count":len(images),"image_bytes":sum(len(x.data) for x in images),"stream_mode":self.config.stream_mode}
        self.calls.append(row)
        self.app.job_update(self.job["id"], calls=self.calls)
        try:
            result = self.provider.generate_json(task=task, payload=payload, images=images, schema=schema, client_request_id=call_id)
            row.update(status="received", usage=result.usage, response_id=result.response_id, finished=time.time())
            self.app.job_update(self.job["id"], calls=self.calls)
            return result.value
        except ProviderError as exc:
            row.update(status=exc.outcome, usage=exc.usage, finished=time.time(),http_status=exc.http_status,provider_code=exc.provider_code,trace_id=exc.trace_id,retry_after_seconds=exc.retry_after_seconds)
            self.app.job_update(self.job["id"], calls=self.calls)
            text = "接口请求失败，未自动重试。"
            if exc.http_status==402:text="服务返回 HTTP 402：账户可用余额不足或计费受限，请检查当前Key所属账号的余额与额度；处理后手动重新解析或送审。未自动重试。"
            elif exc.http_status==401:text="服务返回 HTTP 401：API Key无效或已失效，请检查当前服务商的密钥。未自动重试。"
            elif exc.http_status==403:text="服务返回 HTTP 403：访问权限不足，请核对模型权限及服务商认证要求。未自动重试。"
            elif exc.http_status==429:text="服务返回 HTTP 429：请求或Token额度达到限流，请查看服务商配额后再手动尝试。未自动重试。"
            elif exc.http_status in {503,504}:
                step={"extract_guide":"Guide解析","ocr_design":"设计图文字识别","review_visual":"视觉规范检查"}.get(task,"模型调用")
                text=f"服务返回 HTTP {exc.http_status}：模型服务或上游网关暂时不可用，常见原因是服务负载较高。失败步骤：{step}。请稍后手动尝试，持续失败时把追踪编号交给服务商排查；未自动重试。"
                if exc.retry_after_seconds is not None:text+=f" 服务建议至少等待{exc.retry_after_seconds}秒。"
                if not self.config.stream_mode:text+=" 管理员可在API高级选项中启用流式传输后再尝试，不能保证消除服务过载。"
            elif exc.outcome=="stream_error":text="模型在流式响应中报告错误，未生成完整结果；受理与计费状态请结合调用记录核实，未自动重试。"
            elif exc.outcome == "unknown": text = "请求超时或连接中断，受理与计费状态未知，未自动重试。"
            elif exc.outcome == "incomplete": text = "模型输出被截断或拒绝；请提高输出上限或减少单次内容，再手动重试。"
            elif exc.outcome == "invalid_response": text = "接口没有返回有效 JSON；请检查模型是否支持图像和 JSON 输出。"
            elif "HTTP " in str(exc): text += str(exc).split(";", 1)[0].replace("Model endpoint returned", "服务返回")
            raise ValueError(text + " 已保存本次调用记录。") from None

    def extract_guide(self):
        state = self.app.store.read()
        sources = []
        for aid in self.job.get("guide_asset_ids",self.project["guide_ids"]):
            asset = state["assets"][aid]
            if asset["pages"]:
                for p in asset["pages"]:
                    sources.append((asset, p, p["id"], p["text"]))
            else:
                sources.append((asset, None, asset["id"] + ":text", asset["text"]))
        if not sources:
            raise ValueError("请先上传视觉 guide，或直接逐行填写审核规范。")
        rules, logo_assets, notes = [], [], []
        for i in range(0, len(sources), 4):
            batch = sources[i:i+4]
            self.progress(f"解析Guide规范与LOGO：第 {i+1}–{min(i+4,len(sources))} / {len(sources)} 页或文件")
            result = self.ask("extract_guide", {
                "instruction": "请用中文解析视觉guide：1.提取可执行的品牌核对规则，不创造规范，每条有source_id和来源短引文；2.定位页面里的各个LOGO，以对应页面的归一化box给出紧贴图形的矩形，避免包含整段说明。usage区分approved标准允许版本、forbidden错误/禁止示例、uncertain无法确认；不得把禁用示例当标准。同一LOGO不同配色/组合分别返回；没有可见LOGO返回空列表，纯文字来源不能虚构裁切。字体具体文件身份/授权不能从设计位图认证。只解析资料，不评价设计。",
                "sources": [{"source_id": sid, "file": a["name"], "page": p["page"] if p else None, "text": txt} for a,p,sid,txt in batch]
            }, [page_image(self.app.data, a, p) for a,p,_,_ in batch if p], {
                "rules": [{"category": "logo|color|typography|layout|content", "requirement": "完整明确的中文规则", "source_id": "来源编号", "quote": "来源中的短引文"}],
                "logos": [{"source_id":"来源页面编号","box":[0,0,1,1],"label":"LOGO版本名","usage":"approved|forbidden|uncertain","evidence":"页面中判定标准/禁用的依据；不清楚须写待确认"}],
                "notes": ["识别不清或存在冲突的内容"]})
            if not isinstance(result.get("rules"), list) or len(result["rules"]) > 100:
                raise ValueError("规范提取结果不符合要求，本次未启用任何规范。")
            known = {s[2]:s for s in batch}
            if not isinstance(result.get("logos"),list) or len(result["logos"])>20:raise ValueError("LOGO解析缺少有效列表，未自动采用本批结果。")
            if not isinstance(result.get("notes"),list) or any(not isinstance(n,str) for n in result["notes"]):raise ValueError("解析说明格式无效。")
            notes.extend(n[:1000] for n in result["notes"][:20])
            for logo in result["logos"]:
                sid=logo.get("source_id");usage=logo.get("usage")
                if sid not in known or known[sid][1] is None or usage not in {"approved","forbidden","uncertain"} or not isinstance(logo.get("label"),str) or not logo["label"].strip() or not isinstance(logo.get("evidence"),str):raise ValueError("LOGO候选缺少有效页面来源或分类，未自动采用。")
                a,p,_,_=known[sid]
                crop=crop_logo(self.app.data,a,p,logo.get("box"),logo["label"],usage,logo["evidence"])
                logo_assets.append(crop)
                if len(logo_assets)>80:raise ValueError("LOGO候选超过80个，请拆分Guide。")
            for item in result["rules"]:
                sid = item.get("source_id")
                requirement = item.get("requirement")
                quote = item.get("quote", "")
                if sid not in known or not isinstance(requirement,str) or not requirement.strip() or len(requirement)>2000 or not isinstance(quote,str) or not quote.strip():
                    raise ValueError("模型返回的规范缺少有效内容或来源，未自动采用。")
                a,p,_,source_text = known[sid]
                if not p and normalize(quote) not in normalize(source_text):
                    raise ValueError("规范引文不在文字 guide 中，未自动采用，请改为手动整理规范。")
                if normalize(requirement) in {normalize(x["requirement"]) for x in rules}: continue
                rules.append({"id": f"R{len(rules)+1:03}", "category": item.get("category") if item.get("category") in {"logo","color","typography","layout","content"} else category(requirement),
                              "requirement": requirement.strip(), "source_asset": a["id"], "source_page": p["page"] if p else None,
                              "source_label": a["name"]+(f' · 第{p["page"]}页' if p else ""), "source_quote": quote,
                              "source_text_verified": bool(source_text) and normalize(quote) in normalize(source_text)})
        if not rules:notes.append("未提取到可执行规范，请在项目中补充规则后再确认。")
        if not logo_assets:notes.append("未定位到可用LOGO图形；可检查Guide原页或手动补充标准图。")
        def update(state):
            for a in logo_assets:state["assets"][a["id"]]=a
            result={"rules":rules,"logo_ids":[a["id"] for a in logo_assets],"notes":notes}
            result["applied"]=self.app.apply_guide_result(state,self.job,result)
            state["jobs"][self.job["id"]].update(result=result,status="completed",progress=f"解析完成：{len(rules)}条规范、{len(logo_assets)}个LOGO候选，请检查后确认。",finished=time.time())
        self.app.store.edit(update)

    def review(self):
        state = self.app.store.read()
        asset = state["assets"][self.job["asset_id"]]
        meta = prepare_design(self.app.data, asset, self.job["id"])
        folder = self.app.data / "jobs" / self.job["id"]
        self.app.job_update(self.job["id"], image_meta=meta)
        tiles = meta["tiles"]
        refs=[]
        for aid in self.project["logo_ids"]:
            a=state["assets"][aid]
            refs.extend((a,p) for p in a["pages"])
        if not refs:
            used=set()
            for rule in self.project["rules"]:
                if rule["category"]!="logo" or not rule.get("source_asset") or not rule.get("source_page"): continue
                a=state["assets"][rule["source_asset"]]
                p=next((p for p in a["pages"] if p["page"]==rule["source_page"]),None)
                if p and p["id"] not in used: refs.append((a,p)); used.add(p["id"])
        if 1+len(tiles)+len(refs)>32:
            raise ValueError("视觉检查的图片总数超过32张，请缩短设计稿，或单独上传本篇标准LOGO减少guide参考页。本次尚未调用模型。")
        images=[ImageInput("overview","image/jpeg",(folder/"overview.jpg").read_bytes())]
        images += [ImageInput(t["id"],"image/jpeg",(folder/t["file"]).read_bytes()) for t in tiles]
        images += [page_image(self.app.data,a,p) for a,p in refs]
        if sum(len(x.data) for x in images)>20*1024*1024:
            raise ValueError("审核图片合计超过20MB，请缩小标准参考图或将长图分段。本次尚未调用模型。")
        lines, coverage_notes = [], []
        for i in range(0,len(tiles),2):
            batch = tiles[i:i+2]
            self.progress(f"识别设计文字：第 {i+1}–{min(i+2,len(tiles))} / {len(tiles)} 个切片")
            value = self.ask("ocr_design", {"instruction": "独立识别图片内实际可见的全部文字，按正常阅读顺序返回。不要改错字，不要补全文字，不要猜测。box为该切片中的归一化坐标；coverage不完整时说明原因。", "image_ids":[x["id"] for x in batch]},
                [ImageInput(t["id"],"image/jpeg",(folder/t["file"]).read_bytes()) for t in batch],
                {"images":[{"image_id":"tile编号", "coverage":"complete|partial", "note":"说明", "lines":[{"text":"实际文字", "box":[0,0,1,1], "confidence":0.9}]}]})
            if not isinstance(value.get("images"),list) or {x.get("image_id") for x in value["images"]}!={t["id"] for t in batch} or len(value["images"])!=len(batch):
                raise ValueError("模型未完整返回本批切片的识别结果，停止审核，未自动重试。")
            for info in value["images"]:
                tile = next(t for t in batch if t["id"]==info["image_id"])
                if info.get("coverage") not in {"complete","partial"} or not isinstance(info.get("lines"),list):
                    raise ValueError("文字识别结果缺少覆盖状态或文字行。")
                if info["coverage"] != "complete": coverage_notes.append(f'{tile["id"]}：{str(info.get("note","部分文字识别不清"))[:500]}')
                for line in info["lines"]:
                    if not isinstance(line.get("text"),str) or len(line["text"])>4000: raise ValueError("识别文字格式无效。")
                    box = original_box(line.get("box"),tile)
                    center=(box[1]+box[3])/2
                    if center >= tile["own_bottom"] and tile != tiles[-1]: continue
                    confidence = line.get("confidence")
                    if type(confidence) not in (int,float) or not 0<=confidence<=1: confidence=None
                    lines.append({"text":line["text"],"box":box,"confidence":confidence,"tile_id":tile["id"]})
        if len(lines)>4000 or sum(len(x["text"]) for x in lines)>60000:
            raise ValueError("识别文字量过大，请将长图分段后分别审核。")
        lines.sort(key=lambda x:(round(x["box"][1],4), x["box"][0]))
        dedup=[]
        for line in lines:
            if any(normalize(line["text"])==normalize(old["text"]) and abs(line["box"][1]-old["box"][1])<0.003 and abs(line["box"][0]-old["box"][0])<0.02 for old in dedup[-5:]): continue
            dedup.append(line)
        lines=dedup
        findings=self.copy_diff(lines)
        self.progress("对照 guide 与标准 LOGO 检查视觉规范…")
        visual=self.ask("review_visual",{
            "instruction":"用中文依据已确认规则检查设计。overview是缩小总览，tile为全部局部切片，reference_ids是标准参考。不要把参考图当成设计。仅报告有规则依据的疑似问题；不评判未规定的个人审美，不认证字体文件/授权。精确问题位置使用tile中的box；只在整图范围判断漏放，无法确定时标待确认。每条问题必须引用给定rule_id和该规则requirement中的原句或短引文。",
            "rules": self.project["rules"],"design_ids":[t["id"] for t in tiles],"reference_ids":[p["id"] for a,p in refs],
            "reference_details":[{"image_id":p["id"],"name":a["name"],"source":a.get("derived_from",{"label":"标准参考原图/Guide原页；可能含禁用示例，请结合规则区分"})} for a,p in refs],
        },images,{
            "reviewed_image_ids":["所有已检查的tile编号"],
            "findings":[{"title":"问题名称","category":"logo|color|layout|typography|content","detail":"可见证据及疑点","image_id":"tile编号或overview","box":[0,0,1,1],"rule_id":"R001","rule_quote":"对应规范的引文","observed":"图中情况","expected":"规范要求"}],
            "coverage": {c:{"status":"checked|partial|not_checked|not_applicable","reason":"检查范围、限制或不适用原因"} for c in ("logo","color","layout","typography")}
        })
        if not isinstance(visual.get("reviewed_image_ids"),list) or set(visual["reviewed_image_ids"])!={t["id"] for t in tiles}:
            raise ValueError("视觉模型没有确认覆盖全部设计切片，本次未生成完整报告。")
        if not isinstance(visual.get("findings"),list) or len(visual["findings"])>300:
            raise ValueError("视觉检查结果格式无效。")
        rulemap={r["id"]:r for r in self.project["rules"]}
        for f in visual["findings"]:
            rid=f.get("rule_id")
            if not isinstance(rid,str) or rid not in rulemap or not isinstance(f.get("rule_quote"),str) or not normalize(f["rule_quote"]) or normalize(f["rule_quote"]) not in normalize(rulemap[rid]["requirement"]):
                raise ValueError("视觉问题包含无法核实的规范引文，已保留调用记录，请检查后手动重试。")
            iid=f.get("image_id")
            if iid not in {t["id"] for t in tiles}|{"overview"}: raise ValueError("视觉问题引用了未知设计图片。")
            if not all(isinstance(f.get(key),str) and f[key].strip() for key in ("title","detail")): raise ValueError("视觉问题缺少描述。")
            box=f.get("box")
            if box is not None:
                box=image_box(box)
                if iid!="overview": box=original_box(box,next(t for t in tiles if t["id"]==iid))
            findings.append({"id":f"F{len(findings)+1:03}","category":f.get("category","layout"),"title":f["title"],"detail":f["detail"],
                "box":box,"precision":"model","observed":str(f.get("observed","")),"expected":str(f.get("expected",rulemap[rid]["requirement"])),
                "rule":rulemap[rid],"decision":"pending","note":""})
        coverage=visual.get("coverage")
        if not isinstance(coverage,dict): raise ValueError("视觉检查缺少覆盖范围。")
        for c in ("logo","color","layout","typography"):
            item=coverage.get(c)
            if not isinstance(item,dict) or item.get("status") not in {"checked","partial","not_checked","not_applicable"} or not isinstance(item.get("reason"),str) or not item["reason"].strip():
                raise ValueError("视觉检查覆盖范围不完整，不能作为完整报告返回。")
        copy_method="按选中单元格逐块对照；不要求块顺序，不核对未选备注与额外背景字。" if self.project.get("copy_blocks") else "逐字对照连续定稿；复杂阅读顺序可能产生误报。"
        coverage["copy"]={"status":"partial" if coverage_notes or not lines else "checked","reason":copy_method+"OCR可能产生误报。"+("；".join(coverage_notes) if coverage_notes else "")}
        coverage["font_file_identity"]={"status":"not_checked","reason":"位图无法可靠认证字体文件身份或授权；需检查源文件。"}
        report={"findings":findings,"coverage":coverage,"recognized_lines":lines,"recognized_text":"\n".join(x["text"] for x in lines),
            "image_meta":meta,"asset":asset,"copy":self.project["copy"],"project_name":self.project["name"],
            "baseline_version":self.project["baseline_version"],"copy_version":self.project["copy_version"],"rules":self.project["rules"],
            "copy_blocks":self.project.get("copy_blocks",[]),"copy_source":self.project.get("copy_source"),
            "article_id":self.job.get("article_id"),"article_name":self.project.get("article_name","旧版迁移稿件"),"activity_type":self.project.get("activity_type","其他"),
            "warnings":["这是初步核对结果，需结合原文和图上证据人工确认。","字体文件身份和字体授权未检查。"]+(["设计稿宽度缩至1500像素识别，极小文字需人工核对。"] if meta["resized"] else [])}
        self.app.job_update(self.job["id"],result=report,status="completed",progress="审核完成，请核对疑点与未覆盖项。",finished=time.time())

    def copy_diff(self, lines):
        if self.project.get("copy_blocks"):return self.block_diff(lines)
        recognized, mapping="",[]
        for i,line in enumerate(lines):
            text=normalize(line["text"])
            recognized+=text
            mapping.extend([i]*len(text))
        expected=normalize(self.project["copy"])
        findings=[]
        for op,a,b,c,d in difflib.SequenceMatcher(None,expected,recognized,autojunk=False).get_opcodes():
            if op=="equal": continue
            ids=sorted(set(mapping[c:d])) if c<d else ([mapping[min(c,len(mapping)-1)]] if mapping else [])
            boxes=[lines[i]["box"] for i in ids]
            box=[min(x[0] for x in boxes),min(x[1] for x in boxes),max(x[2] for x in boxes),max(x[3] for x in boxes)] if boxes else None
            findings.append({"id":f"F{len(findings)+1:03}","category":"copy","title":{"replace":"疑似文字不一致","delete":"疑似漏字 / 漏段","insert":"图中出现额外文字"}[op],
                "detail":"忽略空格与排版换行、统一全半角后存在差异；请结合局部核实 OCR。"+("缺失内容的位置仅为附近锚点。" if op=="delete" else ""),
                "expected":expected[a:b],"observed":recognized[c:d],"context":expected[max(0,a-16):min(len(expected),b+16)],
                "box":box,"precision":"nearby" if op=="delete" else "ocr","decision":"pending","note":""})
        return findings

    def block_diff(self,lines):
        # Check each confirmed cell independently, without requiring page reading order.
        texts=[normalize(l["text"]) for l in lines];joined="".join(texts)
        offsets=[];at=0
        for text in texts:offsets.append((at,at+len(text)));at+=len(text)
        used=[];findings=[]
        unmatched=[]
        for block in self.project["copy_blocks"]:
            expected=normalize(block["text"]);start=joined.find(expected);found=False
            while start>=0:
                end=start+len(expected)
                if not any(start<b and end>a for a,b in used):used.append((start,end));found=True;break
                start=joined.find(expected,start+1)
            if not found:unmatched.append(block)
        for block in unmatched:
            expected=normalize(block["text"])
            best=(0,"",None)
            for i in range(len(lines)):
                for count in range(1,min(8,len(lines)-i)+1):
                    a,b=offsets[i][0],offsets[i+count-1][1]
                    if any(a<y and b>x for x,y in used):continue
                    candidate="".join(texts[i:i+count])
                    if len(candidate)>len(expected)*2+20:break
                    if len(candidate)*3<len(expected):continue
                    ratio=difflib.SequenceMatcher(None,expected,candidate,autojunk=False).ratio()
                    if ratio>best[0]:best=(ratio,candidate,(i,count,a,b))
            observed="";box=None;precision="nearby"
            if best[0]>=.5:
                observed=best[1];i,count,a,b=best[2];used.append((a,b))
                boxes=[x["box"] for x in lines[i:i+count]]
                box=[min(x[0] for x in boxes),min(x[1] for x in boxes),max(x[2] for x in boxes),max(x[3] for x in boxes)]
            findings.append({"id":f"F{len(findings)+1:03}","category":"copy","title":"疑似单元格文案不一致" if observed else "未找到完整上版文案",
                "detail":"依据选中的上版单元格独立核对；附近位置和OCR仅供人工确认。","expected":block["text"],"observed":observed,"box":box,"precision":precision,
                "source_label":block["source_label"],"decision":"pending","note":""})
        return findings
