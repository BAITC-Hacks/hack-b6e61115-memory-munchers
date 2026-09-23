"""Measure real data retrieval; --live adds actual OpenAI calls, --baseline bypasses bot."""
import argparse
import asyncio
from dataclasses import replace
import json
from pathlib import Path
import statistics
import sys
import tempfile
import time

sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from app.config import settings
from app.catalog import Catalog
from app.store import Store
from app.agent import Assistant, SYSTEM, compact


async def run(args):
    start=time.perf_counter(); catalog=Catalog(settings.data_dir)
    report={'mode':'direct_model_baseline' if args.baseline else ('live_agent' if args.live else 'local_retrieval'),
            'model':settings.model,'catalog_products':catalog.count,'load_seconds':round(time.perf_counter()-start,3),'runs':[]}
    queries=['010500006_','310100024_','автомат 16А','кабель 3х2.5']
    for iteration in range(2):
        for query in queries:
            t=time.perf_counter(); result=catalog.search(query,limit=3)
            report['runs'].append({'query':query,'iteration':iteration+1,'seconds':round(time.perf_counter()-t,5),
                                   'articles':[p['article'] for p in result]})
    if args.live or args.baseline:
        if not settings.api_key:
            report['blocked']='OPENAI_API_KEY не заполнен; генерация не запускалась.'
        else:
            with tempfile.TemporaryDirectory() as folder:
                store=Store(Path(folder)/'benchmark.sqlite')
                assistant=Assistant(catalog,store,replace(settings,data_dir=Path(folder)))
                for i in range(args.repeats):
                    if args.baseline:
                        product=catalog.get('010500006_')
                        started=time.perf_counter()
                        try:
                            response=await assistant.client.responses.create(model=settings.model,
                                instructions=SYSTEM,input='Есть ли 010500006_? Дай наличие, характеристики, сертификат. Единственный источник: '+json.dumps(compact(product),ensure_ascii=False),max_output_tokens=4000)
                            report.setdefault('llm_runs',[]).append({'iteration':i+1,'seconds':round(time.perf_counter()-started,3),
                                'text':response.output_text,'usage':response.usage.model_dump() if response.usage else {},'status':response.status})
                        except Exception as exc:
                            report.setdefault('llm_runs',[]).append({'iteration':i+1,'seconds':round(time.perf_counter()-started,3),'error':type(exc).__name__})
                    else:
                        for prompt in ['Есть ли 010500006_? Наличие, характеристики и сертификат.',
                                       '310100024_ нет в наличии? Предложи аналог и объясни различия.',
                                       'Какие условия оплаты, доставки и минимальной партии?',
                                       'Подготовь 2 штуки 010500006_ в корзину. Пока не добавляй.']:
                            result=await assistant.reply('bench:'+str(i),prompt)
                            report.setdefault('llm_runs',[]).append({'iteration':i+1,'prompt':prompt,**result})
                        before=store.get_cart('bench:'+str(i))['items']
                        report.setdefault('checks',[]).append({'before_confirmation_cart_empty':not before})
                        confirmed=await assistant.reply('bench:'+str(i),'да, добавь')
                        basket=store.get_cart('bench:'+str(i))
                        snapshot=store.cart_by_token(basket['token'])
                        report['checks'].append({'iteration':i+1,'confirmed':confirmed.get('status')=='confirmed',
                            'quantity':sum(p['quantity'] for p in basket['items']),
                            'direct_link_matches_cart':snapshot['items']==basket['items']})
                        store.save_request('bench:'+str(i))
                        if args.dialogue:
                            result=await assistant.reply('bench:'+str(i),'Нужен однополюсный автомат C16 на 230 В, 4.5 кА, с реальными документами. Подбери подходящий товар из каталога.')
                            report.setdefault('llm_runs',[]).append({'iteration':i+1,'prompt':'natural_selection',**result})
                await assistant.client.close();store.close()
    report['retrieval_median_ms']=round(statistics.median(r['seconds'] for r in report['runs'])*1000,3)
    path=settings.data_dir/('benchmark_'+report['mode']+'.json')
    path.write_text(json.dumps(report,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
    print(json.dumps({'report':str(path), 'catalog_products':catalog.count, 'retrieval_median_ms':report['retrieval_median_ms'],
                      'llm_runs':len(report.get('llm_runs',[])), 'blocked':report.get('blocked')},ensure_ascii=False,indent=2))
    return 2 if report.get('blocked') else 0


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--live',action='store_true');p.add_argument('--baseline',action='store_true');p.add_argument('--dialogue',action='store_true');p.add_argument('--repeats',type=int,default=2)
    raise SystemExit(asyncio.run(run(p.parse_args())))
