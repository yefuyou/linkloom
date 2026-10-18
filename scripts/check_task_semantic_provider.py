"""Read-only Gemini model metadata check. Never generates model content."""
from argparse import ArgumentParser
from datetime import UTC, datetime
import importlib.metadata
import json
import os
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'src'),str(ROOT)]
from linkloom.agents.providers.gemini_api import GeminiProviderAdapter, GEMINI_38_FLASH_MODEL_ID, build_gemini_sdk_client


def connect():
    from google.genai import types
    key=os.getenv('GEMINI_API_KEY') or os.getenv('GOOGLE_API_KEY')
    if not key:
        raise RuntimeError('GEMINI_CREDENTIAL_UNAVAILABLE')
    selected=os.getenv('LINKLOOM_SEMANTIC_MODEL') or GEMINI_38_FLASH_MODEL_ID
    if selected!=GEMINI_38_FLASH_MODEL_ID:
        raise RuntimeError('CONFIGURED_MODEL_MISMATCH_NO_FALLBACK')
    return build_gemini_sdk_client(key,http_options_kwargs=dict(
        base_url='https://generativelanguage.googleapis.com',timeout=30000,
        retry_options=types.HttpRetryOptions(attempts=1),client_args=dict(http2=False)))


def main():
    parser=ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    result=dict(schema='task-semantic-provider-preflight/v1',utc=datetime.now(UTC).isoformat(),
        requested_model=GEMINI_38_FLASH_MODEL_ID,sdk=importlib.metadata.version('google-genai'),
        method='models.get; read-only',generation_calls=0,status='NOT_RUN',model=None,
        docs=dict(model='https://ai.google.dev/gemini-api/docs/models/gemini-3.8-flash',
                  pricing='https://ai.google.dev/gemini-api/docs/whats-new-gemini-3.8'),
        documented_capabilities=dict(structured_outputs=True,function_calling=True,
            input_token_limit=1048576,output_token_limit=65536),
        usage_mapping='ModelUsage prompt/candidates/total; smoke accounting separately includes thoughts',
        prices_usd_per_million=dict(input=.75,output=3.75,valid_through='2026-12-31'))
    client=None
    try:
        client=connect()
        result['adapter_capability']=GeminiProviderAdapter(client.models).capability.to_dict()
        model=client.models.get(model=GEMINI_38_FLASH_MODEL_ID)
        fields=('name','version','input_token_limit','output_token_limit','supported_actions')
        result['model']={name:getattr(model,name,None) for name in fields}
        actual=(result['model']['name'] or '').removeprefix('models/')
        result['status']='PASS' if actual==GEMINI_38_FLASH_MODEL_ID else 'MODEL_ID_MISMATCH'
    except Exception as error:
        result.update(status='READ_ONLY_CHECK_FAILED',error_type=type(error).__name__)
    finally:
        if client:
            client.close()
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open('x',encoding='utf8') as stream:
        json.dump(result,stream,ensure_ascii=False,indent=2)
        stream.write('\n')
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':
    main()
