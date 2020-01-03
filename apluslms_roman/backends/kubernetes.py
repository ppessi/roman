import logging

from os.path import join
from apluslms_yamlidator.utils.decorator import cached_property
from kubernetes import client, config, watch
from apluslms_roman.utils.kubernetes import create_pod
from apluslms_roman.backends import BuildTask
from apluslms_roman.observer import BuildObserver
from . import (
    Backend,
    BuildResult,
)

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)


class KubernetesBackend(Backend):
    """
    Run each step as a Kubernetes Deployment
    Mounting: using mounting in deployment, mapping is same as shepherd
    """
    name = 'kubernetes'

    @cached_property
    def _client(self):
        # Load kubernetes config from from $Home/.kube/config
        config.load_kube_config()
        api = client.CoreV1Api()
        return api

    def _run_opts(self, task, step):
        """
        Define the Pod model
        """
        env = self.environment
        opts = dict(
            image=step.img,
            command=step.cmd or '',
            environment=step.env,
            namespace=env.environ.get('namespace', 'default'),
            name=step.img.split(':')[0].replace('/', '-')
        )
        if step.mnt:
            opts['volumes'] = [
                client.V1Volume(
                    name='build-path',
                    host_path=client.V1HostPathVolumeSource(path="/build-source")
                )
            ]
            opts['mounts'] = [
                client.V1VolumeMount(
                    mount_path=step.mnt,
                    name='build-path'
                )
            ]
            opts['working_dir'] = step.mnt
        else:
            wpath = self.VOLUMES['source']

            opts['volumes'] = [
                client.V1Volume(
                    name='cache',
                    empty_dir=client.V1EmptyDirVolumeSource(size_limit=self.WORK_SIZE, medium='Memory')
                ),
                client.V1Volume(
                    name='source',
                    host_path=client.V1HostPathVolumeSource(path=join(wpath, 'src'))
                ),
                client.V1Volume(
                    name='build',
                    host_path=client.V1HostPathVolumeSource(path=join(wpath, 'build'))
                )
               ]
            opts['mounts'] = [
                client.V1VolumeMount(
                    mount_path=wpath,
                    name='cache',
                    read_only=False
                ),
                client.V1VolumeMount(
                    mount_path=join(wpath, 'src'),
                    name='source',
                    read_only=True
                ),
                client.V1VolumeMount(
                    mount_path=join(wpath, 'build'),
                    name='build',
                    read_only=False
                )
            ]
            opts['working_dir'] = wpath
        return opts

    def prepare(self, task: BuildTask, observer: BuildObserver):
        return BuildResult()

    def build(self, task: BuildTask, observer: BuildObserver):
        api_client = self._client
        for step in task.steps:
            observer.step_running(step)
            opts = self._run_opts(task, step)
            observer.manager_msg(step, "Running deployment with image {}:".format(opts['image']))
            name = opts['name']
            try:
                create_resp = create_pod(**opts)
                # print(create_resp)
                name = create_resp.metadata.name
                # Waiting pod finished
                while True:
                    resp = api_client.read_namespaced_pod(name=name, namespace=opts['namespace'])
                    if resp.status.phase != "Pending":
                        break
                for line in api_client.read_namespaced_pod_log(
                        name=name,
                        namespace=opts['namespace'],
                        follow=True,
                        _preload_content=False).stream():
                    observer.container_msg(step, line.decode('utf-8'))
            except client.rest.ApiException as e:
                logger.warning('Error when create Pod: %s.\n' % e)
                return BuildResult(1, e, step)
            finally:
                api_client.delete_namespaced_pod(
                    name=name,
                    namespace=opts['namespace'],
                )
                observer.step_succeeded(step)
            return BuildResult()

    def verify(self):
        try:
            api_client = self._client
            api_client.list_component_status()
        except Exception as e:
            return "{}: {}".format(e.__class__.__name__, e)
