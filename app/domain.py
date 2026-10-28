"""Long-lived brand projects and independent editorial articles."""
import copy
import time
import uuid

COPY_FIELDS=("copy","copy_version","copy_blocks","copy_source","copy_draft","design_ids")


def new_article(project_id,title,article_id=None):
    now=time.time()
    return {"id":article_id or uuid.uuid4().hex,"project_id":project_id,"title":title,
            "activity_type":"游戏活动","description":"","copy":"","copy_version":0,
            "copy_blocks":[],"requirements":[],"design_ids":[],"revision":0,"created":now,"updated":now}


def article_for(state,project_id,article_id):
    article=state["articles"].get(article_id)
    if not article or article["project_id"]!=project_id:raise ValueError("请先选择当前项目的一篇稿件。")
    return article


def snapshot_for(project,article):
    result=copy.deepcopy(project)
    for key in COPY_FIELDS:
        if key in article:result[key]=copy.deepcopy(article[key])
    result.update(article_id=article["id"],article_name=article["title"],article_revision=article["revision"],
                  article_description=article["description"],activity_type=article["activity_type"])
    result["rules"]+=copy.deepcopy(article["requirements"])
    return result


def migrate_state(state):
    if state.get("schema_version")==4:return False
    state.setdefault("articles",{})
    for pid,p in state["projects"].items():
        reviews=[j for j in state["jobs"].values() if j["project_id"]==pid and j["kind"]=="review"]
        specific=[r for r in p["rules"] if r.get("source_cell") or state["assets"].get(r.get("source_asset"),{}).get("kind")=="copy"]
        if any(p.get(k) for k in COPY_FIELDS if k!="copy_version") or reviews or specific:
            aid="legacy-"+pid
            if aid in state["articles"]:raise ValueError("旧稿迁移编号冲突，资料未覆盖，请保留备份。")
            a=new_article(pid,"旧版迁移稿件",aid)
            for key in COPY_FIELDS:
                if key in p:a[key]=copy.deepcopy(p[key])
            a["requirements"]=copy.deepcopy(specific)
            for n,r in enumerate(a["requirements"],1):r["id"]=f"A{n:03}"
            a["migrated_from"]="project-copy-v0.2-v0.3"
            state["articles"][aid]=a
            for j in reviews:
                j["article_id"]=aid
                # Historical copy, rule IDs and report content stay byte-for-byte values.
        p["rules"]=[r for r in p["rules"] if r not in specific]
        for key in COPY_FIELDS:p.pop(key,None)
        if not p["rules"]:p["confirmed"]=False
    state["schema_version"]=4
    return True
