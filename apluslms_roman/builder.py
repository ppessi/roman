import logging
from os import environ, getuid, getegid, mkdir
from os.path import isdir
from shutil import rmtree

from apluslms_yamlidator.utils.collections import OrderedDict
from apluslms_yamlidator.utils.decorator import cached_property

from apluslms_roman.utils.path_mapping import load_from_env
from .backends import BACKENDS, BuildTask, BuildStep, Environment
from .observer import StreamObserver
from .utils.importing import import_string
from .utils.translation import _

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)


class Builder:
    def __init__(self, engine, config, observer=None,
            environment=None, volumes=None):
        if not isdir(config.dir):
            raise ValueError(_("config.dir isn't a directory."))
        self.config = config
        self.path = config.dir
        self._engine = engine
        self._observer = observer or StreamObserver()
        self._environment = environment or []
        self._volumes = volumes or {}

    def get_steps(self, refs: list = None):
        steps = [BuildStep.from_config(i, step, self._environment, self._volumes)
            for i, step in enumerate(self.config.steps)]
        if refs:
            name_dict = {step.name: step for step in steps}
            refs = [int(ref) if ref.isdigit() else ref.lower() for ref in refs]
            # NOTE: May raise KeyError or IndexError
            steps = [steps[ref] if isinstance(ref, int) else name_dict[ref] for ref in refs]
            steps = list(OrderedDict.fromkeys(steps))
        return steps

    def build(self, step_refs: list = None, clean_build=False):
        backend = self._engine.backend
        observer = self._observer
        steps = self.get_steps(step_refs)
        task = BuildTask(self.path, steps)
        observer.enter_prepare()
        result = backend.prepare(task, observer)
        observer.result_msg(result)
        if result.ok:
            observer.enter_build()
            # FIXME: add support for other build paths
            if clean_build:
                if isdir('_build'):
                    rmtree('_build')
            if clean_build or not isdir('_build'):
                mkdir('_build')
            result = backend.build(task, observer)
            observer.result_msg(result)
        observer.done(result)
        return result


class Engine:
    def __init__(self, backend_class=None, settings=None):
        if backend_class is None:
            if settings and 'backend' in settings:
                backend_class = settings['backend']
            else:
                from .backends.docker import DockerBackend as backend_class
        if isinstance(backend_class, str):
            if '.' not in backend_class:
                backend_class = BACKENDS.get(backend_class, backend_class)
            backend_class = import_string(backend_class)
        self._backend_class = backend_class

        name = getattr(backend_class, 'name', None) or backend_class.__name__.lower()
        env_prefix = name.upper() + '_'
        env = load_from_env(env_prefix, '.')
        logger.debug("env without reading global config:{}".format(env))
        if settings:
            for k, v in settings.get(name, {}).items():
                if v is not None and v != '':
                    env[k] = v
        logger.debug("env after read from global config:{}".format(env))
        self._environment = Environment(getuid(), getegid(), env)

    @cached_property
    def backend(self):
        return self._backend_class(self._environment)

    def verify(self):
        return self.backend.verify()

    def version_info(self):
        return self.backend.version_info()

    def create_builder(self, *args, **kwargs):
        return Builder(self, *args, **kwargs)
