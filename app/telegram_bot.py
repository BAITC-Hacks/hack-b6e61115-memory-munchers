import asyncio
import contextlib
import logging
import time

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, BotCommand
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, MessageHandler, filters

from .attachments import extract_attachment
from .terms import terms_text

log = logging.getLogger(__name__)

MENU = ReplyKeyboardMarkup([['По артикулу', 'Подобрать по задаче'], ['Нужен аналог', 'Моя корзина'],
                           ['Оплата и доставка', 'Прошлые подборы']], resize_keyboard=True)
START = ('Я помогу найти электротехнику в каталоге EKT. Пришли артикул, описание задачи, '
         'фото или спецификацию Excel / Word / PDF.\n\n'
         'Сначала согласуем товар и количество. В корзину добавлю только после отдельного «Да, добавь».')


def user_key(update):
    return 'tg:' + str(update.effective_user.id)


async def send_text(message, text, markup=None):
    # Plain text prevents model/catalog strings becoming Telegram markup.
    chunks = [text[i:i+3800] for i in range(0,len(text),3800)] or ['Готово.']
    for i, chunk in enumerate(chunks):
        await message.reply_text(chunk, reply_markup=markup if i==len(chunks)-1 else None, disable_web_page_preview=True)


def cart_text(cart):
    if not cart['items']: return 'Корзина пока пуста. Напиши товар и количество.'
    lines = ['Твоя корзина:']
    for p in cart['items']:
        lines.append(f"{p['name']}\n{p['article']} · {p['quantity']} × {p['price']} ₸")
    lines.append(f"\nИтого: {cart['total']} ₸")
    lines.append('Это корзина ассистента. Оплата и оформление на ekt.kz пока не связаны; наличие не зарезервировано.')
    return '\n\n'.join(lines)


