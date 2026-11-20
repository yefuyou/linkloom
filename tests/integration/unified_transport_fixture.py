"""Synthetic product fixture with the official SDK and HTTPX FakeTransport."""
import json
from pathlib import Path
from threading import Lock
from time import monotonic, sleep
from uuid import uuid4
import httpx
from google.genai import types

from linkloom.agents.providers.gemini_api import build_gemini_sdk_client
from linkloom.ui.investigation import ProductInvestigationService
from scripts.gemini_transport.diagnostics import TransportDiagnostics
from scripts.gemini_transport.product import TransportGeminiProviderAdapter, environment_snapshot
from tests.smoke.provider_ledger import AppendOnlyProviderLedger, LedgeredGeminiClient, SmokeBlocked
from tests.integration.unified_ask_fixture import UnifiedAskFixture, FakeTransport as ScriptedModel

MODEL = 'gemini-3.8-flash'


def provider_shape(value):
    if isinstance(value,dict):
        return { {'functionResponse':'function_response','functionCall':'function_call'}.get(k,k):provider_shape(v) for k,v in value.items() }
    if isinstance(value,list):
        return [provider_shape(v) for v in value]
    return value


class SDKFakeTransport(httpx.BaseTransport):
    """Stage events are simulated; this does not open a Provider socket."""
    def __init__(self, scenario='normal', *, delay=0):
        self.model = ScriptedModel(scenario=scenario if scenario in ('forged_receipt','wrong_evidence') else 'normal')
        self.scenario, self.delay = scenario, delay
        self.bodies, self.responses = [], []

    def handle_request(self, request):
        self.bodies.append(request.content)
        ordinal = len(self.bodies)
        trace = request.extensions.get('trace',lambda *_:None)
        for stage in ('send_request_headers','send_request_body'):
            trace(f'http11.{stage}.started',{})
            trace(f'http11.{stage}.complete',{})
        trace('http11.receive_response_headers.started',{})
        sleep(self.delay)
        if ordinal==4 and self.scenario=='headers':
            error = httpx.RemoteProtocolError('PRIVATE_HEADER_ERROR')
            trace('http11.receive_response_headers.failed',{'exception':error})
            raise error
        trace('http11.receive_response_headers.complete',{})
        body = provider_shape(json.loads(request.content))
        response = self.model.generate_content(model=MODEL,contents=body['contents'],config={})
        parts = ([{'functionCall':call,'thoughtSignature':'c2ln'} for call in response['function_calls']]
                 if 'function_calls' in response else [{'text':response['text']}])
        encoded = json.dumps({'modelVersion':MODEL,'responseId':f'offline-{ordinal}',
            'candidates':[{'content':{'role':'model','parts':parts},'finishReason':'STOP'}],
            'usageMetadata':{'promptTokenCount':100+ordinal,'candidatesTokenCount':20,'thoughtsTokenCount':0}}).encode()
        self.responses.append(encoded)
        partial = ordinal==4 and self.scenario=='body'
        class Body(httpx.SyncByteStream):
            def __iter__(self):
                trace('http11.receive_response_body.started',{})
                if partial:
                    yield encoded[:10]
                    error = httpx.RemoteProtocolError('PRIVATE_BODY_ERROR')
                    trace('http11.receive_response_body.failed',{'exception':error})
                    raise error
                yield encoded
                trace('http11.receive_response_body.complete',{})
            def close(self):
                trace('http11.response_closed.started',{})
                trace('http11.response_closed.complete',{})
        return httpx.Response(200,headers={'x-request-id':f'offline-provider-{ordinal}'},stream=Body())


