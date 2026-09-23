FROM python:3.13-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends fonts-dejavu-core && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY scripts/run_site.py ./scripts/run_site.py
COPY data/raw_details.jsonl ./seed/raw_details.jsonl
COPY ["дизайн/assets", "./дизайн/assets"]
ENV HOST=0.0.0.0 PORT=8001 PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PYTHONUTF8=1
EXPOSE 8001
HEALTHCHECK --interval=10s --timeout=3s --start-period=60s --retries=6 CMD python -c "import json,os,urllib.request; r=json.load(urllib.request.urlopen('http://127.0.0.1:'+os.getenv('PORT','8001')+'/health',timeout=2)); assert r['status']=='ok' and r['catalog_products']>0"
CMD ["python", "scripts/run_site.py"]
