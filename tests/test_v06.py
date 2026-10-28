"""Self-registration and SSE/503 offline contracts. No real provider/key involved."""
import _bootstrap
import io
import base64
import json
from http.server import ThreadingHTTPServer
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib import request,error
from app import Application,Handler
from team import Accounts,AccessError,team_handler,permission
from provider import ProviderConfig,ProviderError,VisionJsonProvider,read_json_stream
from pipeline import Engine


class RegistrationTests(unittest.TestCase):
    def setUp(self):
        base=Path(__file__).resolve().parents[1]/'TestResults';base.mkdir(exist_ok=True)
        self.root=Path(tempfile.mkdtemp(prefix='registration-',dir=base))
        self.app=Application(self.root/'ServerData');self.accounts=Accounts(self.app.data/'private'/'accounts.sqlite3')
        self.accounts.create({'username':'admin','name':'管理员夹具','password':'Offline-Fixture-2026'},first=True)
        self.server=ThreadingHTTPServer(('127.0.0.1',0),team_handler(Handler));self.server.app=self.app;self.app.server=self.server
        self.server.accounts=self.accounts;self.server.tls=False;self.server.app_version='0.6.0';self.server.setup_code='offline-setup'
        self.host='127.0.0.1:'+str(self.server.server_port);self.url='http://'+self.host;self.server.allowed_hosts={self.host}
        threading.Thread(target=self.server.serve_forever,daemon=True).start();self.opener=request.build_opener(request.ProxyHandler({}))

    def tearDown(self):self.server.shutdown();self.server.server_close();self.accounts.db.close()

    def call(self,path,body=None,session=None,origin=None):
        headers={'Content-Type':'application/json','Origin':origin or self.url}
        if session:headers.update(Cookie='review_team='+session[0],**{'X-Review-Token':session[1]})
        req=request.Request(self.url+path,headers=headers,data=json.dumps(body).encode() if body is not None else None)
        try:
            with self.opener.open(req) as r:return r.status,json.load(r),dict(r.headers)
        except error.HTTPError as e:
            try:return e.code,json.load(e),dict(e.headers)
            finally:e.close()

    def register(self,role='designer',**extras):
        body={'username':'staff_'+role,'password':'New-Staff-2026','role':role}|extras
        return self.call('/api/register',body)

    def test_01_three_positions_need_only_username_password_position(self):
        for role in ('director','operator','designer'):
            with self.subTest(role=role):
                status,value,headers=self.register(role)
                self.assertEqual(status,200,value)
                token=headers['Set-Cookie'].split(';')[0].split('=',1)[1]
                status,state,_=self.call('/api/state',session=(token,''));self.assertEqual(status,200)
                self.assertEqual(state['team']['user']['role'],role)
                self.assertEqual(state['team']['user']['name'],'staff_'+role)
                self.assertTrue(state['team']['user']['enabled'])
                self.assertTrue(state['csrf']);self.assertNotIn('New-Staff-2026',json.dumps(state))

    def test_02_register_cannot_choose_admin_or_legacy_or_malformed_role(self):
        for role in ('admin','editor','root',None,['admin'],True):
            status,value,_=self.call('/api/register',{'username':'attempt','password':'New-Staff-2026','role':role})
            self.assertEqual(status,400,(role,value))
        self.assertEqual(len(self.accounts.users()),1)

    def test_03_public_fields_cannot_elevate_or_disable_or_change_display_identity(self):
        status,value,headers=self.register('designer',name='管理员',enabled=False,is_admin=True,permissions=['admin'])
        self.assertEqual(status,200,value)
        user=self.accounts.users()[-1];self.assertEqual(user['name'],'staff_designer');self.assertEqual(user['role'],'designer');self.assertTrue(user['enabled'])

    def test_04_duplicate_username_does_not_replace_password_or_role(self):
        self.assertEqual(self.register('designer')[0],200)
        self.assertEqual(self.register('operator',username='STAFF_DESIGNER',password='Other-Password-2026')[0],400)
        _,_,user=self.accounts.login('staff_designer','New-Staff-2026','127.0.0.1');self.assertEqual(user['role'],'designer')
        self.assertEqual(len(self.accounts.users()),2)

    def test_05_no_cross_site_registration(self):
        before=len(self.accounts.users())
        self.assertEqual(self.call('/api/register',{'username':'foreign','password':'New-Staff-2026','role':'operator'},origin='http://foreign.invalid')[0],403)
        self.assertEqual(len(self.accounts.users()),before)

    def test_06_registration_does_not_take_first_administrator(self):
        separate=Accounts(self.root/'new-install.sqlite3')
        try:
            with self.assertRaises(AccessError):separate.register({'username':'staff','password':'New-Staff-2026','role':'director'})
            self.assertFalse(separate.has_users())
        finally:separate.db.close()

    def test_07_registered_positions_keep_server_permissions(self):
        for role in ('director','operator'):
            self.register(role);user=self.accounts.users()[-1]
            permission(user,'/api/article',{});permission(user,'/api/confirm',{})
            with self.assertRaises(AccessError):permission(user,'/api/settings',{},local=True)
        self.register('designer');user=self.accounts.users()[-1]
        permission(user,'/api/assets',{'kind':'design'})
        with self.assertRaises(AccessError):permission(user,'/api/article',{})

    def test_08_legacy_editor_and_old_account_hash_are_preserved(self):
        uid=self.accounts.create({'username':'legacy_editor','name':'旧编导','password':'Existing-Password-2026','role':'editor'})
        before=tuple(self.accounts.db.execute('SELECT salt,digest FROM users WHERE id=?',(uid,)).fetchone())
        self.register('operator')
        after=tuple(self.accounts.db.execute('SELECT salt,digest FROM users WHERE id=?',(uid,)).fetchone())
        self.assertEqual(before,after)
        _,_,old=self.accounts.login('legacy_editor','Existing-Password-2026','127.0.0.1');self.assertEqual(old['role'],'editor')

    def test_09_session_lists_exactly_three_self_registration_positions(self):
        status,state,_=self.call('/api/session');self.assertEqual(status,200);self.assertTrue(state['registration_open'])
        self.assertEqual(state['positions'],{'director':'编导','operator':'运营','designer':'设计'})

    def test_10_registration_success_not_blocked_by_other_login_attempts(self):
        import time
        self.accounts.attempts['ip:127.0.0.1'].extend([time.time()]*20)
        status,value,_=self.register('operator');self.assertEqual(status,200,value)


