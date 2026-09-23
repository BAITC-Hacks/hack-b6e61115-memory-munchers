from contextlib import asynccontextmanager
import asyncio
import html
import logging
import secrets
import time
import re
import os

from fastapi import FastAPI, HTTPException, Request, Response, UploadFile, File, Form, Depends
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .config import settings, ROOT
from .catalog_sync import SyncedCatalog, CatalogSync
Catalog = SyncedCatalog
from .store import Store
from .agent import Assistant
from .attachments import extract_attachment
from .telegram_bot import launch
from .site_api import create_site_router

log=logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app):
    catalog=Catalog(settings.data_dir)
    store=Store(settings.db_path)
    app.state.assistant=Assistant(catalog,store)
    from .cms import CMSRepository
    app.state.cms=CMSRepository(settings.data_dir, settings.db_path)
    app.state.catalog_sync=None
    if isinstance(catalog,SyncedCatalog) and os.getenv('CATALOG_SYNC_ENABLED','1')!='0':
        app.state.catalog_sync=CatalogSync(catalog,app.state.cms)
        app.state.assistant.catalog_sync=app.state.catalog_sync
        await app.state.catalog_sync.start()
    app.state.telegram_status='not_configured'
    telegram_app=None
    if settings.telegram_token:
        try:
            telegram_app=await launch(app.state.assistant)
            app.state.telegram_status='polling'
        except Exception as exc:
            app.state.telegram_status='failed:'+type(exc).__name__
            log.warning('Telegram startup: %s',type(exc).__name__)
    yield
    if telegram_app:
        await telegram_app.updater.stop()
        await telegram_app.stop()
        await telegram_app.shutdown()
    if app.state.catalog_sync:
        await app.state.catalog_sync.stop()
    if app.state.assistant.client: await app.state.assistant.client.close()
    if isinstance(catalog,SyncedCatalog): catalog.close()
    app.state.cms.close()
    store.close()


app=FastAPI(title='EKT assistant',lifespan=lifespan)
app.mount('/assets', StaticFiles(directory=ROOT/'дизайн'/'assets'), name='assets')


@app.middleware('http')
async def private_api_responses(request:Request,call_next):
    response=await call_next(request)
    # Dialogues, cart tokens and saved selections must not survive in browser
    # or intermediary caches, including validation and timeout responses.
    if request.url.path.startswith('/api/') and request.url.path not in {'/api/catalog','/api/content'} and not request.url.path.startswith('/api/catalog/'):
        response.headers['Cache-Control']='no-store'
        response.headers['Referrer-Policy']='no-referrer'
    return response


@app.get('/static/{asset}')
def static_asset(asset:str):
    if asset not in {'site.css','site.js','admin.css','admin.js'}:
        raise HTTPException(404,'Файл не найден.')
    return FileResponse(ROOT/'app'/asset)


@app.exception_handler(TimeoutError)
async def request_timeout(request:Request,exc):
    return JSONResponse({'detail':'Запрос не успел завершиться. Повтори действие.'},status_code=503)


def user(request:Request,response:Response):
    token=request.cookies.get('ekt_session')
    if not token or not re.fullmatch(r'[A-Za-z0-9_-]{43}',token):
        token=secrets.token_urlsafe(32)
        response.set_cookie('ekt_session',token,httponly=True,samesite='strict',max_age=30*86400,
                            secure=request.url.scheme=='https')
    return 'web:'+token


app.include_router(create_site_router(user))
from .cms import create_cms_router, admin_access
app.include_router(create_cms_router())


def web_result(a, key, result):
    """Website links always address the same cookie's web cart, not Telegram."""
    cart=a.store.get_cart(key)
    url='/cart/'+cart['token']
    previous=result.get('cart_url')
    if previous and previous != url and result.get('text'):
        result['text']=result['text'].replace(previous,url)
    result['cart_url']=url
    result['cart']=cart | {'url':url}
    result['proposal']=a.store.pending(key)
    return result


