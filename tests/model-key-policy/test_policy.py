#!/usr/bin/env python3
"""Exercise the production Rego with positive and fail-closed negative fixtures."""
import copy, json, os, subprocess, tempfile
from pathlib import Path
policy=Path(__file__).resolve().parents[2]/'charts/hub/trustee-edge/files/resource-policy.rego'
opa=os.environ.get('OPA','opa')
base={'ear.status':'affirming','ear.trustworthiness-vector':{'hardware':2,'executables':3,'configuration':3}}
cpu=dict(copy.deepcopy(base),**{'ear.veraison.annotated-evidence':{'snp':{'policy_debug_allowed':False,'policy_migrate_ma':False,'measurement':'verified-launch-measurement'}}})
gpu=dict(copy.deepcopy(base),**{'ear.veraison.annotated-evidence':{'nvidia':{'x-nvidia-overall-att-result':True,'secboot':True,'dbgstat':'disabled','measres':'success'}}})
valid={'submods':{'cpu0':cpu,'gpu0':gpu}}
resource={'plugin':'resource','resource-path':['default','model-keys','edge-model-key']}
cases=[]
def add(name,claims,expected=False,data=None):cases.append((name,claims,expected,data or resource))
add('valid CPU and GPU',valid,True)
add('CPU-only model key denied',{'submods':{'cpu0':cpu}})
add('GPU-only model key denied',{'submods':{'gpu0':gpu}})
add('no evidence',{'submods':{}})
for module in ['cpu0','gpu0']:
 for facet in ['hardware','executables','configuration']:
  for value in [0,1,32,97,'3',None]:
   d=copy.deepcopy(valid);d['submods'][module]['ear.trustworthiness-vector'][facet]=value
   add(f'{module} {facet}={value!r} denied',d)
  d=copy.deepcopy(valid);del d['submods'][module]['ear.trustworthiness-vector'][facet]
  add(f'{module} missing {facet} denied',d)
 for status in ['warning','contraindicated','none']:
  d=copy.deepcopy(valid);d['submods'][module]['ear.status']=status;add(f'{module} {status} denied',d)
for key,value in [('x-nvidia-overall-att-result',False),('secboot',False),('dbgstat','enabled'),('measres','failure')]:
 d=copy.deepcopy(valid);d['submods']['gpu0']['ear.veraison.annotated-evidence']['nvidia'][key]=value;add(key+' invalid denied',d)
 d=copy.deepcopy(valid);del d['submods']['gpu0']['ear.veraison.annotated-evidence']['nvidia'][key];add(key+' missing denied',d)
d=copy.deepcopy(valid);d['submods']['gpu0']['ear.veraison.annotated-evidence']={};add('GPU type absent denied',d)
d=copy.deepcopy(valid);d['submods']['gpu0']=copy.deepcopy(cpu);add('CPU masquerading as GPU denied',d)
d=copy.deepcopy(valid);d['submods']['cpu0']['ear.veraison.annotated-evidence']['snp']['policy_debug_allowed']=True;add('CPU debug denied',d)
d=copy.deepcopy(valid);d['submods']['gpu1']={'ear.status':'contraindicated'};add('additional invalid GPU denied',d)
add('CPU-only legacy demo allowed',{'submods':{'cpu0':cpu}},True,{'plugin':'resource','resource-path':['default','kbsres1','key3']})
add('different model-key tag still protected',{'submods':{'cpu0':cpu}},False,{'plugin':'resource','resource-path':['default','model-keys','another-key']})
add('missing canonical path denied',valid,False,{'plugin':'resource'})
add('unexpected plugin denied',valid,False,{'plugin':'other','resource-path':['default','model-keys','edge-model-key']})
with tempfile.TemporaryDirectory() as directory:
 root=Path(directory)
 for name,claims,expected,data in cases:
  (root/'input.json').write_text(json.dumps(claims));(root/'data.json').write_text(json.dumps(data))
  result=subprocess.check_output([opa,'eval','--format=raw','--data',str(policy),'--data',str(root/'data.json'),'--input',str(root/'input.json'),'data.policy.allow'],text=True).strip()
  assert result == str(expected).lower(),(name,result,expected)
  print('PASS:',name)
print(f'PASS: {len(cases)} policy cases; fixtures exercise policy decisions, not hardware signatures')
