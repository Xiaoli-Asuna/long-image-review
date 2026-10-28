"""Single-office shared mode. One process owns the workspace; no proxy headers trusted."""
from __future__ import annotations
from collections import defaultdict, deque
import getpass
import hashlib
from http.cookies import SimpleCookie
from http.server import ThreadingHTTPServer
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import ssl
import threading
import time
from urllib import parse, request
import webbrowser

ROLES={"admin":"管理员","director":"编导","operator":"运营","designer":"设计","editor":"编导 / 运营（旧账号）"}
SELF_REGISTER_ROLES={"director":"编导","operator":"运营","designer":"设计"}
OFFICE_NETS=tuple(ipaddress.ip_network(n) for n in ("10.0.0.0/8","172.16.0.0/12","192.168.0.0/16","127.0.0.0/8","fc00::/7","::1/128"))


class AccessError(ValueError):
    def __init__(self,message,status=403):super().__init__(message);self.status=status


def is_local(address):
    try:return ipaddress.ip_address(address).is_loopback
    except ValueError:return False


def password_hash(password,salt):
    return hashlib.pbkdf2_hmac("sha256",password.encode("utf-8"),bytes.fromhex(salt),600_000).hex()


def password_valid(password):
    if not isinstance(password,str) or not 10<=len(password)<=128:raise ValueError("密码请设置为10至128个字符。")


