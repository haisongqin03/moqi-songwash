#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os, sys, re, json, uuid, datetime, sqlite3, hashlib, http.server, urllib.parse, html, io, mimetypes
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
PROJECTS_DIR = DATA_DIR / "projects"
TEMPLATES_DIR = BASE_DIR / "templates"
TOOLSET_DIR = BASE_DIR.parent
DATA_DIR.mkdir(parents=True, exist_ok=True)
PROJECTS_DIR.mkdir(parents=True, exist_ok=True)

SECRET = os.environ.get("SECRET_KEY", "moqi-dev-key")
sessions = {}

def get_db():
    conn = sqlite3.connect(str(DATA_DIR / "moqi.db"))
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT DEFAULT 'user',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS projects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            song_name TEXT NOT NULL,
            grade TEXT DEFAULT 'A',
            bpm INTEGER DEFAULT 120,
            workflow_state TEXT DEFAULT '交接签收',
            project_path TEXT,
            modifications_done INTEGER DEFAULT 0,
            fingerprint_similarity REAL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS workflow_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL,
            from_state TEXT,
            to_state TEXT,
            note TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (project_id) REFERENCES projects(id)
        );
    """)
    conn.commit()
    conn.close()

init_db()

def hash_pw(p):
    return hashlib.sha256((p + "moqi").encode()).hexdigest()

def gu():
    return "{" + str(uuid.uuid4()).upper() + "}"

RPP_NAMES = ["01_cankaogui(AIyequ)","02_renshengui","03_zhuxuanlvMIDI","04_duiweixuanlvMIDI","05_hesheng1","06_hesheng2","07_beisigui","08_guzugui","09_jitatanbo","10_jianpanhecheng","11_xiaoguofenwei","12_hunyinzongshuchu"]
RPP_COLS = ["16711808","8388736","16754790","16711935","65535","16776960","16711680","8421504","10079487","3329330","65535","12632256"]
WORKFLOW_STATES = ["交接签收","AI拆解","人声处理","旋律重构","对位旋律","和声1","和声2","编曲","混音","母带","终审交付"]
MODS_BY_GRADE = {"A":(("拆解原唱",1),("提取人声",1),("修正旋律",1),("重录/修复",0),("声场对齐",1)),"S":(("拆解原唱",2),("提取人声",2),("修正旋律",3),("重录/修复",3),("声场对齐",2))}

WORKFLOW_STEPS = [
    ("交接签收","原唱文件归档"),
    ("AI拆解","AI分辨乐器轨"),
    ("人声处理","提取干声/参考韵"),
    ("旋律重构","MIDI旋律编排"),
    ("对位旋律","对位旋律MIDI"),
    ("和声1","和声配器"),
    ("和声2","和声补充"),
    ("编曲","节奏+白器编排"),
    ("混音","音质平衡/动态处理"),
    ("母带","最终母带输出"),
    ("终审交付","客户确认/存证")
]

def gen_rpp(bpm=120, sr=48000, grade="A"):
    lines = ['<REAPER_PROJECT 0.1 "6.85" ' + str(sr)]
    lines.append('  SAMPLERATE ' + str(sr))
    lines.append('  TEMPO ' + f'{bpm/60:.6f}')
    lines.append('  4')
    lines.append('  <MASTERTRACK')
    lines.append('    NAME "Master"')
    lines.append('    VOLUME 1.000000')
    lines.append('    PAN 0.000000')
    lines.append('    TRACKID ' + gu())
    lines.append('    >')
    for i, name in enumerate(RPP_NAMES):
        lines.append('  <TRACK')
        lines.append('    NAME "' + name + '"')
        lines.append('    VOLUME 0.750000')
        lines.append('    PAN 0.000000')
        lines.append('    TRACKID ' + gu())
        lines.append('    TRACKHEIGHT 30')
        lines.append('    SHOWINMIXER 1')
        lines.append('    TRACKNUMBER ' + str(i+1))
        lines.append('    PEAKCOL ' + RPP_COLS[i])
        lines.append('  >')
    return '\n'.join(lines)

TEMPLATES_CACHE = {}
def load_templates():
    for f in TEMPLATES_DIR.glob("*.html"):
        TEMPLATES_CACHE[f.stem] = f.read_text("utf-8")
load_templates()

def render(template_name, **kwargs):
    if template_name not in TEMPLATES_CACHE:
        return '<h1>Template not found: ' + template_name + '</h1>'
    html_content = TEMPLATES_CACHE[template_name]
    m = re.search(r'{% extends "(.+?)" %}', html_content)
    if m:
        base_name = m.group(1).replace(".html", "")
        if base_name in TEMPLATES_CACHE:
            base = TEMPLATES_CACHE[base_name]
            block_match = re.search(r'{% block content %}(.*?){% endblock %}', html_content, re.DOTALL)
            block_content = block_match.group(1) if block_match else html_content
            title_match = re.search(r'{% block title %}(.*?){% endblock %}', html_content, re.DOTALL)
            if title_match:
                base = base.replace("{{ title }}", title_match.group(1))
                base = base.replace('\u58a8\u6816\u6d17\u6b4c\u5e73\u53f0', title_match.group(1))
            base = base.replace('{% block content %}{% endblock %}', block_content)
            base = base.replace('{% block content %}...{% endblock %}', block_content)
            html_content = base
    for k, v in kwargs.items():
        html_content = html_content.replace('{{ ' + k + ' }}', str(v) if v is not None else '')
    return html_content

class MoqiHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass
    def _session_user(self):
        cookie = self.headers.get("Cookie", "")
        m = re.search(r'session=([^;]+)', cookie)
        if m:
            uid = sessions.get(m.group(1))
            if uid:
                conn = get_db()
                user = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
                conn.close()
                return dict(user) if user else None
        return None
    def _flash(self, msg, cat='info'):
        if not hasattr(self, '_flash_msgs'):
            self._flash_msgs = []
        self._flash_msgs.append((cat, msg))
    def _redirect(self, path):
        self.send_response(302)
        cookies = getattr(self, '_set_cookies', [])
        if cookies:
            self.send_header('Set-Cookie', '; '.join(cookies))
        self.send_header('Location', path)
        self.end_headers()
    def _html(self, content, status=200):
        flash_html = ''
        if hasattr(self, '_flash_msgs'):
            for cat, msg in self._flash_msgs:
                flash_html += '<div class="flash ' + cat + '">' + html.escape(msg) + '</div>'
        user = self._session_user()
        # Step 1: Handle nav conditional first
        if user:
            content = content.replace('{% if session.user_id %}', '')
            content = content.replace('{% if not session.user_id %}', '')
            content = re.sub(r'{% else %}.*?{% endif %}', '', content, flags=re.DOTALL)
        else:
            content = re.sub(r'{% if session.user_id %}.*?{% else %}', '', content, flags=re.DOTALL)
            content = re.sub(r'{% if not session.user_id %}.*?{% else %}', '', content, flags=re.DOTALL)
            content = content.replace('{% endif %}', '')
        # Step 2: Replace url_for calls with actual paths
        url_map = {'login': '/login', 'register': '/register', 'dashboard': '/dashboard', 'downloads': '/downloads', 'logout': '/logout', 'project_new': '/project/new'}
        for n, pa in url_map.items():
            content = re.sub(r"\{\{ url_for\('" + n + r"'\) \}\}", pa, content)
        # Step 3: Remove remaining {{ ... }} tags
        content = re.sub(r'\{\{[^}]*\}\}', '', content)
        # Step 4: Remove remaining {% ... %} tags
        content = re.sub(r'\{%[-]?[^%]*[-]?%\}', '', content)
        # Step 5: Clean up blank lines
        content = re.sub(r'\n\s*\n\s*\n+', '\n\n', content)
        content = content.replace('</nav>', '</nav>\n' + flash_html, 1)
        if user:
            content = content.replace('{{ session.username }}', html.escape(user['username']))
        self.send_response(status)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        cookies = getattr(self, '_set_cookies', [])
        if cookies:
            self.send_header('Set-Cookie', ';'.join(cookies))
        self.end_headers()
        self.wfile.write(content.encode('utf-8'))
    def _parse_post(self):
        length = int(self.headers.get('Content-Length', 0))
        if length == 0:
            return {}
        body = self.rfile.read(length).decode('utf-8')
        return dict(urllib.parse.parse_qsl(body))
    def _logged_in(self):
        user = self._session_user()
        if not user:
            self._redirect('/login')
            return None
        return user
    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path in ('/login', '/'):
            self._handle_login_get()
        elif path == '/register':
            self._handle_register_get()
        elif path == '/logout':
            self._handle_logout()
        elif path == '/dashboard':
            self._handle_dashboard()
        elif path == '/project/new':
            self._handle_project_new_get()
        elif path.startswith('/project/'):
            pid = path.split('/')[2] if len(path.split('/')) > 2 else ''
            if path.endswith('/download_rpp'):
                self._handle_download_rpp(pid)
            elif path.endswith('/delete'):
                self._handle_delete(pid)
            else:
                self._handle_project_detail(pid)
        elif path == '/downloads':
            self._handle_downloads()
        elif path.startswith('/downloads/'):
            fn = path.split('/downloads/')[1]
            self._handle_download_file(fn)
        elif path == '/api/projects':
            self._handle_api_projects()
        elif path.startswith('/static/'):
            self._serve_static(path)
        else:
            self._html('<h1>404</h1>', 404)
    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        data = self._parse_post()
        if path == '/login':
            self._handle_login_post(data)
        elif path == '/register':
            self._handle_register_post(data)
        elif path == '/project/new':
            self._handle_project_new_post(data)
        elif '/advance' in path:
            pid = path.split('/')[2]
            self._handle_advance(pid, data)
        elif '/update_mods' in path:
            pid = path.split('/')[2]
            self._handle_update_mods(pid, data)
        else:
            self._html('<h1>404</h1>', 404)
    def _serve_static(self, path):
        file_path = BASE_DIR / path.lstrip('/')
        if file_path.is_file():
            mime = mimetypes.guess_type(str(file_path))[0] or 'application/octet-stream'
            self.send_response(200)
            self.send_header('Content-Type', mime)
            self.end_headers()
            self.wfile.write(file_path.read_bytes())
        else:
            self._html('<h1>404</h1>', 404)
    def _handle_login_get(self):
        if self._session_user():
            self._redirect('/dashboard')
            return
        self._html(render('login'))
    def _handle_login_post(self, data):
        username = data.get('username', '').strip()
        password = data.get('password', '').strip()
        if not username or not password:
            self._flash('请输入用户名和密码', 'danger')
            self._html(render('login'))
            return
        conn = get_db()
        user = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        conn.close()
        if user and user['password_hash'] == hash_pw(password):
            token = str(uuid.uuid4())
            sessions[token] = user['id']
            if not hasattr(self, '_set_cookies'):
                self._set_cookies = []
            self._set_cookies.append('session=' + token + '; path=/')
            self._redirect('/dashboard')
        else:
            self._flash('用户名或密码错误', 'danger')
            self._html(render('login'))
    def _handle_register_get(self):
        self._html(render('register'))
    def _handle_register_post(self, data):
        username = data.get('username', '').strip()
        email = data.get('email', '').strip()
        password = data.get('password', '').strip()
        if not username or not email or not password:
            self._flash('请填写所有字段', 'danger')
            self._redirect('/register')
            return
        conn = get_db()
        try:
            conn.execute("INSERT INTO users (username, email, password_hash) VALUES (?, ?, ?)", (username, email, hash_pw(password)))
            conn.commit()
            self._flash('注册成功，请登录', 'success')
            conn.close()
            self._redirect('/login')
        except sqlite3.IntegrityError:
            self._flash('用户名或邮箱已被注册', 'danger')
            conn.close()
            self._redirect('/register')
    def _handle_logout(self):
        cookie = self.headers.get("Cookie", "")
        m = re.search(r'session=([^;]+)', cookie)
        if m:
            sessions.pop(m.group(1), None)
        self._set_cookies = ['session=; path=/; expires=Thu, 01 Jan 1970']
        self._redirect('/')
    def _handle_dashboard(self):
        user = self._logged_in()
        if not user:
            return
        conn = get_db()
        projects = conn.execute("SELECT * FROM projects WHERE user_id=? ORDER BY created_at DESC", (user['id'],)).fetchall()
        conn.close()
        rows = []
        for p in projects:
            p = dict(p)
            state_class = "badge-done" if p['workflow_state'] == "已完成" else "badge-progress"
            req = MODS_BY_GRADE.get(p['grade'], MODS_BY_GRADE['A'])
            min_mods = sum(r[1] for r in req)
            rows.append('<tr><td><strong>' + html.escape(p['song_name']) + '</strong></td><td><span class="badge ' + state_class + '">' + html.escape(p['workflow_state']) + '</span></td><td>' + str(p['bpm']) + '</td><td>' + str(p['modifications_done']) + '/' + str(min_mods) + '</td><td style="font-size:13px;color:#86868b">' + str(p['created_at'])[:16] + '</td><td><a href="/project/' + str(p['id']) + '" class="btn btn-secondary btn-sm">查看</a></td></tr>')
        html_content = render('dashboard')
        html_content = html_content.replace('{% for p in projects %}', '\n'.join(rows) if rows else '')
        html_content = html_content.replace('{% endfor %}', '')
        html_content = html_content.replace('{% else %}', '')
        html_content = html_content.replace('{% endif %}', '')
        html_content = html_content.replace('{% for p in projects %}{% endfor %}', '')
        # Replace url_for patterns with actual project URLs
        self._html(html_content)
    def _handle_project_new_get(self):
        user = self._logged_in()
        if not user:
            return
        self._html(render('project_new'))
    def _handle_project_new_post(self, data):
        user = self._logged_in()
        if not user:
            return
        song_name = data.get('song_name', '').strip()
        grade = data.get('grade', 'A')
        bpm = int(data.get('bpm', 120))
        if not song_name:
            self._flash('请输入歌曲名称', 'danger')
            self._redirect('/project/new')
            return
        conn = get_db()
        c = conn.execute("INSERT INTO projects (user_id, song_name, grade, bpm) VALUES (?,?,?,?)", (user['id'], song_name, grade, bpm))
        pid = c.lastrowid
        rpp_content = gen_rpp(bpm=bpm, grade=grade)
        safe_name = re.sub(r'[^\w\u4e00-\u9fff]+', '_', song_name)
        date_str = datetime.datetime.now().strftime('%Y%m%d')
        rpp_filename = safe_name + '_' + date_str + '.RPP'
        rpp_path = PROJECTS_DIR / rpp_filename
        rpp_path.write_text(rpp_content, 'utf-8')
        conn.execute("UPDATE projects SET project_path=? WHERE id=?", (str(rpp_path), pid))
        conn.commit()
        conn.close()
        self._flash('洗歌项目已创建: ' + song_name, 'success')
        self._redirect('/project/' + str(pid))
    def _handle_project_detail(self, pid):
        user = self._logged_in()
        if not user:
            return
        conn = get_db()
        proj = conn.execute("SELECT * FROM projects WHERE id=? AND user_id=?", (pid, user['id'])).fetchone()
        logs = conn.execute("SELECT * FROM workflow_log WHERE project_id=? ORDER BY created_at", (pid,)).fetchall()
        conn.close()
        if not proj:
            self._html('<h1>404</h1>', 404)
            return
        proj = dict(proj)
        states = WORKFLOW_STATES
        current_idx = states.index(proj['workflow_state']) if proj['workflow_state'] in states else 0
        req = MODS_BY_GRADE.get(proj['grade'], MODS_BY_GRADE['A'])
        min_mods = sum(r[1] for r in req)
        human_pct = round(proj['modifications_done'] / min_mods * 100) if min_mods > 0 else 0
        html_content = render('project_detail')
        for k, v in {'project_song_name': html.escape(proj['song_name']), 'project_grade': proj['grade'], 'project_bpm': str(proj['bpm']),
                     'project_created_at_16': str(proj['created_at'])[:16], 'project_created_at_10': str(proj['created_at'])[:10],
                     'project_workflow_state': proj['workflow_state'], 'project_modifications_done': str(proj['modifications_done']),
                     'project_fingerprint_similarity_or_': str(proj['fingerprint_similarity'] or ''),
                     'project_id': str(proj['id']), 'req_human_pct': str(human_pct), 'req_min_mods': str(min_mods)}.items():
            html_content = html_content.replace('{{ ' + k + ' }}', v)
        steps_html = ''
        for i, s in enumerate(states):
            if s == proj['workflow_state']:
                sc, st = 'step-active', '<span style="color:#007aff;font-size:12px">当前</span>'
            elif states.index(s) < current_idx:
                sc, st = 'step-done', '<span style="color:#34c759;font-size:12px">已完成</span>'
            else:
                sc, st = 'step-pending', '等待'
            steps_html += '<div class="workflow-step"><div class="step-num ' + sc + '">' + str(i+1) + '</div><div class="step-label">' + html.escape(s) + '</div><div class="step-status">' + st + '</div></div>'
        html_content = html_content.replace('{% for s in states %}', steps_html)
        html_content = html_content.replace('{% endfor %}', '')
        html_content = html_content.replace('{% set current_idx = states.index(project.workflow_state) %}', '')
        html_content = html_content.replace('{% if current_idx + 1 < states|length %}', '')
        html_content = html_content.replace('{% endif %}', '')
        adv_btn = ''
        if current_idx + 1 < len(states):
            adv_btn = '<form method="POST" action="/project/' + str(proj['id']) + '/advance" style="display:inline"><button type="submit" class="btn btn-primary">推进到: ' + states[current_idx + 1] + '</button></form>'
        html_content = html_content.replace('推进到下一阶段', adv_btn)
        html_content = html_content.replace('{% if project.workflow_state', '')
        logs_html = ''
        for log in logs:
            log = dict(log)
            logs_html += '<li style="padding:6px 0;border-bottom:1px solid #f0f0f0">- ' + html.escape(log['from_state'] or '') + ' → <strong>' + html.escape(log['to_state']) + '</strong> (' + str(log['created_at'])[:16] + ')</li>'
        html_content = html_content.replace('{% for log in logs %}', logs_html if logs_html else '<li>暂无操作记录</li>')
        html_content = html_content.replace('{% if logs %}', '')
        html_content = html_content.replace('{% endif %}', '')
        # Replace url_for patterns with actual project URLs
        html_content = html_content.replace("{{ url_for('project_download_rpp', project_id=project.id) }}", '/project/' + str(proj['id']) + '/download_rpp')
        html_content = html_content.replace("{{ url_for('project_delete', project_id=project.id) }}", '/project/' + str(proj['id']) + '/delete')
        html_content = html_content.replace("{{ url_for('project_update_mods', project_id=project.id) }}", '/project/' + str(proj['id']) + '/update_mods')
        self._html(html_content)
    def _handle_advance(self, pid, data):
        user = self._logged_in()
        if not user:
            return
        conn = get_db()
        proj = conn.execute("SELECT * FROM projects WHERE id=? AND user_id=?", (pid, user['id'])).fetchone()
        if proj:
            proj = dict(proj)
            states = WORKFLOW_STATES
            current_idx = states.index(proj['workflow_state']) if proj['workflow_state'] in states else 0
            if current_idx + 1 < len(states):
                next_state = states[current_idx + 1]
                conn.execute("INSERT INTO workflow_log (project_id, from_state, to_state) VALUES (?,?,?)", (pid, proj['workflow_state'], next_state))
                conn.execute("UPDATE projects SET workflow_state=?, updated_at=CURRENT_TIMESTAMP WHERE id=?", (next_state, pid))
                conn.commit()
                self._flash('流程推进: ' + next_state, 'success')
        conn.close()
        self._redirect('/project/' + str(pid))
    def _handle_download_rpp(self, pid):
        user = self._logged_in()
        if not user:
            return
        conn = get_db()
        proj = conn.execute("SELECT * FROM projects WHERE id=? AND user_id=?", (pid, user['id'])).fetchone()
        conn.close()
        if not proj:
            self._flash('RPP 文件未找到', 'danger')
            self._redirect('/dashboard')
            return
        proj = dict(proj)
        safe_name = re.sub(r'[^\w\u4e00-\u9fff]+', '_', proj['song_name'])
        filename = safe_name + '_' + proj['grade'] + '_' + str(proj['bpm']) + 'BPM.rpp'
        rpp = gen_rpp(bpm=proj['bpm'], grade=proj['grade'])
        self.send_response(200)
        self.send_header('Content-Type', 'application/octet-stream')
        self.send_header('Content-Disposition', 'attachment; filename="' + filename + '"')
        self.end_headers()
        self.wfile.write(rpp.encode('utf-8'))
    def _handle_delete(self, pid):
        user = self._logged_in()
        if not user:
            return
        conn = get_db()
        conn.execute("DELETE FROM workflow_log WHERE project_id=?", (pid,))
        conn.execute("DELETE FROM projects WHERE id=? AND user_id=?", (pid, user['id']))
        conn.commit()
        conn.close()
        self._flash('项目已删除', 'info')
        self._redirect('/dashboard')
    def _handle_downloads(self):
        user = self._logged_in()
        if not user:
            return
        files = []
        if TOOLSET_DIR.exists():
            for f in sorted(TOOLSET_DIR.iterdir()):
                if f.suffix.lower() in ['.rpp', '.lua', '.pdf', '.md', '.txt', '.py', '.html', '.css', '.js', '.png', '.jpg', '.svg']:
                    descs = {'.md': '文档指南', '.rpp': 'REAPER 工程模板', '.lua': 'ReaScript 脚本'}
                    desc = descs.get(f.suffix, f.name)
                    files.append((f.name, desc))
        html_content = render('downloads')
        list_items = ''
        for fn, desc in files:
            list_items += '<li><div><strong>' + html.escape(fn) + '</strong><p style="font-size:13px;color:#86868b;margin-top:4px">' + html.escape(desc) + '</p></div><a href="/downloads/' + urllib.parse.quote(fn) + '" class="btn btn-secondary btn-sm">下载</a></li>'
        html_content = html_content.replace('{% for f in files %}', list_items)
        html_content = html_content.replace('{% endfor %}', '')
        self._html(html_content)
    def _handle_download_file(self, filename):
        user = self._logged_in()
        if not user:
            return
        file_path = TOOLSET_DIR / filename
        if file_path.is_file():
            mime = mimetypes.guess_type(str(file_path))[0] or 'application/octet-stream'
            self.send_response(200)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Disposition', 'attachment; filename="' + filename + '"')
            self.end_headers()
            self.wfile.write(file_path.read_bytes())
        else:
            self._flash('文件未找到', 'danger')
            self._redirect('/downloads')
    def _handle_api_projects(self):
        user = self._logged_in()
        if not user:
            return
        conn = get_db()
        projects = conn.execute("SELECT id, song_name, grade, bpm, workflow_state, created_at FROM projects WHERE user_id=? ORDER BY created_at DESC", (user['id'],)).fetchall()
        conn.close()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps([dict(p) for p in projects], ensure_ascii=False, default=str).encode('utf-8'))

if __name__ == '__main__':
    PORT = int(os.environ.get("PORT", 5000))
    httpd = http.server.HTTPServer(("0.0.0.0", PORT), MoqiHandler)
    print('\n    ========================================')
    print('      墨栖文创 REAPER 洗歌平台 v1.0')
    print('      http://localhost:' + str(PORT))
    print('      (基于Python标准库)')
    print('    ========================================\n')
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print('\nServer stopped.')
        httpd.server_close()

