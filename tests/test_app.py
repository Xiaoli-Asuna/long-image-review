import _bootstrap
import base64
import copy
import io
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from urllib import request, error

from app import Application, Handler, Store, dpapi, export_report
from http.server import ThreadingHTTPServer
from media import ingest, prepare_design, normalize
from fixtures import samples, server as fixture_server, COPY, RULE


class WorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        base=Path(__file__).resolve().parents[1]/"TestResults"
        base.mkdir(parents=True,exist_ok=True)
        cls.temp=Path(tempfile.mkdtemp(prefix="run-",dir=base))
        cls.samples=samples(cls.temp/"samples")
        cls.model=fixture_server();threading.Thread(target=cls.model.serve_forever,daemon=True).start()
        cls.app=Application(cls.temp/"Data")
        cls.http=ThreadingHTTPServer(("127.0.0.1",0),Handler);cls.http.app=cls.app;cls.app.server=cls.http
        threading.Thread(target=cls.http.serve_forever,daemon=True).start()
        cls.url=f"http://127.0.0.1:{cls.http.server_port}"
        cls.opener=request.build_opener(request.ProxyHandler({}))
    @classmethod
    def tearDownClass(cls): cls.http.shutdown();cls.http.server_close();cls.model.shutdown();cls.model.server_close()
    def post(self,path,body,*,token=True):
        headers={"Content-Type":"application/json","Cookie":"review_session="+self.app.token}
        if token: headers["X-Review-Token"]=self.app.token
        req=request.Request(self.url+path,data=json.dumps(body).encode(),headers=headers)
        try:
            with self.opener.open(req) as response: return json.load(response)
        except error.HTTPError as exc:
            exc.close()
            raise
    def get(self,path):
        with self.opener.open(request.Request(self.url+path,headers={"Cookie":"review_session="+self.app.token})) as response:return response.read()
    def wait(self,jid):
        deadline=time.monotonic()+8
        while time.monotonic()<deadline:
            job=self.app.store.read()["jobs"][jid]
            if job["status"] not in {"running","queued"}: return job
            time.sleep(.02)
        self.fail("fixture job timeout")
    def upload(self,pid,name,kind,article_id=None):
        return self.post("/api/assets",{"project_id":pid,"article_id":article_id,"kind":kind,"name":name,"data":base64.b64encode((self.samples/name).read_bytes()).decode()})["asset_id"]

    def test_01_complete_upload_guide_copy_design_review_and_export(self):
        self.post("/api/settings",{"provider":"local","base_url":f"http://127.0.0.1:{self.model.server_port}/v1","model":"offline-fixture-vision","api_key":"offline-dummy","remember_key":True,"disable_thinking":False})
        self.assertIn("已连接",self.post("/api/connection",{})["message"])
        pid=self.post("/api/project",{"name":"完整流程测试"})["project_id"]
        article=self.post("/api/article",{"project_id":pid,"title":"完整流程稿件"})["article_id"]
        self.upload(pid,"视觉Guide-测试样例.pdf","guide")
        self.upload(pid,"标准LOGO.png","logo")
        data={"project_id":pid,"kind":"guide","request_id":"guide-idempotency"}
        jid=self.post("/api/jobs",data)["job_id"]
        guide=self.wait(jid);self.assertEqual(guide["status"],"completed",guide.get("error"))
        n=len(self.model.calls)
        self.assertEqual(self.post("/api/jobs",data)["job_id"],jid)
        self.assertEqual(len(self.model.calls),n)
        self.post("/api/article",{"id":article,"project_id":pid,"copy":COPY})
        self.post("/api/confirm",{"project_id":pid})
        aid=self.upload(pid,"设计长图-含3处已知问题.png","design",article)
        job=self.wait(self.post("/api/jobs",{"project_id":pid,"article_id":article,"asset_id":aid,"kind":"review","request_id":"review-idempotency"})["job_id"])
        self.assertEqual(job["status"],"completed",job.get("error"))
        self.assertEqual(len(job["result"]["findings"]),3)
        self.assertEqual(job["result"]["coverage"]["font_file_identity"]["status"],"not_checked")
        self.assertEqual(job["result"]["copy"],COPY)
        self.post("/api/decision",{"job_id":job["id"],"finding_id":"F001","decision":"false_positive","note":"测试意见"})
        exported=self.get("/api/export?id="+job["id"]).decode()
        self.assertIn("测试意见",exported);self.assertIn("data:image/jpeg;base64,",exported)
        self.post("/api/article",{"id":article,"project_id":pid,"copy":"新文案"})
        self.assertEqual(self.app.store.read()["jobs"][job["id"]]["result"]["copy"],COPY)
        self.assertNotIn("offline-dummy",self.get("/api/state").decode())
        self.assertNotIn("offline-dummy",self.app.store.path.read_text("utf-8"))
        self.assertNotIn(b"offline-dummy",self.app.secret_file.read_bytes())
        self.assertEqual(dpapi(self.app.secret_file.read_bytes(),True).decode(),"offline-dummy")

    def test_02_no_csrf_or_wrong_host_is_rejected(self):
        with self.assertRaises(error.HTTPError) as caught:self.post("/api/project",{"name":"拒绝"},token=False)
        self.assertEqual(caught.exception.code,403)
        req=request.Request(self.url+"/api/state",headers={"Host":"untrusted.invalid","Cookie":"review_session="+self.app.token})
        with self.assertRaises(error.HTTPError) as caught:self.opener.open(req)
        self.assertEqual(caught.exception.code,403)

    def test_03_review_requires_confirmed_rules_and_copy(self):
        pid=self.post("/api/project",{"name":"未准备项目"})["project_id"]
        article=self.post("/api/article",{"project_id":pid,"title":"未准备稿件"})["article_id"]
        with self.assertRaises(error.HTTPError):self.post("/api/jobs",{"project_id":pid,"article_id":article,"kind":"review","request_id":"no-baseline"})

    def test_04_file_validation_and_docx_text(self):
        with self.assertRaises(ValueError):ingest(self.temp/"file-test",name="fake.png",raw=b"not image",kind="design")
        import zipfile
        buf=io.BytesIO()
        with zipfile.ZipFile(buf,"w") as archive:archive.writestr("word/document.xml",'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>第一行</w:t></w:r></w:p><w:p><w:r><w:t>第二行</w:t></w:r></w:p></w:body></w:document>')
        value=ingest(self.temp/"file-test",name="copy.docx",raw=buf.getvalue(),kind="guide")
        self.assertEqual(value["text"],"第一行\n第二行")

    def test_05_store_reload_and_unknown_job_recovery(self):
        data=self.temp/"reload"
        app=Application(data)
        app.store.edit(lambda s:s["jobs"].update({"interrupted":{"status":"running","calls":[]}}))
        again=Application(data)
        self.assertEqual(again.store.read()["jobs"]["interrupted"]["status"],"interrupted")

    def test_06_text_normalization_preserves_punctuation(self):
        self.assertEqual(normalize("ＡＢＣ\n １２３"),"ABC123")
        self.assertNotEqual(normalize("免费。"),normalize("免费！"))

    def test_07_http_failure_stops_without_automatic_retry(self):
        pid=self.post("/api/project",{"name":"失败留证"})["project_id"]
        self.model.failure=True
        count=len(self.model.calls)
        try:
            aid=self.upload(pid,"视觉Guide-测试样例.png","guide")
            jid=self.app.store.read()["projects"][pid]["guide_analysis"][aid]["job_id"]
            job=self.wait(jid)
            self.assertEqual(job["status"],"failed")
            self.assertEqual(len(self.model.calls),count+1)
            self.assertEqual(job["calls"][0]["status"],"http_error")
            self.assertIsNone(job["calls"][0]["usage"])
        finally:self.model.failure=False

    def test_08_detach_reference_preserves_unrelated_manual_rules(self):
        pid=self.post("/api/project",{"name":"移除参考图保护规范","rules_text":"人工规则甲\n人工规则乙"})["project_id"]
        self.app.store.edit(lambda s:s["projects"][pid].update(logo_ids=["synthetic-reference-id"]))
        self.post("/api/detach",{"project_id":pid,"asset_id":"synthetic-reference-id"})
        self.assertEqual(len(self.app.store.read()["projects"][pid]["rules"]),2)


if __name__=="__main__":unittest.main()
