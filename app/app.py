from __future__ import annotations
import argparse
import base64
import copy
import ctypes
from ctypes import wintypes
import hashlib
import html
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import mimetypes
import os
from pathlib import Path
import queue
import secrets
import sys
import threading
import time
import traceback
from urllib import parse, request, error
import uuid
import webbrowser

from provider import ProviderConfig, VisionJsonProvider
from media import ingest, normalize
from pipeline import Engine, category
from domain import new_article, article_for, snapshot_for, migrate_state

VERSION = "1.0.0"
ROOT = Path(__file__).resolve().parent


class Store:
    def __init__(self, root):
        self.root=root
        root.mkdir(parents=True,exist_ok=True)
        self.path=root/"workspace.json"
        self.lock=threading.RLock()
        if self.path.exists():
            try: self.state=json.loads(self.path.read_text("utf-8"))
            except Exception: raise RuntimeError("本机资料库无法读取；请保留 Data 文件夹并检查 workspace.json.bak，程序未覆盖原文件。")
            if self.state.get("schema_version")!=4:
                backup=root/"workspace.pre-v0.4.json"
                if backup.exists():backup=root/("workspace.pre-v0.4-"+uuid.uuid4().hex[:8]+".json")
                with backup.open("xb") as out:out.write(self.path.read_bytes())
                if migrate_state(self.state):self.save()
        else:
            self.state={"schema_version":4,"projects":{},"articles":{},"assets":{},"jobs":{},"settings":{"provider":"cloud","base_url":"https://api.siliconflow.cn/v1","model":"Qwen/Qwen3.8-27B","json_mode":True,"disable_thinking":True,"timeout_seconds":300,"max_output_tokens":8192,"remember_key":False,"stream_mode":True}}
            self.save()

    def save(self):
        raw=json.dumps(self.state,ensure_ascii=False,allow_nan=False,indent=2)
        temp=self.path.with_suffix(".pending")
        with temp.open("w",encoding="utf-8") as out:
            out.write(raw); out.flush(); os.fsync(out.fileno())
        if self.path.exists():
            backup=self.path.with_suffix(".json.bak")
            backup.write_bytes(self.path.read_bytes())
        os.replace(temp,self.path)

    def read(self):
        with self.lock: return copy.deepcopy(self.state)

    def edit(self,fn):
        with self.lock:
            old=copy.deepcopy(self.state)
            try:
                value=fn(self.state)
                self.save()
                return copy.deepcopy(value)
            except Exception:
                self.state=old
                raise


def dpapi(data, decrypt=False):
    if os.name!="nt": raise ValueError("此系统仅支持会话内保存密钥；请取消记住密钥。")
    class Blob(ctypes.Structure):
        _fields_=[("cbData",wintypes.DWORD),("pbData",ctypes.POINTER(ctypes.c_char))]
    buffer=ctypes.create_string_buffer(data)
    source=Blob(len(data),ctypes.cast(buffer,ctypes.POINTER(ctypes.c_char)))
    target=Blob()
    crypt=ctypes.WinDLL("crypt32",use_last_error=True)
    crypt.CryptProtectData.argtypes=[ctypes.POINTER(Blob),wintypes.LPCWSTR,ctypes.POINTER(Blob),wintypes.LPVOID,wintypes.LPVOID,wintypes.DWORD,ctypes.POINTER(Blob)]
    crypt.CryptProtectData.restype=wintypes.BOOL
    crypt.CryptUnprotectData.argtypes=[ctypes.POINTER(Blob),ctypes.POINTER(wintypes.LPWSTR),ctypes.POINTER(Blob),wintypes.LPVOID,wintypes.LPVOID,wintypes.DWORD,ctypes.POINTER(Blob)]
    crypt.CryptUnprotectData.restype=wintypes.BOOL
    if decrypt:
        ok=crypt.CryptUnprotectData(ctypes.byref(source),None,None,None,None,1,ctypes.byref(target))
    else:
        ok=crypt.CryptProtectData(ctypes.byref(source),"LongImageReview",None,None,None,1,ctypes.byref(target))
    if not ok: raise ValueError(f"Windows 密钥保护失败（{ctypes.get_last_error()}），请取消记住密钥或重新填写。")
    try: return ctypes.string_at(target.pbData,target.cbData)
    finally:
        free=ctypes.windll.kernel32.LocalFree
        free.argtypes=[wintypes.HLOCAL]; free.restype=wintypes.HLOCAL
        free(ctypes.cast(target.pbData,wintypes.HLOCAL))