def sse(*chunks,done=True):
    return b''.join(('data: '+json.dumps(c,ensure_ascii=False)+'\r\n\r\n').encode('utf-8') for c in chunks)+(b'data: [DONE]\n\n' if done else b'')


class StreamTests(unittest.TestCase):
    def test_complete_unicode_and_usage_chunks_without_choices(self):
        data=sse({'id':'response-fixture','choices':[{'delta':{'role':'assistant','content':'{"text":"中'},'finish_reason':None}]},
                 {'choices':[{'delta':{'content':'文"}'},'finish_reason':None}]},
                 {'choices':[{'delta':{},'finish_reason':'stop'}]},
                 {'choices':[],'usage':{'prompt_tokens':100,'completion_tokens':5}})
        envelope=read_json_stream(io.BytesIO(data));self.assertEqual(json.loads(envelope['choices'][0]['message']['content']),{'text':'中文'})
        self.assertEqual(envelope['usage']['prompt_tokens'],100)

    def test_no_usage_is_unknown(self):
        envelope=read_json_stream(io.BytesIO(sse({'choices':[{'delta':{'content':'{}'},'finish_reason':'stop'}]})))
        self.assertIsNone(envelope['usage'])

    def test_reasoning_is_not_added_to_json_output(self):
        envelope=read_json_stream(io.BytesIO(sse({'choices':[{'delta':{'reasoning_content':'private thought','content':'{}'},'finish_reason':'stop'}]})))
        self.assertEqual(envelope['choices'][0]['message']['content'],'{}');self.assertNotIn('private thought',json.dumps(envelope))

    def test_partial_stream_and_length_reason_never_become_success(self):
        for finish in (None,'length','content_filter'):
            with self.assertRaises(ProviderError) as caught:read_json_stream(io.BytesIO(sse({'choices':[{'delta':{'content':'{"partial":'},'finish_reason':finish}]})))
            self.assertEqual(caught.exception.outcome,'incomplete')

    def test_stream_error_even_after_stop_discards_partial_result(self):
        data=sse({'choices':[{'delta':{'content':'{}'},'finish_reason':'stop'}]},{'error':{'code':503,'message':'private prompt offline-secret'}})
        with self.assertRaises(ProviderError) as caught:read_json_stream(io.BytesIO(data))
        self.assertEqual(caught.exception.outcome,'stream_error');self.assertEqual(caught.exception.provider_code,503)
        self.assertNotIn('private prompt',str(caught.exception));self.assertIsNone(caught.exception.http_status)

    def test_invalid_data_comment_and_size_limit(self):
        data=b': keepalive\n\n'+sse({'choices':[{'delta':{'content':'{}'},'finish_reason':'stop'}]})
        self.assertEqual(read_json_stream(io.BytesIO(data))['choices'][0]['message']['content'],'{}')
        for bad in (b'data: not-json\n\n',b'data: \xff\n\n',b'not-sse\n',b'data: '+b'x'*(4*1024*1024),sse({'choices':[{'delta':{'content':'{}'},'finish_reason':'stop'}]},{'code':50505,'message':'private service failure'})):
            with self.assertRaises(ProviderError):read_json_stream(io.BytesIO(bad))

    def test_transport_sends_stream_flag_and_validates_final_json(self):
        provider=VisionJsonProvider(ProviderConfig('local','http://127.0.0.1:8000/v1','fixture',stream_mode=True))
        class Reply(io.BytesIO):
            headers={'Content-Type':'text/event-stream'}
        response=Reply(sse({'choices':[{'delta':{'content':'{"valid":true}'},'finish_reason':'stop'}]}))
        with patch.object(provider._opener,'open',return_value=response) as opened:
            result=provider.generate_json(task='test',payload={},images=[],schema={})
        self.assertTrue(result.value['valid']);self.assertTrue(json.loads(opened.call_args.args[0].data)['stream'])
        self.assertEqual(opened.call_count,1)

    def test_http503_retry_after_is_safe_and_not_automatically_retried(self):
        provider=VisionJsonProvider(ProviderConfig('local','http://127.0.0.1:8000/v1','fixture'))
        e=error.HTTPError('http://127.0.0.1:8000/v1/chat/completions',503,'unavailable',{'Retry-After':'30','x-siliconcloud-trace-id':'trace-fixture-2026'},io.BytesIO(b'{"code":50505,"message":"private prompt offline-secret"}'))
        with patch.object(provider._opener,'open',side_effect=e) as opened:
            with self.assertRaises(ProviderError) as caught:provider.generate_json(task='test',payload={},images=[],schema={})
        self.assertEqual(opened.call_count,1);self.assertEqual(caught.exception.retry_after_seconds,30)
        self.assertEqual(caught.exception.provider_code,50505);self.assertEqual(caught.exception.trace_id,'trace-fixture-2026')
        self.assertNotIn('offline-secret',str(caught.exception))

    def test_pipeline503_records_stage_metadata_and_has_actionable_message(self):
        class App:
            def __init__(self):self.updates=[]
            def job_update(self,jid,**kwargs):self.updates.append(kwargs)
        app=App();engine=Engine(app,{'id':'offline-job','snapshot':{}},ProviderConfig('local','http://127.0.0.1:8000/v1','fixture'))
        with patch.object(engine.provider,'generate_json',side_effect=ProviderError('safe failure',outcome='http_error',http_status=503,trace_id='trace-fixture-2026',retry_after_seconds=30)) as called:
            with self.assertRaises(ValueError) as caught:engine.ask('ocr_design',{},[],{})
        self.assertIn('设计图文字识别',str(caught.exception));self.assertIn('流式传输',str(caught.exception));self.assertIn('30秒',str(caught.exception))
        self.assertEqual(called.call_count,1);self.assertEqual(engine.calls[0]['http_status'],503);self.assertEqual(engine.calls[0]['image_count'],0)


