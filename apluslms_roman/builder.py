from os import environ, getuid, getegid
from os.path import isdir, join

from apluslms_yamlidator.utils.decorator import cached_property
from apluslms_yamlidator.utils.collections import OrderedDict

from apluslms_roman.utils.path_mapping import get_host_path
from .backends import BACKENDS, BuildTask, BuildStep, Environment
from .observer import StreamObserver
from .utils.importing import import_string
from .utils.translation import _


class Builder:
    def __init__(self, engine, config, observer=None):
        if not isdir(config.dir):
            raise ValueError(_("config.dir isn't a directory."))
        self.config = config
        self.path = config.dir
        self._engine = engine
        self._observer = observer or StreamObserver()

    def get_steps(self, refs: list = None):
        steps = [BuildStep.from_config(i, step) for i, step in enumerate(self.config.steps)]
        if refs:
            name_dict = {step.name: step for step in steps}
            step_list = [int(ref) if ref.isdigit() else ref for ref in refs]
            try:
                steps = [steps[ref] if isinstance(ref, int) else name_dict[ref] for ref in step_list]
            except KeyError as e:
                raise ValueError(_("No step named {}").format(e))
            except IndexError as e:
                raise ValueError(_("A step index was too big. Remember, indexing begins with 0."))
        return list(OrderedDict.fromkeys(steps))

    def build(self, step_refs: list = None):
        backend = self._engine.backend
        observer = self._observer
        steps = self.get_steps(step_refs)
        # Check if has global config set.
            # Using path in global config file
        print("dir mapping set in config:", self._engine._dir_mapping if hasattr(self._engine, '_dir_mapping') else None)
        print("env set:", self._engine._environment.environ.get('DOCKER_HOST_PATH', None))

        if hasattr(self._engine, '_dir_mapping'):
            print("Using roman config")
            path = get_host_path(self.path, self._engine._dir_mapping)
        elif self._engine._environment.environ.get('DOCKER_HOST_PATH', None) is not None:
            print("Using DOCKER_HOST_PATH")
            path = self._engine._environment.environ.get('DOCKER_HOST_PATH', None)
        else:
            path = self.path
            print("No config find")
        task = BuildTask(path, steps)
        # Using config file
        # Using -p flag or global environment variable
        # task = BuildTask(self.path if host_path is None else host_path, steps)

        task = BuildTask(self.path, steps)

        observer.enter_prepare()
        backend.prepare(task, observer)
        observer.enter_build()
        result = backend.build(task, observer)
        observer.done(data=result)
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
        env = {k: v for k, v in environ.items() if k.startswith(env_prefix)}
        if settings:
            for k, v in settings.get(name, {}).items():
                if v is not None and v != '':
                    env[env_prefix + k.replace('-', '_').upper()] = v
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
