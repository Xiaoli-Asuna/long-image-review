"""Offline shared-office authentication, permissions and lost-update regression."""
import _bootstrap
from concurrent.futures import ThreadPoolExecutor
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib import request,error
from app import Application,Handler
from team import Accounts,AccessError,check_revision,permission,team_handler


class TeamTests(unittest.TestCase):
    def setUp(self):
        base=Path(__file__).resolve().parents[1]/'TestResults';base.mkdir(exist_ok=True)
        self.root=Path(tempfile.mkdtemp(prefix='team-',dir=base))
        self.app=Application(self.root/'ServerData')
        self.accounts=Accounts(self.app.data/'private'/'accounts.sqlite3')
        self.server=ThreadingHTTPServer(('127.0.0.1',0),team_handler(Handler));self.server.app=self.app;self.app.server=self.server
        self.server.accounts=self.accounts;self.server.tls=False;self.server.app_version='0.5.0';self.server.setup_code='offline-setup-code'
        self.host='127.0.0.1:'+str(self.server.server_port);self.url='http://'+self.host
        self.server.allowed_hosts={self.host,'192.168.1.100:'+str(self.server.server_port)}
        threading.Thread(target=self.server.serve_forever,daemon=True).start()
        self.opener=request.build_opener(request.ProxyHandler({}))
        self.password='Offline-Fixture-2026'
        self.accounts.create({'username':'admin','name':'测试管理员','password':self.password},first=True)
        self.admin=self.login('admin')

    def tearDown(self):self.server.shutdown();self.server.server_close();self.accounts.db.close()

    def call(self,path,body=None,session=None,headers=None):
        h={'Origin':self.url,'Content-Type':'application/json'}
        if session:h.update(Cookie='review_team='+session[0],**{'X-Review-Token':session[1]})
        h.update(headers or {})
        req=request.Request(self.url+path,headers=h,data=json.dumps(body).encode() if body is not None else None)
        try:
            with self.opener.open(req) as res:return res.status,json.load(res),dict(res.headers)
        except error.HTTPError as exc:
            try:return exc.code,json.load(exc),dict(exc.headers)
            finally:exc.close()

    def login(self,username):
        status,value,headers=self.call('/api/login',{'username':username,'password':self.password})
        self.assertEqual(status,200,value);token=headers['Set-Cookie'].split(';')[0].split('=',1)[1]
        status,value,_=self.call('/api/state',session=(token,''));self.assertEqual(status,200,value)
        return token,value['csrf']

    def member(self,role):
        status,value,_=self.call('/api/accounts/create',{'username':role,'name':'测试'+role,'role':role,'password':self.password},self.admin)
        self.assertEqual(status,200,value);return self.login(role),value['id']

    def project_article(self):
        status,p,_=self.call('/api/project',{'name':'办公室共享项目'},self.admin);self.assertEqual(status,200,p)
        status,a,_=self.call('/api/article',{'project_id':p['project_id'],'title':'本篇稿件','copy':'定稿一'},self.admin);self.assertEqual(status,200,a)
        return p['project_id'],a['article_id']

    def test_01_unauthenticated_assets_exports_and_global_token_rejected(self):
        for path in ('/api/state','/api/asset?id=x','/api/export?id=x','/api/accounts'):
            self.assertEqual(self.call(path)[0],401)
        self.assertEqual(self.call('/api/state',session=(self.app.token,self.app.token))[0],401)
        self.assertNotIn('csrf',self.call('/api/session')[1])

    def test_02_session_csrf_host_origin_and_logout(self):
        self.assertEqual(self.call('/api/project',{'name':'bad'},(self.admin[0],self.app.token))[0],403)
        self.assertEqual(self.call('/api/project',{'name':'bad'},self.admin,{'Origin':'http://untrusted.invalid'})[0],403)
        self.assertEqual(self.call('/api/state',session=self.admin,headers={'Host':'evil.invalid'})[0],403)
        self.assertEqual(self.call('/api/logout',{},self.admin)[0],200)
        self.assertEqual(self.call('/api/state',session=self.admin)[0],401)

    def test_03_designer_cannot_change_copy_rules_or_model(self):
        designer,_=self.member('designer');pid,aid=self.project_article()
        for path,body in [('/api/article',{'project_id':pid,'id':aid,'copy':'篡改','_revision':1}),('/api/project',{'id':pid,'rules_text':'篡改','_revision':1}),('/api/settings',{}),('/api/assets',{'kind':'guide'}),('/api/jobs',{'kind':'guide'}),('/api/shutdown',{}),('/api/accounts/create',{})]:
            self.assertEqual(self.call(path,body,designer)[0],403,(path,body))
        state=self.call('/api/state',session=designer)[1]
        self.assertEqual(state['articles'][0]['copy'],'定稿一')
        self.assertEqual(set(state['settings']),{'model','provider','has_key'})
        self.assertEqual(self.call('/api/accounts',session=designer)[0],403)

    def test_04_editor_can_write_copy_but_cannot_manage_accounts(self):
        editor,_=self.member('editor');pid,aid=self.project_article()
        a=self.app.store.read()['articles'][aid]
        status,result,_=self.call('/api/article',{'id':aid,'project_id':pid,'copy':'定稿二','_revision':a['revision']},editor)
        self.assertEqual(status,200,result);self.assertEqual(self.app.store.read()['articles'][aid]['copy'],'定稿二')
        self.assertEqual(self.call('/api/accounts/create',{},editor)[0],403)

    def test_05_two_simultaneous_writes_only_one_wins(self):
        pid,aid=self.project_article();revision=self.app.store.read()['articles'][aid]['revision']
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda text:self.call('/api/article',{'id':aid,'project_id':pid,'copy':text,'_revision':revision},self.admin),['甲的新稿','乙的新稿']))
        self.assertEqual(sorted(r[0] for r in results),[200,409])
        self.assertIn(self.app.store.read()['articles'][aid]['copy'],{'甲的新稿','乙的新稿'})
        self.assertEqual(self.app.store.read()['articles'][aid]['revision'],revision+1)

    def test_06_stale_project_or_unversioned_save_rejected(self):
        pid,aid=self.project_article();revision=self.app.store.read()['projects'][pid]['revision']
        body={'id':pid,'name':'修改项目','_revision':revision}
        self.assertEqual(self.call('/api/project',body,self.admin)[0],200)
        self.assertEqual(self.call('/api/project',body,self.admin)[0],409)
        self.assertEqual(self.call('/api/article',{'project_id':pid,'id':aid,'copy':'不带版本'},self.admin)[0],409)

    def test_07_password_change_requires_old_password_and_revokes_session(self):
        status,_,_=self.call('/api/password',{'password':'Changed-Password-2026'},self.admin);self.assertEqual(status,400)
        self.assertEqual(self.call('/api/password',{'old_password':'incorrect','password':'Changed-Password-2026'},self.admin)[0],403)
        self.assertEqual(self.call('/api/password',{'old_password':self.password,'password':'Changed-Password-2026'},self.admin)[0],200)
        self.assertEqual(self.call('/api/state',session=self.admin)[0],401)
        self.assertNotIn(self.password.encode(),(self.app.data/'private'/'accounts.sqlite3').read_bytes())

    def test_08_disable_reset_and_self_lockout_protection(self):
        designer,uid=self.member('designer')
        self.assertEqual(self.call('/api/accounts/update',{'id':uid,'role':'designer','enabled':False},self.admin)[0],200)
        self.assertEqual(self.call('/api/state',session=designer)[0],401)
        admin_uid=self.call('/api/state',session=self.admin)[1]['team']['user']['id']
        self.assertEqual(self.call('/api/accounts/update',{'id':admin_uid,'role':'designer','enabled':False},self.admin)[0],400)
        self.assertEqual(self.call('/api/accounts/reset',{'id':uid,'password':'Reset-Test-Password-2026'},self.admin)[0],200)

    def test_09_restart_keeps_accounts_and_shared_workspace(self):
        pid,aid=self.project_article()
        again=Accounts(self.app.data/'private'/'accounts.sqlite3',clear_sessions=True)
        try:
            self.assertTrue(again.has_users());self.assertIsNone(again.session(self.admin[0]))
            token,csrf,user=again.login('admin',self.password,'127.0.0.1');self.assertEqual(user['role'],'admin')
            self.assertEqual(Application(self.app.data).store.read()['articles'][aid]['copy'],'定稿一')
        finally:again.db.close()

    def test_10_setup_not_repeated_and_rate_limit(self):
        self.assertEqual(self.call('/api/setup',{'setup_code':self.server.setup_code,'username':'second','name':'第二个','password':self.password})[0],409)
        for _ in range(19):self.call('/api/login',{'username':'wrong','password':'wrong-password'})
        self.assertEqual(self.call('/api/login',{'username':'wrong','password':'wrong-password'})[0],429)

    def test_11_api_key_configuration_requires_server_localhost_without_tls(self):
        user={'role':'admin'}
        with self.assertRaises(AccessError):permission(user,'/api/settings',{},local=False,tls=False)
        permission(user,'/api/settings',{},local=True,tls=False)
        permission(user,'/api/settings',{},local=False,tls=True)

    def test_12_stale_review_stops_before_provider_or_queue(self):
        pid,aid=self.project_article()
        status,body,_=self.call('/api/jobs',{'project_id':pid,'article_id':aid,'kind':'review','request_id':'stale-test','_project_revision':-1,'_article_revision':-1},self.admin)
        self.assertEqual(status,409,body);self.assertEqual(len(self.app.store.read()['jobs']),0)

    def test_13_decision_conflict_does_not_overwrite_other_users_note(self):
        job={'id':'job','result':{'findings':[{'id':'F001','decision':'pending','note':'','revision':0}]}}
        self.app.store.edit(lambda s:s['jobs'].update(job=job))
        body={'job_id':'job','finding_id':'F001','decision':'fixed','note':'已修正','_decision_version':0}
        self.assertEqual(self.call('/api/decision',body,self.admin)[0],200)
        self.assertEqual(self.call('/api/decision',body|{'note':'旧页面覆盖'},self.admin)[0],409)
        self.assertEqual(self.app.store.read()['jobs']['job']['result']['findings'][0]['note'],'已修正')


class InitialSetupTests(unittest.TestCase):
    def test_first_account_and_password_hash_reopen(self):
        root=Path(__file__).resolve().parents[1]/'TestResults';root.mkdir(exist_ok=True)
        path=Path(tempfile.mkdtemp(prefix='account-',dir=root))/'accounts.sqlite3'
        accounts=Accounts(path)
        try:
            self.assertFalse(accounts.has_users())
            uid=accounts.create({'username':'first','name':'首个管理员','password':'Initial-Password-2026','role':'designer'},first=True)
            self.assertTrue(accounts.has_users());self.assertEqual(accounts.users()[0]['role'],'admin')
            with self.assertRaises(ValueError):accounts.create({'username':'new','name':'短密码','password':'123','role':'editor'})
            with self.assertRaises(AccessError):accounts.login('first','incorrect','127.0.0.1')
            token,csrf,user=accounts.login('first','Initial-Password-2026','127.0.0.1');self.assertEqual(user['id'],uid)
            self.assertNotIn(b'Initial-Password-2026',path.read_bytes())
        finally:accounts.db.close()


if __name__=='__main__':unittest.main()
