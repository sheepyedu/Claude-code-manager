"""Codex canonicalizes Darwin /tmp to /private/tmp in read-only thread proof."""
import pytest
from backend.services.codex_app_server import _audit_task_ssh_thread_response


@pytest.mark.parametrize('variant', ['alias', 'sibling', 'extra_root', 'network', 'profile'])
def test_read_only_thread_proof_accepts_only_the_same_scratch_leaf(tmp_path, variant):
    scratch = tmp_path / 'real-scratch'
    scratch.mkdir()
    alias = tmp_path / 'tmp-alias'
    alias.symlink_to(scratch, target_is_directory=True)
    sibling = tmp_path / 'other-scratch'
    sibling.mkdir()
    response = {
        'activePermissionProfile': {'id': 'exact-profile', 'extends': None},
        'sandbox': {'type': 'workspaceWrite', 'networkAccess': False,
                    'writableRoots': [str(scratch)]},
        'instructionSources': [],
    }
    if variant == 'sibling': response['sandbox']['writableRoots'] = [str(sibling)]
    if variant == 'extra_root': response['sandbox']['writableRoots'].append(str(tmp_path))
    if variant == 'network': response['sandbox']['networkAccess'] = True
    if variant == 'profile': response['activePermissionProfile']['id'] = 'other-profile'
    def audit():
        _audit_task_ssh_thread_response(response, permission_profile_id='exact-profile',
            private_tmpdir=str(alias), disable_network=True, managed_network_proxy=False,
            direct_ssh=False, sandbox_mode='read-only', cwd=str(tmp_path))
    if variant == 'alias': audit()
    else:
        with pytest.raises(ValueError): audit()
