#!/usr/bin/env python3
"""Live GPU model-key authorization checks; never print key values or tokens."""
import argparse,base64,datetime,hashlib,http.client,json,re,socket,subprocess,ssl
from pathlib import Path
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--ingress-ip',default='10.14.202.14')
p.add_argument('--negative-only',action='store_true')
p.add_argument('--output',type=Path)
a=p.parse_args()
oc=['oc','--request-timeout=30s']
results={}
def get(*args):return json.loads(subprocess.check_output(oc+['get',*args,'-o','json']))
def passed(name,detail):
    results[name]=detail
    print('PASS:',detail,flush=True)
if not a.negative_only:
    report=subprocess.check_output(oc+['get','--raw','/api/v1/namespaces/gpu-workload/services/gpu-validation:8080/proxy/status.txt'],text=True)
    secret=get('secret','model-keys','-n','trustee-operator-system')
    key=base64.b64decode(secret['data']['edge-model-key'])
    assert len(bytes.fromhex(key.decode()))==32,'Model key is not 256-bit hex-encoded material'
    digest=hashlib.sha256(key).hexdigest()
    assert 'GPU_MODEL_KEY_BEGIN\n'+digest+'  -\nGPU_MODEL_KEY_PASS' in report,'GPU result did not prove matching model-key retrieval'
    assert 'GPU_VECTORADD_PASS' in report and 'GPU_SECRET_FETCH_PASS' in report
    passed('gpu_model_key','GPU guest retrieved matching model key after CUDA; values withheld')
deployment=get('deployment','insecure-policy','-n','hello-openshift')
assert deployment['spec']['template']['spec']['runtimeClassName']=='kata-cc'
started=datetime.datetime.now(datetime.timezone.utc).isoformat().replace('+00:00','Z')
r=subprocess.run(oc+['exec','-n','hello-openshift','deployment/insecure-policy','--','curl','--silent','--show-error','--max-time','45','--write-out','\n%{http_code}','http://127.0.0.1:8006/cdh/resource/default/model-keys/edge-model-key'],capture_output=True)
assert r.returncode==0,'CPU-only request transport or exec failed'
body,status=r.stdout.rsplit(b'\n',1)
assert status in [b'401',b'403',b'500'],'CPU-only key request was not denied; body withheld'
logs=subprocess.check_output(oc+['logs','-n','trustee-operator-system','deployment/trustee-deployment','-c','kbs','--since-time='+started],text=True)
logs=re.sub(r'\x1b\[[0-9;]*m','',logs)
assert 'PolicyDeny' in logs,'Missing authoritative KBS policy-denial log'
assert re.search(r'GET /kbs/v0/resource/default/model-keys/edge-model-key HTTP/1.1" 401',logs),'Missing KBS HTTP 401 for protected resource'
passed('cpu_only_denied','CPU-only SNP guest denied: KBS PolicyDeny/HTTP 401 (CDH maps to HTTP '+status.decode()+')')
route=get('route','kbs','-n','trustee-operator-system')
host=route['status']['ingress'][0]['host']
certificate=base64.b64decode(get('secret','kbs-https-certificate','-n','trustee-operator-system')['data']['tls.crt']).decode()
context=ssl.create_default_context(cadata=certificate)
class LabConnection(http.client.HTTPSConnection):
    def connect(self):
        raw=socket.create_connection((a.ingress_ip,443),self.timeout)
        self.sock=self._context.wrap_socket(raw,server_hostname=self.host)
def request(path,data=None,cookie=None,token=None):
    connection=LabConnection(host,timeout=75,context=context)
    headers={}
    if cookie:headers['Cookie']=cookie
    if token:headers['Authorization']='Bearer '+token
    body=None
    if data is not None:
        headers['Content-Type']='application/json';body=json.dumps(data)
    try:
        connection.request('POST' if data is not None else 'GET','/kbs/v0/'+path,body=body,headers=headers)
        response=connection.getresponse()
        return response.status,response.read(),response.getheader('Set-Cookie')
    finally:connection.close()
def enc(value):return base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip('=')
fake=enc({'alg':'none','typ':'JWT'})+'.'+enc({'submods':{'cpu0':{'ear.status':'affirming'},'gpu0':{'ear.status':'affirming'}}})+'.'
code,_,_=request('resource/default/model-keys/edge-model-key',token=fake)
assert code in [401,403],'Unsigned token was not denied; response withheld'
passed('forged_token_denied','Unsigned token claiming CPU/GPU attestation rejected (HTTP '+str(code)+')')
code,body,cookie=request('auth',{'version':'0.4.0','tee':'nvidia','extra-params':{}})
assert code==200 and cookie,'NVIDIA handshake failed before the evidence test'
nonce=json.loads(body)['nonce'];cookie=cookie.split(';',1)[0]
def coordinate(value):return base64.urlsafe_b64encode(bytes.fromhex(value)).decode().rstrip('=')
pub={'kty':'EC','crv':'P-256','alg':'ECDH-ES+A256KW',
'x':coordinate('6b17d1f2e12c4247f8bce6e563a440f277037d812deb33a0f4a13945d898c296'),
'y':coordinate('4fe342e2fe1a7f9b8ee7eb4a7c0f9e162bce33576b315ececbb6406837bf51f5')}
evidence={'device_evidence_list':[{'arch':'BLACKWELL','uuid':'deliberately-invalid-test-device','evidence':'AA==','certificate':'not-a-valid-device-certificate'}]}
code,body,_=request('attest',{'runtime-data':{'nonce':nonce,'tee-pubkey':pub},'tee-evidence':{'primary_evidence':evidence,'additional_evidence':''}},cookie=cookie)
assert code in [400,401,403,500] and any(x in body.lower() for x in [b'verif',b'attest',b'nvidia']),'Invalid GPU evidence was not rejected by attestation'
passed('invalid_gpu_evidence_denied','Fresh NVIDIA handshake accepted; invalid Blackwell report/certificate rejected (HTTP '+str(code)+')')
code,_,_=request('resource/default/model-keys/edge-model-key',cookie=cookie)
assert code in [401,403],'Failed GPU session accessed protected key; response withheld'
passed('failed_session_denied','Failed GPU-attestation session denied model key (HTTP '+str(code)+')')
if a.output:a.output.write_text(json.dumps(results,indent=2)+'\n')