class UnifiedTransportHarness:
    """Lazy SDK construction; one shared factory gate owns the Runtime budget."""
    def __init__(self, root, *, diagnostics=False, max_runtime_runs=1, live_key=None,
                 scenario='normal', broken_sink=False, port=0, delay=0, new_send_budget_seconds=900):
        self.root = Path(root)
        self.fixture = UnifiedAskFixture(self.root/'product.sqlite',port=port)
        self.enabled, self.maximum, self.live_key = diagnostics, max_runtime_runs, live_key
        self.scenario, self.broken_sink, self.delay = scenario, broken_sink, delay
        self.new_send_budget_seconds = new_send_budget_seconds
        self.runs = []
        self.runtime_reservations = 0
        self.lock = Lock()
        self.sdk_constructions = 0
        self.service = ProductInvestigationService(self.fixture.app,model_factory=self.model_factory,
            run_mode='GEMINI_38_FLASH_LEDGERED_UNIFIED' if live_key else 'OFFLINE_SDK_FAKE_TRANSPORT',
            max_steps=6,external_provider=bool(live_key))
        self.fixture.service = self.service
        self.fixture.server.investigation_service = self.service

    def model_factory(self, query):
        with self.lock:
            if self.runtime_reservations>=self.maximum:
                raise SmokeBlocked('RUNTIME_BUDGET_EXHAUSTED')
            self.runtime_reservations += 1  # Startup failure also consumes the one-run permission.
            run_id = 'UAT-' + uuid4().hex
            directory = self.root/run_id
            directory.mkdir(exist_ok=False)
            selected = self.scenario
            if self.live_key is None:
                if 'headers failure' in query.casefold(): selected='headers'
                elif 'body failure' in query.casefold(): selected='body'
            transport = None if self.live_key else SDKFakeTransport(selected,delay=self.delay)
            events, turns = [], []
            event_path = directory/'transport_events.jsonl'
            def sink(event):
                if self.broken_sink:
                    raise OSError('PRIVATE_SINK_FAILURE')
                with event_path.open('a',encoding='utf-8') as stream:
                    stream.write(json.dumps(event)+'\n')
                events.append(event)
            diagnostics = TransportDiagnostics(sink,enabled=self.enabled)
            if self.enabled:
                # Startup evidence failure blocks BEFORE SDK construction / Provider reservation.
                event_path.touch(exist_ok=False)
                with (directory/'transport_environment.json').open('x',encoding='utf-8') as stream:
                    stream.write(json.dumps(environment_snapshot()))
            options = {'timeout':130000,'retry_options':types.HttpRetryOptions(attempts=1)}
            if transport is not None:
                options['client_args'] = {'transport':transport}
            sdk = build_gemini_sdk_client(self.live_key or 'PRIVATE_OFFLINE_KEY',http_options_kwargs=options,
                **({'transport_diagnostics':diagnostics} if self.enabled else {}))
            self.sdk_constructions += 1
            ledger = AppendOnlyProviderLedger(directory/'provider_ledger.jsonl',batch_id='UAT-INDEPENDENT',
                run_id=run_id,max_requests=6,max_runtime_runs=1,max_input_tokens_per_request=None)
            deadline = monotonic()+self.new_send_budget_seconds
            class DeadlineClient(LedgeredGeminiClient):
                def generate_content(self, **kwargs):
                    if monotonic()>deadline:
                        raise SmokeBlocked('EXPERIMENT_DEADLINE')
                    return super().generate_content(**kwargs)
            client = DeadlineClient(sdk,ledger,transport_diagnostics=diagnostics if self.enabled else None)
            class CaptureAdapter(TransportGeminiProviderAdapter):
                def complete(self, request):
                    if transport is not None:
                        turns.append(request)  # Offline byte-equality evidence only.
                    return super().complete(request)
            ledger.runtime_started()
            self.runs.append({'run_id':run_id,'directory':directory,'sdk':sdk,'ledger':ledger,
                              'diagnostics':diagnostics,'events':events,'turns':turns,'transport':transport,
                              'client':client,'send_deadline':deadline})
            return CaptureAdapter(client,model_id=MODEL)

    def close(self):
        self.fixture.stop()
        for run in self.runs:
            run['ledger'].close()
            run['sdk'].close()
