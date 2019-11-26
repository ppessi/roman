import logging
from contextlib import contextmanager
from datetime import datetime, timedelta
from io import BytesIO
from os.path import join

import docker
import tarfile
from apluslms_yamlidator.utils.decorator import cached_property

from ..utils.translation import _
from . import (
    Backend,
    BuildResult,
)


Mount = docker.types.Mount


logger = logging.getLogger(__name__)


@contextmanager
def create_container(client, **opts):
    container = client.containers.create(**opts)
    try:
        container.start()
        yield container
    finally:
        try:
            container.remove(force=True)
        except docker.errors.APIError as err:
            logger.warning("Failed to stop container %s: %s", container, err)


class DockerBackend(Backend):
    name = 'docker'
    debug_hint = _("""Do you have docker-ce installed and running?
Are you in local 'docker' group? Have you logged out and back in after joining?
You might be able to add yourself to that group with 'sudo adduser docker'.""")


    @cached_property
    def _client(self):
        env = self.environment.environ
        kwargs = {}
        version = env.get('DOCKER_VERSION', None)
        if version:
            kwargs['version'] = version
        timeout = env.get('DOCKER_TIMEOUT', None)
        if timeout:
            kwargs['timeout'] = timeout
        return docker.from_env(environment=env, **kwargs)

    def _run_opts(self, task, step):
        env = self.environment

        now = datetime.now()
        expire = now + timedelta(days=1)
        labels = {
            '': True,
            '.created': now,
            '.expire': expire,
        }
        labels = {self.LABEL_PREFIX + k: str(v) for k, v in labels.items()}

        opts = dict(
            image=step.img,
            command=step.cmd,
            environment=step.env,
            user='{}:{}'.format(env.uid, env.gid),
            labels=labels
        )

        # mounts and workdir
        vols = self.VOLUMES
        work_dir = step.dir
        if work_dir in vols:
            work_dir = vols[work_dir]
        if step.mnt:
            names = [vol['name'] for vol in step.mnt if 'name' in vol]
            step_vols = list(step.mnt)
            step_vols.extend([{'name': key, 'path': item}
                for key, item in vols.items() if key not in names])
            opts['mounts'] = [
                Mount(volume['path'],
                    (task.path if volume.get('type', 'volume') != 'volume'
                    else volume.get('name')),
                    type=volume.get('type', 'volume'), no_copy=False,
                    tmpfs_size=volume.get('tmpfsSize'))
                for volume in step_vols]
            opts['working_dir'] = work_dir
        else:
            wpath = vols['source']
            opts['mounts'] = [
                Mount(wpath, None, type='tmpfs', tmpfs_size=self.WORK_SIZE),
                Mount(join(wpath, 'src'), task.path, type='bind', read_only=True),
                Mount(join(wpath, 'build'), join(task.path, '_build'), type='bind'),
            ]
            opts['working_dir'] = work_dir


        return opts

    def prepare(self, task, observer):
        client = self._client
        for step in task.steps:
            observer.step_preflight(step)
            image, tag = step.img.split(':', 1)
            try:
                img = client.images.get(step.img)
            except docker.errors.ImageNotFound:
                observer.step_running(step)
                observer.manager_msg(step, "Downloading image {}".format(step.img))
                try:
                    img = client.images.pull(image, tag)
                except docker.errors.APIError as err:
                    observer.step_failed(step)
                    error = "%s %s" % (err.__class__.__name__, err)
                    return BuildResult(-1, error, step)
            observer.step_succeeded(step)
        return BuildResult()

    def build(self, task, observer):
        client = self._client
        for step in task.steps:
            observer.step_pending(step)
            opts = self._run_opts(task, step)
            self.update_volume('source', opts['working_dir'])
            observer.manager_msg(step, "Starting container {}:".format(opts['image']))
            try:
                with create_container(client, **opts) as container:
                    observer.step_running(step)
                    for line in container.logs(stderr=True, stream=True):
                        observer.container_msg(step, line.decode('utf-8'))
                    ret = container.wait(timeout=10)
            except docker.errors.APIError as err:
                observer.step_failed(step)
                error = "%s %s" % (err.__class__.__name__, err)
                return BuildResult(-1, error, step)
            except KeyboardInterrupt:
                observer.step_cancelled(step)
                raise
            else:
                code = ret.get('StatusCode', None)
                error = ret.get('Error', None)
                if code or error:
                    observer.step_failed(step)
                    return BuildResult(code, error, step)
                observer.step_succeeded(step)
            self.update_local('source', opts['working_dir'])
        return BuildResult()

    def opts_for_update(self, volume_name, working_dir):
        opts = {}
        opts['mounts'] = [Mount(
            working_dir, volume_name,
            type='volume', no_copy=False)]
        opts['working_dir'] = working_dir
        opts['image'] = 'file_manifest:latest'
        return opts

    def update_volume(self, volume_name, working_dir):
        opts = self.opts_for_update(volume_name, working_dir)
        with create_container(self._client, **opts) as container:
            print("Making tarball...")
            tar_stream = BytesIO()
            tar = tarfile.TarFile(fileobj=tar_stream, mode='w')
            tar.add('.')
            tar.close()
            tar_stream.seek(0)

            apiclient = docker.APIClient()
            apiclient.put_archive(
                container=container.id,
                path=opts['working_dir'],
                data=tar_stream)
            print("\nTar copied to volume")

    def update_local(self, volume_name, working_dir):
        opts = self.opts_for_update(volume_name, working_dir)
        with create_container(self._client, **opts) as container:
            print("Loading files from volume")
            apiclient = docker.APIClient()
            tar, _ = apiclient.get_archive(
                container=container.id,
                path=join(opts['working_dir'], '.'))
            bytes_ = BytesIO()
            for chunk in tar:
                bytes_.write(chunk)
            bytes_.seek(0)
            tar = tarfile.open(mode="r", fileobj=bytes_)
            files = [f for f in tar.getmembers() if '/.git' not in f.name]
            tar.extractall(members=files)
            print("Files loaded")

    def verify(self):
        try:
            client = self._client
            client.ping()
        except Exception as e:
            return "{}: {}".format(e.__class__.__name__, e)

    def cleanup(self, force=False):
        containers = self._client.containers.list({'label': self.LABEL_PREFIX})
        if not force:
            now = str(datetime.now())
            expire_label = self.LABEL_PREFIX + '.expire'
            containers = [c for c in containers if
                expire_label in c.labels and now > c.labels[expire_label]]
        for container in containers:
            container.remove(force=True)

    def version_info(self):
        version = self._client.version()
        if not version:
            return

        out = []
        okeys = ['Version', 'ApiVersion', 'MinAPIVersion', 'GoVersion', 'BuildTime', 'GitCommit', 'Experimental', 'Os', 'Arch', 'KernelVersion']
        version['Name'] = 'Client'
        components = version.pop('Components', [])
        components.insert(0, version)

        for component in components:
            name = component.pop('Name', '-')
            if 'Details' in component:
                component.update(component.pop('Details'))
            out.append("Docker {}:".format(name))
            keys = okeys + [k for k in component.keys() if k not in okeys]
            for key in keys:
                if key in component:
                    val = component[key]
                    if isinstance(val, dict):
                        out.append("  {}:".format(key))
                        for k, v in val.items(): out.append("    {}: {}".format(k, v))
                    elif isinstance(val, list):
                        out.append("  {}:".format(key))
                        for v in val: out.append("   - {}".format(v))
                    else:
                        out.append("  {}: {}".format(key, val))

        return '\n'.join(out)