class Application:
    def __init__(self,data):
        self.data=data
        self.store=Store(data)
        self.token=secrets.token_urlsafe(32)
        self.app_id=hashlib.sha256(str(data.resolve()).encode()).hexdigest()[:24]
        self.secret_file=data/"private"/"api-key.bin"
        self.secret_file.parent.mkdir(exist_ok=True)
        self.api_key=""
        self.key_warning=""
        self.settings_lock=threading.RLock()
        if self.secret_file.exists():
            try: self.api_key=dpapi(self.secret_file.read_bytes(),True).decode("utf-8")
            except Exception: self.key_warning="原密钥无法在当前 Windows 账号解密，请重新填写。"
        self.tasks=queue.Queue()
        self.configs={}
        self.server=None
        def recover(state):
            for job in state["jobs"].values():
                if job["status"] in {"queued","running"}:
                    job.update(status="interrupted",progress="上次程序退出时任务未完成，未自动重发；请先核实调用记录。",finished=time.time())
        self.store.edit(recover)
        self.worker=threading.Thread(target=self.run_tasks,daemon=True)
        self.worker.start()

    def config(self):
        with self.settings_lock:
            s=self.store.read()["settings"]
            return ProviderConfig(s["provider"],s["base_url"],s["model"],self.api_key,float(s["timeout_seconds"]),int(s["max_output_tokens"]),bool(s["json_mode"]),bool(s["disable_thinking"]),bool(s.get("stream_mode",False)))

    def public_state(self):
        state=self.store.read()
        settings=state["settings"]
        settings.update(has_key=bool(self.api_key),key_warning=self.key_warning)
        jobs=[]
        for job in sorted(state["jobs"].values(),key=lambda j:j["created"],reverse=True):
            jobs.append({k:job.get(k) for k in ("id","kind","project_id","article_id","article_name","asset_id","status","progress","created","finished","error","calls")}|{"finding_count":len((job.get("result") or {}).get("findings",[]))})
        return {"version":VERSION,"csrf":self.token,"settings":settings,"projects":list(state["projects"].values()),"articles":list(state["articles"].values()),
                "assets":[{k:v for k,v in a.items() if k not in {"text","pages"}}|{"pages":[{k:v for k,v in p.items() if k!="text"} for p in a["pages"]],"text_preview":a["text"][:600]} for a in state["assets"].values()],"jobs":jobs}

    def save_settings(self,body):
        with self.settings_lock:
            old=self.store.read()["settings"]
            old.setdefault("stream_mode",False)
            s={k:body.get(k,old.get(k)) for k in old}
            s["base_url"]=str(s["base_url"]).strip().rstrip("/")
            if s["base_url"].endswith("/chat/completions"): s["base_url"]=s["base_url"][:-17]
            s["model"]=str(s["model"]).strip()
            for k in ("json_mode","disable_thinking","remember_key","stream_mode"):
                if type(s[k]) is not bool: raise ValueError("配置开关格式无效。")
            s["timeout_seconds"]=int(s["timeout_seconds"])
            s["max_output_tokens"]=int(s["max_output_tokens"])
            if not 10<=s["timeout_seconds"]<=600: raise ValueError("请求超时请设置在10至600秒。")
            key=str(body.get("api_key","")).strip()
            same=old["provider"]==s["provider"] and old["base_url"].rstrip("/")==s["base_url"]
            if not key and same and not body.get("clear_key"): key=self.api_key
            config=ProviderConfig(s["provider"],s["base_url"],s["model"],key,s["timeout_seconds"],s["max_output_tokens"],s["json_mode"],s["disable_thinking"],s["stream_mode"])
            encrypted=dpapi(key.encode()) if key and s["remember_key"] else None
            if encrypted is not None:
                temp=self.secret_file.with_suffix(".pending")
                temp.write_bytes(encrypted); os.replace(temp,self.secret_file)
            elif self.secret_file.exists(): self.secret_file.unlink()
            self.store.edit(lambda state:state.update(settings=s))
            self.api_key=key
            self.key_warning=""
        return {"message":"API 配置已保存；保存配置不会产生模型调用。"}

    def connection(self):
        config=self.config()
        opener=VisionJsonProvider(config)._opener
        headers={"Accept":"application/json"}
        if config.api_key: headers["Authorization"]="Bearer "+config.api_key
        try:
            with opener.open(request.Request(config.base_url.rstrip("/")+"/models",headers=headers),timeout=15) as response:
                value=json.loads(response.read(2*1024*1024))
            models=[x["id"] for x in value.get("data",[]) if isinstance(x,dict) and isinstance(x.get("id"),str)]
            return {"message":"已连接到模型目录。"+("已找到配置模型。" if config.model in models else "目录未列出配置模型；部分接口使用别名，仍可尝试送审。"),"models":models[:200]}
        except error.HTTPError as exc:
            status_code=exc.code; exc.close()
            if status_code in {404,405}: return {"message":"该接口不提供模型目录，暂时无法用此方式验证；可以继续尝试送审。","warning":True}
            raise ValueError(f"模型目录返回 HTTP {status_code}，请检查地址、密钥和账户权限。") from None
        except Exception: raise ValueError("无法连接模型目录，请检查 API 地址、网络和代理配置。") from None

    def job_update(self,jid,**values):
        self.store.edit(lambda state:state["jobs"][jid].update(copy.deepcopy(values)))

    def apply_guide_result(self,state,job,result,force=False):
        p=state["projects"][job["project_id"]]
        targets=set(job.get("guide_asset_ids",job["snapshot"]["guide_ids"]))
        if not targets.issubset(p["guide_ids"]):
            if force:raise ValueError("原Guide已移除，请重新上传后解析。")
            return False
        def relevant(rules):
            return [{k:v for k,v in r.items() if k!="id"} for r in rules if r.get("source_asset") in targets]
        def selected(ids):
            return {a for a in ids if state["assets"].get(a,{}).get("derived_from",{}).get("asset_id") in targets}
        if not force and (relevant(p["rules"])!=relevant(job["snapshot"]["rules"]) or selected(p["logo_ids"])!=selected(job["snapshot"]["logo_ids"]) or any(p.get("guide_analysis",{}).get(a,{}).get("job_id")!=job["id"] for a in targets)):
            return False
        previous={a for a in p.get("extracted_logo_ids",[]) if state["assets"][a].get("derived_from",{}).get("asset_id") in targets}
        p["extracted_logo_ids"]=[a for a in p.get("extracted_logo_ids",[]) if a not in previous]+result.get("logo_ids",[])
        p["logo_ids"]=[a for a in p["logo_ids"] if a not in previous]+[a for a in result.get("logo_ids",[]) if state["assets"][a]["derived_from"]["usage"]=="approved"]
        p["rules"]=[r for r in p["rules"] if r.get("source_asset") not in targets]+copy.deepcopy(result["rules"])
        for n,r in enumerate(p["rules"],1):r["id"]=f"R{n:03}"
        p.update(confirmed=False,revision=p["revision"]+1)
        return True

    def create_job(self,body):
        config=self.config()
        pid=body.get("project_id")
        kind=body.get("kind")
        request_id=str(body.get("request_id",""))
        if not request_id or len(request_id)>128 or kind not in {"guide","review"}: raise ValueError("任务参数无效。")
        with self.settings_lock:
            def create(state):
                p=state["projects"].get(pid)
                if not p: raise ValueError("项目不存在。")
                aid=body.get("asset_id") if kind=="review" else None
                article=article_for(state,pid,body.get("article_id")) if kind=="review" else None
                article_id=article["id"] if article else None
                snapshot=snapshot_for(p,article) if article else copy.deepcopy(p)
                input_hash=hashlib.sha256(json.dumps({k:snapshot.get(k) for k in ("copy","copy_version","copy_blocks","rules","logo_ids","baseline_version","article_id","article_name")},ensure_ascii=False,sort_keys=True).encode()).hexdigest()
                gids=sorted(set(body.get("guide_asset_ids",p["guide_ids"]))) if kind=="guide" else []
                if kind=="guide" and (not gids or not set(gids).issubset(p["guide_ids"])):raise ValueError("请先上传本项目的视觉Guide。")
                for j in state["jobs"].values():
                    if j.get("request_id")==request_id or request_id in j.get("request_aliases",[]):
                        if j["project_id"]!=pid or j["kind"]!=kind or j.get("asset_id")!=aid or j.get("article_id")!=article_id or (kind=="guide" and j.get("guide_asset_ids",j["snapshot"]["guide_ids"])!=gids): raise ValueError("请求编号冲突。")
                        return j["id"],False
                if kind=="review":
                    if not p["confirmed"]: raise ValueError("请先检查并确认项目规范。")
                    if not article["copy"].strip(): raise ValueError("请先在稿件文案中保存本篇定稿。")
                    if aid not in article["design_ids"]: raise ValueError("请上传属于当前稿件的设计长图。")
                elif not p["guide_ids"]: raise ValueError("请先上传视觉 guide。")
                for j in state["jobs"].values():
                    if j["status"] in {"running","queued"} and j["project_id"]==pid and j["kind"]==kind and j.get("asset_id")==aid and j.get("article_id")==article_id and (kind!="review" or j.get("input_hash")==input_hash) and (kind!="guide" or j.get("guide_asset_ids",j["snapshot"]["guide_ids"])==gids):
                        j.setdefault("request_aliases",[]).append(request_id)
                        return j["id"],False
                jid=uuid.uuid4().hex
                state["jobs"][jid]={"id":jid,"request_id":request_id,"project_id":pid,"asset_id":aid,"kind":kind,"status":"queued",
                    "progress":"排队等待审核服务…","created":time.time(),"snapshot":snapshot,"calls":[],"article_id":article_id,"article_name":article["title"] if article else None,"input_hash":input_hash,
                    "provider":config.provider,"model":config.model,"result":None,"guide_asset_ids":gids}
                if kind=="guide":
                    for a in gids:p.setdefault("guide_analysis",{})[a]={"job_id":jid,"state":"queued"}
                return jid,True
            jid,isnew=self.store.edit(create)
            if isnew: self.configs[jid]=config; self.tasks.put(jid)
        return {"job_id":jid}

    def run_tasks(self):
        while True:
            jid=self.tasks.get()
            try:
                job=self.store.read()["jobs"][jid]
                self.job_update(jid,status="running",progress="正在准备输入资料…")
                engine=Engine(self,job,self.configs[jid])
                if job["kind"]=="guide": engine.extract_guide()
                else: engine.review()
            except Exception as exc:
                message=str(exc) if isinstance(exc,ValueError) else "处理任务时出现异常，请查看本机诊断日志；未自动重试。"
                if not isinstance(exc,ValueError): self.log_error()
                self.job_update(jid,status="failed",error=message,progress=message,finished=time.time())
            finally:
                self.configs.pop(jid,None)
                self.tasks.task_done()

    def log_error(self):
        with (self.data/"diagnostics.log").open("a",encoding="utf-8") as out:
            out.write(time.strftime("%Y-%m-%d %H:%M:%S")+"\n"+traceback.format_exc()+"\n")

    def post(self,path,body):
        if path=="/api/settings": return self.save_settings(body)
        if path=="/api/connection": return self.connection()
        if path=="/api/jobs": return self.create_job(body)
        if path=="/api/project":
            if any(k in body for k in ("copy","copy_draft","copy_blocks","requirements_text")):raise ValueError("项目只维护长期品牌规范；请到稿件文案保存本次活动内容。")
            def save(state):
                pid=body.get("id") or uuid.uuid4().hex
                p=state["projects"].get(pid) or {"id":pid,"name":"","description":"","guide_ids":[],"logo_ids":[],"rules":[],"confirmed":False,"revision":0,"baseline_version":0}
                name=str(body.get("name",p["name"])).strip()
                if not 1<=len(name)<=100: raise ValueError("请输入1至100字的项目名称。")
                old_rules=p["rules"]
                if "rules_text" in body:
                    rows=[x.strip() for x in str(body["rules_text"]).splitlines() if x.strip()]
                    if len(rows)>150 or sum(map(len,rows))>30000: raise ValueError("规范过长，请精简为最多150条、合计3万字。")
                    if rows!=[r["requirement"] for r in old_rules]:
                        rules=[]
                        for i,text in enumerate(rows):
                            old=next((r for r in old_rules if r["requirement"]==text),None)
                            rules.append(copy.deepcopy(old) if old else {"category":category(text),"requirement":text,"source_label":"人工补充或修改","source_quote":text})
                            rules[-1]["id"]=f"R{i+1:03}"
                        p.update(rules=rules,confirmed=False)
                p.update(name=name,description=str(body.get("description",p["description"]))[:1000],revision=p["revision"]+1)
                state["projects"][pid]=p
                return {"project_id":pid}
            return self.store.edit(save)
        if path=="/api/article":
            def save_article(state):
                pid=body.get("project_id")
                if pid not in state["projects"]:raise ValueError("请先选择长期运营的项目。")
                a=article_for(state,pid,body["id"]) if body.get("id") else new_article(pid,"")
                title=str(body.get("title",a["title"])).strip()
                if not 1<=len(title)<=100:raise ValueError("请填写1至100字的稿件标题。")
                activity=str(body.get("activity_type",a["activity_type"]))
                if activity not in {"游戏活动","版本更新","商业化上新","其他"}:raise ValueError("稿件类型无效。")
                if "copy" in body:
                    text=str(body["copy"])
                    if len(text)>20000:raise ValueError("本篇文案最多2万字，请分篇。")
                    if text!=a["copy"]:a.update(copy=text,copy_blocks=[],copy_version=a["copy_version"]+1)
                if "requirements_text" in body:
                    rows=[r.strip() for r in str(body["requirements_text"]).splitlines() if r.strip()]
                    if len(rows)>100 or sum(map(len,rows))>20000:raise ValueError("本篇设计要求最多100条、合计2万字。")
                    old=a["requirements"];rules=[]
                    for n,text in enumerate(rows,1):
                        r=copy.deepcopy(next((r for r in old if r["requirement"]==text),{"requirement":text,"category":category(text),"source_label":"本篇设计要求","source_quote":text}))
                        r["id"]=f"A{n:03}";rules.append(r)
                    a["requirements"]=rules
                a.update(title=title,activity_type=activity,description=str(body.get("description",a["description"]))[:2000],revision=a["revision"]+1,updated=time.time())
                state["articles"][a["id"]]=a
                return {"article_id":a["id"],"message":"稿件文案已保存，项目Guide继续共用。"}
            return self.store.edit(save_article)
        if path=="/api/confirm":
            def confirm(state):
                p=state["projects"][body["project_id"]]
                if not p["rules"]: raise ValueError("请先提取规范，或逐行填写需要检查的规则。")
                p.update(confirmed=True,baseline_version=p["baseline_version"]+1,revision=p["revision"]+1)
                return {"message":"本版规范已确认，可以提交设计稿。"}
            return self.store.edit(confirm)
        if path=="/api/assets":
            pid=body.get("project_id")
            if pid not in self.store.read()["projects"]: raise ValueError("请先创建项目。")
            if body.get("kind")=="design":article_for(self.store.read(),pid,body.get("article_id"))
            try: raw=base64.b64decode(body["data"],validate=True)
            except Exception: raise ValueError("上传文件的编码无效。") from None
            if body.get("kind") not in {"guide","logo","design"}:raise ValueError("资料类型无效。")
            asset=ingest(self.data,name=str(body.get("name","")),raw=raw,kind=body.get("kind"))
            def attach(state):
                state["assets"][asset["id"]]=asset
                p=state["projects"][pid]
                if asset["kind"]=="design":
                    a=article_for(state,pid,body.get("article_id"));a["design_ids"].append(asset["id"]);a["revision"]+=1;a["updated"]=time.time()
                else:p[{"guide":"guide_ids","logo":"logo_ids"}[asset["kind"]]].append(asset["id"]);p["revision"]+=1
                if asset["kind"] in {"guide","logo"}: p["confirmed"]=False
                return {"asset_id":asset["id"],"text":asset["text"] if asset["kind"]=="guide" and not asset["pages"] else ""}
            result=self.store.edit(attach)
            if asset["kind"]=="guide":
                try:
                    result.update(self.create_job({"project_id":pid,"kind":"guide","guide_asset_ids":[asset["id"]],"request_id":"auto-guide-"+asset["id"]}))
                except ValueError:
                    self.store.edit(lambda s:s["projects"][pid].setdefault("guide_analysis",{}).update({asset["id"]:{"state":"waiting_api"}}))
                    result["warning"]="Guide已保存；请先配置API，再点击重新解析。"
            return result
        if path=="/api/detach":
            def detach(state):
                p=state["projects"][body["project_id"]]
                aid=body["asset_id"]
                if body.get("article_id"):
                    a=article_for(state,body["project_id"],body["article_id"])
                    if aid in a["design_ids"]:a["design_ids"].remove(aid);a["revision"]+=1
                for field in ("guide_ids","logo_ids"):
                    if aid in p[field]:
                        p[field].remove(aid); p["revision"]+=1
                        if field!="design_ids": p["confirmed"]=False
                        if field=="guide_ids":
                            p["rules"]=[r for r in p["rules"] if r.get("source_asset")!=aid]
                            derived={a for a in p.get("extracted_logo_ids",[]) if state["assets"][a].get("derived_from",{}).get("asset_id")==aid}
                            p["extracted_logo_ids"]=[a for a in p.get("extracted_logo_ids",[]) if a not in derived]
                            p["logo_ids"]=[a for a in p["logo_ids"] if a not in derived]
                return {"message":"已从当前项目移除；历史审核仍保留原素材。"}
            return self.store.edit(detach)
        if path=="/api/import-copy":
            article_for(self.store.read(),body.get("project_id"),body.get("article_id"))
            try: raw=base64.b64decode(body["data"],validate=True)
            except Exception: raise ValueError("文件编码无效。") from None
            asset=ingest(self.data,name=str(body.get("name","")),raw=raw,kind="copy")
            if asset.get("workbook"):
                def draft(state):
                    state["assets"][asset["id"]]=asset
                    a=article_for(state,body["project_id"],body["article_id"])
                    a["copy_draft"]={"asset_id":asset["id"],"name":asset["name"],**asset["workbook"]};a["revision"]+=1
                    return {"draft":True,"message":"请选择对应工作表，并确认上版文案；原定稿尚未替换。"}
                return self.store.edit(draft)
            if not asset["text"].strip(): raise ValueError("未读取到文案，请粘贴文字或导入TXT/DOCX。")
            def save_copy(state):
                state["assets"][asset["id"]]=asset
                p=article_for(state,body["project_id"],body["article_id"])
                p.update(copy=asset["text"][:20000],copy_blocks=[],copy_version=p["copy_version"]+1,revision=p["revision"]+1)
                return {"text":p["copy"]}
            if len(asset["text"])>20000: raise ValueError("文案超过2万字，请拆分后导入。")
            return self.store.edit(save_copy)
        if path=="/api/adopt-copy":
            def adopt_copy(state):
                p=article_for(state,body["project_id"],body["article_id"]); draft=p.get("copy_draft")
                if not draft or draft["asset_id"]!=body.get("asset_id"):raise ValueError("需求表预览已变化，请重新选择。")
                sheet=next(s for s in draft["sheets"] if s["name"]==body.get("sheet"))
                cells={c["id"]:c for c in sheet["cells"]};selected=body.get("selected")
                if not isinstance(selected,list) or not 1<=len(selected)<=500:raise ValueError("请选择1至500项上版文案。")
                blocks=[];seen=set()
                for row in selected:
                    cid=row.get("id");text=row.get("text")
                    if cid not in cells or cid in seen or not isinstance(text,str) or not text.strip():raise ValueError("文案选择无效，请重新检查。")
                    seen.add(cid);c=cells[cid]
                    blocks.append({"id":cid,"text":text.strip(),"sheet":sheet["name"],"cell":c["cell"],"source_label":draft["name"]+" · "+sheet["name"]+"!"+c["cell"]})
                text="\n".join(b["text"] for b in blocks)
                if len(text)>20000:raise ValueError("本篇文案超过2万字，请减少选中项。")
                requirements=body.get("requirements",[])
                if not isinstance(requirements,list) or len(requirements)>100:raise ValueError("本篇设计要求最多100项。")
                new_rules=[];seen_rules=set()
                for row in requirements:
                    cid=row.get("id");requirement=row.get("text")
                    if cid not in cells or cid in seen_rules or not isinstance(requirement,str) or not 1<=len(requirement.strip())<=2000:raise ValueError("设计要求选择无效。")
                    seen_rules.add(cid);c=cells[cid]
                    new_rules.append({"category":category(requirement),"requirement":requirement.strip(),"source_asset":draft["asset_id"],"source_cell":c["cell"],"source_label":draft["name"]+" · "+sheet["name"]+"!"+c["cell"],"source_quote":c["text"]})
                remaining=[r for r in p["requirements"] if r.get("source_asset")!=draft["asset_id"]]
                if len(remaining)+len(new_rules)>150 or sum(len(r["requirement"]) for r in remaining+new_rules)>30000:raise ValueError("审核要求过多，请精简后采用。")
                if new_rules or len(remaining)!=len(p["requirements"]):
                    p["requirements"]=remaining+new_rules
                    for n,r in enumerate(p["requirements"],1):r["id"]=f"A{n:03}"
                p.update(copy=text,copy_blocks=blocks,copy_source={"asset_id":draft["asset_id"],"sheet":sheet["name"],"name":draft["name"]},copy_version=p["copy_version"]+1,revision=p["revision"]+1)
                return {"message":f"已采用{len(blocks)}项上版文案及{len(new_rules)}项设计要求；请确认审核依据。"}
            return self.store.edit(adopt_copy)
        if path=="/api/logo-selection":
            def select_logo(state):
                p=state["projects"][body["project_id"]];aid=body["asset_id"]
                if aid not in p.get("extracted_logo_ids",[]):raise ValueError("此LOGO不在当前Guide解析结果中。")
                if body.get("enabled") and aid not in p["logo_ids"]:p["logo_ids"].append(aid)
                if not body.get("enabled") and aid in p["logo_ids"]:p["logo_ids"].remove(aid)
                p.update(confirmed=False,revision=p["revision"]+1)
                return {"message":"参考LOGO选择已保存，请确认规范后送审。"}
            return self.store.edit(select_logo)
        if path=="/api/adopt-rules":
            def adopt(state):
                j=state["jobs"][body["job_id"]]
                if j["kind"]!="guide" or j["status"]!="completed": raise ValueError("此任务没有可采用的规范。")
                self.apply_guide_result(state,j,j["result"],force=True)
                return {"message":"已采用此批规范与LOGO候选，请检查并确认。"}
            return self.store.edit(adopt)
        if path=="/api/decision":
            def decision(state):
                j=state["jobs"][body["job_id"]]
                f=next(x for x in j["result"]["findings"] if x["id"]==body["finding_id"])
                status=body.get("decision")
                if status not in {"pending","fixed","false_positive","accepted"}: raise ValueError("处理状态无效。")
                f.update(decision=status,note=str(body.get("note",""))[:1000])
                return {"message":"处理记录已保存。"}
            return self.store.edit(decision)
        if path=="/api/shutdown":
            if any(j["status"] in {"queued","running"} for j in self.store.read()["jobs"].values()): raise ValueError("仍有任务在处理中，请等待完成后退出，避免计费状态不明。")
            threading.Thread(target=lambda:(time.sleep(.2),self.server.shutdown()),daemon=True).start()
            return {"message":"程序已退出，可以关闭此页面。"}
        raise ValueError("操作不存在。")