class StreamWorkflowTests(unittest.TestCase):
    def test_complete_guide_ocr_and_visual_review_over_real_loopback_sse(self):
        from fixtures import FixtureHandler,samples,COPY,RULE
        class StreamingFixture(FixtureHandler):
            def emit(self,value,status=200):
                if status!=200 or not isinstance(value,dict) or 'choices' not in value:return super().emit(value,status)
                text=value['choices'][0]['message']['content']
                chunks=[{'id':'sse-fixture','choices':[{'delta':{'content':text[i:i+80]},'finish_reason':None}]} for i in range(0,len(text),80)]
                chunks.extend([{'choices':[{'delta':{},'finish_reason':'stop'}]},{'choices':[],'usage':value['usage']}])
                raw=sse(*chunks)
                self.send_response(200);self.send_header('Content-Type','text/event-stream');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
        model=ThreadingHTTPServer(('127.0.0.1',0),StreamingFixture);model.calls=[]
        threading.Thread(target=model.serve_forever,daemon=True).start()
        base=Path(__file__).resolve().parents[1]/'TestResults';base.mkdir(exist_ok=True)
        root=Path(tempfile.mkdtemp(prefix='sse-workflow-',dir=base))
        app=Application(root/'Data');assets=samples(root/'samples')
        def wait(jid):
            end=time.monotonic()+10
            while time.monotonic()<end:
                job=app.store.read()['jobs'][jid]
                if job['status'] not in {'queued','running'}:return job
                time.sleep(.02)
            raise AssertionError('SSE fixture job timed out')
        try:
            app.save_settings({'provider':'local','base_url':f'http://127.0.0.1:{model.server_port}/v1','model':'offline-fixture-vision','api_key':'','remember_key':False,'disable_thinking':False,'stream_mode':True})
            pid=app.post('/api/project',{'name':'SSE完整流程虚构项目'})['project_id']
            guide=app.post('/api/assets',{'project_id':pid,'kind':'guide','name':'视觉Guide-测试样例.png','data':base64.b64encode((assets/'视觉Guide-测试样例.png').read_bytes()).decode()})
            parsed=wait(guide['job_id']);self.assertEqual(parsed['status'],'completed',parsed.get('error'))
            self.assertEqual(parsed['calls'][0]['usage']['prompt_tokens'],1234)
            aid=app.post('/api/article',{'project_id':pid,'title':'SSE完整稿件','copy':COPY})['article_id']
            app.post('/api/confirm',{'project_id':pid})
            image=app.post('/api/assets',{'project_id':pid,'article_id':aid,'kind':'design','name':'设计长图-含3处已知问题.png','data':base64.b64encode((assets/'设计长图-含3处已知问题.png').read_bytes()).decode()})['asset_id']
            report=wait(app.create_job({'project_id':pid,'article_id':aid,'kind':'review','asset_id':image,'request_id':'offline-sse-whole-review'})['job_id'])
            self.assertEqual(report['status'],'completed',report.get('error'));self.assertEqual(len(report['result']['findings']),3)
            self.assertTrue(all(c['stream_mode'] and c['status']=='received' for c in report['calls']))
            self.assertEqual([c['task'] for c in report['calls']],['ocr_design','ocr_design','review_visual'])
        finally:model.shutdown();model.server_close()

    def test_old_api_settings_preserved_until_stream_option_saved(self):
        base=Path(__file__).resolve().parents[1]/'TestResults';base.mkdir(exist_ok=True)
        app=Application(Path(tempfile.mkdtemp(prefix='old-config-',dir=base)))
        app.store.edit(lambda s:s['settings'].pop('stream_mode',None))
        app.save_settings({'provider':'local','base_url':'http://127.0.0.1:8000/v1','model':'old-model','api_key':'','stream_mode':False})
        before=app.store.read()['settings'].copy();self.assertFalse(app.config().stream_mode)
        app.save_settings({'stream_mode':True})
        self.assertTrue(app.config().stream_mode)
        after=app.store.read()['settings'];self.assertEqual({k:v for k,v in before.items() if k!='stream_mode'},{k:v for k,v in after.items() if k!='stream_mode'})


if __name__=='__main__':unittest.main()
