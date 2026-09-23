"""Set variables through Railway CLI stdin; never print secret values."""
import argparse
from pathlib import Path
import secrets
import shutil
import subprocess
import os

from dotenv import dotenv_values

ROOT=Path(__file__).resolve().parent.parent


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--project',required=True)
    p.add_argument('--service',required=True)
    p.add_argument('--environment',required=True)
    p.add_argument('--url',required=True)
    args=p.parse_args()
    settings=dotenv_values(ROOT/'.env',encoding='utf-8-sig')
    admin_file=ROOT/'.env.railway'
    admin=dotenv_values(admin_file,encoding='utf-8') if admin_file.exists() else {}
    if not admin.get('CMS_ADMIN_TOKEN'):
        admin['CMS_ADMIN_TOKEN']=secrets.token_urlsafe(36)
        admin_file.write_text('CMS_ADMIN_TOKEN='+admin['CMS_ADMIN_TOKEN']+'\n',encoding='utf-8')
    cli=shutil.which('railway') or str(Path(os.environ['APPDATA'])/'npm'/'railway.cmd')
    values={key:settings.get(key,'') for key in ('OPENAI_API_KEY','EKT_API_BASE','EKT_API_USER','EKT_API_PASSWORD')}
    values.update(OPENAI_MODEL='gpt-5.4-mini',OPENAI_RESEARCH_MODEL='gpt-5.4-mini',
                  MODEL_TIMEOUT_SECONDS='5.2',RESPONSE_TIMEOUT_SECONDS='7.2',HOST='0.0.0.0',PORT='8001',
                  PUBLIC_BASE_URL=args.url,CMS_ADMIN_TOKEN=admin['CMS_ADMIN_TOKEN'],CATALOG_SYNC_ENABLED='1')
    if not values['OPENAI_API_KEY'] or not values['EKT_API_USER'] or not values['EKT_API_PASSWORD']:
        raise SystemExit('Missing required local environment values; no variables uploaded.')
    for key,value in values.items():
        command=[cli,'variable','set',key,'--stdin','--skip-deploys','--project',args.project,
                 '--service',args.service,'--environment',args.environment]
        done=subprocess.run(command,input=str(value),text=True,capture_output=True,encoding='utf-8',errors='replace',timeout=60)
        if done.returncode:
            # CLI error text may contain submitted values, so only its code is logged.
            raise SystemExit(f'Railway variable {key} failed (exit {done.returncode}).')
        print('Configured '+key,flush=True)
    print('Admin credential saved to ignored .env.railway; plaintext was not printed.')


if __name__=='__main__':
    main()
