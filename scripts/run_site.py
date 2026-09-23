"""Run the integrated website without starting a second Telegram poller."""
import os
from pathlib import Path
import sys
import logging
import shutil

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ['TELEGRAM_BOT_TOKEN'] = ''
os.environ['EKT_SITE_ONLY'] = '1'
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s: %(message)s')
logging.getLogger('httpx').setLevel(logging.WARNING)
logging.getLogger('httpcore').setLevel(logging.WARNING)

if __name__ == '__main__':
    # Railway mounts an empty persistent volume over /app/data on first start.
    # Keep the seed outside that mount and copy it only when no database exists.
    data_dir=ROOT/'data'
    data_dir.mkdir(parents=True,exist_ok=True)
    seed=ROOT/'seed'/'raw_details.jsonl'
    if seed.is_file() and not (data_dir/'catalog.sqlite').exists() and not (data_dir/'raw_details.jsonl').exists():
        shutil.copyfile(seed,data_dir/'raw_details.jsonl')
    import uvicorn
    uvicorn.run('app.main:app', host=os.getenv('HOST', '127.0.0.1'),
                port=int(os.getenv('PORT', '8001')), access_log=False)