async def bounded_reply(request, key, text, parsed=None, budget=7.6):
    a=request.app.state.assistant
    started=time.perf_counter()
    try:
        async with asyncio.timeout(budget):
            syncer=getattr(request.app.state,'catalog_sync',None)
            assertion=bool(re.search(r'товар.{0,35}существует|точно.{0,25}есть|есть.{0,30}на сайте|на (?:вашем )?сайте.{0,30}есть|я (?:видел|вижу).{0,50}(?:сайт|товар)',text,re.I))
            if syncer and assertion and parsed is None:
                found=await syncer.lookup(text)
                p=found.get('product')
                if found['status']=='found' and p:
                    answer='Проверил EKT и сохранил карточку в локальную базу. Запустил внеочередное обновление каталога.'
                elif found['status']=='ambiguous':
                    answer='На сайте нашлось несколько вариантов. Пришли точный артикул или ссылку на товар. Обновление базы запущено.'
                elif found['status']=='not_found':
                    answer='По текущему запросу не удалось подтвердить товар на сайте. Пришли артикул или ссылку на карточку; обновление базы уже запущено.'
                else:
                    answer='Быстрая проверка сайта не завершилась. Запустил фоновое обновление базы; пришли точный артикул или ссылку на товар.'
                a.store.add_message(key,'user',text)
                a.store.add_message(key,'assistant',answer)
                result={'text':answer,'products':[p] if p else [],'trace':['website_lookup','catalog_sync_trigger'],
                        'status':found['status'],'sync_triggered':found.get('sync_triggered',True),'usage':[],
                        'seconds':round(time.perf_counter()-started,3)}
            else:
                result=await a.reply(key,text,parsed)
    except TimeoutError:
        # Never describe a partial research/selection as completed.
        products=a.catalog.search(text,limit=3) if text else []
        answer=('Полный подбор не успел завершиться. Вот ближайшие результаты каталога; '
                'уточни артикул или один главный параметр.' if products else
                'Не успел завершить обработку. Уточни артикул или одну позицию из файла — проверю её отдельно.')
        result={'text':answer,'products':products,'trace':['deadline_fallback'],
                'status':'timeout','usage':[],'seconds':round(time.perf_counter()-started,3)}
        a.store.add_message(key,'assistant',answer)
    result['server_seconds']=round(time.perf_counter()-started,3)
    failed=result.get('status') in {'timeout','error','deadline','unavailable','provider_error','busy'}
    request.app.state.cms.record_event('support','chat_error' if failed else 'assistant_reply',
        {'seconds':result['server_seconds'],'status':result.get('status','completed'),
         'manager':result.get('manager',False),'success':not failed})
    return web_result(a,key,result)


class ChatInput(BaseModel):
    text:str=Field(min_length=1,max_length=12000)


class ConfirmInput(BaseModel):
    proposal_id:str=Field(min_length=1,max_length=100)
    confirmation:str


@app.get('/health')
def health(request:Request):
    a=request.app.state.assistant
    return {'status':'ok','catalog_products':a.catalog.count,'openai_configured':bool(a.client),
            'model':a.config.model,'telegram':request.app.state.telegram_status,
            'bot_username':a.config.bot_username or None,'ekt_cart_bridge':False,
            'cms':True,'response_deadline_seconds':7.6}


@app.get('/api/admin/catalog-sync',dependencies=[Depends(admin_access)])
def catalog_sync_status(request:Request):
    syncer=request.app.state.catalog_sync
    return syncer.status() if syncer else {'enabled':False,'state':'disabled','interval_seconds':300}


@app.post('/api/admin/catalog-sync/trigger',dependencies=[Depends(admin_access)])
def catalog_sync_trigger(request:Request):
    syncer=request.app.state.catalog_sync
    if not syncer: raise HTTPException(503,'Фоновая синхронизация отключена.')
    syncer.trigger('admin')
    return syncer.status()


