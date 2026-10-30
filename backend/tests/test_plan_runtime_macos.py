"""Darwin Plan admission and cleanup must retain exact runtime ownership."""
import os
import subprocess
import sys
import time
import uuid
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import psutil
import pytest

from backend.models.plan_agent import PlanAgentStep
from backend.services import plan_runtime_receipt as receipts
from backend.services import process_identity


@pytest.fixture
def darwin(monkeypatch):
    monkeypatch.setattr(receipts, 'sys', SimpleNamespace(platform='darwin'), raising=False)
    monkeypatch.setattr(receipts, 'psutil', psutil, raising=False)


def snapshot():
    return receipts.RuntimeReceiptSnapshot(
        id=1, run_id=1, step_id=1, run_generation=1, attempt_index=1,
        provider='claude', runtime_token=uuid.uuid4().hex,
        prepared_boot_id=str(uuid.uuid4()), prepared_start_ticks=100,
        prepared_uid=os.getuid(), status='admitting', process_id=None,
        process_group_id=None, process_start_ticks=None, process_uid=None,
        boot_id=None, codex_home=None, codex_thread_id=None,
    )


def identity(s, pid=400, **changes):
    return replace(receipts.ProcessIdentity(
        pid=pid, process_group_id=pid, start_ticks=101,
        uid=s.prepared_uid, boot_id=s.prepared_boot_id, state='R',
    ), **changes)


def test_darwin_prepared_receipt_needs_no_procfs(darwin, monkeypatch):
    boot=str(uuid.uuid4())
    monkeypatch.setattr(process_identity, 'darwin_boot_session_uuid', lambda: boot)
    monkeypatch.setattr(time, 'time_ns', lambda: 2_000_000_000, raising=False)
    monkeypatch.setattr(receipts.Path, 'read_text', Mock(side_effect=AssertionError('procfs read')))
    step=PlanAgentStep(id=1,run_id=2,generation=3,provider='claude',step_type='planner',round=1,status='running')
    r=receipts.new_prepared_runtime_receipt(step,attempt_index=1)
    assert r.prepared_boot_id==boot
    assert 0 < r.prepared_start_ticks <= 2_000_000
    assert r.prepared_uid==os.getuid()


def test_darwin_process_identity_uses_native_shared_reader(darwin, monkeypatch):
    s=snapshot(); native=process_identity.ProcessIdentity(
        pid=400,start_ticks=101,boot_id=s.prepared_boot_id,
        uid=s.prepared_uid,process_group_id=400,state='R',
    )
    monkeypatch.setattr(process_identity,'read_process_identity',lambda pid:native)
    assert receipts.read_process_identity(400)==identity(s)
    monkeypatch.setattr(process_identity,'read_process_identity',lambda pid:None)
    assert receipts.read_process_identity(400) is None
    def unreadable(pid):raise process_identity.ProcessIdentityError('permission denied')
    monkeypatch.setattr(process_identity,'read_process_identity',unreadable)
    with pytest.raises(receipts.PlanRuntimeReceiptError): receipts.read_process_identity(400)


@pytest.mark.parametrize('kind',['match','mismatch','old','other_user','denied','reused','vanished'])
def test_darwin_token_scan_proves_identity_and_fails_closed(darwin,monkeypatch,kind):
    s=snapshot(); before=identity(s)
    if kind=='old':before=replace(before,start_ticks=99)
    if kind=='other_user':before=replace(before,uid=s.prepared_uid+1)
    after=replace(before,start_ticks=102) if kind=='reused' else before
    calls=iter([before,after])
    monkeypatch.setattr(receipts,'_read_boot_id',lambda:s.prepared_boot_id)
    monkeypatch.setattr(receipts,'read_process_identity',lambda pid:next(calls))
    monkeypatch.setattr(process_identity,'darwin_user_process_ids',lambda uid:[400])
    env=Mock(return_value={'CCM_PLAN_RUNTIME_TOKEN':s.runtime_token if kind!='mismatch' else 'wrong'})
    if kind=='denied':env.side_effect=psutil.AccessDenied(400)
    if kind=='vanished':env.side_effect=psutil.NoSuchProcess(400)
    monkeypatch.setattr(receipts.psutil,'Process',lambda pid:SimpleNamespace(environ=env))
    if kind in {'denied','reused'}:
        with pytest.raises(receipts.PlanRuntimeReceiptError):receipts._token_process_identities(s)
    else:
        assert receipts._token_process_identities(s)==([after] if kind=='match' else [])
    if kind in {'old','other_user'}:env.assert_not_called()


@pytest.mark.skipif(sys.platform!='darwin',reason='native Darwin integration')
def test_real_darwin_child_token_and_process_group_cleanup():
    s=replace(snapshot(),prepared_boot_id=receipts._read_boot_id(),prepared_start_ticks=receipts._read_current_start_ticks())
    child=subprocess.Popen([sys.executable,'-I','-c','import time;time.sleep(60)'],env={**os.environ,**receipts.runtime_token_environment(s)},start_new_session=True)
    try:
        found=receipts._token_process_identities(s)
        own=[p for p in found if p.pid==child.pid]
        assert len(own)==1 and own[0].process_group_id==child.pid
        assert own[0].start_ticks>=s.prepared_start_ticks
        # No model request: exercise exact native identity and token cleanup.
        import asyncio
        bound=replace(s,process_id=child.pid,process_group_id=child.pid,
            process_start_ticks=own[0].start_ticks,process_uid=own[0].uid,boot_id=own[0].boot_id)
        try:
            asyncio.run(receipts._terminate_token_groups(bound))
        except receipts.PlanRuntimeReceiptError as exc:
            # A Popen child stays a zombie until this parent reaps it; a live
            # group must retain the receipt until that final proof succeeds.
            assert str(exc)=="Plan runtime process group remains live without token-bearing evidence"
        child.wait(timeout=5)
        asyncio.run(receipts._terminate_token_groups(bound))
    finally:
        if child.poll() is None:child.kill();child.wait(timeout=5)


def test_darwin_enumeration_rejects_truncated_or_failed_kernel_result(monkeypatch):
    # A saturated PID buffer is not evidence that no token-bearing child exists.
    def saturated(kind, uid, buffer, size):
        assert kind == 4 and uid == os.getuid()
        return 4 if buffer is None else size
    monkeypatch.setattr(process_identity, '_darwin_libproc', lambda: SimpleNamespace(proc_listpids=Mock(side_effect=saturated)))
    with pytest.raises(process_identity.ProcessIdentityError, match='changed during enumeration'):
        process_identity.darwin_user_process_ids(os.getuid())
    monkeypatch.setattr(process_identity, '_darwin_libproc', lambda: SimpleNamespace(proc_listpids=Mock(return_value=0)))
    with pytest.raises(process_identity.ProcessIdentityError, match='Could not size'):
        process_identity.darwin_user_process_ids(os.getuid())


def test_linux_receipt_clock_and_boot_format_still_use_procfs(monkeypatch):
    monkeypatch.setattr(receipts, 'sys', SimpleNamespace(platform='linux'))
    boot = str(uuid.uuid4())
    def read(path, **kwargs):
        return boot if str(path).endswith('/boot_id') else '123.45 100.0'
    monkeypatch.setattr(receipts.Path, 'read_text', read)
    monkeypatch.setattr(receipts.os, 'sysconf', lambda key: 100)
    assert receipts._read_boot_id() == boot
    assert receipts._read_current_start_ticks() == 12343
