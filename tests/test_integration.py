"""Website integration with real catalog and isolated carts; no external APIs."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
import asyncio
import time

import pytest
from fastapi.testclient import TestClient

from app import main
from app.agent import Assistant
from app.catalog import Catalog
from app.config import ROOT, Settings


@pytest.fixture(scope='module')
def catalog():
    return Catalog(ROOT/'data')


@pytest.fixture
def site(tmp_path, monkeypatch, catalog):
    config=Settings(api_key='',telegram_token='',data_dir=tmp_path,db_path=tmp_path/'assistant.sqlite')
    monkeypatch.setattr(main,'settings',config)
    monkeypatch.setattr(main,'Catalog',lambda path:catalog)
    monkeypatch.setattr(main,'Assistant',lambda cat,store:Assistant(cat,store,config))
    monkeypatch.delenv('CMS_ADMIN_TOKEN',raising=False)
    monkeypatch.setenv('CATALOG_SYNC_ENABLED','0')
    async def stock(pid):
        return catalog.get(pid) | {'stock_fresh':True}
    monkeypatch.setattr(catalog,'refresh',AsyncMock(side_effect=stock))
    with TestClient(main.app,client=('127.0.0.1',54321)) as client:
        yield SimpleNamespace(client=client,a=main.app.state.assistant,cms=main.app.state.cms)


def draft(site, quantity=2):
    p=site.a.catalog.get('010500006_')
    response=site.client.post('/api/proposal',json={'items':[{'product_id':p['id'],'quantity':quantity}]})
    assert response.status_code==200,response.text
    return response.json(),p


def test_real_catalog_pagination_and_unknown_id(site):
    first=site.client.get('/api/catalog?limit=3').json()
    second=site.client.get('/api/catalog?limit=3&offset=3').json()
    assert first['total']==site.a.catalog.count
    assert len(first['items'])==len(second['items'])==3
    assert {p['id'] for p in first['items']}.isdisjoint(p['id'] for p in second['items'])
    assert first['categories']
    assert site.client.get('/api/catalog?limit=100').status_code==422
    assert site.client.get('/api/catalog/no-such-product').status_code==404


@pytest.mark.parametrize('item',[
    {'product_id':21449,'quantity':True}, {'product_id':True,'quantity':1},
    {'product_id':21449,'quantity':0}, {'product_id':21449,'quantity':-1},
    {'product_id':21449,'quantity':1,'price':1}, {'product_id':999999999,'quantity':1},
])
def test_client_cannot_invent_prices_products_or_quantities(site,item):
    assert site.client.post('/api/proposal',json={'items':[item]}).status_code in {409,422}
    assert site.client.get('/api/cart').json()['items']==[]


def test_draft_confirm_link_memory_and_repeat_requires_new_consent(site):
    proposal,product=draft(site)
    assert site.client.get('/api/cart').json()['items']==[]
    session=site.client.get('/api/session').json()
    assert session['proposal']['id']==proposal['id']
    assert len(session['workflows'])==6
    bad=site.client.post('/api/confirm',json={'proposal_id':proposal['id'],'confirmation':'да'})
    assert bad.status_code==400
    ok=site.client.post('/api/confirm',json={'proposal_id':proposal['id'],'confirmation':'Да, добавь'}).json()
    assert ok['status']=='confirmed'
    assert ok['cart_url'].startswith('/cart/')
    assert ok['cart']['items'][0]['quantity']==2
    assert site.client.get(ok['cart_url']).status_code==200
    site.client.post('/api/confirm',json={'proposal_id':proposal['id'],'confirmation':'Да, добавь'})
    assert site.client.get('/api/cart').json()['items'][0]['quantity']==2
    saved=site.client.post('/api/orders/save').json()
    assert saved['status']=='request'
    assert site.client.get('/api/orders').json()['items'][0]['id']==saved['id']
    cleared=site.client.request('DELETE','/api/cart',json={'confirmation':'Да, очистить корзину'})
    assert cleared.status_code==200
    repeat=site.client.post('/api/orders/'+saved['id']+'/repeat').json()
    assert repeat['status']=='pending'
    assert repeat['id']!=proposal['id']
    assert site.client.get('/api/cart').json()['items']==[]


def test_full_proposal_shows_all_lines_and_never_partial_add(site):
    products=[p for p in site.a.catalog._items.values() if p['quantity']>=10][:2]
    response=site.client.post('/api/proposal',json={'items':[{'product_id':p['id'],'quantity':2} for p in products]})
    proposal=response.json()
    assert len(proposal['items'])==2
    assert site.client.get('/api/cart').json()['items']==[]
    async def changed(pid):
        return site.a.catalog.get(pid) | {'stock_fresh':True,'quantity':1 if pid==products[1]['id'] else 50}
    site.a.catalog.refresh.side_effect=changed
    result=site.client.post('/api/confirm',json={'proposal_id':proposal['id'],'confirmation':'Да, добавь'}).json()
    assert result['status']=='insufficient_stock'
    assert site.client.get('/api/cart').json()['items']==[]


def test_clear_cart_requires_explicit_consent_and_preserves_other_sessions(site):
    proposal,product=draft(site)
    result=site.client.post('/api/confirm',json={'proposal_id':proposal['id'],'confirmation':'Да, добавь'})
    assert result.json()['status']=='confirmed'
    original=site.client.get('/api/cart').json()
    for payload in (None,{}, {'confirmation':'да'}, {'confirmation':'Не очищать'}):
        rejected=site.client.request('DELETE','/api/cart',json=payload)
        assert rejected.status_code in {400,422}
        assert site.client.get('/api/cart').json()['items']==original['items']
    other=TestClient(main.app)
    try:
        other.get('/api/session')
        response=other.request('DELETE','/api/cart',json={'confirmation':'Да, очистить корзину'})
        assert response.status_code==200
        assert site.client.get('/api/cart').json()['items']==original['items']
    finally:
        other.close()
    confirmed=site.client.request('DELETE','/api/cart',json={'confirmation':'Да, очистить корзину'})
    assert confirmed.status_code==200
    assert confirmed.json()['items']==[]
    assert product['article'] not in site.client.get(original['url']).text


@pytest.mark.parametrize('path',['/api/session','/api/cart','/api/orders'])
def test_private_session_responses_are_never_cached(site,path):
    response=site.client.get(path)
    assert response.status_code==200
    assert response.headers['cache-control']=='no-store'
    assert response.headers['referrer-policy']=='no-referrer'


def test_cms_content_is_published_and_tasks_persist(site):
    response=site.client.put('/api/admin/content',json={'welcome':'Проверим товар из базы EKT.','delivery_note':'Уточните город доставки.'})
    assert response.status_code==200,response.text
    assert site.client.get('/api/content').json()['welcome']=='Проверим товар из базы EKT.'
    response=site.client.post('/api/admin/tasks',json={'title':'Проверить скорость ответа','department':'development','status':'in_progress','assignee':'Техническая команда'})
    assert response.status_code==201,response.text
    found=site.client.get('/api/admin/tasks?department=development').json()['tasks']
    assert found[0]['id']==response.json()['id']
    assert not site.client.get('/api/admin/tasks?department=marketing').json()['tasks']


def test_static_source_files_are_not_exposed(site):
    assert site.client.get('/static/main.py').status_code==404
    assert site.client.get('/static/site.css').status_code==200
    assert site.client.get('/').status_code==200
    assert site.client.get('/admin').status_code==200


def test_timeout_returns_honest_reply_within_eight_seconds(site,monkeypatch):
    async def slow(*args,**kwargs):
        await asyncio.sleep(20)
    monkeypatch.setattr(site.a,'reply',slow)
    started=time.perf_counter()
    response=site.client.post('/api/chat',json={'text':'кабель 3х2.5'})
    assert time.perf_counter()-started<8
    result=response.json()
    assert result['status']=='timeout'
    assert 'не успел' in result['text']
    assert result['cart']['items']==[]
