"""NetSentinel desktop client. Live mode only; telemetry comes from the local collector."""
import csv, os, threading, tkinter as tk
from tkinter import ttk, messagebox, filedialog
from datetime import datetime
from pathlib import Path
from netsentinel_core import Store, Monitor, API, utc_now

BG='#0b1020'; PANEL='#121a2d'; PANEL2='#172238'; TEXT='#e6edf7'; MUTED='#8292ad'; CYAN='#39d4ff'; GREEN='#38d996'; AMBER='#ffbf5f'; RED='#ff6378'; PURPLE='#b69cff'

def show(v, suffix=''):
    if v is None:return 'Unavailable'
    if isinstance(v,float):return f'{v:.1f}{suffix}'
    return f'{v}{suffix}'

class NetSentinel:
 def __init__(self,root):
  self.root=root;root.title('NetSentinel | Live Network Monitoring');root.geometry('1440x920');root.minsize(1080,720);root.configure(bg=BG)
  self.store=Store();self.monitor=Monitor(self.store);self.api=None;self.selected=None;self.last_api_error=None
  if not self.store.targets():self.store.add_target('Local machine','127.0.0.1',None,5,150,20)
  self.monitor.start()
  try:self.api=API(self.store);self.api.start();api_text='API online · 127.0.0.1:8765'
  except OSError:api_text='API unavailable · port 8765 is in use'
  self.build(api_text);self.refresh()
  root.protocol('WM_DELETE_WINDOW',self.close)
 def build(self,api_text):
  head=tk.Frame(self.root,bg=BG);head.pack(fill='x',padx=22,pady=(16,11))
  tk.Label(head,text='◈',fg=CYAN,bg=BG,font=('Segoe UI',26,'bold')).pack(side='left',padx=(0,12))
  title=tk.Frame(head,bg=BG);title.pack(side='left');tk.Label(title,text='NetSentinel',fg=TEXT,bg=BG,font=('Segoe UI',21,'bold')).pack(anchor='w');tk.Label(title,text='LIVE NETWORK MONITORING  /  FAULT DETECTION',fg=MUTED,bg=BG,font=('Segoe UI',9,'bold')).pack(anchor='w')
  self.live=tk.Label(head,text='● LIVE COLLECTION',fg=GREEN,bg='#102b2b',font=('Segoe UI',9,'bold'),padx=12,pady=8);self.live.pack(side='right',padx=(10,0),pady=5)
  self.api_label=tk.Label(head,text=api_text,fg=CYAN if self.api else AMBER,bg=BG,font=('Segoe UI',9));self.api_label.pack(side='right',pady=5)
  toolbar=tk.Frame(self.root,bg=BG);toolbar.pack(fill='x',padx=22,pady=(0,12))
  for label,cmd,color in [('＋ Add target',self.add_target,CYAN),('− Remove selected',self.remove_target,RED),('↻ Check now',self.check_now,GREEN),('⬇ Export CSV',self.export_csv,PURPLE)]:
   tk.Button(toolbar,text=label,command=cmd,fg=color,bg=PANEL2,activebackground='#23314a',activeforeground=color,relief='flat',font=('Segoe UI',9,'bold'),padx=12,pady=8,cursor='hand2').pack(side='left',padx=(0,7))
  self.cards={}; row=tk.Frame(self.root,bg=BG);row.pack(fill='x',padx=22,pady=(0,12))
  for label,key,color in [('TARGETS','targets',CYAN),('ONLINE','online',GREEN),('OFFLINE','offline',RED),('ACTIVE ALERTS','alerts',AMBER),('LOCAL CPU / MEMORY','resources',PURPLE),('LOCAL TX / RX','traffic',CYAN)]:
   box=tk.Frame(row,bg=PANEL,highlightthickness=1,highlightbackground='#202d45');box.pack(side='left',fill='x',expand=True,padx=(0,8));tk.Label(box,text=label,fg=MUTED,bg=PANEL,font=('Segoe UI',8,'bold')).pack(anchor='w',padx=12,pady=(9,0));v=tk.Label(box,text='—',fg=color,bg=PANEL,font=('Segoe UI',16,'bold'));v.pack(anchor='w',padx=12,pady=(1,9));self.cards[key]=v
  self.note=tk.Label(self.root,text='Live collector feeds threshold rules. SOFI Random Forest is skipped until matching 33-field SNMP telemetry is available.',fg=MUTED,bg=BG,font=('Segoe UI',9));self.note.pack(anchor='w',padx=24,pady=(0,8))
  style=ttk.Style();style.theme_use('clam');style.configure('TNotebook',background=BG,borderwidth=0);style.configure('TNotebook.Tab',background=PANEL2,foreground=TEXT,padding=(15,8),font=('Segoe UI',9,'bold'));style.map('TNotebook.Tab',background=[('selected',PANEL)],foreground=[('selected',CYAN)]);style.configure('Treeview',background='#0e1729',fieldbackground='#0e1729',foreground=TEXT,rowheight=29,borderwidth=0,font=('Segoe UI',9));style.configure('Treeview.Heading',background=PANEL2,foreground=MUTED,relief='flat',font=('Segoe UI',8,'bold'));style.map('Treeview',background=[('selected','#263752')])
  nb=ttk.Notebook(self.root);nb.pack(fill='both',expand=True,padx=22,pady=(0,8))
  live_tab=tk.Frame(nb,bg=PANEL);alert_tab=tk.Frame(nb,bg=PANEL);nb.add(live_tab,text='  MONITORING  ');nb.add(alert_tab,text='  FAULTS & HISTORY  ')
  self.targets_tree=self.tree(live_tab,[('name','TARGET',150),('host','HOST',170),('status','STATUS',95),('latency','LATENCY',95),('loss','ICMP MISS (ROLLING)',130),('service','SERVICE',100),('last','LAST CHECK',175)])
  self.targets_tree.pack(fill='both',expand=True,padx=12,pady=(12,7));self.targets_tree.bind('<<TreeviewSelect>>',self.select_target)
  chartbox=tk.Frame(live_tab,bg='#0e1729');chartbox.pack(fill='x',padx=12,pady=(0,12));cap=tk.Frame(chartbox,bg='#0e1729');cap.pack(fill='x',padx=10,pady=(8,0));tk.Label(cap,text='LATENCY HISTORY',fg=TEXT,bg='#0e1729',font=('Segoe UI',9,'bold')).pack(side='left');self.chart_info=tk.Label(cap,text='Select a target to view measured history',fg=MUTED,bg='#0e1729',font=('Segoe UI',8));self.chart_info.pack(side='right');self.chart=tk.Canvas(chartbox,height=175,bg='#0e1729',highlightthickness=0);self.chart.pack(fill='x',padx=8,pady=4)
  alert_actions=tk.Frame(alert_tab,bg=PANEL);alert_actions.pack(fill='x',padx=12,pady=(12,6));tk.Label(alert_actions,text='Faults are based on measured checks and configured thresholds.',fg=MUTED,bg=PANEL,font=('Segoe UI',9)).pack(side='left');tk.Button(alert_actions,text='Acknowledge',command=lambda:self.alert_action('ack'),fg=AMBER,bg=PANEL2,relief='flat',font=('Segoe UI',9,'bold'),padx=11,pady=7).pack(side='right',padx=(6,0));tk.Button(alert_actions,text='Resolve selected',command=lambda:self.alert_action('resolve'),fg=GREEN,bg=PANEL2,relief='flat',font=('Segoe UI',9,'bold'),padx=11,pady=7).pack(side='right')
  self.alert_tree=self.tree(alert_tab,[('severity','SEVERITY',85),('status','STATUS',85),('target','TARGET',145),('fault','FAULT',130),('observed','OBSERVED',140),('reason','DETECTION EVIDENCE',250),('recommendation','SUGGESTED ACTION',260),('start','DETECTED',165),('ack','ACKNOWLEDGED',145)])
  self.alert_tree.pack(fill='both',expand=True,padx=12,pady=(0,12))
  foot=tk.Frame(self.root,bg=BG);foot.pack(fill='x',padx=22,pady=(2,12));tk.Label(foot,text=f'Checks only configured targets  ·  SQLite history  ·  database: {self.store.__class__.__name__}  ·  API bound to loopback only',fg=MUTED,bg=BG,font=('Segoe UI',8)).pack(side='left');tk.Label(foot,text='Rule alerts active · SOFI RF gated: verified SNMP schema required',fg=MUTED,bg=BG,font=('Segoe UI',8)).pack(side='right')
 def tree(self,parent,columns):
  t=ttk.Treeview(parent,columns=[c[0] for c in columns],show='headings',selectmode='browse')
  for name,label,width in columns:t.heading(name,text=label);t.column(name,width=width,minwidth=65,stretch=name in ('reason','name'))
  return t
 def add_target(self):
  win=tk.Toplevel(self.root);win.title('Add authorized monitoring target');win.configure(bg=PANEL);win.resizable(False,False);win.transient(self.root);win.grab_set()
  fields=[('Display name','name',''),('IP address or hostname','host',''),('TCP service port (optional)','port',''),('Check interval, seconds','interval','5'),('Latency alert threshold, ms','latency','150'),('Packet loss alert threshold, %','loss','20')];vars={}
  for i,(label,key,default) in enumerate(fields):
   tk.Label(win,text=label,fg=MUTED,bg=PANEL,font=('Segoe UI',9)).grid(row=i,column=0,sticky='w',padx=14,pady=6);v=tk.StringVar(value=default);vars[key]=v;tk.Entry(win,textvariable=v,bg='#0e1729',fg=TEXT,insertbackground=TEXT,relief='flat',width=30).grid(row=i,column=1,padx=14,pady=6,ipady=6)
  tk.Label(win,text='Only add hosts/services you are authorized to monitor.',fg=AMBER,bg=PANEL,font=('Segoe UI',8)).grid(row=6,column=0,columnspan=2,sticky='w',padx=14,pady=5)
  def save():
   try:self.store.add_target(vars['name'].get(),vars['host'].get(),vars['port'].get() or None,vars['interval'].get(),vars['latency'].get(),vars['loss'].get());win.destroy();self.refresh()
   except (ValueError,TypeError) as e:messagebox.showerror('Invalid target',str(e),parent=win)
  tk.Button(win,text='Add target',command=save,fg=BG,bg=CYAN,relief='flat',font=('Segoe UI',9,'bold'),padx=16,pady=7).grid(row=7,column=0,columnspan=2,pady=12)
 def selected_target_id(self):
  sel=self.targets_tree.selection();return int(sel[0]) if sel else None
 def remove_target(self):
  tid=self.selected_target_id()
  if not tid:messagebox.showinfo('Select target','Select a monitoring target first.');return
  target=next((t for t in self.store.targets() if t['id']==tid),None)
  if target and messagebox.askyesno('Remove target',f"Remove {target['name']} and its stored measurements and alerts?"):self.store.remove_target(tid);self.selected=None;self.refresh()
 def check_now(self):
  tid=self.selected_target_id()
  if not tid:messagebox.showinfo('Select target','Select a target to request an immediate check.');return
  target=next((t for t in self.store.targets() if t['id']==tid),None)
  if target:threading.Thread(target=self._check,args=(target,),daemon=True).start()
 def _check(self,target):
  try:self.monitor.sample(target)
  except Exception as e:self.root.after(0,lambda:messagebox.showerror('Check failed',str(e)))
 def select_target(self,_=None):
  self.selected=self.selected_target_id();self.draw_chart()
 def alert_action(self,action):
  sel=self.alert_tree.selection()
  if not sel:messagebox.showinfo('Select alert','Select an alert first.');return
  self.store.alert_action(int(sel[0]),action);self.refresh()
 def export_csv(self):
  path=filedialog.asksaveasfilename(defaultextension='.csv',filetypes=[('CSV files','*.csv')],initialfile='netsentinel-measurements.csv')
  if path:
   Path(path).write_text(self.store.export_csv(),encoding='utf-8-sig');messagebox.showinfo('Export complete',f'Measurement history saved to:\n{path}')
 def draw_chart(self):
  c=self.chart;c.delete('all');w=max(200,c.winfo_width());h=175;pad=34;c.create_line(pad,12,pad,h-24,fill='#354a68');c.create_line(pad,h-24,w-10,h-24,fill='#354a68')
  if not self.selected:self.chart_info.config(text='Select a target to view measured history');return
  target=next((t for t in self.store.targets() if t['id']==self.selected),None)
  data=self.store.history(self.selected,60);valid=[r for r in data if r['latency_ms'] is not None]
  self.chart_info.config(text=f"{target['name'] if target else ''} · measured RTT (ms) · {len(valid)} successful samples")
  if not valid:c.create_text(w/2,h/2,text='No successful latency measurements yet',fill=MUTED,font=('Segoe UI',9));return
  ymax=max(10,max(float(r['latency_ms']) for r in valid)*1.2);threshold=float(target['latency_limit_ms']) if target else ymax
  ty=h-24-(min(threshold,ymax)/ymax)*(h-42);c.create_line(pad,ty,w-10,ty,fill=AMBER,dash=(4,3));c.create_text(w-12,max(12,ty-8),anchor='e',text=f'threshold {threshold:g} ms',fill=AMBER,font=('Segoe UI',8))
  coords=[]
  for i,r in enumerate(data):
   if r['latency_ms'] is None:continue
   x=pad+(i/max(1,len(data)-1))*(w-pad-12);y=h-24-(min(float(r['latency_ms']),ymax)/ymax)*(h-42);coords.extend([x,y])
  if len(coords)>=4:c.create_line(*coords,fill=CYAN,width=2,smooth=True)
  for i in range(0,6):
   v=ymax*i/5;y=h-24-(v/ymax)*(h-42);c.create_text(4,y,text=f'{v:.0f}',anchor='w',fill=MUTED,font=('Consolas',7))
 def refresh(self):
  try:
   targets=self.store.targets();latest=self.store.latest();byid={m['target_id']:m for m in latest};active=self.store.alerts(True);self.cards['targets'].config(text=str(len(targets)));online=sum(1 for m in latest if m['reachable']);self.cards['online'].config(text=str(online));self.cards['offline'].config(text=str(max(0,len(targets)-online)));self.cards['alerts'].config(text=str(len(active)))
   local=next((m for m in latest if m.get('host') in ('127.0.0.1','localhost')),None);tx=local.get('tx_bytes_sec') if local else None;rx=local.get('rx_bytes_sec') if local else None;self.cards['traffic'].config(text=f"{show(tx/1024,' KB/s')} / {show(rx/1024,' KB/s')}" if tx is not None and rx is not None else 'Unavailable');cpu=local.get('cpu_pct') if local else None;mem=local.get('memory_pct') if local else None;self.cards['resources'].config(text=f"{show(cpu,'%')} / {show(mem,'%')}" if cpu is not None and mem is not None else 'Unavailable')
   self.targets_tree.delete(*self.targets_tree.get_children())
   for t in targets:
    m=byid.get(t['id']);status=('ONLINE' if m['reachable'] else 'OFFLINE') if m else 'WAITING';service='—' if not m or m['service_ok'] is None else ('UP' if m['service_ok'] else 'DOWN');last=m['timestamp'] if m else 'Waiting for first sample';values=(t['name'],t['host'],status,show(m['latency_ms'],' ms') if m else 'Unavailable',show(m['packet_loss_pct'],'%') if m else 'Unavailable',service,last);self.targets_tree.insert('','end',iid=str(t['id']),values=values,tags=(status,))
   self.targets_tree.tag_configure('OFFLINE',foreground=RED);self.targets_tree.tag_configure('ONLINE',foreground=GREEN);self.targets_tree.tag_configure('WAITING',foreground=AMBER)
   if self.selected and self.targets_tree.exists(str(self.selected)):self.targets_tree.selection_set(str(self.selected))
   self.alert_tree.delete(*self.alert_tree.get_children())
   for a in self.store.alerts():self.alert_tree.insert('','end',iid=str(a['id']),values=(a['severity'].upper(),a['status'].upper(),f"{a['name']} ({a['host']})",a['category'].replace('_',' '),a['observed'],a['reason'],a['recommendation'],a['started_at'],a['acknowledged_at'] or '—'))
   if self.selected:self.draw_chart()
  except Exception as e:self.note.config(text=f'Data refresh issue: {type(e).__name__}',fg=RED)
  self.root.after(1200,self.refresh)
 def close(self):
  self.monitor.stop()
  if self.api:
   threading.Thread(target=self.api.stop,daemon=True).start()
  self.root.destroy()

def main():
 root=tk.Tk();NetSentinel(root);root.mainloop()
if __name__=='__main__':main()

