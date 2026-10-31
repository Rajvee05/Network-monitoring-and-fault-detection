"""NetSentinel desktop replay and Faculty Demo Mode for the trained SOFI model."""
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox
import joblib
import numpy as np
import pandas as pd
from ml.data_preprocessing import load_sofi, make_splits
from ml.predict import predict_network_fault
from netsentinel_simulation import TopologySimulation, SCENARIOS

ROOT = Path(__file__).resolve().parent
MODEL = ROOT / 'models' / 'sofi_hybrid_fault_model_v1.joblib'
DATA = ROOT / 'data' / 'raw'
BG='#08111f'; PANEL='#101d30'; FG='#e7eef8'; MUTED='#9baec5'; CYAN='#46d8ff'; GREEN='#45d39b'; RED='#ff657a'; AMBER='#ffbf5f'; SELECTED='#b69cff'

# Example addressing/interfaces only. SOFI CoreSwitch-I/II labels do not map to these endpoints.
TOPOLOGY_DEVICES = {
    'pc1': {'name':'PC1','type':'End device · workstation','example_addressing':'Ethernet0: 192.168.10.10/24','interfaces':'Ethernet0 ↔ S1 FastEthernet0/1','role':'Client workstation on the illustrative user LAN.'},
    'pc2': {'name':'PC2','type':'End device · workstation','example_addressing':'Ethernet0: 192.168.10.11/24','interfaces':'Ethernet0 ↔ S1 FastEthernet0/2','role':'Second client workstation on the illustrative user LAN.'},
    's1': {'name':'Switch S1','type':'Layer 2 access switch','example_addressing':'VLAN 1 management: 192.168.10.2/24','interfaces':'Fa0/1 ↔ PC1 · Fa0/2 ↔ PC2 · Gi0/1 ↔ R1 Gi0/0','role':'Illustrative access layer connecting both workstations to the router.'},
    'r1': {'name':'Router R1','type':'Layer 3 router','example_addressing':'Gi0/0: 192.168.10.1/24 · Gi0/1: 192.168.20.1/24','interfaces':'Gi0/0 ↔ S1 Gi0/1 · Gi0/1 ↔ Server1 Fa0','role':'Illustrative gateway between the user LAN and server LAN.'},
    'server1': {'name':'Server1','type':'End device · server','example_addressing':'FastEthernet0: 192.168.20.10/24','interfaces':'FastEthernet0 ↔ R1 Gi0/1','role':'Illustrative service endpoint on the server LAN.'},
}
TOPOLOGY_LINKS = {
    'pc1_s1': {'label':'PC1 Ethernet0 — S1 Fa0/1','ends':('pc1','s1')},
    'pc2_s1': {'label':'PC2 Ethernet0 — S1 Fa0/2','ends':('pc2','s1')},
    's1_r1': {'label':'S1 Gi0/1 — R1 Gi0/0','ends':('s1','r1')},
    'r1_server1': {'label':'R1 Gi0/1 — Server1 Fa0','ends':('r1','server1')},
}
PACKET_TRACER_INTEGRATED = False



def select_faculty_examples(actual_labels, predictions):
    """Select reproducible correctly classified normal and actual-fault test rows."""
    actual = np.asarray(actual_labels, dtype=int)
    predicted = np.asarray(predictions, dtype=int)
    normal = np.flatnonzero((actual == 0) & (predicted == 0))
    faults_detected = np.flatnonzero((actual == 1) & (predicted == 1))
    any_normal = np.flatnonzero(actual == 0)
    any_fault = np.flatnonzero(actual == 1)
    if not len(any_normal) or not len(any_fault):
        raise ValueError('The held-out test data must contain both normal and fault labels.')
    # Prefer a correctly classified example for a clear viva walkthrough. If none
    # exists, show the first genuine label and let the model's miss remain visible.
    return int(normal[0] if len(normal) else any_normal[0]), int(faults_detected[0] if len(faults_detected) else any_fault[0])


def infer_demo_case(rows, actual_labels, index, artifact, expected_prediction=None):
    """Run one selected held-out row through the actual saved-model inference API."""
    record = rows.iloc[int(index)].to_dict()
    result = predict_network_fault(record, artifact=artifact)
    prediction = 1 if result['predicted_condition'] == 'fault' else 0
    if expected_prediction is not None and prediction != int(expected_prediction):
        raise RuntimeError('Single-row inference disagrees with the trained held-out prediction; refusing to display a cached result.')
    return int(actual_labels[int(index)]), result


def build_topology_device_inspection(device_id, heldout_result=None):
    """Return truthful device details without assigning SOFI data to an illustrative node."""
    if device_id not in TOPOLOGY_DEVICES:
        raise KeyError(f'Unknown illustrative topology device: {device_id}')
    device=TOPOLOGY_DEVICES[device_id]
    return {
        'device':device['name'],'type':device['type'],'role':device['role'],
        'example_addressing':device['example_addressing'],'interfaces':device['interfaces'],
        'monitoring_status':'Not measured · recorded SOFI replay has no telemetry mapped to this illustrative device.',
        'link_status':'Illustrative links only · no live interface or Packet Tracer status is available.',
        'fault_attribution':'No fault is attributed to this device. SOFI source labels identify CoreSwitch-I/II records, not PC1, PC2, S1, R1, or Server1.',
        'heldout_result':heldout_result,
    }


def build_topology_link_inspection(link_id, heldout_result=None):
    if link_id not in TOPOLOGY_LINKS:
        raise KeyError(f'Unknown illustrative topology link: {link_id}')
    link=TOPOLOGY_LINKS[link_id]
    return {
        'link':link['label'],'endpoints':[TOPOLOGY_DEVICES[key]['name'] for key in link['ends']],
        'status':'Illustrative connection · not probed and not mapped to a SOFI record.',
        'fault_attribution':'No fault is assigned to this link. The selected SOFI row has no endpoint-level topology mapping.',
        'heldout_result':heldout_result,
    }