class Accounts:
    def __init__(self,path,clear_sessions=False):
        path.parent.mkdir(parents=True,exist_ok=True)
        self.db=sqlite3.connect(path,check_same_thread=False)
        self.db.row_factory=sqlite3.Row
        self.lock=threading.RLock()
        self.attempts=defaultdict(deque)
        with self.db:
            self.db.executescript('''
            CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL,
                name TEXT NOT NULL, role TEXT NOT NULL, salt TEXT NOT NULL, digest TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1, created REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS sessions(digest TEXT PRIMARY KEY, user_id TEXT NOT NULL,
                csrf TEXT NOT NULL, expires REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY, created REAL NOT NULL,
                user_id TEXT NOT NULL, action TEXT NOT NULL, target TEXT NOT NULL);
            ''')
            if clear_sessions:self.db.execute("DELETE FROM sessions")
        self.dummy_salt=secrets.token_hex(16)

    @staticmethod
    def public(row):return {k:row[k] for k in ("id","username","name","role","enabled")}

    def has_users(self):
        with self.lock:return bool(self.db.execute("SELECT 1 FROM users LIMIT 1").fetchone())

    def users(self):
        with self.lock:return [self.public(r) for r in self.db.execute("SELECT * FROM users ORDER BY created")]

    def create(self,body,first=False):
        username=str(body.get("username","")).strip().lower()
        name=str(body.get("name","")).strip()
        role="admin" if first else body.get("role")
        password=body.get("password")
        if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{2,39}",username):raise ValueError("账号使用3至40位英文字母、数字、点、下划线或短横线。")
        if not 1<=len(name)<=40 or role not in ROLES:raise ValueError("请填写姓名并选择账号角色。")
        password_valid(password)
        salt=secrets.token_hex(16);digest=password_hash(password,salt);uid=secrets.token_hex(16)
        with self.lock,self.db:
            if first and self.has_users():raise AccessError("管理员已创建，请登录。",409)
            try:self.db.execute("INSERT INTO users VALUES(?,?,?,?,?,?,1,?)",(uid,username,name,role,salt,digest,time.time()))
            except sqlite3.IntegrityError:raise ValueError("此账号已存在。") from None
        return uid

    def login(self,username,password,address):
        username=str(username).strip().lower()[:100]
        if not isinstance(password,str) or len(password)>128:raise AccessError("账号或密码不正确。",401)
        now=time.time()
        with self.lock:
            # Bound unauthenticated state, even if many distinct names are submitted.
            for key in list(self.attempts):
                while self.attempts[key] and self.attempts[key][0]<now-300:self.attempts[key].popleft()
                if not self.attempts[key]:del self.attempts[key]
            keys=("ip:"+address,"name:"+username)
            if len(self.attempts)>4096 or any(len(self.attempts[k])>=20 for k in keys):raise AccessError("登录尝试过多，请5分钟后再试。",429)
            for key in keys:self.attempts[key].append(now)
            row=self.db.execute("SELECT * FROM users WHERE username=?",(username,)).fetchone()
            digest=password_hash(password,row["salt"] if row else self.dummy_salt)
            if not row or not secrets.compare_digest(digest,row["digest"]) or not row["enabled"]:raise AccessError("账号或密码不正确。",401)
            return self.start_session(row["id"])

    def start_session(self,uid):
        """Internal only: called after password verification or successful registration."""
        with self.lock,self.db:
            row=self.db.execute("SELECT * FROM users WHERE id=? AND enabled=1",(uid,)).fetchone()
            if not row:raise AccessError("账号不可用。",401)
            now=time.time();token=secrets.token_urlsafe(32);csrf=secrets.token_urlsafe(32)
            self.db.execute("DELETE FROM sessions WHERE expires<?",(now,))
            sessions=self.db.execute("SELECT digest FROM sessions WHERE user_id=? ORDER BY expires DESC",(uid,)).fetchall()
            for old in sessions[9:]:self.db.execute("DELETE FROM sessions WHERE digest=?",(old[0],))
            self.db.execute("INSERT INTO sessions VALUES(?,?,?,?)",(hashlib.sha256(token.encode()).hexdigest(),uid,csrf,now+12*3600))
            return token,csrf,self.public(row)

    def register(self,body):
        role=body.get("role")
        if not isinstance(role,str) or role not in SELF_REGISTER_ROLES:raise ValueError("请选择编导、运营或设计；管理员账号不能自助注册。")
        username=body.get("username")
        if not isinstance(username,str):raise ValueError("请填写用户名。")
        with self.lock:
            if not self.has_users():raise AccessError("服务器尚未初始化，请管理员先在服务器本机创建管理员。",409)
            return self.create({"username":username,"name":username.strip().lower(),"password":body.get("password"),"role":role})

    def session(self,token):
        if not token or len(token)>100:return None
        with self.lock:
            row=self.db.execute("SELECT u.*,s.csrf FROM sessions s JOIN users u ON s.user_id=u.id WHERE s.digest=? AND s.expires>? AND u.enabled=1",(hashlib.sha256(token.encode()).hexdigest(),time.time())).fetchone()
            return (self.public(row),row["csrf"]) if row else None

    def logout(self,token):
        with self.lock,self.db:self.db.execute("DELETE FROM sessions WHERE digest=?",(hashlib.sha256(token.encode()).hexdigest(),))

    def password(self,uid,new_password,old_password=None):
        password_valid(new_password)
        with self.lock,self.db:
            row=self.db.execute("SELECT * FROM users WHERE id=?",(uid,)).fetchone()
            if not row:raise ValueError("账号不存在。")
            if old_password is not None and (not isinstance(old_password,str) or len(old_password)>128 or not secrets.compare_digest(password_hash(old_password,row["salt"]),row["digest"])):raise AccessError("当前密码不正确。")
            salt=secrets.token_hex(16)
            self.db.execute("UPDATE users SET salt=?,digest=? WHERE id=?",(salt,password_hash(new_password,salt),uid))
            self.db.execute("DELETE FROM sessions WHERE user_id=?",(uid,))

    def update(self,actor,body):
        uid=body.get("id");role=body.get("role");enabled=body.get("enabled")
        if role not in ROLES or type(enabled) is not bool:raise ValueError("账号设置无效。")
        if uid==actor["id"] and (not enabled or role!="admin"):raise ValueError("不能停用或降级当前管理员账号。")
        with self.lock,self.db:
            row=self.db.execute("SELECT * FROM users WHERE id=?",(uid,)).fetchone()
            if not row:raise ValueError("账号不存在。")
            self.db.execute("UPDATE users SET role=?,enabled=? WHERE id=?",(role,int(enabled),uid))
            self.db.execute("DELETE FROM sessions WHERE user_id=?",(uid,))

    def audit(self,user,path,body,result):
        # No copy, filenames, API credentials, passwords, or provider payloads in account audit.
        target={k:str((result or {}).get(k,body.get(k,"")))[:128] for k in ("project_id","article_id","job_id","asset_id","id")}
        with self.lock,self.db:self.db.execute("INSERT INTO audit(created,user_id,action,target) VALUES(?,?,?,?)",(time.time(),user["id"],path,json.dumps(target)))


