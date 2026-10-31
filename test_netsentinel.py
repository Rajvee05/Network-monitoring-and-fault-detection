"""Controlled tests for storage, probes, detector transitions, and REST integration."""
import json, socket, tempfile, threading, unittest, urllib.request
from pathlib import Path
import netsentinel_core as core

class FakeCollector:
    def __init__(self, results): self.results=iter(results);self.loss_window={}
    def check(self,target): return next(self.results)

class NetSentinelTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.old_db=core.DB_PATH;core.DB_PATH=Path(self.tmp.name)/'test.sqlite3';self.store=core.Store()
    def tearDown(self):core.DB_PATH=self.old_db;self.tmp.cleanup()
    def add(self,host='127.0.0.1',port=None):return self.store.add_target('test target',host,port,2,40,20)
    def test_target_validation_and_storage(self):
        tid=self.add();self.assertEqual(self.store.targets()[0]['id'],tid)
        with self.assertRaises(ValueError):self.store.add_target('bad','-n 100',None)
        with self.assertRaises(ValueError):self.store.add_target('bad','localhost',70000)
        self.store.remove_target(tid);self.assertEqual(self.store.targets(),[])
    def test_actual_loopback_tcp_measurement(self):
        server=socket.socket();server.bind(('127.0.0.1',0));server.listen();port=server.getsockname()[1]
        thread=threading.Thread(target=lambda: server.accept()[0].close(),daemon=True);thread.start()
        try:
            ok,lat,error=core.Collector().service('127.0.0.1',port)
            self.assertTrue(ok,error);self.assertIsNotNone(lat);self.assertGreaterEqual(lat,0)
        finally:server.close()
    def test_icmp_success_and_failure_parsing(self):
        from unittest.mock import patch
        from types import SimpleNamespace
        c=core.Collector()
        with patch.object(core.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout='Reply time=12ms',stderr='')):
            self.assertEqual(c.ping('127.0.0.1')[:2],(True,12.0))
        with patch.object(core.subprocess,'run',return_value=SimpleNamespace(returncode=1,stdout='',stderr='')):
            self.assertFalse(c.ping('192.0.2.1')[0])
    def test_alert_duplicate_suppression_ack_and_recovery(self):
        tid=self.add(port=8080)
        failure={'reachable':False,'method':'ICMP','latency_ms':None,'service_ok':False,'packet_loss_pct':100.0,'cpu_pct':None,'memory_pct':None,'tx_bytes_sec':None,'rx_bytes_sec':None,'error':'timeout','fail_streak':2,'good_streak':0}
        good={'reachable':True,'method':'ICMP','latency_ms':5.0,'service_ok':True,'packet_loss_pct':0.0,'cpu_pct':None,'memory_pct':None,'tx_bytes_sec':None,'rx_bytes_sec':None,'error':None,'fail_streak':0,'good_streak':1}
        fake=FakeCollector([failure,failure,good]);fake.loss_window[tid]=[1,1,1,1,1]
        monitor=core.Monitor(self.store,fake);target=self.store.targets()[0]
        monitor.sample(target);monitor.sample(target)
        active=self.store.alerts(True);self.assertEqual(len(active),3) # unreachable + service + ICMP loss
        self.store.alert_action(active[0]['id'],'ack');monitor.sample(target)
        self.assertEqual(self.store.alerts(True),[])
        self.assertEqual(len(self.store.alerts()),3);self.assertTrue(any(a['acknowledged_at'] for a in self.store.alerts()))
    def test_live_measurement_storage_fault_recovery_and_api(self):
        listener=socket.socket();listener.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1);listener.bind(('127.0.0.1',0));listener.listen();port=listener.getsockname()[1]
        stop=threading.Event()
        def accept_loop(sock):
            sock.settimeout(.2)
            while not stop.is_set():
                try:conn,_=sock.accept();conn.close()
                except socket.timeout:continue
                except OSError:return
        worker=threading.Thread(target=accept_loop,args=(listener,),daemon=True);worker.start()
        tid=self.add('127.0.0.1',port);target=self.store.targets()[0];monitor=core.Monitor(self.store)
        api=core.API(self.store,port=0);api.start();api_port=api.httpd.server_address[1]
        try:
            first=monitor.sample(target);self.assertTrue(first['reachable']);self.assertTrue(first['service_ok'])
            saved=self.store.history(tid);self.assertEqual(len(saved),1);self.assertIsNotNone(saved[-1]['latency_ms'])
            listener.close();worker.join(timeout=.5)
            failed=monitor.sample(target);self.assertFalse(failed['service_ok'])
            active=[a for a in self.store.alerts(True) if a['category']=='service'];self.assertEqual(len(active),1)
            listener=socket.socket();listener.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1);listener.bind(('127.0.0.1',port));listener.listen();worker=threading.Thread(target=accept_loop,args=(listener,),daemon=True);worker.start()
            recovered=monitor.sample(target);self.assertTrue(recovered['service_ok'])
            self.assertFalse([a for a in self.store.alerts(True) if a['category']=='service'])
            with urllib.request.urlopen(f'http://127.0.0.1:{api_port}/api/metrics?target_id={tid}') as response:
                history=json.load(response);self.assertGreaterEqual(len(history),3);self.assertIn('latency_ms',history[-1])
            with urllib.request.urlopen(f'http://127.0.0.1:{api_port}/api/alerts') as response:
                alerts=json.load(response);self.assertTrue(any(a['category']=='service' and a['status']=='resolved' for a in alerts))
        finally:
            stop.set();listener.close();api.stop()

    def test_real_collector_measurement_gets_model_compatibility_status_without_rf_mapping(self):
        from unittest.mock import patch
        from ml import predict as inference
        tid=self.add()
        live={'reachable':False,'method':'ICMP unavailable','latency_ms':None,'service_ok':None,
              'packet_loss_pct':100.0,'cpu_pct':None,'memory_pct':None,'tx_bytes_sec':None,
              'rx_bytes_sec':None,'error':'timeout','ping_ok':False,'fail_streak':2,'good_streak':0}
        fake=FakeCollector([live]);fake.loss_window[tid]=[1,1,1,1,1]
        with patch.object(inference,'predict_network_fault',side_effect=AssertionError('live collector data must not enter SOFI RF')):
            result=core.Monitor(self.store,fake).sample(self.store.targets()[0])
        self.assertEqual(result['model_assessment']['status'],'not_supported')
        self.assertFalse(result['model_assessment']['model_executed'])
        self.assertTrue(result['model_assessment']['missing_required_features'])
        self.assertTrue(any(a['category']=='unreachable' for a in self.store.alerts(True)))
    def test_api_end_to_end(self):
        api=core.API(self.store,port=0);api.start();port=api.httpd.server_address[1]
        try:
            req=urllib.request.Request(f'http://127.0.0.1:{port}/api/targets',data=json.dumps({'name':'loopback','host':'127.0.0.1','interval_sec':2}).encode(),headers={'Content-Type':'application/json'},method='POST')
            with urllib.request.urlopen(req) as r:self.assertEqual(r.status,201);tid=json.load(r)['id']
            with urllib.request.urlopen(f'http://127.0.0.1:{port}/api/targets') as r:self.assertEqual(json.load(r)[0]['id'],tid)
            with urllib.request.urlopen(f'http://127.0.0.1:{port}/api/health') as r:self.assertEqual(json.load(r)['mode'],'live')
            with urllib.request.urlopen(f'http://127.0.0.1:{port}/api/export.csv') as r:self.assertIn('timestamp,name,host',r.read().decode())
            req=urllib.request.Request(f'http://127.0.0.1:{port}/api/targets/{tid}',method='DELETE')
            with urllib.request.urlopen(req) as r:self.assertTrue(json.load(r)['removed'])
        finally:api.stop()

if __name__=='__main__':unittest.main(verbosity=2)
