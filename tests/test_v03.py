"""Offline feature contracts. Synthetic fixture responses are not AI accuracy evidence."""
import _bootstrap
import copy
import io
from pathlib import Path
import unittest
import zipfile

from media import crop_logo, ingest
from pipeline import Engine
from workbook import read_workbook
from test_app import WorkflowTests


def xlsx_fixture():
    # Minimal OOXML for parser tests; no spreadsheet application or formula engine.
    out=io.BytesIO()
    with zipfile.ZipFile(out,"w") as z:
        z.writestr("xl/workbook.xml",'<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="机制图" sheetId="1" r:id="r1"/><sheet name="礼包图" sheetId="2" r:id="r2"/></sheets></workbook>')
        z.writestr("xl/_rels/workbook.xml.rels",'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="r1" Target="worksheets/sheet1.xml"/><Relationship Id="r2" Target="worksheets/sheet2.xml"/></Relationships>')
        z.writestr("xl/worksheets/sheet1.xml",'<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>活动免费</t></is></c><c r="D1" t="inlineStr"><is><t>这里放KV素材</t></is></c></row></sheetData></worksheet>')
        z.writestr("xl/worksheets/sheet2.xml",'<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="C1" t="inlineStr"><is><t>版块标题</t></is></c><c r="D1" t="inlineStr"><is><t>内容文案</t></is></c><c r="H1" t="inlineStr"><is><t>备注</t></is></c></row><row r="2"><c r="C2" t="inlineStr"><is><t>礼包介绍</t></is></c><c r="D2" t="inlineStr"><is><t>售价829VP</t></is></c><c r="H2" t="inlineStr"><is><t>素材私聊领取</t></is></c></row></sheetData></worksheet>')
    return out.getvalue()


class GuideAndWorkbookTests(WorkflowTests):
    # Reuse only the harness; do not run inherited baseline test methods twice.
    pass

for key in list(WorkflowTests.__dict__):
    if key.startswith("test_"):setattr(GuideAndWorkbookTests,key,None)


def test_09_auto_parse_selects_only_positive_logo(self):
    self.post("/api/settings",{"provider":"local","base_url":f"http://127.0.0.1:{self.model.server_port}/v1","model":"offline-fixture-vision","api_key":"","remember_key":False})
    pid=self.post("/api/project",{"name":"自动解析","rules_text":"保留手动审核要求"})["project_id"]
    aid=self.upload(pid,"视觉Guide-测试样例.png","guide")
    jid=self.app.store.read()["projects"][pid]["guide_analysis"][aid]["job_id"]
    job=self.wait(jid);self.assertEqual(job["status"],"completed",job.get("error"))
    p=self.app.store.read()["projects"][pid]
    self.assertEqual(len(p["extracted_logo_ids"]),2);self.assertEqual(len(p["logo_ids"]),1)
    self.assertEqual(p["rules"][0]["requirement"],"保留手动审核要求")
    self.assertFalse(p["confirmed"])
    selected=self.app.store.read()["assets"][p["logo_ids"][0]]
    self.assertEqual(selected["derived_from"]["asset_id"],aid)
    self.assertEqual(selected["derived_from"]["usage"],"approved")
    self.assertTrue((self.app.data/"assets"/selected["id"]/selected["original"]).is_file())
    self.post("/api/logo-selection",{"project_id":pid,"asset_id":selected["id"],"enabled":False})
    self.assertFalse(self.app.store.read()["projects"][pid]["logo_ids"])
    self.post("/api/detach",{"project_id":pid,"asset_id":aid})
    p=self.app.store.read()["projects"][pid]
    self.assertFalse(p["extracted_logo_ids"]);self.assertEqual(len(p["rules"]),1)


def test_10_invalid_logo_source_is_not_applied(self):
    pid=self.post("/api/project",{"name":"无效LOGO来源"})["project_id"]
    self.model.invalid_logo=True
    try:
        aid=self.upload(pid,"视觉Guide-测试样例.png","guide")
        jid=self.app.store.read()["projects"][pid]["guide_analysis"][aid]["job_id"]
        self.assertEqual(self.wait(jid)["status"],"failed")
        self.assertFalse(self.app.store.read()["projects"][pid].get("extracted_logo_ids"))
    finally:self.model.invalid_logo=False


