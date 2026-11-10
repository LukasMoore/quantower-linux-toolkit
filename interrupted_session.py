"""Guard interrupted integrated-session history for an explicit desktop Open.

History never proves process inactivity. Adapters supply their production
``inactive_lock`` context manager, a safe ``read_json(path)`` callback and a
no-argument ``verify_deployment()`` callback. The lock must acquire the shared
launch lock and prove canonical-prefix, recorded-process-birth and all dedicated
runtime-user inactivity; unknown ownership must refuse. ``read_json`` must reject
missing/redirected, foreign-owned, nonprivate, nonregular, oversized or malformed
files. ``verify_deployment`` must verify the complete selected runtime and adapter
configuration and return a nonempty JSON-serializable identity dictionary.

The adapter holds its own duplicate-action lock across recognition, explicit Open
and one normal front-door request. Only ``validate_start`` returning True may
reach that request, which repeats production guards. Recovery-only actions never
validate or start a session. This module never launches, signals, cleans up, writes
history or a receipt, asks a question, retries, or watches failures in background.
"""
import datetime
import hashlib
import json
import os
from pathlib import Path
import re


STATE = 'inactive-interrupted'
REFUSED = 'Interrupted Quantower session could not be safely re-verified; no launch requested.'
SESSION = re.compile(r'[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}')


def aware(value):
    if not isinstance(value, str):
        return None
    try:
        result = datetime.datetime.fromisoformat(value)
        return result if result.utcoffset() is not None else None
    except ValueError:
        return None


def pid(value):
    return type(value) is int and 0 < value <= 2147483647


def eligible(summary):
    """Recognize well-formed history only; this grants no launch permission."""
    if not isinstance(summary, dict):
        return False
    start, updated = aware(summary.get('started_at')), aware(summary.get('updated_at'))
    starters = summary.get('starter_pids')
    if (type(summary.get('schema_version')) is not int or summary['schema_version'] != 1 or
            summary.get('window_mode') != 'integrated' or
            not isinstance(summary.get('session_id'), str) or SESSION.fullmatch(summary['session_id']) is None or
            start is None or updated is None or updated < start or
            not pid(summary.get('supervisor_pid')) or not pid(summary.get('child_pid')) or
            not isinstance(starters, list) or any(not pid(value) for value in starters) or
            len(starters) != len(set(starters)) or
            type(summary.get('child_exited')) is not bool or type(summary.get('handoff_pending')) is not bool):
        return False
    for key in ('child_exit_code', 'child_final_exit_code'):
        if key in summary and type(summary[key]) is not int:
            return False
    for key in ('child_exit_unexpected', 'cleanup_sent_child_sigterm'):
        if key in summary and type(summary[key]) is not bool:
            return False
    if ('child_signal' in summary and summary['child_signal'] is not None and
            (type(summary['child_signal']) is not int or not 0 < summary['child_signal'] < 128)):
        return False
    if ('child_exit_code' in summary and 'child_final_exit_code' in summary and
            summary['child_exit_code'] != summary['child_final_exit_code']):
        return False
    if summary.get('status') == 'failed':
        end = aware(summary.get('ended_at'))
        return (summary.get('phase') == 'failed' and type(summary.get('exit_code')) is int and
                summary['exit_code'] != 0 and summary.get('cleanup_complete') is False and
                end is not None and start <= end <= updated)
    if summary.get('status') == 'running':
        return (summary.get('phase') in ('startup', 'starting', 'running', 'stopping') and
                'exit_code' not in summary and 'ended_at' not in summary and
                ('cleanup_complete' not in summary or summary['cleanup_complete'] is False))
    return False


def safe_bytes(read_json, path):
    value = read_json(path)
    content = path.read_bytes()
    if (json.loads(content) != value or read_json(path) != value or
            path.read_bytes() != content):
        raise RuntimeError(REFUSED)
    return value, content


def digest(content):
    return hashlib.sha256(content).hexdigest()


def snapshot(app, read_json):
    """Bind latest/history bytes and the matching private ownership ledger."""
    latest_path = app / 'logs/latest-session.json'
    summary, latest_bytes = safe_bytes(read_json, latest_path)
    if not eligible(summary):
        raise RuntimeError(REFUSED)
    historical_path = app / 'logs/sessions' / summary['session_id'] / 'summary.json'
    historical, historical_bytes = safe_bytes(read_json, historical_path)
    # Both files are emitted from the same diagnostics record. A byte difference
    # is ambiguous, even when the parsed JSON happens to compare equal.
    if historical != summary or historical_bytes != latest_bytes:
        raise RuntimeError(REFUSED)
    ledger, ledger_bytes = safe_bytes(read_json, app / 'logs/session-processes.json')
    if (not isinstance(ledger, dict) or type(ledger.get('version')) is not int or ledger['version'] != 1 or
            type(ledger.get('uid')) is not int or ledger['uid'] != os.getuid() or
            ledger.get('prefix') != str(app / 'prefix') or ledger.get('window_mode') != 'integrated' or
            not isinstance(ledger.get('token'), str) or re.fullmatch(r'[0-9a-f]{32}', ledger['token']) is None or
            not isinstance(ledger.get('processes'), list)):
        raise RuntimeError(REFUSED)
    recorded = set()
    for row in ledger['processes']:
        if (not isinstance(row, dict) or not pid(row.get('pid')) or row['pid'] in recorded or
                type(row.get('uid')) is not int or row['uid'] != os.getuid() or
                type(row.get('start_ticks')) is not int or row['start_ticks'] < 0):
            raise RuntimeError(REFUSED)
        recorded.add(row['pid'])
    if not {summary['child_pid'], *summary['starter_pids']}.issubset(recorded):
        raise RuntimeError(REFUSED)
    if safe_bytes(read_json, latest_path)[1] != latest_bytes:
        raise RuntimeError(REFUSED)
    return {'session_id': summary['session_id'], 'summary_sha256': digest(latest_bytes),
            'historical_summary_sha256': digest(historical_bytes), 'ownership_ledger_sha256': digest(ledger_bytes)}


def _checked(*, app, read_json, verify_deployment):
    """Caller holds the full lock; fingerprints never substitute for that lock."""
    app = Path(app)
    if not app.is_absolute() or app.resolve(strict=True) != app or os.getuid() == 0:
        raise RuntimeError(REFUSED)
    current = snapshot(app, read_json)
    deployment = verify_deployment()
    if not isinstance(deployment, dict) or not deployment:
        raise RuntimeError(REFUSED)
    fingerprint = digest(json.dumps(deployment, sort_keys=True, separators=(',', ':'), allow_nan=False).encode())
    if snapshot(app, read_json) != current:
        raise RuntimeError(REFUSED)
    return {'snapshot': current, 'deployment_sha256': fingerprint}


def recognize(*, app, inactive_lock, read_json, verify_deployment):
    with inactive_lock():
        _checked(app=app, read_json=read_json, verify_deployment=verify_deployment)
    return STATE


def validate_start(*, app, inactive_lock, read_json, verify_deployment):
    """Recheck one explicit Open twice; the adapter still uses its normal launcher."""
    with inactive_lock():
        before = _checked(app=app, read_json=read_json, verify_deployment=verify_deployment)
    with inactive_lock():
        if _checked(app=app, read_json=read_json, verify_deployment=verify_deployment) != before:
            raise RuntimeError('Previous session, ownership ledger or deployment changed; no launch requested.')
    return True