def permission(user,path,body,local=False,tls=False):
    role=user["role"]
    if path in {"/api/settings","/api/connection"}:
        if role!="admin":raise AccessError("只有管理员可以配置模型服务。")
        if not local and not tls:raise AccessError("请通过远程桌面在服务器本机配置API；当前局域网入口使用HTTP。")
        return
    if path in {"/api/accounts/create","/api/accounts/update","/api/accounts/reset","/api/shutdown"}:
        if role!="admin":raise AccessError("此操作需要管理员账号。")
        return
    if path=="/api/password" or path=="/api/logout":return
    editor={"/api/project","/api/article","/api/confirm","/api/import-copy","/api/adopt-copy","/api/logo-selection","/api/adopt-rules"}
    if path in editor and role=="designer":raise AccessError("当前为设计账号；品牌规范和定稿文案由编导 / 运营维护。")
    if path=="/api/assets" and body.get("kind")!="design" and role=="designer":raise AccessError("设计账号只可上传设计成稿。")
    if path=="/api/jobs" and body.get("kind")!="review" and role=="designer":raise AccessError("Guide解析由编导 / 运营提交。")
    if path=="/api/detach" and role=="designer":raise AccessError("移除资料由编导 / 运营处理。")


def check_revision(state,path,body):
    target=None;key="_revision"
    if path=="/api/project" and body.get("id"):target=state["projects"].get(body["id"])
    elif path=="/api/article" and body.get("id"):target=state["articles"].get(body["id"])
    elif path in {"/api/import-copy","/api/adopt-copy"}:
        target=state["articles"].get(body.get("article_id"));key="_article_revision"
    elif path in {"/api/confirm","/api/logo-selection","/api/detach"}:
        target=state["projects"].get(body.get("project_id"));key="_project_revision"
    elif path=="/api/adopt-rules":
        job=state["jobs"].get(body.get("job_id"),{})
        target=state["projects"].get(job.get("project_id"));key="_project_revision"
    elif path=="/api/decision":
        job=state["jobs"].get(body.get("job_id"),{})
        target=next((f for f in (job.get("result") or {}).get("findings",[]) if f["id"]==body.get("finding_id")),None)
        if target is not None:
            if type(body.get("_decision_version")) is not int or body["_decision_version"]!=target.get("revision",0):raise AccessError("此问题的处理记录已被其他人更新，请重新打开报告后再保存。",409)
        return
    elif path=="/api/jobs":
        targets=[(state["projects"].get(body.get("project_id")),"_project_revision")]
        if body.get("kind")=="review":targets.append((state["articles"].get(body.get("article_id")),"_article_revision"))
        if any(t is not None and (type(body.get(k)) is not int or body[k]!=t["revision"]) for t,k in targets):raise AccessError("审核依据已被更新，请加载服务器最新资料后重新送审；尚未发起调用。",409)
        return
    if target is not None and (type(body.get(key)) is not int or body[key]!=target["revision"]):
        raise AccessError("其他人或后台任务已更新这份资料；当前输入已保留，请先备份草稿并加载服务器最新版本。",409)