@app.get('/',response_class=HTMLResponse)
def index():
    return (ROOT/'app'/'site.html').read_text(encoding='utf-8')


@app.get('/admin',response_class=HTMLResponse)
def admin():
    return (ROOT/'app'/'admin.html').read_text(encoding='utf-8')


@app.post('/api/chat')
async def chat(data:ChatInput,request:Request,response:Response):
    return await bounded_reply(request,user(request,response),data.text)


@app.post('/api/attachment')
async def attachment(request:Request,response:Response,file:UploadFile=File(...),text:str=Form('',max_length=12000)):
    started=time.perf_counter()
    content=await file.read(settings.max_attachment_bytes+1)
    if len(content)>settings.max_attachment_bytes: raise HTTPException(413,'Максимум 15 МБ.')
    try:
        async with asyncio.timeout(1):
            parsed=await asyncio.to_thread(extract_attachment,file.filename or '',content)
    except TimeoutError:
        raise HTTPException(422,'Файл слишком сложный для быстрого чтения. Пришли одну страницу или небольшой фрагмент таблицы.') from None
    if not parsed.get('text') and not parsed.get('image_data_url'):
        raise HTTPException(422,parsed.get('warning') or 'Файл не прочитан.')
    result=await bounded_reply(request,user(request,response),text,parsed,
                               budget=max(0.1,7.6-(time.perf_counter()-started)))
    result['attachment_warning']=parsed.get('warning')
    return result


@app.post('/api/confirm')
async def confirm(data:ConfirmInput,request:Request,response:Response):
    if data.confirmation!='Да, добавь': raise HTTPException(400,'Нужно явное подтверждение «Да, добавь».')
    a=request.app.state.assistant
    key=user(request,response)
    try:
        async with asyncio.timeout(7.6):
            result=await a.confirm(key,data.proposal_id,explicit=True)
    except TimeoutError:
        result={'status':'stock_unavailable','text':'Проверка наличия не успела завершиться. Корзина не изменена. Повтори подтверждение.'}
    request.app.state.cms.record_event('sales','cart_confirmation' if result['status']=='confirmed' else 'confirm_error',{'status':result['status']})
    return web_result(a,key,result)


@app.get('/api/cart')
def cart(request:Request,response:Response):
    a=request.app.state.assistant
    key=user(request,response)
    cart=a.store.get_cart(key)
    return cart | {'url':'/cart/'+cart['token']}


@app.get('/cart/{token}',response_class=HTMLResponse)
def cart_page(token:str,request:Request):
    cart=request.app.state.assistant.store.cart_by_token(token)
    if not cart: raise HTTPException(404,'Корзина не найдена.')
    esc=lambda v: html.escape(str(v))
    # Reuse the accepted storefront CSS and its original cart row structure.
    site=(ROOT/'app'/'site.html').read_text(encoding='utf-8')
    css=site.split('<style>',1)[1].split('</style>',1)[0]
    rows=''.join(f'<article class="row"><div><div class="sku">{esc(p["article"])}</div><b>{esc(p["name"])}</b><div>{esc(p["price"])} ₸</div></div><div class="buybox">{esc(p["quantity"])} × {esc(p["price"])} ₸</div></article>' for p in cart['items'])
    return HTMLResponse('<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Корзина EKT</title><style>'+css+'</style><header class="bar"><a class="logo" href="/"><span class="mark">E<i></i></span><b>ЭЛЕКТРОКОМПЛЕКТ</b></a></header><main><section class="crumb"><h1>Корзина</h1><p>Это актуальная корзина ассистента. Позиции не зарезервированы. Оформление и оплата на ekt.kz пока не связаны.</p></section>'+rows+f'<p style="padding:16px"><b>Итого: {esc(cart["total"])} ₸</b></p><p class="foot"><a href="/">В каталог</a> · <a href="https://ekt.kz/checkout-delivery/">Условия покупки EKT</a></p></main></html>',headers={'Cache-Control':'no-store','Referrer-Policy':'no-referrer'})
