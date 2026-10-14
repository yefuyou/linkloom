"""Independent append-only ledger. UNCERTAIN is a terminal state."""
from datetime import datetime, timezone
import json
import os
import re
import uuid


class ExperimentStopped(RuntimeError):
    pass


class DiagnosticLedger:
    def __init__(self, path, *, experiment_id, max_requests=4):
        if not re.fullmatch(r'[A-Za-z0-9_.-]{1,100}', experiment_id) or not 1 <= max_requests <= 4:
            raise ValueError('invalid experiment boundary')
        self.path, self.experiment_id, self.max_requests = path, 'gnt-' + uuid.uuid4().hex, max_requests
        self.sequence, self.stopped = 0, False
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open('x', encoding='utf-8') as stream:
            stream.write(json.dumps({'event': 'experiment_created', 'experiment_id': self.experiment_id, 'max_requests': max_requests}) + '\n')
            stream.flush()
            os.fsync(stream.fileno())

    def append(self, record):
        with self.path.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps({'utc': datetime.now(timezone.utc).isoformat(), **record}) + '\n')
            stream.flush()
            os.fsync(stream.fileno())

    def call(self, operation, *, diagnostics=None):
        if self.stopped or self.sequence >= self.max_requests:
            raise ExperimentStopped('experiment stopped or exhausted')
        self.sequence += 1
        request_id = f'{self.experiment_id}-{self.sequence:02d}'
        self.stopped = True  # Ledger failure must not permit another send.
        reservation = {'event': 'request_reserved', 'request_id': request_id, 'generation_sequence': self.sequence}
        if diagnostics is not None:
            reservation['diagnostic_request_id'] = diagnostics.safe_identity(request_id)
        per_request = self.path.parent / f'{request_id}.ledger.jsonl'
        with per_request.open('x', encoding='utf-8') as stream:
            stream.write(json.dumps(reservation) + '\n')
            stream.flush()
            os.fsync(stream.fileno())
        self.append(reservation)
        try:
            result = operation(request_id)
        except BaseException:
            self.finish(per_request, request_id, 'UNCERTAIN')
            raise
        self.finish(per_request, request_id, 'SUCCESS')
        self.stopped = False
        return result

    def finish(self, per_request, request_id, status):
        row = {'event': 'request_finished', 'request_id': request_id, 'status': status}
        with per_request.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(row) + '\n')
            stream.flush()
            os.fsync(stream.fileno())
        self.append(row)
