import _bootstrap
import base64
import copy
import json
from urllib import error

from app import Store
from test_app import WorkflowTests
from test_v03 import xlsx_fixture


class ArticleWorkflowTests(WorkflowTests):
    pass

for key in list(WorkflowTests.__dict__):
    if key.startswith("test_"):setattr(ArticleWorkflowTests,key,None)


def configure(self):
    self.post("/api/settings",{"provider":"local","base_url":f"http://127.0.0.1:{self.model.server_port}/v1","model":"offline-fixture-vision","api_key":"","remember_key":False})


def test_14_one_brand_two_articles_reuse_guide_and_keep_reports(self):
    configure(self)
    pid=self.post("/api/project",{"name":"长期游戏运营"})["project_id"]
    guide=self.upload(pid,"视觉Guide-测试样例.png","guide")
    jid=self.app.store.read()["projects"][pid]["guide_analysis"][guide]["job_id"]
    self.assertEqual(self.wait(jid)["status"],"completed")
    self.post("/api/confirm",{"project_id":pid})
    brand=copy.deepcopy(self.app.store.read()["projects"][pid]);calls=len(self.model.calls)
    a=self.post("/api/article",{"project_id":pid,"title":"十月活动","copy":"秋日品牌开放日","requirements_text":"展示三种形态"})["article_id"]
    b=self.post("/api/article",{"project_id":pid,"title":"十一月更新","activity_type":"版本更新","copy":"全新版本更新","requirements_text":"展示新版地图"})["article_id"]
    self.assertEqual(len(self.model.calls),calls)
    design=self.upload(pid,"设计长图-含3处已知问题.png","design",a)
    with self.assertRaises(error.HTTPError):self.post("/api/jobs",{"project_id":pid,"article_id":b,"kind":"review","asset_id":design,"request_id":"wrong-article"})
    body={"project_id":pid,"article_id":a,"kind":"review","asset_id":design,"request_id":"article-a-v1"}
    j=self.wait(self.post("/api/jobs",body)["job_id"])
    self.assertEqual(j["status"],"completed",j.get("error"))
    self.assertEqual(j["result"]["article_name"],"十月活动")
    self.assertEqual(j["result"]["copy"],"秋日品牌开放日")
    self.assertIn("展示三种形态",[r["requirement"] for r in j["snapshot"]["rules"]])
    self.assertNotIn("展示新版地图",[r["requirement"] for r in j["snapshot"]["rules"]])
    self.post("/api/article",{"id":b,"project_id":pid,"copy":"十一月新定稿"})
    self.post("/api/article",{"id":a,"project_id":pid,"copy":"十月修订稿"})
    self.assertEqual(self.app.store.read()["jobs"][j["id"]]["result"]["copy"],"秋日品牌开放日")
    self.assertEqual(self.app.store.read()["articles"][b]["copy"],"十一月新定稿")
    self.assertEqual(self.app.store.read()["projects"][pid],brand)
    self.assertEqual(self.app.store.read()["articles"][b]["design_ids"],[])
    self.assertEqual(self.post("/api/jobs",body)["job_id"],j["id"])
    exported=self.get("/api/export?id="+j["id"]).decode()
    self.assertIn("十月活动",exported)


def test_15_article_excel_requirements_do_not_modify_brand(self):
    pid=self.post("/api/project",{"name":"表格边界","rules_text":"使用项目标准LOGO"})["project_id"]
    self.post("/api/confirm",{"project_id":pid})
    original=copy.deepcopy(self.app.store.read()["projects"][pid])
    a=self.post("/api/article",{"project_id":pid,"title":"礼包介绍稿"})["article_id"]
    b=self.post("/api/article",{"project_id":pid,"title":"机制说明稿","copy":"另一篇文案"})["article_id"]
    self.post("/api/import-copy",{"project_id":pid,"article_id":a,"name":"虚构表.xlsx","data":base64.b64encode(xlsx_fixture()).decode()})
    draft=self.app.store.read()["articles"][a]["copy_draft"]
    cells=draft["sheets"][1]["cells"]
    selected=[{"id":c["id"],"text":c["text"]} for c in cells if c["role"]=="copy"]
    note=next(c for c in cells if c["cell"]=="H2")
    self.post("/api/adopt-copy",{"project_id":pid,"article_id":a,"asset_id":draft["asset_id"],"sheet":"礼包图","selected":selected,"requirements":[{"id":note["id"],"text":"挂饰标注x2"}]})
    state=self.app.store.read()
    self.assertEqual(state["projects"][pid],original)
    self.assertEqual(state["articles"][b]["copy"],"另一篇文案")
    self.assertEqual(state["articles"][a]["requirements"][0]["id"],"A001")
    self.assertNotIn("挂饰标注x2",state["articles"][a]["copy"])
    with self.assertRaises(error.HTTPError):self.post("/api/project",{"id":pid,"copy":"不能写进项目"})
    other=self.post("/api/project",{"name":"另一游戏"})["project_id"]
    with self.assertRaises(error.HTTPError):self.post("/api/article",{"id":a,"project_id":other,"title":"错误归属"})


