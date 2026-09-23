import logging
import uvicorn

logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(name)s: %(message)s')
# HTTP request URLs may contain Telegram tokens; never log them.
logging.getLogger('httpx').setLevel(logging.WARNING)
logging.getLogger('httpcore').setLevel(logging.WARNING)
uvicorn.run('app.main:app',host='127.0.0.1',port=8000,access_log=False)
