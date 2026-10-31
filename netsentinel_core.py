"""Live monitoring backend for NetSentinel. Only checks configured targets."""
from __future__ import annotations
import csv, io, json, os, platform, re, socket, sqlite3, subprocess, threading, time
from contextlib import contextmanager
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

APP_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get('NETSENTINEL_DB', APP_DIR / 'netsentinel.sqlite3'))

def utc_now(): return datetime.now(timezone.utc).isoformat(timespec='seconds')

def db_connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c=sqlite3.connect(DB_PATH, timeout=5); c.row_factory=sqlite3.Row; return c

@contextmanager
def db_session():
    c=db_connect()
    try:
        yield c
        c.commit()
    except Exception:
        c.rollback(); raise
    finally:c.close()

SCHEMA='''
CREATE TABLE IF NOT EXISTS targets(id INTEGER PRIMARY KEY, name TEXT NOT NULL, host TEXT NOT NULL, port INTEGER, interval_sec INTEGER NOT NULL DEFAULT 5, latency_limit_ms REAL NOT NULL DEFAULT 150, loss_limit_pct REAL NOT NULL DEFAULT 20, created_at TEXT NOT NULL, last_checked TEXT);
CREATE TABLE IF NOT EXISTS measurements(id INTEGER PRIMARY KEY, target_id INTEGER NOT NULL, timestamp TEXT NOT NULL, reachable INTEGER NOT NULL, method TEXT NOT NULL, latency_ms REAL, service_ok INTEGER, packet_loss_pct REAL, cpu_pct REAL, memory_pct REAL, tx_bytes_sec REAL, rx_bytes_sec REAL, error TEXT, FOREIGN KEY(target_id) REFERENCES targets(id));
CREATE INDEX IF NOT EXISTS idx_measurements_target_time ON measurements(target_id,timestamp);
CREATE TABLE IF NOT EXISTS alerts(id INTEGER PRIMARY KEY, target_id INTEGER NOT NULL, category TEXT NOT NULL, severity TEXT NOT NULL, started_at TEXT NOT NULL, updated_at TEXT NOT NULL, observed TEXT NOT NULL, reason TEXT NOT NULL, recommendation TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active', acknowledged_at TEXT, resolved_at TEXT, FOREIGN KEY(target_id) REFERENCES targets(id));
CREATE INDEX IF NOT EXISTS idx_alerts_status ON alerts(status,target_id,category);
'''

def init_db():
    with db_session() as c: c.execute('PRAGMA journal_mode=WAL'); c.executescript(SCHEMA)

def validate_host(host):
    host=host.strip()
    if not host or len(host)>253 or host.startswith('-') or not re.fullmatch(r'[A-Za-z0-9.:-]+',host): raise ValueError('Enter a valid IPv4 address or DNS hostname.')
    return host

