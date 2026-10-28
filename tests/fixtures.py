"""Explicit offline fixtures. This is not an AI model or a quality benchmark."""
import argparse
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

RULE="LOGO 必须保持正圆比例，不得拉伸。"
COPY="秋日品牌开放日\n活动时间：2026年11月15日\n报名免费\n名额有限，欢迎参与。"
ACTUAL=[("秋日品牌开放日",220,280),("活动时间：2026年11月16日",880,925),("报名免贵",1620,1670),("名额有限，欢迎参与。",2500,2550)]


def samples(folder):
    folder=Path(folder); folder.mkdir(parents=True,exist_ok=True)
    fonts=Path(os.environ.get("WINDIR","C:/Windows"))/"Fonts"
    face=next((fonts/name for name in ("msyh.ttc","simsun.ttc","segoeui.ttf","arial.ttf") if (fonts/name).is_file()),None)
    font=lambda size:ImageFont.truetype(str(face),size) if face else ImageFont.load_default(size=size)
    logo=Image.new("RGBA",(300,300),(255,255,255,0)); d=ImageDraw.Draw(logo)
    d.ellipse((20,20,280,280),fill="#365fd4");d.text((84,82),"校",font=font(130),fill="white")
    logo.save(folder/"标准LOGO.png")
    guide=Image.new("RGB",(1100,1400),"white");d=ImageDraw.Draw(guide)
    d.text((70,65),"校样 · 测试品牌视觉 Guide",font=font(42),fill="#263348")
    guide.paste(logo.resize((220,220)),(70,170),logo.resize((220,220)))
    d.text((70,470),RULE,font=font(29),fill="#263348")
    d.text((70,550),"品牌主色为蓝色 #365FD4。",font=font(29),fill="#263348")
    d.text((70,680),"本资料是虚构测试样例，不代表任何公司的规范。",font=font(24),fill="#7a879b")
    guide.save(folder/"视觉Guide-测试样例.pdf",resolution=100)
    guide.save(folder/"视觉Guide-测试样例.png")
    design=Image.new("RGB",(780,3400),"#fafbfe");d=ImageDraw.Draw(design)
    stretched=logo.resize((190,85));design.paste(stretched,(60,55),stretched)
    for text,top,bottom in ACTUAL:
        d.text((65,top),text,font=font(46 if top==220 else 29),fill="#263348")
        d.line((65,bottom+90,715,bottom+90),fill="#dde4f0",width=2)
    design.save(folder/"设计长图-含3处已知问题.png")
    (folder/"定稿文案.txt").write_text(COPY,encoding="utf-8")
    (folder/"样例说明.txt").write_text("虚构测试样例。已知问题：LOGO被横向拉伸；活动日期15日写成16日；报名免费写成报名免贵。请用真实视觉API审核，查看它实际能找到哪些问题。自动化用离线固定响应验证程序流程，不代表模型准确率。",encoding="utf-8")
    return folder


class FixtureHandler(BaseHTTPRequestHandler):
    def log_message(self,*args): pass
    def emit(self,value,status=200):
        raw=json.dumps(value,ensure_ascii=False).encode()
        self.send_response(status);self.send_header("Content-Type","application/json");self.send_header("Content-Length",str(len(raw)));self.end_headers();self.wfile.write(raw)
    def do_GET(self): self.emit({"data":[{"id":"offline-fixture-vision"}]})
    def do_POST(self):
        body=json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        payload=json.loads(body["messages"][1]["content"][0]["text"])
        task=payload["task"]; inputs=payload["input"]
        self.server.calls.append(task)
        if getattr(self.server,"failure",False): return self.emit({"code":30001,"message":"offline fixture failure"},getattr(self.server,"failure_status",429))
        if task=="extract_guide":
            sid=inputs["sources"][0]["source_id"]
            value={"rules":[{"category":"logo","requirement":RULE,"source_id":sid,"quote":RULE}],"logos":[{"source_id":sid,"box":[.06,.12,.27,.29],"label":"标准LOGO测试候选","usage":"approved","evidence":"离线夹具：标准页正例"},{"source_id":sid,"box":[.06,.12,.27,.29],"label":"禁用测试候选","usage":"forbidden","evidence":"离线夹具：故意重复同一位置验证禁用分类，不代表图中实际有禁用示例"}],"notes":[]}
            if getattr(self.server,"invalid_logo",False):value["logos"][0]["source_id"]="unknown-source"
        elif task=="ocr_design":
            value={"images":[]}
            for iid in inputs["image_ids"]:
                index=int(iid.split("-")[-1])-1;top=index*1400;bottom=min(top+1600,3400)
                lines=[]
                for text,y1,y2 in ACTUAL:
                    if y1>=top and y2<=bottom:
                        lines.append({"text":text,"box":[.08,(y1-top)/(bottom-top),.94,(y2-top)/(bottom-top)],"confidence":.97})
                value["images"].append({"image_id":iid,"coverage":"complete","note":"离线测试固定响应","lines":lines})
        elif task=="review_visual":
            rule=inputs["rules"][0]
            value={"reviewed_image_ids":inputs["design_ids"],"findings":[{"title":"LOGO 疑似被横向拉伸","category":"logo","detail":"离线夹具：设计中的圆形LOGO呈扁椭圆，需核对标准比例。","image_id":"tile-1","box":[.077,.034,.32,.09],"rule_id":rule["id"],"rule_quote":rule["requirement"],"observed":"图标呈扁椭圆","expected":"保持正圆比例"}],"coverage":{c:{"status":"checked" if c=="logo" else "not_applicable","reason":"离线固定响应，仅验证程序流程。"} for c in ("logo","color","layout","typography")}}
        else: return self.emit({"error":"unexpected fixture task"},400)
        self.emit({"id":"offline-fixture-response","choices":[{"finish_reason":"stop","message":{"content":json.dumps(value,ensure_ascii=False)}}],"usage":{"prompt_tokens":1234,"completion_tokens":300}})


def server(port=0):
    srv=ThreadingHTTPServer(("127.0.0.1",port),FixtureHandler);srv.calls=[];return srv

if __name__=="__main__":
    parser=argparse.ArgumentParser();parser.add_argument("--samples",type=Path);parser.add_argument("--serve",action="store_true");parser.add_argument("--port",type=int,default=0);args=parser.parse_args()
    if args.samples: samples(args.samples);print("Fixture samples created.",flush=True)
    if args.serve:
        srv=server(args.port);print(f"OFFLINE FIXTURE: http://127.0.0.1:{srv.server_port}/v1",flush=True);srv.serve_forever()
