"""Explicit new-session choice for desktop adapters after a finalized failure.

Session history never proves process inactivity. The adapter supplies its existing
inactivity lock, safe summary reader and complete deployment verifier. This module
does not launch the application, clean up processes, rewrite history or retry.
"""
import datetime
import re
import subprocess


def stopped_session_state(latest):
    """Classify history only after the caller establishes guarded inactivity."""
    refused = 'Previous session is unfinished or cannot be verified; no automatic restart.'
    if (not isinstance(latest, dict) or type(latest.get('schema_version')) is not int or
            latest['schema_version'] != 1 or type(latest.get('exit_code')) is not int or
            latest.get('cleanup_complete') is not True):
        raise RuntimeError(refused)
    if latest.get('status') == 'finished' and latest['exit_code'] == 0:
        return 'inactive'
    if (latest.get('status') == 'failed' and latest['exit_code'] != 0 and
            latest.get('phase') == 'failed' and latest.get('child_exited') is True and
            latest.get('starter_pids') == [] and latest.get('handoff_pending') is False and
            isinstance(latest.get('session_id'), str) and
            re.fullmatch(r'[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}', latest['session_id']) and
            isinstance(latest.get('ended_at'), str)):
        try:
            ended = datetime.datetime.fromisoformat(latest['ended_at'])
            if ended.utcoffset() is not None:
                return 'inactive-failed'
        except ValueError:
            pass
    raise RuntimeError(refused)


def ask_start_new_session():
    """Show one Cancel-default question; return its exit code, never launch."""
    return subprocess.run(
        ['/usr/bin/zenity', '--question', '--title=Quantower Integrated', '--no-markup', '--width=520',
         '--default-cancel', '--ok-label=Start a new session', '--cancel-label=Cancel',
         '--text=The previous Quantower session ended with an error, and cleanup completed. Quantower is fully stopped.\n\nStart a new session?'],
        stdin=subprocess.DEVNULL, check=False).returncode


def confirm_failed_session_start(*, inactive_lock, read_summary, verify_deployment, ask):
    """Validate, ask outside the launch lock, then recheck the exact snapshot.

All callbacks take no arguments. ``inactive_lock`` returns a context manager that
locks the normal front door and proves process inactivity on each entry.
``read_summary`` safely reads a fresh private JSON object. ``verify_deployment``
checks the complete selected runtime/configuration under that lock. ``ask`` returns
an integer question exit code: 0 accepts, 1 cancels; other results refuse.

The adapter must retain its own duplicate-action lock across this call and its
one-shot start. A True result is not a substitute for the normal launcher's fresh
inactivity, ownership, deployment and shared-lock checks. Never retry a refusal.
"""
    with inactive_lock():
        previous = read_summary()
        if stopped_session_state(previous) != 'inactive-failed':
            raise RuntimeError('The previous session changed; open Quantower again to review its current state.')
        verify_deployment()
    result = ask()
    if type(result) is not int:
        raise RuntimeError('New-session confirmation could not be completed; no launch requested.')
    if result == 1:
        return False
    if result != 0:
        raise RuntimeError('New-session confirmation could not be completed; no launch requested.')
    with inactive_lock():
        if read_summary() != previous:
            raise RuntimeError('The previous session changed during confirmation; no launch requested.')
    return True
