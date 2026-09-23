"""Website routes over the same grounded assistant used by Telegram."""
from __future__ import annotations

import asyncio
import copy
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, StrictInt


WORKFLOWS = [
    {"id": "article", "title": "По артикулу", "prompt": "Проверить товар по артикулу"},
    {"id": "alternatives", "title": "Найти замену", "prompt": "Подбери аналог товара"},
    {"id": "selection", "title": "Помочь с выбором", "prompt": "Не могу выбрать. Помоги как менеджер: задай один вопрос о моей задаче."},
    {"id": "repeat", "title": "Повторить подбор", "prompt": "Покажи мои прошлые сохранённые подборы"},
    {"id": "terms", "title": "Условия покупки", "prompt": "Какие оплата, доставка и минимальная партия?"},
    {"id": "specification", "title": "Загрузить спецификацию", "prompt": "Сверь спецификацию с каталогом и подготовь состав для подтверждения"},
]


class ProposalItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    product_id: Annotated[StrictInt, Field(gt=0)]
    quantity: Annotated[float, Field(gt=0, le=1e7, strict=True, allow_inf_nan=False)]


class ProposalInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[ProposalItem] = Field(min_length=1, max_length=100)


class ClearCartInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirmation: str = Field(max_length=100)


def create_site_router(session_user):
    router = APIRouter()

    def local_cart(a, key):
        cart = a.store.get_cart(key)
        return cart | {"url": "/cart/" + cart["token"]}

    def record(request, department, kind, payload=None):
        cms = getattr(request.app.state, "cms", None)
        if cms:
            cms.record_event(department, kind, payload or {})

    @router.get('/api/catalog')
    def catalog(request: Request, q: str = Query('', max_length=500),
                limit: int = Query(24, ge=1, le=30), offset: int = Query(0, ge=0),
                category: str = Query('', max_length=200)):
        cat = request.app.state.assistant.catalog
        # Search is bounded to the ranked 30 candidates; browse is paginated.
        products = cat.search(q, limit=30) if q.strip() else list(cat._items.values())
        if category:
            products = [p for p in products if p['category'] == category]
        if not q.strip():
            products = sorted(products, key=lambda p: (p['quantity'] <= 0, p['id']))
        categories = [{"id": k, "name": k.replace('_', ' ').replace('-', ' '), "count": len(v)}
                      for k, v in sorted(cat._categories.items(), key=lambda pair: -len(pair[1]))]
        record(request, 'marketing', 'catalog_search' if q.strip() else 'catalog_view',
               {"count": len(products)})
        return {"items": copy.deepcopy(products[offset:offset + limit]), "total": len(products),
                "catalog_total": cat.count, "categories": categories,
                "offset": offset, "limit": limit, "search_limit": 30 if q.strip() else None}

    @router.get('/api/catalog/{product_id}')
    async def product(product_id: str, request: Request):
        cat = request.app.state.assistant.catalog
        p = cat.get(product_id)
        if not p:
            raise HTTPException(404, 'Товар не найден в каталоге.')
        # Browsing never waits for EKT. Background sync owns catalog updates.
        return p

    @router.get('/api/session')
    def session(request: Request, response: Response):
        a = request.app.state.assistant
        key = session_user(request, response)
        return {'history': a.store.history(key, limit=80), 'proposal': a.store.pending(key),
                'cart': local_cart(a, key), 'orders': a.store.orders(key), 'workflows': WORKFLOWS}

    @router.get('/api/content')
    def content(request: Request):
        cms = getattr(request.app.state, 'cms', None)
        return cms.public_content() if cms else {'welcome': 'Помогу найти товар и проверить наличие.', 'delivery_note': ''}

    @router.post('/api/proposal')
    async def propose(data: ProposalInput, request: Request, response: Response):
        a = request.app.state.assistant
        key = session_user(request, response)
        try:
            async with asyncio.timeout(7.5):
                async with a.locks[str(key)]:
                    result = await a.run_tool(key, 'propose_cart', {'items': [x.model_dump() for x in data.items]})
        except TimeoutError:
            raise HTTPException(503, 'Предыдущий запрос ещё обрабатывается. Корзина не изменена.') from None
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None
        if result.get('error'):
            raise HTTPException(409, result['error'])
        record(request, 'sales', 'proposal_created', {'items_count': len(result.get('items', []))})
        return result

    @router.post('/api/proposal/cancel')
    async def cancel(request: Request, response: Response):
        a = request.app.state.assistant
        key = session_user(request, response)
        async with asyncio.timeout(7.5):
            async with a.locks[str(key)]:
                a.store.cancel_proposal(key)
        return {'status': 'cancelled'}

    @router.get('/api/orders')
    def orders(request: Request, response: Response):
        a = request.app.state.assistant
        return {'items': a.store.orders(session_user(request, response)), 'kind': 'saved_selection'}

    @router.post('/api/orders/save')
    async def save(request: Request, response: Response):
        a = request.app.state.assistant
        key = session_user(request, response)
        async with asyncio.timeout(7.5):
            async with a.locks[str(key)]:
                result = a.store.save_request(key)
        record(request, 'sales', 'selection_saved', {'status': result['status']})
        return result

    @router.post('/api/orders/{order_id}/repeat')
    async def repeat(order_id: str, request: Request, response: Response):
        a = request.app.state.assistant
        key = session_user(request, response)
        order = next((x for x in a.store.orders(key, limit=100) if x['id'] == order_id), None)
        if not order:
            raise HTTPException(404, 'Сохранённый подбор не найден в вашей сессии.')
        return await propose(ProposalInput(items=[{'product_id': p['id'], 'quantity': p['quantity']}
                                                for p in order['items']]), request, response)

    @router.delete('/api/cart')
    async def clear(data: ClearCartInput, request: Request, response: Response):
        if data.confirmation != 'Да, очистить корзину':
            raise HTTPException(400, 'Нужно явное подтверждение «Да, очистить корзину».')
        a = request.app.state.assistant
        key = session_user(request, response)
        async with asyncio.timeout(7.5):
            async with a.locks[str(key)]:
                a.store.clear_cart(key)
        record(request, 'sales', 'cart_cleared')
        return local_cart(a, key)

    return router
