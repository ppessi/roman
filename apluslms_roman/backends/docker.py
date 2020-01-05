import logging
from contextlib import contextmanager
from datetime import datetime, timedelta
from io import BytesIO
from json import loads
from os import getcwd, listdir, makedirs
from os.path import basename, dirname, isdir, join
from sys import stdout

import docker
import tarfile
from apluslms_yamlidator.utils.decorator import cached_property

from .. import print_files
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
    vol_opts = {
        'mounts': [Mount('/source', 'source')],
        'working_dir': '/source',
        'image': 'file_manifest:latest'
    }

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
        self.update_volume(observer)
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
        self.update_local(observer)
        return BuildResult()

    def get_files_to_update(self, source, target):
        files = sorted([f[2:] for f in source
            if f not in target or source[f] != target[f]])

        filtered = set()

        if len(files) == len(source) and files:
            return ['.']

        subfolder_level = 1
        # go through folders one 'level' at a time, if everything in a folder
        # is going to be copied, we'll just copy the folder instead of files individually
        while files:
            filtered = filtered.union({f for f in files if f.count('/') < subfolder_level})
            files = [f for f in files if f.count('/') >= subfolder_level]
            folders = {dirname(f) for f in files if f.count('/') == subfolder_level}
            for folder in folders:
                update_whole_folder = (
                    len([f for f in files if folder in f]) ==
                    len([f for f in source if folder in f]))
                if update_whole_folder:
                    files = [f for f in files if folder not in f]
                    filtered.add(folder)
                else:
                    files_in_folder = {f for f in files
                        if folder in f and f.count('/') == subfolder_level}
                    files = [f for f in files if f not in files_in_folder]
                    filtered = filtered.union(files_in_folder)
            subfolder_level += 1

        return list(filtered)

    def get_file_manifest(self, container, path):
        tar, _ = docker.APIClient().get_archive(
            container=container.id,
            path=path)
        bytes_ = BytesIO()
        for chunk in tar:
            bytes_.write(chunk)
        bytes_.seek(0)
        tar = tarfile.open(mode="r", fileobj=bytes_)
        return loads(tar.extractfile('file_manifest.json').read().decode())

    def update_volume(self, observer, step=None):
        opts = self.vol_opts
        observer.manager_msg(step, "Copying files to container")
        with create_container(self._client, **opts) as container:
            print_files.main()
            local_files = loads(open('file_manifest.json', 'r').read())

            container.wait(timeout=10)
            # If the volume is new, for some reason file_manifest isn't created
            # and the volume is completely empty, so this errors
            # There's probably a better fix
            try:
                vol_files = self.get_file_manifest(container,
                    opts['working_dir'] + '/file_manifest.json')
            except docker.errors.NotFound:
                vol_files = dict()
            files = self.get_files_to_update(local_files, vol_files)

            if not files:
                observer.manager_msg(step, "No files to copy")
                return
            else:
                msg = "Making tarball"
                observer.start_progress(step, msg)
                tar_stream = BytesIO()
                tar = tarfile.open(mode='w:gz', fileobj=tar_stream)
                total = len(files)
                for i in range(total):
                    observer.notify_progress(step, msg, int((i + 1) * 100 / total))
                    tar.add(files[i])
                tar.close()

                tar_stream.seek(0)
                apiclient = docker.APIClient()
                apiclient.put_archive(
                    container=container.id,
                    path=opts['working_dir'],
                    data=tar_stream)
                observer.manager_msg(step, "Tar loaded to volume")


    def update_local(self, observer, step=None):
        opts = self.vol_opts
        with create_container(self._client, **opts) as container:
            print_files.main()
            local_files = loads(open('file_manifest.json', 'r').read())

            container.wait(timeout=10)
            vol_files = self.get_file_manifest(container,
                opts['working_dir'] + '/file_manifest.json')
            files = self.get_files_to_update(vol_files, local_files)

            observer.manager_msg(step, "Updating local files")
            msg = "Downloading files from container"
            if not files:
                observer.manager_msg(step, "No files to update")
                return
            total = len(files)
            apiclient = docker.APIClient()
            tar, _ = apiclient.get_archive(
                container=container.id,
                path=join(opts['working_dir'], '.'))
            bytes_ = BytesIO()
            for chunk in tar:
                bytes_.write(chunk)
            bytes_.seek(0)
            tar = tarfile.open(mode="r", fileobj=bytes_)
            observer.start_progress(step, msg)
            for i in range(total):
                f = files[i]
                folder = dirname(f)
                if folder:
                    makedirs(folder, exist_ok=True)
                observer.notify_progress(step, msg, int((i + 1) * 100 / total))
                tar.extract(join('.', f))

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
