from collections import namedtuple
from collections.abc import Mapping

from ..observer import BuildObserver
from ..utils.env import EnvDict


BACKENDS = {
    'docker': 'apluslms_roman.backends.docker.DockerBackend',
}


BuildTask = namedtuple('BuildTask', [
    'path',
    'steps',
])


def clean_image_name(image):
    if ':' not in image:
        image += ':latest'
    return image


class BuildStep:
    """
    img: docker image
    cmd: If not None, docker command
    mnt: If not None, course data is mounted to this path in RW mode
    env: If not None, dict that is given as environment for the image
    ref: Name/index of the step
    """
    __slots__ = ('img', 'cmd', 'mnt', 'env', 'name', 'ref')

    @classmethod
    def from_config(cls, index, data, environment=None):
        if isinstance(data, Mapping):
            if 'img' not in data:
                raise RuntimeError(
                    "Missing image name (img) in step configuration: {}".format(data))
            return cls(
                index,
                data['img'],
                data.get('cmd'),
                data.get('mnt'),
                environment,
                data.get('env'),
                data.get('name'),
            )
        return cls(index, clean_image_name(data))

    def __init__(
            self, ref, img, cmd=None, mnt=None,
            project_env=None, step_env=None, name=None):
        self.ref = ref
        self.img = clean_image_name(img)
        self.cmd = cmd if (cmd is None or isinstance(cmd, str)) else tuple(cmd)
        self.mnt = mnt
        self.name = name
        self.env = EnvDict(
            (project_env, "project configuration"),
            (step_env, "step {}".format(str(self)))
        ).get_combined()

    def __str__(self):
        return self.name or str(self.ref)


class BuildResult:
    __slots__ = ('code', 'error', 'step')

    def __init__(self, code=0, error=None, step=None):
        self.code = code
        self.error = error
        self.step = step
        assert self.ok or step is not None, "step is required for failed result"

    @property
    def ok(self):
        return self.code == 0 and self.error is None

    def __str__(self):
        if self.ok:
            return "Build ok"
        error = self.error or 'exit code {}'.format(self.code)
        return "Build failed on step {}: {}".format(self.step, error)


Environment = namedtuple('Environment', [
    'uid',
    'gid',
    'environ',
])


class Backend:
    WORK_SIZE = '100M'
    WORK_PATH = '/work'
    LABEL_PREFIX = 'io.github.apluslm.roman'

    def __init__(self, environment: Environment):
        self.environment = environment

    def prepare(self, task: BuildTask, observer: BuildObserver, force=False):
        raise NotImplementedError

    def build(self, task: BuildTask, observer: BuildObserver):
        """
            Returns BuildResult
        """
        raise NotImplementedError

    def verify(self):
        """Verify that connections to backend is working
        Returns:
          On success: None
          On failure: exception or error string
        """
        raise NotImplementedError

    def cleanup(self, force=False):
        """
            Deletes containers
        """
        pass

    def version_info(self):
        pass