class Store:
    def __init__(self): init_db()
    def targets(self):
        with db_session() as c:return [dict(r) for r in c.execute('SELECT * FROM targets ORDER BY id')]
    def add_target(self,name,host,port=None,interval_sec=5,latency_limit_ms=150,loss_limit_pct=20):
        name=name.strip() or host; host=validate_host(host)
        if port in ('',None):port=None
        else:
            port=int(port)
            if not 1<=port<=65535:raise ValueError('Port must be between 1 and 65535.')
        interval_sec=int(interval_sec); latency_limit_ms=float(latency_limit_ms); loss_limit_pct=float(loss_limit_pct)
        if not 2<=interval_sec<=3600:raise ValueError('Interval must be between 2 and 3600 seconds.')
        if not 1<=latency_limit_ms<=60000:raise ValueError('Latency threshold must be 1–60000 ms.')
        if not 0<=loss_limit_pct<=100:raise ValueError('Packet loss threshold must be 0–100%.')
        with db_session() as c:
            cur=c.execute('INSERT INTO targets(name,host,port,interval_sec,latency_limit_ms,loss_limit_pct,created_at) VALUES(?,?,?,?,?,?,?)',(name,host,port,interval_sec,latency_limit_ms,loss_limit_pct,utc_now()))
            return cur.lastrowid
    def remove_target(self,target_id):
        with db_session() as c:
            c.execute('DELETE FROM measurements WHERE target_id=?',(target_id,));c.execute('DELETE FROM alerts WHERE target_id=?',(target_id,));c.execute('DELETE FROM targets WHERE id=?',(target_id,))
    def due_targets(self):
        now=time.time()
        result=[]
        for t in self.targets():
            try:last=datetime.fromisoformat(t['last_checked']).timestamp() if t['last_checked'] else 0
            except ValueError:last=0
            if now-last>=t['interval_sec']:result.append(t)
        return result
    def record(self,target_id,measurement):
        stamp=utc_now()
        with db_session() as c:
            c.execute('INSERT INTO measurements(target_id,timestamp,reachable,method,latency_ms,service_ok,packet_loss_pct,cpu_pct,memory_pct,tx_bytes_sec,rx_bytes_sec,error) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
              (target_id,stamp,int(measurement['reachable']),measurement['method'],measurement.get('latency_ms'),None if measurement.get('service_ok') is None else int(measurement['service_ok']),measurement.get('packet_loss_pct'),measurement.get('cpu_pct'),measurement.get('memory_pct'),measurement.get('tx_bytes_sec'),measurement.get('rx_bytes_sec'),measurement.get('error')))
            c.execute('UPDATE targets SET last_checked=? WHERE id=?',(stamp,target_id))
            return stamp
    def history(self,target_id,limit=300):
        with db_session() as c:return [dict(r) for r in c.execute('SELECT * FROM measurements WHERE target_id=? ORDER BY id DESC LIMIT ?',(target_id,limit))][::-1]
    def latest(self,target_id=None):
        with db_session() as c:
            if target_id:return dict(c.execute('SELECT * FROM measurements WHERE target_id=? ORDER BY id DESC LIMIT 1',(target_id,)).fetchone() or {})
            return [dict(r) for r in c.execute('SELECT m.*,t.name,t.host FROM measurements m JOIN targets t ON t.id=m.target_id WHERE m.id IN (SELECT MAX(id) FROM measurements GROUP BY target_id) ORDER BY t.id')]
    def alerts(self,active_only=False):
        sql='SELECT a.*,t.name,t.host FROM alerts a JOIN targets t ON t.id=a.target_id'
        if active_only:sql+=" WHERE a.status='active'"
        sql+=' ORDER BY a.id DESC'
        with db_session() as c:return [dict(r) for r in c.execute(sql)]
    def transition_alert(self,target_id,category,condition,severity,observed,reason,recommendation):
        stamp=utc_now()
        with db_session() as c:
            row=c.execute("SELECT * FROM alerts WHERE target_id=? AND category=? AND status='active' ORDER BY id DESC LIMIT 1",(target_id,category)).fetchone()
            if condition:
                if row:
                    c.execute('UPDATE alerts SET updated_at=?,observed=?,reason=?,severity=? WHERE id=?',(stamp,observed,reason,severity,row['id']))
                else:c.execute('INSERT INTO alerts(target_id,category,severity,started_at,updated_at,observed,reason,recommendation) VALUES(?,?,?,?,?,?,?,?)',(target_id,category,severity,stamp,stamp,observed,reason,recommendation))
            elif row:
                c.execute("UPDATE alerts SET status='resolved',resolved_at=?,updated_at=? WHERE id=?",(stamp,stamp,row['id']))
                return {'recovered':dict(row)}
            return {'active':dict(row) if row and condition else None}
    def alert_action(self,alert_id,action):
        stamp=utc_now()
        with db_session() as c:
            if action=='ack':c.execute("UPDATE alerts SET acknowledged_at=? WHERE id=? AND status='active'",(stamp,alert_id))
            elif action=='resolve':c.execute("UPDATE alerts SET status='resolved',resolved_at=?,updated_at=? WHERE id=? AND status='active'",(stamp,stamp,alert_id))
            else:raise ValueError('Action must be ack or resolve.')
            return c.execute('SELECT * FROM alerts WHERE id=?',(alert_id,)).fetchone() is not None
    def export_csv(self):
        rows=[]
        with db_session() as c:rows=[dict(r) for r in c.execute('SELECT m.*,t.name,t.host FROM measurements m JOIN targets t ON t.id=m.target_id ORDER BY m.id')]
        out=io.StringIO();fields=['timestamp','name','host','reachable','method','latency_ms','service_ok','packet_loss_pct','cpu_pct','memory_pct','tx_bytes_sec','rx_bytes_sec','error'];w=csv.DictWriter(out,fieldnames=fields);w.writeheader();w.writerows(rows);return out.getvalue()

