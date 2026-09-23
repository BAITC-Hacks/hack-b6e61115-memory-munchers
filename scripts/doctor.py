"""Read-only credential and service checks; never prints secrets or request URLs."""
import asyncio
import json
from pathlib import Path
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
import httpx
from app.config import settings


async def main():
    report={'openai':{'configured':bool(settings.api_key)},'telegram':{'configured':bool(settings.telegram_token)}}
    async with httpx.AsyncClient(timeout=12) as client:
        for name, url, headers in [
            ('openai','https://api.openai.com/v1/models',{'Authorization':'Bearer '+settings.api_key}),
            ('telegram','https://api.telegram.org/bot'+settings.telegram_token+'/getMe',{}),
        ]:
            if not report[name]['configured']: continue
            started=time.perf_counter()
            try:
                response=await client.get(url,headers=headers)
                report[name].update(http=response.status_code,seconds=round(time.perf_counter()-started,3))
                if response.is_success:
                    data=response.json()
                    if name=='openai':
                        ids={m['id'] for m in data.get('data',[])}
                        report[name]['selected_model']=settings.model
                        report[name]['model_available']=settings.model in ids
                    else: report[name]['username']=data.get('result',{}).get('username')
            except Exception as exc: report[name]['error']=type(exc).__name__
    print(json.dumps(report,ensure_ascii=False,indent=2))
    return 0 if all(x.get('http')==200 for x in report.values()) else 2


if __name__=='__main__': raise SystemExit(asyncio.run(main()))
