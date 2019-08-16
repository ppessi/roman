import logging
from contextlib import contextmanager
from datetime import datetime, timedelta
from os.path import join

import docker
from apluslms_yamlidator.utils.decorator import cached_property

from ..cache_file import CacheFile
from ..utils.translation import _
from . import (
    Backend,
    BuildResult,
)


Mount = docker.types.Mount


logger = logging.getLogger(__name__)


class DockerCache(CacheFile):
    name = 'roman_docker_cache'
    schema = name
    version = (1, 0)
    version_key = None

    @property
    def images(self):
        return self.setdefault('images', {})


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

    @cached_property
    def _cache(self):
        return DockerCache.load()

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
        if step.mnt:
            opts['mounts'] = [Mount(step.mnt, task.path, type='bind', read_only=False)]
            opts['working_dir'] = step.mnt
        else:
            wpath = self.WORK_PATH
            opts['mounts'] = [
                Mount(wpath, None, type='tmpfs', read_only=False, tmpfs_size=self.WORK_SIZE),
                Mount(join(wpath, 'src'), task.path, type='bind', read_only=True),
                Mount(join(wpath, 'build'), join(task.path, '_build'), type='bind', read_only=False),
            ]
            opts['working_dir'] = wpath

        return opts

    def prepare(self, task, observer, force=False):
        client = self._client
        images = self._cache.images
        day = timedelta(days=1)
        for step in task.steps:
            observer.step_preflight(step)
            image, tag = step.img.split(':', 1)

            last_update= images.get(step.img, None)
            should_update = (not last_update or
                datetime.now() - last_update >= day)

            try:
                client.images.get(step.img)
                img_found = True
            except docker.errors.ImageNotFound:
                img_found = False

            if any((not img_found, should_update, force)):
                observer.step_running(step)
                if should_update:
                    observer.manager_msg(step,
                        ("Checking for updates for {} and "
                        "downloading if any").format(step.img))
                else:
                    observer.manager_msg(step,
                        "Downloading image {}".format(step.img))
                try:
                    client.images.pull(image, tag)
                    images[step.img] = datetime.now()
                except docker.errors.APIError as err:
                    if not img_found:
                        observer.step_failed(step)
                        error = "%s %s" % (err.__class__.__name__, err)
                        return BuildResult(-1, error, step)
                    observer.manager_msg(step, "Couldn't download image. "
                        "Using previously downloaded image")

            observer.step_succeeded(step)
        return BuildResult()

    def build(self, task, observer):
        client = self._client
        for step in task.steps:
            observer.step_pending(step)
            opts = self._run_opts(task, step)
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
        return BuildResult()

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

    def __del__(self):
        self._cache.save()