try:
    import psutil
except ImportError: psutil=None

class Collector:
    def __init__(self): self.last_net=None;self.last_net_time=None;self.loss_window={};self.fail_streak={};self.good_streak={}
    def ping(self,host,timeout=1.5):
        if platform.system()=='Windows':args=['ping.exe','-n','1','-w',str(int(timeout*1000)),host]
        else:args=['ping','-c','1','-W',str(max(1,int(timeout))),host]
        try:
            p=subprocess.run(args,capture_output=True,text=True,timeout=timeout+1,check=False,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            output=p.stdout+'\n'+p.stderr
            if p.returncode==0:
                match=re.search(r'time[=<]\s*([0-9.]+)\s*ms',output,re.I)
                latency=float(match.group(1)) if match else None
                return True,latency,None
            return False,None,'ICMP echo timed out or was blocked.'
        except (OSError,subprocess.TimeoutExpired) as e:return False,None,f'Ping unavailable: {type(e).__name__}.'
    def service(self,host,port,timeout=1.5):
        started=time.perf_counter()
        try:
            with socket.create_connection((host,int(port)),timeout=timeout):return True,(time.perf_counter()-started)*1000,None
        except OSError as e:return False,None,f'TCP service check failed: {e.__class__.__name__}.'
    def local_metrics(self):
        cpu=memory=tx=rx=None
        if psutil:
            try:cpu=psutil.cpu_percent(interval=None);memory=psutil.virtual_memory().percent
            except Exception:pass
            try:
                counters=psutil.net_io_counters();now=time.monotonic()
                if self.last_net is not None:
                    dt=max(.001,now-self.last_net_time);tx=max(0,(counters.bytes_sent-self.last_net[0])/dt);rx=max(0,(counters.bytes_recv-self.last_net[1])/dt)
                self.last_net=(counters.bytes_sent,counters.bytes_recv);self.last_net_time=now
            except Exception:pass
        return cpu,memory,tx,rx
    def check(self,target):
        host=target['host'];ping_ok,latency,ping_err=self.ping(host)
        service_ok=service_latency=None;service_err=None
        if target['port'] is not None:service_ok,service_latency,service_err=self.service(host,target['port'])
        reachable=ping_ok or service_ok is True
        if latency is None and service_latency is not None:latency=service_latency
        key=target['id'];window=self.loss_window.setdefault(key,[]);window.append(0 if ping_ok else 1);del window[:-20]
        loss=100*sum(window)/len(window)
        if not reachable:self.fail_streak[key]=self.fail_streak.get(key,0)+1;self.good_streak[key]=0
        else:self.good_streak[key]=self.good_streak.get(key,0)+1;self.fail_streak[key]=0
        cpu,memory,tx,rx=self.local_metrics()
        errors=[]
        if ping_err:errors.append(ping_err)
        if service_err:errors.append(service_err)
        return {'reachable':reachable,'method':'ICMP echo RTT' if ping_ok else ('TCP connect time' if service_latency is not None else 'ICMP unavailable'),'latency_ms':latency,'service_ok':service_ok,'packet_loss_pct':loss,'cpu_pct':cpu,'memory_pct':memory,'tx_bytes_sec':tx,'rx_bytes_sec':rx,'error':' '.join(errors) or None,
          'ping_ok':ping_ok,'fail_streak':self.fail_streak[key],'good_streak':self.good_streak[key]}

class Monitor:
    def __init__(self,store=None,collector=None):self.store=store or Store();self.collector=collector or Collector();self.stop_event=threading.Event();self.thread=None
    def sample(self,target):
        m=self.collector.check(target);self.store.record(target['id'],m)
        # Model compatibility is assessed on the real collected measurement. It does
        # not replace or affect the independently maintained threshold alerts.
        try:
            from ml.predict import predict_live_measurement
            m['model_assessment']=predict_live_measurement(m)
        except Exception as exc:
            # Keep monitoring available if the optional saved model cannot be loaded.
            m['model_assessment']={'status':'unavailable','reason':f'{type(exc).__name__}: {exc}','model_executed':False}
        failed=m['fail_streak']>=2
        self.store.transition_alert(target['id'],'unreachable',failed and not m['reachable'],'critical',str(m['reachable']),'Two consecutive reachability checks failed.' if failed else '', 'Check host power, routing, firewall policy, and target address.')
        service_failed=target['port'] is not None and m['service_ok'] is False
        self.store.transition_alert(target['id'],'service',service_failed,'high',f"TCP/{target['port']} unavailable",f"TCP connection to {target['host']}:{target['port']} failed.",'Confirm the service is listening and the configured port is reachable.')
        latency_bad=m['latency_ms'] is not None and m['latency_ms']>target['latency_limit_ms']
        latency_observed=f"{m['latency_ms']:.1f} ms" if m['latency_ms'] is not None else 'Unavailable'
        self.store.transition_alert(target['id'],'latency',latency_bad,'medium',latency_observed,f"Measured latency exceeds {target['latency_limit_ms']:.1f} ms threshold.",'Check congestion, route quality, and endpoint load; compare with the historical trend.')
        loss_bad=len(self.collector.loss_window[target['id']])>=5 and m['packet_loss_pct']>target['loss_limit_pct']
        self.store.transition_alert(target['id'],'packet_loss',loss_bad,'high',f"{m['packet_loss_pct']:.1f}%",f"Rolling 20-sample packet loss exceeds {target['loss_limit_pct']:.1f}% threshold.",'Compare ICMP with the configured TCP check; firewall filtering can cause echo misses without service loss.')
        return m
    def run(self):
        while not self.stop_event.is_set():
            for target in self.store.due_targets():
                if self.stop_event.is_set():break
                try:self.sample(target)
                except Exception as e:print('Monitoring cycle error:',type(e).__name__)
            self.stop_event.wait(.25)
    def start(self):
        self.thread=threading.Thread(target=self.run,name='netsentinel-monitor',daemon=True);self.thread.start()
    def stop(self):self.stop_event.set()

class API:
    def __init__(self,store,host='127.0.0.1',port=8765):
        self.store=store;owner=self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def reply(self,code,data,ctype='application/json'):
                raw=data.encode() if isinstance(data,str) else json.dumps(data,default=str).encode();self.send_response(code);self.send_header('Content-Type',ctype);self.send_header('Content-Length',str(len(raw)));self.send_header('Access-Control-Allow-Origin','http://127.0.0.1');self.end_headers();self.wfile.write(raw)
            def body(self):return json.loads(self.rfile.read(int(self.headers.get('Content-Length','0'))))
            def do_GET(self):
                path=urlparse(self.path);q=parse_qs(path.query)
                if path.path=='/api/health':return self.reply(200,{'status':'ok','mode':'live','database':str(DB_PATH)})
                if path.path=='/api/targets':return self.reply(200,owner.store.targets())
                if path.path=='/api/metrics':
                    tid=q.get('target_id',[None])[0]
                    return self.reply(200,owner.store.history(int(tid)) if tid else owner.store.latest())
                if path.path=='/api/alerts':return self.reply(200,owner.store.alerts(q.get('active',['false'])[0].lower()=='true'))
                if path.path=='/api/export.csv':return self.reply(200,owner.store.export_csv(),'text/csv; charset=utf-8')
                return self.reply(404,{'error':'not found'})
            def do_POST(self):
                try:
                    path=urlparse(self.path).path;body=self.body()
                    if path=='/api/targets':return self.reply(201,{'id':owner.store.add_target(**body)})
                    m=re.fullmatch(r'/api/alerts/(\d+)/(ack|resolve)',path)
                    if m:return self.reply(200,{'updated':owner.store.alert_action(int(m.group(1)),m.group(2))})
                    return self.reply(404,{'error':'not found'})
                except (ValueError,TypeError,json.JSONDecodeError) as e:return self.reply(400,{'error':str(e)})
            def do_DELETE(self):
                m=re.fullmatch(r'/api/targets/(\d+)',urlparse(self.path).path)
                if not m:return self.reply(404,{'error':'not found'})
                owner.store.remove_target(int(m.group(1)));return self.reply(200,{'removed':True})
        self.httpd=ThreadingHTTPServer((host,port),Handler);self.thread=threading.Thread(target=self.httpd.serve_forever,name='netsentinel-api',daemon=True)
    def start(self):self.thread.start()
    def stop(self):self.httpd.shutdown();self.httpd.server_close()