def team_handler(Base):
    class TeamHandler(Base):
        def setup(self):
            super().setup();self.connection.settimeout(45)

        def host_ok(self):
            return self.headers.get("Host","").lower() in self.server.allowed_hosts and any(ipaddress.ip_address(self.client_address[0]) in n for n in OFFICE_NETS)

        def session_token(self):
            try:
                c=SimpleCookie();c.load(self.headers.get("Cookie",""));return c["review_team"].value if "review_team" in c else ""
            except Exception:return ""

        def identity(self):return self.server.accounts.session(self.session_token())
        def authorized(self):return bool(self.identity())
        def cookie(self,token):return "review_team="+token+"; HttpOnly; SameSite=Strict; Path=/"+("; Secure" if self.server.tls else "")

        def static(self,name,mime):return self.send((Path(__file__).parent/"static"/name).read_bytes(),mime=mime)

        def do_GET(self):
            try:
                if not self.host_ok():return self.send({"error":"此地址未在办公室服务器配置中启用。"},403)
                url=parse.urlsplit(self.path)
                if url.path=="/api/session":
                    setup=not self.server.accounts.has_users()
                    return self.send({"team":True,"setup_required":setup,"registration_open":not setup,"positions":SELF_REGISTER_ROLES,"can_setup":is_local(self.client_address[0]),"version":self.server.app_version})
                if url.path=="/login":return self.static("login.html","text/html; charset=utf-8")
                if url.path=="/login.js":return self.static("login.js","text/javascript; charset=utf-8")
                if url.path=="/style.css":return self.static("style.css","text/css; charset=utf-8")
                if url.path=="/api/health":return self.send({"app_id":self.app.app_id,"version":self.server.app_version,"team":True})
                identity=self.identity()
                if not identity:
                    if url.path=="/":return self.send(b"",302,"text/plain",{"Location":"/login"})
                    return self.send({"error":"请登录办公室工作台。"},401)
                user,csrf=identity
                if url.path=="/api/state":
                    state=self.app.public_state();state["csrf"]=csrf
                    state["team"]={"user":user,"can_configure":user["role"]=="admin" and (is_local(self.client_address[0]) or self.server.tls),"roles":ROLES}
                    if user["role"]!="admin":state["settings"]={k:state["settings"][k] for k in ("provider","model","has_key")}
                    return self.send(state)
                if url.path=="/api/accounts":
                    if user["role"]!="admin":return self.send({"error":"只有管理员可以查看账号。"},403)
                    return self.send({"users":self.server.accounts.users()})
                if url.path=="/" and url.query:return self.send(b"",302,"text/plain",{"Location":"/"})
                return super().do_GET()
            except (BrokenPipeError,ConnectionResetError):pass
            except Exception:
                self.app.log_error();return self.send({"error":"读取失败，请联系管理员。"},500)

        def do_POST(self):
            try:
                if not self.host_ok():raise AccessError("服务器地址未启用。")
                expected=("https" if self.server.tls else "http")+"://"+self.headers.get("Host","")
                if self.headers.get("Origin")!=expected:raise AccessError("拒绝跨站请求。")
                if self.headers.get("Content-Type","").split(";")[0].strip()!="application/json":raise AccessError("仅接受JSON请求。",415)
                path=parse.urlsplit(self.path).path
                auth_paths={"/api/login","/api/setup","/api/register"}
                identity=self.identity()
                if path not in auth_paths:
                    if not identity:raise AccessError("登录已过期，请重新登录。",401)
                    if not secrets.compare_digest(self.headers.get("X-Review-Token",""),identity[1]):raise AccessError("会话校验失败，请刷新后重试。")
                limit=48*1024*1024 if path in {"/api/assets","/api/import-copy"} else 128*1024
                length=int(self.headers.get("Content-Length","0"))
                if not 0<length<=limit:raise AccessError("请求过大或为空。",413)
                body=json.loads(self.rfile.read(length))
                if not isinstance(body,dict):raise ValueError("请求格式无效。")
                accounts=self.server.accounts
                if path=="/api/setup":
                    if not is_local(self.client_address[0]):raise AccessError("首次管理员必须在服务器本机创建。")
                    code=body.get("setup_code","")
                    if not isinstance(code,str) or not secrets.compare_digest(code,self.server.setup_code):raise AccessError("初始化码不正确，请查看服务器启动窗口。")
                    accounts.create(body,first=True)
                    return self.send({"message":"管理员已创建，请登录。"})
                if path=="/api/login":
                    token,csrf,user=accounts.login(body.get("username",""),body.get("password",""),self.client_address[0])
                    accounts.audit(user,path,{},{});return self.send({"message":"登录成功。"},headers={"Set-Cookie":self.cookie(token)})
                if path=="/api/register":
                    uid=accounts.register(body)
                    token,csrf,user=accounts.start_session(uid)
                    accounts.audit(user,path,{},{})
                    return self.send({"message":"注册成功，可以开始使用。"},headers={"Set-Cookie":self.cookie(token)})
                user=identity[0]
                permission(user,path,body,is_local(self.client_address[0]),self.server.tls)
                if path=="/api/logout":
                    accounts.logout(self.session_token());return self.send({"message":"已退出登录。"},headers={"Set-Cookie":self.cookie("")+"; Max-Age=0"})
                if path=="/api/password":
                    if "old_password" not in body:raise ValueError("请填写当前密码。")
                    accounts.password(user["id"],body.get("password"),body["old_password"])
                    accounts.audit(user,path,{},{});return self.send({"message":"密码已修改，请重新登录。"})
                if path=="/api/accounts/create":result={"id":accounts.create(body),"message":"账号已创建。"}
                elif path=="/api/accounts/update":accounts.update(user,body);result={"message":"账号设置已保存，原会话已失效。"}
                elif path=="/api/accounts/reset":
                    accounts.password(body.get("id"),body.get("password"));result={"message":"密码已重置，原会话已失效。"}
                else:
                    # Same lock order as Application.config/create_job. The worker's writes
                    # share Store.lock, so compare-and-save is atomic against both users and AI.
                    with self.app.settings_lock,self.app.store.lock:
                        check_revision(self.app.store.state,path,body)
                        result=self.app.post(path,body)
                        if path=="/api/decision":
                            def stamp(state):
                                f=next(f for f in state["jobs"][body["job_id"]]["result"]["findings"] if f["id"]==body["finding_id"])
                                f.update(revision=f.get("revision",0)+1,updated_by=user["name"],updated_at=time.time())
                            self.app.store.edit(stamp)
                accounts.audit(user,path,body,result)
                return self.send(result)
            except AccessError as exc:return self.send({"error":str(exc)},exc.status)
            except (ValueError,KeyError,StopIteration) as exc:return self.send({"error":str(exc) if isinstance(exc,ValueError) else "项目、稿件或账号不存在。"},400)
            except (BrokenPipeError,ConnectionResetError):pass
            except Exception:
                self.app.log_error();return self.send({"error":"操作失败，请联系管理员检查服务器日志。"},500)
    return TeamHandler


