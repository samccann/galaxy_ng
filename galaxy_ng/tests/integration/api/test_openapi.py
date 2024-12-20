"""test_openapi.py - Tests related to open api generation and schema.

See: https://issues.redhat.com/browse/AAH-1169

"""

import json
import os
import pytest
import subprocess
import tempfile

from openapi_spec_validator import validate_spec

from ..utils import is_docker_installed
from ..utils.iqe_utils import is_dev_env_standalone

pytestmark = pytest.mark.qa  # noqa: F821


PULPY_VARIABLES = [
    '{ansible_collection_href}',
    '{ansible_collection_version_href}',
    '{ansible_collection_import_href}',
    '{role_href}',
    '{task_group_href}',
    '{upload_href}',
    '{worker_href}'
]


@pytest.mark.openapi
@pytest.mark.all
@pytest.mark.skipif(not is_dev_env_standalone(), reason="AAP-20597")
def test_galaxy_openapi_no_pulp_variables(galaxy_client):
    """Tests whether openapi.json has valid path names"""

    gc = galaxy_client("basic_user")
    galaxy_spec = gc.get('v3/openapi.json')
    assert 'paths' in galaxy_spec

    paths_keys = list(galaxy_spec['paths'].keys())
    for path in paths_keys:
        assert not path.startswith('{')


@pytest.mark.openapi
@pytest.mark.skip(
    reason="uncomment after https://github.com/pulp/pulpcore/pull/3564 is merged"
           " and pulpcore version is upgraded"
)
@pytest.mark.all
def test_galaxy_openapi_validation(galaxy_client):
    """Tests whether openapi.json passes openapi linter"""

    gc = galaxy_client("basic_user")
    galaxy_spec = gc.get('v3/openapi.json')
    validate_spec(galaxy_spec)


@pytest.mark.openapi
@pytest.mark.min_hub_version("4.6dev")
@pytest.mark.all
@pytest.mark.skipif(not is_dev_env_standalone(), reason="AAP-20597")
def test_pulp_openapi_has_variables(galaxy_client):
    """Tests whether openapi.json has valid path names for pulp"""
    gc = galaxy_client("basic_user")
    pulp_spec = gc.get('pulp/api/v3/docs/api.json')
    assert 'paths' in pulp_spec

    paths_keys = list(pulp_spec['paths'].keys())
    for ev in PULPY_VARIABLES:
        assert ev in paths_keys


@pytest.mark.deployment_standalone
@pytest.mark.openapi
@pytest.mark.openapi_generate_bindings
@pytest.mark.skipif(not is_docker_installed(), reason="docker is not installed on this machine")
@pytest.mark.all
def test_openapi_bindings_generation(ansible_config, galaxy_client):
    """Verify client bindings can be built from the pulp'ish api spec"""

    config = ansible_config("basic_user")
    gc = galaxy_client("basic_user")

    if config["container_engine"] != "docker":
        pytest.skip("Container engine is not Docker")

    pulp_spec = gc.get('pulp/api/v3/docs/api.json')
    status = gc.get('pulp/api/v3/status/')
    version = next(x['version'] for x in status['versions'] if x['component'] == 'galaxy')
    my_id = subprocess.run('id -u', shell=True, capture_output=True).stdout.decode('utf-8').strip()
    volume_name = '/local'
    generator_repo = 'https://github.com/pulp/pulp-openapi-generator'

    with tempfile.TemporaryDirectory(prefix='galaxy-bindings-') as output_dir:

        generator_checkout = os.path.join(output_dir, 'pulp-openapi-generator')
        clone_pid = subprocess.run(
            f'git clone {generator_repo} {generator_checkout}',
            shell=True,
            capture_output=True,
        )
        assert clone_pid.returncode == 0, clone_pid.stderr.decode('utf-8')

        with open(os.path.join(generator_checkout, 'api.json'), 'w') as f:
            f.write(json.dumps(pulp_spec))

        properties = '--additional-properties=packageName=pulpcore.client.galaxy_ng'
        properties += ',projectName=galaxy_ng-client'
        properties += f',packageVersion={version}'

        cmd = [
            'docker',
            'run',
            '--ulimit', 'nofile=122880:122880',
            '-u',
            my_id,
            '--rm',
            '-v',
            f'{generator_checkout}:{volume_name}',
            'openapitools/openapi-generator-cli:v4.3.1',
            'generate',
            '-i', '/local/api.json',
            '-g', 'python',
            '-o', '/local/galaxy_ng-client',
            properties,
            '-t', '/local/templates/python',
            '--skip-validate-spec',
            '--strict-spec=false'
        ]

        docker_pid = subprocess.run(' '.join(cmd), shell=True, capture_output=True)
        try:
            assert docker_pid.returncode == 0, docker_pid.stderr.decode('utf-8')
        except AssertionError as e:
            if "toomanyrequests" in str(e):
                pytest.skip("Docker error: toomanyrequests: You have reached your pull rate "
                            "limit.")
            else:
                raise e
        assert os.path.exists(os.path.join(generator_checkout, 'galaxy_ng-client'))