def test_16_legacy_migration_backs_up_and_preserves_history(self):
    data=self.temp/"legacy-v03";data.mkdir()
    old={"projects":{"p":{"id":"p","name":"旧项目","description":"","guide_ids":[],"logo_ids":[],"design_ids":["d"],"rules":[{"id":"R001","requirement":"品牌规范","category":"layout"},{"id":"R002","requirement":"本篇要求","source_cell":"H2","source_asset":"x","category":"layout"}],"confirmed":True,"revision":7,"baseline_version":2,"copy":"旧版当前定稿","copy_version":3,"copy_blocks":[],"copy_draft":{"name":"旧表"}}},"assets":{"x":{"kind":"copy"}},"jobs":{"j":{"id":"j","project_id":"p","kind":"review","status":"failed","error":"HTTP 402","calls":[],"snapshot":{"copy":"历史定稿"},"result":{"copy":"历史报告原文"}}},"settings":{"model":"custom-model"}}
    raw=json.dumps(old,ensure_ascii=False).encode();(data/"workspace.json").write_bytes(raw)
    store=Store(data);state=store.read();a=state["articles"]["legacy-p"]
    self.assertEqual((data/"workspace.pre-v0.4.json").read_bytes(),raw)
    self.assertEqual(a["copy"],"旧版当前定稿");self.assertEqual(a["design_ids"],["d"])
    self.assertEqual(a["requirements"][0]["requirement"],"本篇要求")
    self.assertNotIn("copy",state["projects"]["p"])
    self.assertEqual(len(state["projects"]["p"]["rules"]),1)
    self.assertEqual(state["jobs"]["j"]["snapshot"],old["jobs"]["j"]["snapshot"])
    self.assertEqual(state["jobs"]["j"]["result"],old["jobs"]["j"]["result"])
    self.assertEqual(state["jobs"]["j"]["article_id"],"legacy-p")
    self.assertEqual(Store(data).read(),state)
    self.assertEqual(len(list(data.glob('workspace.pre-v0.4*.json'))),1)


def test_17_http402_is_actionable_and_not_retried(self):
    configure(self)
    pid=self.post("/api/project",{"name":"402诊断"})["project_id"]
    self.model.failure=True;self.model.failure_status=402;count=len(self.model.calls)
    try:
        guide=self.upload(pid,"视觉Guide-测试样例.png","guide")
        jid=self.app.store.read()["projects"][pid]["guide_analysis"][guide]["job_id"]
        job=self.wait(jid)
        self.assertEqual(job["status"],"failed");self.assertIn("余额",job["error"])
        self.assertEqual(job["calls"][0]["http_status"],402)
        self.assertEqual(job["calls"][0]["provider_code"],30001)
        self.assertEqual(len(self.model.calls),count+1)
        self.assertNotIn("offline fixture failure",job["error"])
        self.assertIsNone(job["calls"][0]["usage"])
    finally:self.model.failure=False;self.model.failure_status=429


for function in (test_14_one_brand_two_articles_reuse_guide_and_keep_reports,test_15_article_excel_requirements_do_not_modify_brand,test_16_legacy_migration_backs_up_and_preserves_history,test_17_http402_is_actionable_and_not_retried):setattr(ArticleWorkflowTests,function.__name__,function)
del WorkflowTests