class OfficeServer(ThreadingHTTPServer):
    daemon_threads=True
    request_queue_size=32
    def __init__(self,*args,**kwargs):
        self.slots=threading.BoundedSemaphore(24)
        super().__init__(*args,**kwargs)
    def process_request(self,request,address):
        if not self.slots.acquire(blocking=False):self.shutdown_request(request);return
        try:super().process_request(request,address)
        except Exception:self.slots.release();raise
    def process_request_thread(self,request,address):
        try:super().process_request_thread(request,address)
        finally:self.slots.release()


def run_server(args,Application,Base,version):
    config=json.loads(args.server_config.read_text("utf-8-sig"))
    port=config.get("port",8765);host=config.get("bind_host","0.0.0.0")
    if type(port) is not int or not 1024<=port<=65535:raise ValueError("服务器端口请设置在1024至65535。")
    if not isinstance(config.get("allowed_hosts"),list) or not config["allowed_hosts"]:raise ValueError("请先运行配置服务器脚本，填写局域网地址。")
    allowed={str(x).lower() for x in config["allowed_hosts"]}|{f"127.0.0.1:{port}",f"localhost:{port}"}
    if any(not re.fullmatch(r"[a-z0-9][a-z0-9.\-]*:[0-9]{1,5}",x) for x in allowed):raise ValueError("allowed_hosts必须是明确的主机名或IPv4加端口，不可填写通配符。")
    data=args.data.resolve()
    data.mkdir(parents=True,exist_ok=True)
    if args.reset_user:
        accounts=Accounts(data/"private"/"accounts.sqlite3")
        user=next((u for u in accounts.users() if u["username"]==args.reset_user.strip().lower()),None)
        if not user:raise ValueError("账号不存在。")
        password=getpass.getpass("New password (10-128 chars): ")
        if password!=getpass.getpass("Repeat password: "):raise ValueError("两次密码不一致。")
        accounts.password(user["id"],password);print("Password reset. Sign in again.",flush=True);return
    instance=data/"instance.json"
    try:
        old=json.loads(instance.read_text("utf-8"))
        scheme="https" if old.get("tls") else "http"
        handlers=[request.ProxyHandler({})]
        # This probe is loopback only and returns no secrets, not a model/API connection.
        if old.get("tls"):handlers.append(request.HTTPSHandler(context=ssl._create_unverified_context()))
        with request.build_opener(*handlers).open(f'{scheme}://127.0.0.1:{old["port"]}/api/health',timeout=.5) as response:health=json.load(response)
        expected=hashlib.sha256(str(data).encode()).hexdigest()[:24]
        if health.get("app_id")==expected:
            if not health.get("team"):raise ValueError("当前资料目录被单机版占用；请先正常退出单机版。")
            print("Server already running.",flush=True)
            if args.open:webbrowser.open(f'{scheme}://127.0.0.1:{old["port"]}/')
            return
    except ValueError:raise
    except Exception:pass
    lock=(data/"instance.lock").open("a+b")
    if os.name=="nt":
        import msvcrt
        lock.seek(0);lock.write(b"1");lock.flush();lock.seek(0)
        try:msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
        except OSError:raise ValueError("此资料目录已有程序运行，请在原服务器窗口查看。") from None
    server=OfficeServer((host,port),team_handler(Base))
    app=Application(data)
    server.app=app;app.server=server;server.allowed_hosts=allowed;server.tls=False;server.app_version=version
    cert=config.get("tls_cert");key=config.get("tls_key")
    if bool(cert)!=bool(key):raise ValueError("HTTPS证书和私钥必须同时配置。")
    if cert:
        context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);context.minimum_version=ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(cert,key);server.socket=context.wrap_socket(server.socket,server_side=True);server.tls=True
    server.accounts=Accounts(data/"private"/"accounts.sqlite3",clear_sessions=True)
    server.setup_code=secrets.token_urlsafe(12)
    instance.write_text(json.dumps({"port":port,"app_id":app.app_id,"pid":os.getpid(),"version":version,"team":True,"tls":server.tls}),encoding="utf-8")
    scheme="https" if server.tls else "http"
    print(f"Office Review {version} - keep this window running. Ctrl+C to stop.",flush=True)
    for name in sorted(allowed):print(f"  {scheme}://{name}/",flush=True)
    if not server.accounts.has_users():print("\nFIRST ADMIN SETUP CODE (enter only on server localhost): "+server.setup_code,flush=True)
    if args.open:webbrowser.open(f"{scheme}://127.0.0.1:{port}/")
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close();lock.close()