def build_application(assistant):
    cfg = assistant.config
    application = (Application.builder().token(cfg.telegram_token).concurrent_updates(8)
                   .connect_timeout(10).read_timeout(25).write_timeout(25).build())

    async def show_cart(update, context):
        key=user_key(update)
        cart=assistant.store.get_cart(key)
        markup=InlineKeyboardMarkup([[InlineKeyboardButton('Сохранить подбор',callback_data='save_request')],
                                     [InlineKeyboardButton('Открыть актуальную корзину',url=assistant.cart_url(key))]]) if cart['items'] else None
        await send_text(update.effective_message,cart_text(cart),markup)

    async def orders(update, context):
        records=assistant.store.orders(user_key(update))
        if not records:
            await send_text(update.effective_message,'Сохранённых подборов пока нет. Они появятся после добавления в корзину и кнопки «Сохранить подбор».')
            return
        lines=['Сохранённые подборы — это не оплаченные заказы ekt.kz:']
        for record in records:
            lines.append(str(record.get('created_at','')) + '\n' + '\n'.join(f"{p['article']} — {p['name']} × {p['quantity']}" for p in record['items']))
        lines.append('Напиши «повтори последний подбор», чтобы заново проверить цены, остатки и подтвердить состав.')
        await send_text(update.effective_message,'\n\n'.join(lines))

    async def start(update, context):
        if context.args and context.args[0]=='cart': return await show_cart(update,context)
        await send_text(update.effective_message,START,MENU)

    async def callback(update, context):
        q=update.callback_query
        await q.answer()
        if update.effective_chat.type!='private': return
        key=user_key(update)
        if q.data.startswith('confirm:'):
            await send_text(q.message,'Проверяю остаток и согласованный состав…')
            result=await assistant.confirm(key,q.data.split(':',1)[1],explicit=True)
            await send_text(q.message,result['text'])
        elif q.data=='cancel':
            assistant.store.cancel_proposal(key)
            await send_text(q.message,'Предложение отменено, корзина не изменилась.')
        elif q.data=='save_request':
            try:
                saved=assistant.store.save_request(key)
                if saved.get('status')=='empty_cart':
                    return await send_text(q.message,'Корзина пуста; сначала добавь товар.')
                await send_text(q.message,'Подбор сохранён в твоей истории. Его можно повторить через /orders. Заказ в магазин не отправлялся.')
            except ValueError:
                await send_text(q.message,'Корзина пуста; сначала добавь товар.')
        with contextlib.suppress(Exception): await q.edit_message_reply_markup(reply_markup=None)

    async def typing(chat):
        while True:
            with contextlib.suppress(Exception): await chat.send_action('typing')
            await asyncio.sleep(4)

    async def message(update, context):
        msg=update.effective_message
        if update.effective_chat.type!='private':
            return await send_text(msg,'Для личной корзины открой со мной личный чат.')
        text=msg.text or msg.caption or ''
        if text=='Моя корзина': return await show_cart(update,context)
        if text=='Прошлые подборы': return await orders(update,context)
        if text=='Оплата и доставка': return await send_text(msg,terms_text())
        questions={'По артикулу':'Пришли артикул — проверю цену, наличие, характеристики и документы.',
                   'Подобрать по задаче':'Для какой задачи подбираешь оборудование?',
                   'Нужен аналог':'Пришли артикул или фото товара, которому нужна замена.'}
        if text in questions: return await send_text(msg,questions[text])
        async def prepare_answer():
            attachment=None
            warning=None
            if msg.document or msg.photo:
                f=msg.document or msg.photo[-1]
                if f.file_size and f.file_size>cfg.max_attachment_bytes:
                    return {'text':'Файл больше 15 МБ. Пришли нужный фрагмент спецификации.'}
                try:
                    async with asyncio.timeout(2.5):
                        file=await f.get_file()
                        payload=bytes(await file.download_as_bytearray())
                        attachment=await asyncio.to_thread(extract_attachment, getattr(f,'file_name',None) or 'photo.jpg',payload)
                except Exception:
                    return {'text':'Не удалось быстро прочитать файл. Пришли нужную страницу или напиши артикул текстом; корзина не менялась.'}
                warning=attachment.get('warning')
                if not attachment.get('text') and not attachment.get('image_data_url'):
                    return {'text':warning or 'Файл не удалось прочитать.'}
            result=await assistant.reply(user_key(update),text,attachment)
            if warning: result['text']=warning+'\n\n'+result['text']
            return result
        worker=asyncio.create_task(typing(update.effective_chat))
        try:
            try:
                # The same complete budget covers file download and planning.
                # Keep enough room for the measured 6.6 s research route.
                result=await asyncio.wait_for(prepare_answer(),timeout=cfg.response_timeout)
            except TimeoutError:
                result={'text':'Внешний сервис не успел ответить. Корзина не изменена. Пришли точный артикул — проверю его сразу по локальной базе.'}
            proposal=result.get('proposal')
            markup=InlineKeyboardMarkup([[InlineKeyboardButton('Да, добавь',callback_data='confirm:'+proposal['id']),
                                          InlineKeyboardButton('Отмена',callback_data='cancel')]]) if proposal else None
            await send_text(msg,result['text'],markup)
        finally:
            worker.cancel()
            with contextlib.suppress(asyncio.CancelledError): await worker

    async def error_handler(update, context):
        log.warning('Telegram handler failed: %s',type(context.error).__name__)
        if update and update.effective_message:
            with contextlib.suppress(Exception):
                await send_text(update.effective_message,'Не удалось завершить действие. Проверь /cart перед повторным добавлением.')

    application.add_handler(CommandHandler('start',start,filters=filters.ChatType.PRIVATE))
    application.add_handler(CommandHandler('cart',show_cart,filters=filters.ChatType.PRIVATE))
    application.add_handler(CommandHandler('orders',orders,filters=filters.ChatType.PRIVATE))
    application.add_handler(CallbackQueryHandler(callback))
    application.add_handler(MessageHandler((filters.TEXT | filters.PHOTO | filters.Document.ALL) & ~filters.COMMAND,message))
    application.add_error_handler(error_handler)
    return application


async def launch(assistant):
    application=build_application(assistant)
    await application.initialize()
    me=await application.bot.get_me()
    assistant.config.bot_username=me.username
    webhook=await application.bot.get_webhook_info()
    if webhook.url:
        await application.shutdown()
        raise RuntimeError('У этого бота включён webhook; он не был изменён. Нужен отдельный токен или согласованное переключение.')
    await application.bot.set_my_commands([BotCommand('start','Начать подбор'),BotCommand('cart','Моя корзина'),BotCommand('orders','Прошлые подборы')])
    await application.start()
    await application.updater.start_polling(drop_pending_updates=False,allowed_updates=['message','callback_query'])
    return application
