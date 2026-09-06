import json, urllib.request, urllib.error, time
from pathlib import Path
values = {}
for line in Path('.env').read_text(encoding='utf-8').splitlines():
    if '=' in line and not line.lstrip().startswith('#'):
        k,v = line.split('=',1)
        values[k.strip()] = v.strip().strip(chr(34)).strip(chr(39))
key = values.get('CLOVA_STUDIO_API_KEY','')
if not key:
    print('CLOVA_STUDIO_API_KEY missing')
    raise SystemExit(1)
results=[]
for model in ['HCX-007','HCX-005','HCX-DASH-002']:
    payload={'messages':[{'role':'user','content':'Reply only OK.'}],'temperature':0,'maxTokens':16}
    if model=='HCX-007':
        payload.pop('maxTokens')
        payload.update(thinking={'effort':'none'},maxCompletionTokens=16)
    req=urllib.request.Request('https://clovastudio.stream.ntruss.com/v3/chat-completions/'+model,data=json.dumps(payload).encode(),headers={'Authorization':'Bearer '+key,'Content-Type':'application/json','Accept':'application/json'})
    start=time.monotonic()
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req,timeout=15) as response:
            data=json.load(response)
            result={'model':model,'http_status':response.status,'api_status':data.get('status',{}).get('code'),'has_content':bool(data.get('result',{}).get('message',{}).get('content')),'seconds':round(time.monotonic()-start,2)}
    except urllib.error.HTTPError as error:
        result={'model':model,'http_status':error.code}
    except urllib.error.URLError as error:
        result={'model':model,'error_type':type(error.reason).__name__,'reason':str(error.reason)}
    results.append(result)
    print(json.dumps(result),flush=True)
Path('reports/hyperclova_availability.json').write_text(json.dumps(results,indent=2),encoding='utf-8')