class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args): pass
    @property
    def app(self): return self.server.app

    def send(self,data,status=200,mime="application/json; charset=utf-8",headers=None):
        if not isinstance(data,bytes): data=json.dumps(data,ensure_ascii=False,allow_nan=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type",mime)
        self.send_header("Content-Length",str(len(data)))
        self.send_header("X-Content-Type-Options","nosniff")
        self.send_header("Cache-Control","no-store")
        self.send_header("Referrer-Policy","no-referrer")
        self.send_header("Content-Security-Policy","default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'")
        for key,value in (headers or {}).items(): self.send_header(key,value)
        self.end_headers()
        self.wfile.write(data)

    def host_ok(self):
        return self.headers.get("Host") in {f"127.0.0.1:{self.server.server_port}",f"localhost:{self.server.server_port}"}

    def authorized(self):
        return ("review_session="+self.app.token) in [x.strip() for x in self.headers.get("Cookie","").split(";")]

    def do_GET(self):
        try:
            if not self.host_ok(): return self.send({"error":"无效的本机访问地址。"},403)
            url=parse.urlsplit(self.path); qs=parse.parse_qs(url.query)
            if url.path=="/api/health": return self.send({"app_id":self.app.app_id,"version":VERSION})
            if url.path=="/" and secrets.compare_digest(qs.get("token",[""])[0],self.app.token):
                return self.send(b"",302,"text/plain",{"Location":"/","Set-Cookie":"review_session="+self.app.token+"; HttpOnly; SameSite=Strict; Path=/"})
            if not self.authorized(): return self.send({"error":"请双击启动程序打开本机工作台。"},403)
            if url.path=="/api/state": return self.send(self.app.public_state())
            if url.path=="/api/job": return self.send(self.app.store.read()["jobs"][qs["id"][0]])
            if url.path=="/api/asset":
                asset=self.app.store.read()["assets"][qs["id"][0]]
                if qs.get("original")==["1"]:
                    path=self.app.data/"assets"/asset["id"]/asset["original"]
                    return self.send(path.read_bytes(),mime="application/octet-stream",headers={"Content-Disposition":"attachment; filename*=UTF-8''"+parse.quote(asset["name"])})
                page=next(p for p in asset["pages"] if p["page"]==int(qs.get("page",["1"])[0]))
                return self.send((self.app.data/"assets"/asset["id"]/page["file"]).read_bytes(),mime="image/jpeg")
            if url.path=="/api/export":
                job=self.app.store.read()["jobs"][qs["id"][0]]
                if not job.get("result"): raise ValueError("报告尚未生成。")
                report=export_report(self.app,job)
                return self.send(report.encode("utf-8"),mime="text/html; charset=utf-8",headers={"Content-Disposition":"attachment; filename=review-report.html"})
            files={"/":"index.html","/app.js":"app.js","/style.css":"style.css"}
            if url.path in files:
                name=files[url.path]
                return self.send((ROOT/"static"/name).read_bytes(),mime={"index.html":"text/html; charset=utf-8","app.js":"text/javascript; charset=utf-8","style.css":"text/css; charset=utf-8"}[name])
            return self.send({"error":"页面不存在。"},404)
        except (ValueError,KeyError,StopIteration): return self.send({"error":"未找到资料或请求参数无效。"},400)
        except (BrokenPipeError,ConnectionResetError): pass
        except Exception:
            self.app.log_error(); self.send({"error":"读取失败，请查看诊断日志。"},500)

    def do_POST(self):
        try:
            if not self.host_ok() or not self.authorized() or not secrets.compare_digest(self.headers.get("X-Review-Token",""),self.app.token): return self.send({"error":"访问会话已失效，请重新启动打开。"},403)
            origin=self.headers.get("Origin")
            if origin and origin not in {f"http://127.0.0.1:{self.server.server_port}",f"http://localhost:{self.server.server_port}"}: return self.send({"error":"拒绝跨站请求。"},403)
            length=int(self.headers.get("Content-Length","0"))
            if not 0<length<=48*1024*1024: return self.send({"error":"请求为空或文件超过32 MB。"},413)
            body=json.loads(self.rfile.read(length))
            if not isinstance(body,dict): raise ValueError("请求格式无效。")
            return self.send(self.app.post(parse.urlsplit(self.path).path,body))
        except (ValueError,KeyError,StopIteration) as exc:
            message=str(exc) if isinstance(exc,ValueError) else "项目、任务或资料不存在。"
            return self.send({"error":message},400)
        except (BrokenPipeError,ConnectionResetError): pass
        except Exception:
            self.app.log_error(); self.send({"error":"保存或处理失败，原有资料已保留；请查看诊断日志。"},500)


def export_report(app,job):
    r=job["result"]
    if job["kind"]!="review": raise ValueError("请选择设计审核报告。")
    asset=r["asset"]
    data=base64.b64encode((app.data/"assets"/asset["id"]/"page-1.jpg").read_bytes()).decode()
    esc=lambda x:html.escape(str(x))
    rects="".join(f'<rect x="{f["box"][0]*1000}" y="{f["box"][1]*1000}" width="{(f["box"][2]-f["box"][0])*1000}" height="{(f["box"][3]-f["box"][1])*1000}"/>' for f in r["findings"] if f.get("box"))
    cards="".join(f'<article><h3>{esc(f["id"])} · {esc(f["title"])}</h3><p>{esc(f["detail"])}</p><p>应为：{esc(f.get("expected",""))}<br>图中：{esc(f.get("observed",""))}</p><p>依据：{esc((f.get("rule") or {}).get("source_label",f.get("source_label","本篇定稿文案")))}</p><p>处理：{esc({"pending":"待核对","fixed":"已修正","false_positive":"误报","accepted":"接受例外"}.get(f["decision"]))} {esc(f.get("note",""))}</p></article>' for f in r["findings"])
    cov="".join(f'<li>{esc(c)}：{esc(i["status"])} · {esc(i["reason"])}</li>' for c,i in r["coverage"].items())
    return f'<!doctype html><meta charset="utf-8"><title>长图审核报告</title><style>body{{font:15px/1.7 system-ui;margin:32px;color:#263046}}main{{display:grid;grid-template-columns:minmax(300px,1fr) minmax(350px,1fr);gap:28px}}img{{width:100%;display:block}}.picture{{position:relative;align-self:start}}svg{{position:absolute;inset:0;width:100%;height:100%;pointer-events:none}}rect{{fill:rgba(220,70,50,.08);stroke:#d54836;stroke-width:2;vector-effect:non-scaling-stroke}}article{{border:1px solid #ddd;padding:18px;margin:0 0 12px}}p{{white-space:pre-wrap;overflow-wrap:anywhere}}@media(max-width:800px){{main{{display:block}}}}</style><h1>{esc(r["project_name"])} / {esc(r.get("article_name","旧版报告"))} · 初步审核报告</h1><p>规范 v{r["baseline_version"]} / 文案 v{r["copy_version"]} / {esc(job["model"])}<br>原图 SHA-256：{esc(asset["sha256"])}<br>报告包含本篇文案与设计，请按项目资料管理。</p><ul>{cov}</ul><main><div class="picture"><img src="data:image/jpeg;base64,{data}"><svg viewBox="0 0 1000 1000" preserveAspectRatio="none">{rects}</svg></div><section>{cards or "本次已检范围未发现差异，请核对上方未覆盖项。"}</section></main><h2>定稿文案</h2><p>{esc(r["copy"])}</p>'


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--data",type=Path,default=ROOT.parent/"Data")
    parser.add_argument("--open",action="store_true")
    parser.add_argument("--port",type=int,default=0)
    parser.add_argument("--server",action="store_true")
    parser.add_argument("--server-config",type=Path,default=ROOT.parent/"ServerConfig.json")
    parser.add_argument("--reset-user")
    args=parser.parse_args()
    if args.server:
        from team import run_server
        return run_server(args,Application,Handler,VERSION)
    data=args.data.resolve(); data.mkdir(parents=True,exist_ok=True)
    instance=data/"instance.json"
    try:
        old=json.loads(instance.read_text("utf-8"))
        app_id=hashlib.sha256(str(data).encode()).hexdigest()[:24]
        with request.build_opener(request.ProxyHandler({})).open(f'http://127.0.0.1:{old["port"]}/api/health',timeout=.5) as response: health=json.load(response)
        if health.get("app_id")==app_id:
            if args.open: webbrowser.open(f'http://127.0.0.1:{old["port"]}/?token={old["token"]}')
            return
    except Exception: pass
    lock=(data/"instance.lock").open("a+b")
    if os.name=="nt":
        import msvcrt
        lock.seek(0); lock.write(b"1"); lock.flush(); lock.seek(0)
        try: msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
        except OSError: return
    app=Application(data)
    server=ThreadingHTTPServer(("127.0.0.1",args.port),Handler)
    server.daemon_threads=True; server.app=app; app.server=server
    info={"port":server.server_port,"token":app.token,"app_id":app.app_id,"pid":os.getpid(),"version":VERSION}
    instance.write_text(json.dumps(info),encoding="utf-8")
    print(f"Long Image Review {VERSION} listening on 127.0.0.1:{server.server_port}",flush=True)
    if args.open: webbrowser.open(f'http://127.0.0.1:{server.server_port}/?token={app.token}')
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close(); lock.close()


if __name__=="__main__":
    try: main()
    except Exception:
        path=ROOT.parent/"startup-error.txt"
        path.write_text(traceback.format_exc(),encoding="utf-8")
        if os.name=="nt": ctypes.windll.user32.MessageBoxW(None,f"程序启动失败，诊断记录：\n{path}","长图视觉审稿",16)
        raise
