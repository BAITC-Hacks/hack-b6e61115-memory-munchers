"""Estimate recorded API usage; no API calls and no credential access."""
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RATES = {'gpt-5.4-mini': (0.75, 4.50), 'gpt-6-luna': (0.10, 0.50), 'o1-pro': (150.0, 600.0)}


def main():
    source = ROOT / 'data' / 'fast_flow_results.json'
    report = json.loads(source.read_text(encoding='utf-8'))
    input_rate, output_rate = RATES[report['model']]
    rows = []
    for run in report['runs']:
        tokens = run.get('tokens', {})
        inputs, outputs = tokens.get('input_tokens', 0), tokens.get('output_tokens', 0)
        # research() forces one hosted web tool call. Incomplete requests may
        # still be billed; their unknown usage is reported separately below.
        web_calls = int('research_task' in run.get('trace', []) and inputs > 0)
        estimate = (inputs * input_rate + outputs * output_rate) / 1_000_000 + web_calls * 0.01
        rows.append({'scenario': run['scenario'], 'completed': run['completed_bool'],
                     'input_tokens': inputs, 'output_tokens': outputs,
                     'hosted_web_calls_assumed': web_calls,
                     'estimated_usd_without_cache_discount': round(estimate, 8)})
    output = {
        'created_at': datetime.now(timezone.utc).isoformat(),
        'source': str(source.relative_to(ROOT)), 'model': report['model'],
        'pricing_source': 'https://developers.openai.com/api/docs/pricing',
        'rates_checked_on': '2026-09-23',
        'standard_usd_per_million_tokens': {k: {'input': v[0], 'output': v[1]} for k, v in RATES.items()},
        'hosted_web_usd_per_call': 0.01,
        'method': 'All reported input is charged at the uncached Standard rate; ignores cache discounts. Includes one forced hosted web call for completed research. Not an invoice.',
        'scope': 'Current recorded scenarios only; excludes previous attempts, failed-call unknown usage, hosting, EKT traffic and developer/Codex usage.',
        'rows': rows,
        'estimated_total_usd_without_cache_discount': round(sum(r['estimated_usd_without_cache_discount'] for r in rows), 8),
        'zero_llm_cost_scenarios': sum(not r['input_tokens'] and not r['output_tokens'] for r in rows),
        'unreported_usage_scenarios': report.get('summary', {}).get('unreported_usage_scenarios', []),
    }
    path = ROOT / 'data' / 'budget_estimate.json'
    path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k: output[k] for k in ['model', 'estimated_total_usd_without_cache_discount', 'zero_llm_cost_scenarios', 'unreported_usage_scenarios']}))


if __name__ == '__main__':
    main()
