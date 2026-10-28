'use strict';
const field=id=>document.getElementById(id);
let loginMode='login',registrationOpen=false;
function setMode(mode){
  loginMode=mode;const registering=mode==='register';
  field('register-position').hidden=!registering;
  field('login-title').textContent=registering?'注册团队账号':'登录工作台';
  field('login-subtitle').textContent=registering?'填写用户名、密码并选择职位，注册后直接使用。':'已有账号直接登录，新同事可自行注册。';
  field('login-submit').textContent=registering?'注册并进入工作台':'登录';
  field('login-switch').textContent=registering?'已有账号？返回登录':'没有账号？自行注册';
  field('login-password').autocomplete=registering?'new-password':'current-password';
  field('login-password').value='';field('login-message').textContent='';
}
async function initialize(){
  const response=await fetch('/api/session',{cache:'no-store'});if(!response.ok)throw Error('服务器不可用');const state=await response.json();
  registrationOpen=!!state.registration_open;
  if(state.setup_required){
    if(!state.can_setup){field('login-message').textContent='服务器尚未初始化，请管理员通过远程桌面在服务器上创建首个账号。';field('login-submit').disabled=true;return;}
    loginMode='setup';field('setup-fields').hidden=false;field('setup-code').required=true;field('setup-name').required=true;
    field('login-title').textContent='创建首个管理员';field('login-subtitle').textContent='从服务器启动窗口复制初始化码；此步骤仅可在服务器本机完成。';field('login-submit').textContent='创建管理员';field('login-password').autocomplete='new-password';
  }else field('login-switch').hidden=!registrationOpen;
}
field('login-switch').addEventListener('click',()=>{if(registrationOpen)setMode(loginMode==='register'?'login':'register');});
field('login-form').addEventListener('submit',async event=>{
  event.preventDefault();field('login-submit').disabled=true;field('login-switch').disabled=true;field('login-message').textContent=loginMode==='register'?'正在创建账号…':'正在验证…';
  try{
    const body={username:field('login-user').value,password:field('login-password').value};
    if(loginMode==='setup'){body.setup_code=field('setup-code').value;body.name=field('setup-name').value;}
    if(loginMode==='register')body.role=field('register-role').value;
    const response=await fetch('/api/'+loginMode,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    const value=await response.json();if(!response.ok)throw Error(value.error||'操作失败。');
    field('login-password').value='';location.replace(loginMode==='setup'?'/login':'/');
  }catch(error){field('login-message').textContent=error.message;field('login-submit').disabled=false;field('login-switch').disabled=false;}
});
initialize().catch(()=>{field('login-message').textContent='无法连接办公室服务器，请联系管理员。';});
