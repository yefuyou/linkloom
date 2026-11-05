"""One-attempt evaluation transport and offline official V4.1 prompt counter."""
from copy import deepcopy
from datetime import UTC,datetime
from pathlib import Path
import ast,hashlib,importlib.util,json,os
from linkloom.agents.providers.deepseek_api import DeepSeekProviderAdapter,build_deepseek_request


class OfficialTokenCounter:
    def __init__(self,directory):
        from tokenizers import Tokenizer
        root=Path(directory);manifest=json.loads((root/'source.json').read_text())
        if manifest['repository']!='https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash-1017':raise ValueError('OFFICIAL_TOKENIZER_REQUIRED')
        for name,expected in manifest['files'].items():
            if name not in {'tokenizer.json','encoding.py'} or hashlib.sha256((root/name).read_bytes()).hexdigest()!=expected:raise ValueError('TOKENIZER_CHANGED')
        # Reviewed official module has only pure constants/functions at import.
        # Its load_cases helper opens a file only when explicitly called; never used.
        tree=ast.parse((root/'encoding.py').read_bytes())
        for node in tree.body:
            if isinstance(node,ast.Import) and any(n.name not in {'copy','json','re'} for n in node.names):raise ValueError('UNEXPECTED_ENCODER_IMPORT')
            if isinstance(node,ast.ImportFrom) and node.module!='typing':raise ValueError('UNEXPECTED_ENCODER_IMPORT')
            if isinstance(node,(ast.Expr,ast.Assign,ast.AnnAssign)):
                if any(isinstance(x,ast.Call) and (isinstance(x.func,ast.Name) and x.func.id in {'open','eval','exec','__import__'}) for x in ast.walk(node)):raise ValueError('ENCODER_IMPORT_SIDE_EFFECT')
        spec=importlib.util.spec_from_file_location('l6_official_encoder',root/'encoding.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        self.encode=module.encode_messages;self.tokenizer=Tokenizer.from_file(str(root/'tokenizer.json'))
    def __call__(self,request):
        payload=build_deepseek_request(request);messages=deepcopy(payload['messages'])
        if payload.get('tools'):messages[0]['tools']=payload['tools']
        if payload.get('response_format'):messages[0]['response_format']=payload['response_format']
        prompt=self.encode(messages,thinking_mode='chat')
        # Official framing plus a fixed safety margin for server-side wrappers.
        return len(self.tokenizer.encode(prompt,add_special_tokens=False).ids)+256


def assert_off_peak(config,now=None):
    now=now or datetime.now(UTC)
    if now>=datetime.fromisoformat(config['pricing_valid_until_utc']):raise ValueError('PRICE_WINDOW_ENDED')
    hour=now.hour+now.minute/60
    if now.weekday()<5 and (1<=hour<4 or 6<=hour<10):raise ValueError('PEAK_PRICE_WINDOW')


def adapter_factory(config):
    from openai import OpenAI
    client=OpenAI(api_key=os.environ['DEEPSEEK_API_KEY'],base_url='https://api.deepseek.com',max_retries=0,timeout=20)
    class OneAttempt:
        def create_chat_completion(self,**payload):
            assert_off_peak(config)
            thinking=payload.pop('thinking')
            return client.chat.completions.create(**payload,extra_body={'thinking':thinking})
    return DeepSeekProviderAdapter(OneAttempt(),model_id=config['model_id'])