def test_11_workbook_preview_then_adopt_one_sheet(self):
    import base64
    pid=self.post("/api/project",{"name":"需求表选择"})["project_id"]
    article=self.post("/api/article",{"project_id":pid,"title":"需求表稿件","copy":"原定稿保留"})["article_id"]
    result=self.post("/api/import-copy",{"project_id":pid,"article_id":article,"name":"虚构测试需求.xlsx","data":base64.b64encode(xlsx_fixture()).decode()})
    self.assertTrue(result["draft"])
    p=self.app.store.read()["articles"][article];self.assertEqual(p["copy"],"原定稿保留")
    draft=p["copy_draft"];self.assertEqual(len(draft["sheets"]),2)
    cells=draft["sheets"][1]["cells"]
    self.assertEqual(next(c for c in cells if c["cell"]=="H2")["role"],"note")
    chosen=[{"id":c["id"],"text":c["text"]} for c in cells if c["role"]=="copy"]
    note=next(c for c in cells if c["cell"]=="H2")
    payload={"project_id":pid,"article_id":article,"asset_id":draft["asset_id"],"sheet":"礼包图","selected":chosen,"requirements":[{"id":note["id"],"text":"挂饰需展示两种状态"}]}
    self.post("/api/adopt-copy",payload)
    p=self.app.store.read()["articles"][article]
    self.assertEqual(len(p["copy_blocks"]),2);self.assertNotIn("素材私聊",p["copy"])
    self.assertNotIn("活动免费",p["copy"])
    self.assertIn("!D2",p["copy_blocks"][1]["source_label"])
    self.assertEqual(p["requirements"][0]["requirement"],"挂饰需展示两种状态")
    self.assertNotIn("挂饰需展示",p["copy"])
    self.post("/api/article",{"id":article,"project_id":pid,"copy":"人工更新定稿"})
    self.assertEqual(self.app.store.read()["articles"][article]["copy_blocks"],[])
    self.post("/api/adopt-copy",payload)


def test_12_blocks_allow_reorder_background_text_and_report_cell(self):
    engine=object.__new__(Engine)
    engine.project={"copy_blocks":[{"text":"礼包介绍","source_label":"需求表!C2"},{"text":"售价829VP","source_label":"需求表!D2"}]}
    lines=[{"text":"游戏HUD额外文字","box":[0,0,.4,.1]},{"text":"售价829VP","box":[0,.2,.5,.3]},{"text":"礼包介绍","box":[0,.4,.5,.5]}]
    self.assertEqual(engine.copy_diff(lines),[])
    lines[1]["text"]="售价899VP"
    result=engine.copy_diff(lines)
    self.assertEqual(len(result),1);self.assertEqual(result[0]["source_label"],"需求表!D2")
    self.assertEqual(result[0]["expected"],"售价829VP")


def test_13_late_parse_cannot_overwrite_changed_source_rule(self):
    pid=self.post("/api/project",{"name":"保护解析期间人工编辑"})["project_id"]
    aid=self.upload(pid,"视觉Guide-测试样例.png","guide")
    jid=self.app.store.read()["projects"][pid]["guide_analysis"][aid]["job_id"]
    old=self.wait(jid)
    replay=copy.deepcopy(old);replay["snapshot"]=copy.deepcopy(self.app.store.read()["projects"][pid])
    self.post("/api/project",{"id":pid,"rules_text":"人工重写后的要求"})
    applied=self.app.store.edit(lambda s:self.app.apply_guide_result(s,replay,replay["result"]))
    self.assertFalse(applied)
    self.assertEqual(self.app.store.read()["projects"][pid]["rules"][0]["requirement"],"人工重写后的要求")


for function in (test_09_auto_parse_selects_only_positive_logo,test_10_invalid_logo_source_is_not_applied,test_11_workbook_preview_then_adopt_one_sheet,test_12_blocks_allow_reorder_background_text_and_report_cell,test_13_late_parse_cannot_overwrite_changed_source_rule):
    setattr(GuideAndWorkbookTests,function.__name__,function)

# Do not expose the imported harness as a second discovered TestCase.
del WorkflowTests
