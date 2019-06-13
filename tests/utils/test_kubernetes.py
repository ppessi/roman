import unittest
from apluslms_roman.utils.kubernetes import create_pod
from kubernetes import client, config

test_cases = [
    {
        'image': 'foo/bar:latest',
        'command': 'ls',
        'environment': {'foo': 'bar'},
        'name': 'bar',
        'namespace': 'default',
        'mounts': [
                client.V1VolumeMount(
                    mount_path='/',
                    name='build-path'
                )
            ],
        'volumes':  [
                client.V1Volume(
                    name='build-path',
                    host_path=client.V1HostPathVolumeSource(path="/build-source")
                )
            ],
        'working_dir': '/'
    }
]


class TestPod(unittest.TestCase):
    def test_create_pod(self):
        for i, test_case in enumerate(test_cases):
            with self.subTest(i=i):
                config.load_kube_config()
                pod = create_pod(**test_case)
                self.assertTrue(pod.metadata.name.startswith(test_case['name']))
                self.assertEqual(pod.metadata.namespace, test_case['namespace'])
                self.assertEqual(pod.spec.containers[0].env, [client.V1EnvVar(k, v) for k, v in {'foo': 'bar'}.items()])
                self.assertEqual(pod.spec.containers[0].working_dir, test_case['working_dir'])
                self.assertEqual(pod.spec.containers[0].volume_mounts[0].name, test_case['volumes'][0].name)