class Replay:
    def __init__(self, root):
        self.root=root
        root.title('NetSentinel | Faculty Demo · SOFI + Illustrative Topology')
        root.geometry('1320x850'); root.minsize(1180,740); root.configure(bg=BG)
        self.rows,self.artifact,self.truth,self.pred,self.scores,self.source=self.load_test_data()
        self.demo_normal_index,self.demo_fault_index=select_faculty_examples(self.truth,self.pred)
        self.i=-1; self.running=False; self.after_id=None; self.speed=tk.IntVar(value=1)
        self.demo_cases_shown=0; self.demo_next_index=0; self.mode='demo'; self.current_case=None; self.selected_topology_device=None; self.selected_topology_link=None; self.simulation=TopologySimulation(model_artifact=self.artifact); self.sim_after_id=None; self.packet_items=[]; self.sim_scenario_var=tk.StringVar(value=SCENARIOS['normal'])
        self.build(); self.render_summary(); self.show_ready_state(); self.set_mode('demo')
        root.protocol('WM_DELETE_WINDOW',self.close)

    @staticmethod
    def load_test_data():
        if not MODEL.is_file(): raise FileNotFoundError(f'Trained model not found: {MODEL}')
        X,y,meta,_=load_sofi(DATA)
        _,_,test_idx,_=make_splits(X,y,meta['groups'],seed=42,test_size=.20,validation_size=.25)
        Xtest=X.iloc[test_idx].reset_index(drop=True); truth=np.asarray(y.iloc[test_idx],dtype=int)
        artifact=joblib.load(MODEL)
        probs=artifact['classifier'].predict_proba(Xtest)[:,list(artifact['classifier'].classes_).index(1)]
        scores=np.asarray(probs,dtype=float); pred=(scores>=float(artifact['classifier_threshold'])).astype(int)
        source=np.asarray(meta['source_device'].iloc[test_idx].astype(str))
        return Xtest,artifact,truth,pred,scores,source

    def build(self):
        style=ttk.Style(); style.theme_use('clam')
        style.configure('Treeview',background='#0b1728',fieldbackground='#0b1728',foreground=FG,rowheight=21,font=('Segoe UI',8),borderwidth=0)
        style.configure('Treeview.Heading',background='#192a41',foreground=MUTED,font=('Segoe UI',8,'bold'),relief='flat')
        style.configure('TCombobox',fieldbackground='#14243a',background='#14243a',foreground=FG)
        header=tk.Frame(self.root,bg=BG); header.pack(fill='x',padx=22,pady=(13,3))
        tk.Label(header,text='NetSentinel',fg=CYAN,bg=BG,font=('Segoe UI',22,'bold')).pack(side='left')
        tk.Label(header,text='  /  FACULTY DEMO · SOFI MODEL + ILLUSTRATIVE NETWORK TOPOLOGY',fg=MUTED,bg=BG,font=('Segoe UI',9,'bold')).pack(side='left',pady=(9,0))
        self.source_banner=tk.Label(self.root,text='Recorded SOFI held-out examples · illustrative topology only · no live telemetry or Cisco Packet Tracer connection',fg=AMBER,bg=BG,font=('Segoe UI',9,'bold')); self.source_banner.pack(anchor='w',padx=24,pady=(2,8))

        self.mode_controls=tk.Frame(self.root,bg=BG); self.mode_controls.pack(fill='x',padx=22,pady=(0,8))
        self.demo_controls=tk.Frame(self.mode_controls,bg=BG); self.demo_controls.pack(fill='x')
        self.button(self.demo_controls,'Demonstrate Normal Case',self.demonstrate_normal,GREEN).pack(side='left',padx=(0,7))
        self.button(self.demo_controls,'Demonstrate Actual Fault',self.demonstrate_fault,RED).pack(side='left',padx=7)
        self.button(self.demo_controls,'Run Next Test',self.run_next_test,CYAN).pack(side='left',padx=7)
        self.button(self.demo_controls,'Reset Demo',self.reset_demo,AMBER).pack(side='left',padx=7)
        self.button(self.demo_controls,'Topology Simulation',self.return_to_simulation,AMBER).pack(side='right',padx=7)
        self.button(self.demo_controls,'Return to Full Evaluation',self.return_to_evaluation,MUTED).pack(side='right')

        self.eval_controls=tk.Frame(self.mode_controls,bg=BG)
        self.button(self.eval_controls,'▶  Start replay',self.start,CYAN).pack(side='left',padx=(0,7))
        self.button(self.eval_controls,'Ⅱ  Pause',self.pause,AMBER).pack(side='left',padx=7)
        self.button(self.eval_controls,'›  Next record',self.next_record,GREEN).pack(side='left',padx=7)
        self.button(self.eval_controls,'⚑  Next actual fault',self.next_fault,RED).pack(side='left',padx=7)
        tk.Label(self.eval_controls,text='Replay speed',fg=MUTED,bg=BG,font=('Segoe UI',9)).pack(side='left',padx=(18,5))
        ttk.Combobox(self.eval_controls,textvariable=self.speed,values=(1,5,20),width=4,state='readonly').pack(side='left')
        self.button(self.eval_controls,'Topology Simulation',self.return_to_simulation,AMBER).pack(side='right',padx=7)
        self.button(self.eval_controls,'Return to Faculty Demo',self.return_to_demo,CYAN).pack(side='right')

        self.sim_controls=tk.Frame(self.mode_controls,bg=BG)
        self.button(self.sim_controls,'▶ Start',self.start_simulation,GREEN).pack(side='left',padx=(0,6))
        self.button(self.sim_controls,'Ⅱ Pause',self.pause_simulation,AMBER).pack(side='left',padx=6)
        self.scenario_box=ttk.Combobox(self.sim_controls,textvariable=self.sim_scenario_var,values=tuple(SCENARIOS.values()),width=30,state='readonly'); self.scenario_box.pack(side='left',padx=8)
        self.button(self.sim_controls,'Apply scenario',self.apply_simulation_scenario,RED).pack(side='left',padx=6)
        self.button(self.sim_controls,'Clear fault / begin recovery',self.clear_simulation_fault,GREEN).pack(side='left',padx=6)
        self.button(self.sim_controls,'Reset simulation',self.reset_simulation,AMBER).pack(side='left',padx=6)
        self.button(self.sim_controls,'Faculty Demo',self.return_to_demo,CYAN).pack(side='right',padx=4)
        self.button(self.sim_controls,'Full Evaluation',self.return_to_evaluation,MUTED).pack(side='right',padx=4)

        self.mode_note=tk.Label(self.root,text='',fg=MUTED,bg=BG,font=('Segoe UI',9)); self.mode_note.pack(anchor='w',padx=24,pady=(0,4))
        self.progress=tk.Label(self.root,text=f'0 / {len(self.rows):,}',fg=CYAN,bg=BG,font=('Consolas',9,'bold')); self.progress.pack(anchor='e',padx=24,pady=(0,2))
        main=tk.Frame(self.root,bg=BG); main.pack(fill='both',expand=True,padx=22,pady=4)
        left=tk.Frame(main,bg=BG); left.pack(side='left',fill='both',expand=True,padx=(0,10))
        self.canvas=tk.Canvas(left,bg=PANEL,height=275,highlightthickness=1,highlightbackground='#22354e')
        self.canvas.pack(fill='x'); self.canvas.bind('<Configure>',self.resize_topology)
        self.measurement_heading=tk.Label(left,text='SELECTED INPUT MEASUREMENTS  ·  all model features in saved feature order',fg=MUTED,bg=BG,font=('Segoe UI',8,'bold')); self.measurement_heading.pack(anchor='w',pady=(8,3))
        table=tk.Frame(left,bg=PANEL); table.pack(fill='both',expand=True)
        self.measurements=ttk.Treeview(table,columns=('feature','value'),show='headings',height=8)
        self.measurements.heading('feature',text='FEATURE'); self.measurements.heading('value',text='OBSERVED VALUE')
        self.measurements.column('feature',width=270,anchor='w'); self.measurements.column('value',width=270,anchor='w')
        scrollbar=ttk.Scrollbar(table,orient='vertical',command=self.measurements.yview); self.measurements.configure(yscrollcommand=scrollbar.set)
        self.measurements.pack(side='left',fill='both',expand=True); scrollbar.pack(side='right',fill='y')

        side=tk.Frame(main,bg=PANEL,width=330); side.pack(side='right',fill='y'); side.pack_propagate(False)
        self.result_heading=tk.Label(side,text='SELECTED HELD-OUT SOFI RESULT',fg=MUTED,bg=PANEL,font=('Segoe UI',9,'bold')); self.result_heading.pack(anchor='w',padx=16,pady=(13,3))
        self.status=tk.Label(side,text='READY',fg=CYAN,bg=PANEL,font=('Segoe UI',21,'bold')); self.status.pack(anchor='w',padx=16,pady=(2,5))
        self.actual=tk.Label(side,text='Actual dataset label: —',fg=MUTED,bg=PANEL,font=('Segoe UI',10,'bold')); self.actual.pack(anchor='w',padx=16,pady=3)
        self.match=tk.Label(side,text='Prediction check: —',fg=MUTED,bg=PANEL,font=('Segoe UI',10)); self.match.pack(anchor='w',padx=16,pady=3)
        self.score=tk.Label(side,text='Fault score: —',fg=FG,bg=PANEL,font=('Segoe UI',11)); self.score.pack(anchor='w',padx=16,pady=3)
        self.threshold=tk.Label(side,text='Decision threshold: —',fg=MUTED,bg=PANEL,font=('Segoe UI',9)); self.threshold.pack(anchor='w',padx=16,pady=3)
        self.sim_counters=tk.Label(side,text='',wraplength=292,justify='left',fg=CYAN,bg=PANEL,font=('Segoe UI',9,'bold')); self.sim_counters.pack(anchor='w',padx=16,pady=(8,2))
        self.timeline_label=tk.Label(side,text='',wraplength=292,justify='left',fg=MUTED,bg=PANEL,font=('Consolas',7)); self.timeline_label.pack(anchor='w',padx=16,pady=(2,4))
        self.anomaly=tk.Label(side,text='Anomaly score: —',fg=MUTED,bg=PANEL,font=('Segoe UI',9)); self.anomaly.pack(anchor='w',padx=16,pady=3)
        self.device=tk.Label(side,text='Source: —',fg=MUTED,bg=PANEL,font=('Segoe UI',9)); self.device.pack(anchor='w',padx=16,pady=3)
        self.viva_heading=tk.Label(side,text='VIVA WALKTHROUGH',fg=CYAN,bg=PANEL,font=('Segoe UI',9,'bold')); self.viva_heading.pack(anchor='w',padx=16,pady=(11,3))
        self.explain=tk.Label(side,text='Choose a demo action. Each selected record is run through the saved model immediately.',wraplength=292,justify='left',fg=FG,bg=PANEL,font=('Segoe UI',9)); self.explain.pack(anchor='w',padx=16,pady=(0,8))
        self.score_note=tk.Label(side,text='The fault score is not a calibrated probability. SOFI provides normal/fault labels, not exact root causes.',wraplength=292,justify='left',fg=AMBER,bg=PANEL,font=('Segoe UI',8)); self.score_note.pack(anchor='w',padx=16,pady=(3,8))

        self.stats_frame=tk.Frame(self.root,bg=BG); self.stats_frame.pack(fill='x',padx=22,pady=(4,7)); self.stats=[]
        for title in ('REPLAYED','CORRECT','FAULTS DETECTED','FAULTS MISSED'):
            block=tk.Frame(self.stats_frame,bg=PANEL,highlightthickness=1,highlightbackground='#22354e'); block.pack(side='left',fill='x',expand=True,padx=(0,8))
            tk.Label(block,text=title,fg=MUTED,bg=PANEL,font=('Segoe UI',8,'bold')).pack(anchor='w',padx=12,pady=(6,0))
            value=tk.Label(block,text='0',fg=CYAN,bg=PANEL,font=('Segoe UI',15,'bold')); value.pack(anchor='w',padx=12,pady=(0,6)); self.stats.append(value)
        self.footer=tk.Label(self.root,text='SOFI recorded benchmark replay · source files: CoreSwitch-I / CoreSwitch-II · no live telemetry or Packet Tracer feed',fg=MUTED,bg=BG,font=('Segoe UI',8)); self.footer.pack(anchor='w',padx=24,pady=(0,7))

    def button(self,parent,text,command,color):
        return tk.Button(parent,text=text,command=command,fg=color,bg='#14243a',activebackground='#233a55',activeforeground=color,relief='flat',font=('Segoe UI',9,'bold'),padx=11,pady=8,cursor='hand2')

    def set_mode(self,mode):
        self.mode=mode
        if mode=='demo':
            self.pause_simulation(); self.eval_controls.pack_forget(); self.sim_controls.pack_forget(); self.demo_controls.pack(fill='x'); self.stats_frame.pack_forget(); self.measurement_heading.config(text='SELECTED INPUT MEASUREMENTS  ·  all model features in saved feature order'); self.result_heading.config(text='SELECTED HELD-OUT SOFI RESULT'); self.source_banner.config(text='Recorded SOFI held-out examples · illustrative topology only · no live telemetry or Cisco Packet Tracer connection'); self.footer.config(text='SOFI recorded benchmark replay · source files: CoreSwitch-I / CoreSwitch-II · no live telemetry or Packet Tracer feed')
            self.anomaly.pack(anchor='w',padx=16,pady=3,before=self.viva_heading); self.device.pack(anchor='w',padx=16,pady=3,before=self.viva_heading)
            self.score_note.pack(anchor='w',padx=16,pady=(3,8))
            self.draw_topology()
            self.mode_note.config(text='FACULTY DEMO MODE · PAUSED · no records run until you choose an action',fg=GREEN)
            self.root.title('NetSentinel | Faculty Demo · SOFI + Illustrative Topology')
        elif mode=='evaluation':
            self.pause_simulation(); self.demo_controls.pack_forget(); self.sim_controls.pack_forget(); self.eval_controls.pack(fill='x'); self.stats_frame.pack(fill='x',padx=22,pady=(4,7),before=self.footer); self.measurement_heading.config(text='SELECTED INPUT MEASUREMENTS  ·  SOFI held-out feature values'); self.result_heading.config(text='SELECTED HELD-OUT SOFI RESULT'); self.source_banner.config(text='Recorded SOFI full test partition · held-out evaluation · no live telemetry or Packet Tracer feed'); self.footer.config(text='SOFI recorded benchmark replay · source files: CoreSwitch-I / CoreSwitch-II · no live telemetry or Packet Tracer feed')
            self.anomaly.pack(anchor='w',padx=16,pady=3,before=self.viva_heading); self.device.pack(anchor='w',padx=16,pady=3,before=self.viva_heading)
            self.score_note.pack(anchor='w',padx=16,pady=(3,8))
            self.mode_note.config(text='FULL-DATASET EVALUATION · recorded SOFI test partition · start or step manually',fg=CYAN)
            self.root.title('NetSentinel | Full SOFI Test Evaluation')
        else:
            self.pause(); self.demo_controls.pack_forget(); self.eval_controls.pack_forget(); self.sim_controls.pack(fill='x'); self.stats_frame.pack_forget(); self.anomaly.pack_forget(); self.device.pack_forget(); self.score_note.pack_forget(); self.measurement_heading.config(text='SIMULATED MONITORING MEASUREMENTS  ·  synthetic probes, not real telemetry'); self.result_heading.config(text='SIMULATION · ML OUTPUT UNAVAILABLE'); self.source_banner.config(text='SIMULATION MODE · generated topology checks and packet animation · not SOFI, live telemetry, or Packet Tracer data'); self.footer.config(text='Synthetic classroom simulation only · no Packet Tracer or live-network integration')
            self.mode_note.config(text='TOPOLOGY SIMULATION · PAUSED · generated scenario data · simulation time and wall collection time shown separately · SOFI ML skipped (incompatible fields)',fg=AMBER)
            self.root.title('NetSentinel | Topology Simulation · Simulated Data')
            self.render_simulation(); self.draw_topology()
    def draw_topology(self):
        c=self.canvas; c.delete('all'); w=max(780,c.winfo_width()); h=max(270,c.winfo_height())
        c.create_text(18,15,text='ILLUSTRATIVE PACKET-TRACER-STYLE LOGICAL TOPOLOGY',anchor='w',fill=CYAN,font=('Segoe UI',9,'bold'))
        sim=self.mode=='simulation'
        banner=('SIMULATED TOPOLOGY · generated packet/check animation · no live source' if sim else 'Example IPs/links only · status not measured · violet outline = selected item, not a fault')
        c.create_text(18,33,text=banner,anchor='w',fill=AMBER,font=('Segoe UI',8))
        cy=int(h*.55); pcx=95; sx=int(w*.36); rx=int(w*.63); serverx=int(w*.87); pcy1=cy-61; pcy2=cy+61
        gray='#6d829b'; selected_color=SELECTED
        fault_color=RED if sim and self.simulation.active_fault and self.simulation.scenario=='link_down' else AMBER if sim and self.simulation.active_fault and self.simulation.scenario=='degraded' else gray
        server_color=RED if sim and self.simulation.active_fault and self.simulation.scenario=='server_unreachable' else CYAN
        def link(link_id,x1,y1,x2,y2,label,lx,ly):
            chosen=self.selected_topology_link==link_id; sim_down=sim and self.simulation.active_fault and self.simulation.scenario=='link_down' and link_id=='s1_r1'; sim_slow=sim and self.simulation.active_fault and self.simulation.scenario=='degraded'
            color=selected_color if chosen else RED if sim_down else AMBER if sim_slow else gray
            tags=('link',f'link:{link_id}')
            c.create_line(x1,y1,x2,y2,fill=color,width=4 if chosen else 3,arrow='last',arrowshape=(10,12,5),tags=tags)
            c.create_text(lx,ly,text=label,fill=MUTED,font=('Segoe UI',7),justify='center',tags=tags)
            c.tag_bind(f'link:{link_id}','<Button-1>',lambda event,key=link_id:self.inspect_link(key))
        # Connections are drawn first so recognizable device icons sit on top of them.
        link('pc1_s1',pcx+35,pcy1,sx-47,cy-14,'PC1 Eth0 — S1 Fa0/1',(pcx+sx)/2,pcy1-13)
        link('pc2_s1',pcx+35,pcy2,sx-47,cy+14,'PC2 Eth0 — S1 Fa0/2',(pcx+sx)/2,pcy2+19)
        link('s1_r1',sx+48,cy,rx-32,cy,'S1 Gi0/1 — R1 Gi0/0',(sx+rx)/2,cy-19)
        link('r1_server1',rx+32,cy,serverx-31,cy,'R1 Gi0/1 — Server1 Fa0',(rx+serverx)/2,cy-19)

        def device_tag(key):
            tag=f'device:{key}'; c.tag_bind(tag,'<Button-1>',lambda event,device_id=key:self.inspect_device(device_id)); return ('device',tag)
        def label_device(key,cx,icon_bottom,ip_lines=()):
            data=TOPOLOGY_DEVICES[key]; color=selected_color if self.selected_topology_device==key else FG
            c.create_text(cx,icon_bottom+14,text=data['name'],fill=color,font=('Segoe UI',9,'bold'),tags=device_tag(key))
            for i,line in enumerate(ip_lines): c.create_text(cx,icon_bottom+28+i*11,text=line,fill=MUTED,font=('Segoe UI',7),tags=device_tag(key))
        # PC monitor icons
        for key,cx,cy_pc in (('pc1',pcx,pcy1),('pc2',pcx,pcy2)):
            tags=device_tag(key); outline=selected_color if self.selected_topology_device==key else CYAN
            c.create_rectangle(cx-34,cy_pc-22,cx+34,cy_pc+18,fill='#172a41',outline=outline,width=2,tags=tags)
            c.create_rectangle(cx-27,cy_pc-15,cx+27,cy_pc+10,fill='#0a1524',outline='#496580',tags=tags)
            c.create_line(cx,cy_pc+18,cx,cy_pc+25,fill=outline,width=3,tags=tags); c.create_line(cx-14,cy_pc+26,cx+14,cy_pc+26,fill=outline,width=3,tags=tags)
            c.create_text(cx,cy_pc-2,text='PC',fill=CYAN,font=('Segoe UI',9,'bold'),tags=tags)
            label_device(key,cx,cy_pc+28,[TOPOLOGY_DEVICES[key]['example_addressing'].replace('Ethernet0: ','')])
        # Access switch icon with neutral port marks (they do not indicate link state).
        tags=device_tag('s1'); outline=selected_color if self.selected_topology_device=='s1' else CYAN
        c.create_rectangle(sx-47,cy-23,sx+47,cy+23,fill='#172a41',outline=outline,width=2,tags=tags)
        c.create_text(sx,cy-9,text='SWITCH',fill=CYAN,font=('Segoe UI',8,'bold'),tags=tags)
        for j in range(8): c.create_rectangle(sx-34+j*9,cy+3,sx-29+j*9,cy+9,fill=gray,outline=gray,tags=tags)
        label_device('s1',sx,cy+27,['VLAN1 · 192.168.10.2/24'])
        # Router icon and crossed forwarding arrows.
        tags=device_tag('r1'); outline=selected_color if self.selected_topology_device=='r1' else CYAN
        c.create_oval(rx-32,cy-32,rx+32,cy+32,fill='#172a41',outline=outline,width=2,tags=tags)
        c.create_line(rx-18,cy,rx+18,cy,fill=CYAN,width=2,arrow='last',tags=tags)
        c.create_line(rx+15,cy-13,rx-15,cy+13,fill=CYAN,width=2,arrow='last',tags=tags)
        c.create_line(rx,cy-18,rx,cy+18,fill=CYAN,width=2,arrow='last',tags=tags)
        label_device('r1',rx,cy+35,['G0/0 .1 · G0/1 .1'])
        # Server tower icon.
        tags=device_tag('server1'); outline=selected_color if self.selected_topology_device=='server1' else server_color
        c.create_rectangle(serverx-27,cy-39,serverx+27,cy+39,fill='#172a41',outline=outline,width=2,tags=tags)
        for j in range(3):
            yy=cy-28+j*22; c.create_rectangle(serverx-18,yy-6,serverx+17,yy+6,fill='#0a1524',outline='#496580',tags=tags)
            c.create_oval(serverx+8,yy-2,serverx+12,yy+2,fill=gray,outline=gray,tags=tags)
        label_device('server1',serverx,cy+44,['192.168.20.10/24'])
        if sim: self._draw_packet_indicators(c,pcx,pcy1,pcy2,sx,rx,serverx,cy)
        c.create_text(w-10,h-10,text=('SIMULATION · packet markers are generated; faults are rule-based' if sim else 'Click a device or link for its status and fault-mapping details'),anchor='e',fill=MUTED,font=('Segoe UI',8))

    def return_to_simulation(self):
        self.pause(); self.set_mode('simulation')

    def _simulation_tick(self):
        self.sim_after_id=None
        if self.mode!='simulation' or not self.simulation.running: return
        self.simulation.check()
        self.render_simulation()
        self.draw_topology()
        self.sim_after_id=self.root.after(1200,self._simulation_tick)

    def _animate_packets(self):
        if self.mode!='simulation' or not self.simulation.running: return
        self.draw_topology()
        self.root.after(55,self._animate_packets)

    def start_simulation(self):
        if self.mode!='simulation': self.set_mode('simulation')
        self.simulation.start()
        self.mode_note.config(text='TOPOLOGY SIMULATION · RUNNING · accelerated simulation clock · generated checks only',fg=GREEN)
        self.render_simulation(); self.draw_topology()
        if self.sim_after_id is None: self.sim_after_id=self.root.after(1200,self._simulation_tick)
        self.root.after(45,self._animate_packets)

    def pause_simulation(self):
        if hasattr(self,'simulation'): self.simulation.pause()
        if getattr(self,'mode',None)=='simulation' and hasattr(self,'mode_note'):
            self.mode_note.config(text='TOPOLOGY SIMULATION · PAUSED · generated checks only · no live telemetry',fg=AMBER)
        if getattr(self,'sim_after_id',None):
            try: self.root.after_cancel(self.sim_after_id)
            except tk.TclError: pass
            self.sim_after_id=None

    def apply_simulation_scenario(self):
        selected=self.sim_scenario_var.get()
        scenario=next((key for key,value in SCENARIOS.items() if value==selected),'normal')
        result=self.simulation.inject(scenario)
        if result is None: self.simulation.check()
        self.render_simulation(); self.draw_topology()

    def clear_simulation_fault(self):
        self.simulation.clear_fault()
        if self.simulation.recovery_pending: self.simulation.check()
        self.sim_scenario_var.set(SCENARIOS['normal'])
        self.render_simulation(); self.draw_topology()

    def reset_simulation(self):
        self.pause_simulation(); self.simulation.reset(); self.sim_scenario_var.set(SCENARIOS['normal'])
        self.render_simulation(); self.draw_topology()

    def render_simulation(self):
        sim=self.simulation; measurement=sim.last_measurement; diagnosis=sim.last_diagnosis
        self.measurements.delete(*self.measurements.get_children())
        rows=[('Source','SIMULATED topology'),('Checks collected',str(sim.checks)),('Probe packets sent',str(sim.total_packets)),('Successful probes',str(sim.successes)),('Failed probes',str(sim.failures)),('Current scenario',SCENARIOS[sim.scenario])]
        if measurement:
            rows += [('Current RTT (ms)','No RTT · no response' if measurement['latency_ms'] is None else f"{measurement['latency_ms']:.1f}"),('Current loss (%)',f"{measurement['packet_loss_pct']:.1f}"),('Last wall collection time',measurement['collected_at']),('Simulation time',f"T+{sim.tick_number:02d}s")]
        for feature,value in rows: self.measurements.insert('','end',values=(feature,value))
        running='RUNNING' if sim.running else 'PAUSED'
        condition=diagnosis['condition'].upper() if diagnosis else 'READY'
        self.progress.config(text=f"Topology Simulation · {running} · T+{sim.tick_number:02d}s · {condition}")
        self.status.config(text=f'SIMULATED {condition}',fg=RED if sim.active_fault else GREEN if diagnosis else CYAN)
        self.actual.config(text='Data source: generated simulation',fg=AMBER)
        self.match.config(text='SOFI Random Forest: NOT RUN',fg=MUTED)
        self.score.config(text='ML fault score: unavailable for these features',fg=FG)
        self.threshold.config(text='ML threshold: not applicable · feature schema mismatch',fg=MUTED)
        self.anomaly.config(text='Anomaly score: not computed · no SOFI feature vector',fg=MUTED)
        self.device.config(text='Rule detector: simulation only',fg=CYAN)
        if measurement and diagnosis:
            rtt='no response' if measurement['latency_ms'] is None else f"{measurement['latency_ms']:.1f} ms"
            self.sim_counters.config(text=f"SIMULATED COUNTERS\nPackets {sim.total_packets} · checks {sim.checks}\nSuccess {sim.successes} · failed {sim.failures}\nRTT {rtt} · loss {measurement['packet_loss_pct']:.1f}%")
            gate=sim.last_model_assessment or {}
            self.explain.config(text=f"SIM RULE: {diagnosis['condition'].upper()}\n{diagnosis['rule']}\nEvidence: {diagnosis['evidence']}\nSOFI model: NOT RUN. {gate.get('reason','No compatible SOFI vector.') }\nClear fault; verify recovery after a successful next check.")
        else:
            self.sim_counters.config(text=f"SIMULATED COUNTERS\nPackets {sim.total_packets} · checks {sim.checks}\nSuccess {sim.successes} · failed {sim.failures}\nNo measurements yet · press Start")
            self.explain.config(text="Paused, ready-to-present synthetic topology. Start for a baseline, apply a scenario, then clear it and verify recovery with a successful check. RF is gated off until an approved SOFI feature vector is supplied.")
        recent=sim.events[-4:]
        self.timeline_label.config(text='EVENT TIMELINE · wall / sim clock\n'+'\n'.join(f"{e['wall_time'][11:23]} {e['simulation_time']} · {e['event'][:22]} · {e['detail'][:24]}" for e in recent))

    def _draw_packet_indicators(self,c,pcx,pcy1,pcy2,sx,rx,serverx,cy):
        if self.mode!='simulation' or not self.simulation.running: return
        import time
        phase=(time.monotonic()*0.72)%1.0
        route=((pcx+35,pcy1,sx-47,cy),(sx+48,cy,rx-32,cy),(rx+32,cy,serverx-31,cy))
        route2=((pcx+35,pcy2,sx-47,cy),(sx+48,cy,rx-32,cy),(rx+32,cy,serverx-31,cy))
        slow=self.simulation.scenario=='degraded' and self.simulation.active_fault
        down=self.simulation.scenario=='link_down' and self.simulation.active_fault
        for offset,path in ((0.0,route),(0.5,route2)):
            value=(phase+offset)%1.0
            segment=min(2,int(value*3)); fraction=value*3-segment
            if down and segment>=1: segment=1; fraction=0.82
            x1,y1,x2,y2=path[segment]
            x=x1+(x2-x1)*fraction; y=y1+(y2-y1)*fraction
            color=RED if (down and segment==1) or (self.simulation.scenario=='server_unreachable' and value>0.85 and self.simulation.active_fault) else AMBER if slow else GREEN if self.simulation.active_fault is False else CYAN
            radius=7 if slow else 6
            c.create_oval(x-radius,y-radius,x+radius,y+radius,fill=color,outline='#f0f5fb',width=1,tags=('packet',))
        if down: c.create_text((sx+rx)/2,cy-11,text='×  LINK DOWN',fill=RED,font=('Segoe UI',8,'bold'),tags=('sim_fault_marker',))
        elif self.simulation.scenario=='server_unreachable' and self.simulation.active_fault: c.create_text(serverx,cy+58,text='×  UNREACHABLE',fill=RED,font=('Segoe UI',8,'bold'),tags=('sim_fault_marker',))
        elif slow: c.create_text((sx+rx)/2,cy-11,text='!  HIGH LATENCY / LOSS',fill=AMBER,font=('Segoe UI',8,'bold'),tags=('sim_fault_marker',))
        # simulation indicators are removed and redrawn with the topology every frame.

    def _heldout_summary(self):
        case=self.current_case
        if not case: return None
        return {'source':case['source'],'row':case['row'],'actual':case['actual'],'predicted':case['predicted'],'score':case['score'],'threshold':case['threshold']}

    def inspect_device(self,device_id):
        self.selected_topology_device=device_id; self.selected_topology_link=None; self.draw_topology()
        details=build_topology_device_inspection(device_id,self._heldout_summary())
        lines=[f"Type: {details['type']}",f"Role: {details['role']}",f"Example addressing: {details['example_addressing']}",f"Example interfaces: {details['interfaces']}"]
        if self.mode=='simulation': lines += [f"Monitoring status: SIMULATED · {self.simulation.scenario}",f"Checks: {self.simulation.checks} · successful probes: {self.simulation.successes} · failed probes: {self.simulation.failures}",f"Diagnostic: {self.simulation.last_diagnosis}",'SOFI Random Forest: skipped; simulated measurements do not match its feature schema.']
        else: lines += [f"Monitoring status: {details['monitoring_status']}",f"Link status: {details['link_status']}",f"Fault information: {details['fault_attribution']}"]
        self.show_inspection_window(details['device'],lines,details['heldout_result'])

    def inspect_link(self,link_id):
        self.selected_topology_link=link_id; self.selected_topology_device=None; self.draw_topology()
        details=build_topology_link_inspection(link_id,self._heldout_summary())
        lines=[f"Endpoints: {' ↔ '.join(details['endpoints'])}"]
        if self.mode=='simulation': lines += [f"Status: SIMULATED · {'DOWN' if link_id=='s1_r1' and self.simulation.scenario=='link_down' and self.simulation.active_fault else 'UP (illustrative state)' }",f"Failed probes: {self.simulation.failures}",'Synthetic state only; no real interface was read.']
        else: lines += [f"Status: {details['status']}",f"Fault information: {details['fault_attribution']}"]
        self.show_inspection_window(details['link'],lines,details['heldout_result'])

    def show_inspection_window(self,title,lines,heldout_result):
        win=tk.Toplevel(self.root); win.title(f'{title} · illustrative topology details'); win.configure(bg=PANEL); win.geometry('500x500'); win.transient(self.root); win.update_idletasks(); sw=win.winfo_screenwidth(); sh=win.winfo_screenheight(); px=max(10,(sw-500)//2); py=max(10,(sh-500)//2); win.geometry(f'500x500+{px}+{py}')
        tk.Label(win,text=title,fg=CYAN,bg=PANEL,font=('Segoe UI',15,'bold')).pack(anchor='w',padx=18,pady=(16,7))
        tk.Label(win,text=('SIMULATED STATUS · generated scenario data; no live or Packet Tracer telemetry' if self.mode=='simulation' else 'ILLUSTRATIVE ONLY · no live, SNMP, or Packet Tracer measurements'),fg=AMBER,bg=PANEL,font=('Segoe UI',9,'bold')).pack(anchor='w',padx=18,pady=(0,10))
        for line in lines: tk.Label(win,text=line,wraplength=460,justify='left',fg=FG,bg=PANEL,font=('Segoe UI',9)).pack(anchor='w',padx=18,pady=3)
        if heldout_result:
            tk.Label(win,text='SELECTED SOFI RESULT · separate, not associated with this device/link',fg=CYAN,bg=PANEL,font=('Segoe UI',9,'bold')).pack(anchor='w',padx=18,pady=(12,4))
            summary=f"Source {heldout_result['source']} · row {heldout_result['row']} | actual {heldout_result['actual']} | predicted {heldout_result['predicted']} | fault score {heldout_result['score']:.2f}% vs threshold {heldout_result['threshold']:.2f}%"
            tk.Label(win,text=summary,wraplength=460,justify='left',fg=FG,bg=PANEL,font=('Segoe UI',9)).pack(anchor='w',padx=18,pady=3)
        tk.Button(win,text='Close',command=win.destroy,fg=CYAN,bg='#14243a',relief='flat',padx=16,pady=6).pack(anchor='e',padx=18,pady=14)

    def resize_topology(self,_event=None):
        if hasattr(self,'canvas'): self.draw_topology()

    def render_summary(self):
        for value in self.stats: value.config(text='0')

    def display_measurements(self,index):
        self.measurements.delete(*self.measurements.get_children())
        for feature in self.artifact['feature_names']:
            value=self.rows.iloc[index][feature]
            if pd.isna(value): shown='Missing (handled by fitted imputer)'
            elif isinstance(value,(float,np.floating)): shown=f'{float(value):.6g}'
            else: shown=str(value)
            self.measurements.insert('','end',values=(feature,shown))

    def show_ready_state(self):
        self.status.config(text='READY · PAUSED',fg=CYAN); self.actual.config(text='Actual dataset label: —',fg=MUTED)
        self.match.config(text='Prediction check: —',fg=MUTED); self.score.config(text='Fault score: —',fg=FG)
        self.threshold.config(text=f"Decision threshold: {self.artifact['classifier_threshold']*100:.1f}%",fg=MUTED)
        self.anomaly.config(text='Anomaly score: —',fg=MUTED); self.device.config(text='Source: —',fg=MUTED)
        self.explain.config(text='Ready for viva. 1) Select a held-out row. 2) Review its input measurements. 3) The saved preprocessing and classifier produce a fault score. 4) The score is compared with the validation threshold. 5) Compare predicted output with the actual SOFI label.')
        self.progress.config(text=f'Faculty Demo · held-out test set ({len(self.rows):,} rows)')
        self.measurements.delete(*self.measurements.get_children()); self.current_case=None; self.selected_topology_device=None; self.selected_topology_link=None; self.draw_topology()

    def show_demo_case(self,index,case_kind):
        try:
            actual,result=infer_demo_case(self.rows,self.truth,index,self.artifact,expected_prediction=self.pred[index])
        except Exception as exc:
            messagebox.showerror('Demo inference failed',str(exc),parent=self.root); return
        predicted=1 if result['predicted_condition']=='fault' else 0; correct=predicted==actual
        label='F (fault)' if actual==1 else 'NE (normal)'
        self.status.config(text='PREDICTED FAULT' if predicted else 'PREDICTED NORMAL',fg=RED if predicted else GREEN)
        self.actual.config(text=f'Actual dataset label: {label}',fg=AMBER if actual else MUTED)
        check='CORRECT' if correct else ('MISSED ACTUAL FAULT' if actual else 'FALSE ALARM')
        self.match.config(text=f'Prediction check: {check}',fg=GREEN if correct else RED)
        self.score.config(text=f"Fault score: {result['supervised_fault_score']*100:.2f}%",fg=FG)
        self.threshold.config(text=f"Decision threshold: {result['supervised_threshold']*100:.2f}%",fg=MUTED)
        anomaly=result['components']['isolation_forest']
        self.anomaly.config(text=f"Anomaly score: {anomaly['anomaly_score']:.4f} · {'flagged' if anomaly['flag'] else 'within baseline'}",fg=AMBER if anomaly['flag'] else MUTED)
        self.device.config(text=f'Source: {self.source[index]} · held-out row {index+1:,}',fg=MUTED)
        self.current_case={'source':self.source[index],'row':index+1,'actual':label,'predicted':'F (fault)' if predicted else 'NE (normal)','score':result['supervised_fault_score']*100,'threshold':result['supervised_threshold']*100}
        prediction_text='correctly flags this fault' if predicted==1 else 'does not flag this labeled fault' if actual else 'keeps this normal row below threshold'
        self.explain.config(text=f"Viva steps\n1. This is a real held-out {label} record from {self.source[index]}.\n2. Review the 33 input features in the table; missing values use the model's fitted imputer.\n3. The saved Random Forest returns a fault score of {result['supervised_fault_score']*100:.2f}%.\n4. The validation-selected threshold is {result['supervised_threshold']*100:.2f}%.\n5. The model {prediction_text}; compare the output with the actual label. This does not identify an exact root cause.")
        self.display_measurements(index); self.draw_topology()
        self.progress.config(text=f"Faculty Demo · {case_kind} · held-out row {index+1:,} / {len(self.rows):,}")
        self.demo_cases_shown+=1; self.demo_next_index=(index+1)%len(self.rows)

    def demonstrate_normal(self):
        self.pause(); self.show_demo_case(self.demo_normal_index,'normal example')

    def demonstrate_fault(self):
        self.pause(); self.show_demo_case(self.demo_fault_index,'actual fault example')

    def run_next_test(self):
        self.pause(); index=self.demo_next_index; self.show_demo_case(index,'next test')

    def reset_demo(self):
        self.pause(); self.demo_cases_shown=0; self.demo_next_index=0; self.render_summary(); self.show_ready_state()

    def return_to_evaluation(self):
        self.pause(); self.set_mode('evaluation')
        if self.i<0: self.progress.config(text=f'0 / {len(self.rows):,} · full evaluation is paused')

    def return_to_demo(self):
        self.pause(); self.set_mode('demo')
        if self.demo_cases_shown==0:self.show_ready_state()
        else:self.progress.config(text=f'Faculty Demo · ready · {self.demo_cases_shown} example(s) shown')

    def next_record(self):
        if self.i+1>=len(self.rows): self.pause(); self.i=-1
        self.i+=1; self.show_record(self.i)

    def next_fault(self):
        start=self.i+1; found=next((j for j in range(start,len(self.truth)) if self.truth[j]==1),None)
        if found is None: found=next((j for j in range(0,min(start,len(self.truth))) if self.truth[j]==1),None)
        if found is None:return
        self.pause(); self.i=found; self.show_record(self.i)

    def show_record(self,index):
        actual=int(self.truth[index]); predicted=int(self.pred[index]); self.progress.config(text=f'Full evaluation · {index+1:,} / {len(self.rows):,}')
        self.status.config(text='PREDICTED FAULT' if predicted else 'PREDICTED NORMAL',fg=RED if predicted else GREEN)
        self.actual.config(text='Actual dataset label: F (fault)' if actual else 'Actual dataset label: NE (normal)',fg=AMBER if actual else MUTED)
        correct=predicted==actual; self.match.config(text='Prediction check: CORRECT' if correct else ('MISSED ACTUAL FAULT' if actual else 'FALSE ALARM'),fg=GREEN if correct else RED)
        self.score.config(text=f'Fault score: {self.scores[index]*100:.2f}%'); self.threshold.config(text=f"Decision threshold: {self.artifact['classifier_threshold']*100:.2f}%")
        self.anomaly.config(text='Anomaly score: available in Faculty Demo inference')
        self.device.config(text=f'Source: {self.source[index]} · held-out row {index+1:,}')
        self.current_case={'source':self.source[index],'row':index+1,'actual':'F (fault)' if actual else 'NE (normal)','predicted':'F (fault)' if predicted else 'NE (normal)','score':self.scores[index]*100,'threshold':self.artifact['classifier_threshold']*100}
        self.explain.config(text='Full-dataset replay uses the saved classifier scores computed from the same held-out partition. Switch to Faculty Demo to inspect all 33 input values and run a selected row through the unified inference API.')
        self.display_measurements(index); self.draw_topology()
        n=index+1; correct_n=int(np.sum(self.pred[:n]==self.truth[:n])); found=int(np.sum((self.pred[:n]==1)&(self.truth[:n]==1))); missed=int(np.sum((self.pred[:n]==0)&(self.truth[:n]==1)))
        for value,number in zip(self.stats,(n,correct_n,found,missed)):value.config(text=f'{number:,}')

    def start(self):
        if self.running:return
        self.running=True; self.tick()
    def pause(self):
        self.running=False
        if self.after_id:
            try:self.root.after_cancel(self.after_id)
            except tk.TclError:pass
            self.after_id=None
    def tick(self):
        if not self.running:return
        self.next_record(); delay=max(35,int(450/int(self.speed.get()))); self.after_id=self.root.after(delay,self.tick)
    def close(self):
        self.pause(); self.root.destroy()


def main():
    root=tk.Tk()
    try: Replay(root)
    except Exception as exc:
        messagebox.showerror('SOFI replay could not start',f'{exc}\n\nCheck that the SOFI CSV files and trained model are present in outputs/data/raw and outputs/models.')
        root.destroy(); return
    root.mainloop()

if __name__=='__main__': main()
