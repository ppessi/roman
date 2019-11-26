from collections import Counter
from itertools import chain
from os import listdir
from os.path import basename, join, isdir, isfile

from apluslms_yamlidator.document import Document
from apluslms_yamlidator.utils.collections import Mapping
from apluslms_yamlidator.utils.version import Version

from .utils.translation import _


class ProjectConfigError(Exception):
    pass


class ProjectConfig(Document):
    name = 'roman_project'
    schema = name
    version = Version(2, 0)
    DEFAULT_NAMES = ('roman', 'course')
    DEFAULT_PREFIXES = ('yml', 'yaml', 'json')
    DEFAULT_FILENAME = '%s.%s' % (DEFAULT_NAMES[0], DEFAULT_PREFIXES[0])

    @classmethod
    def find_from(cls, path):

        files = [
            ['%s.%s' % (name, prefix) for prefix in cls.DEFAULT_PREFIXES]
            for name in cls.DEFAULT_NAMES
        ]
        files = list(chain.from_iterable(files))

        if not isdir(path):
            raise ProjectConfigError(
                _("Path {} doesn't exist or is not a directory").format(path)
            )

        file_ = next((file_ for file_ in files if isfile(file_)), None)
        config = join(path, file_) if file_ else None
        if not config or not isfile(config):
            raise FileNotFoundError((
                _("Couldn't find project configuration from {}.\nExpected to find one of these: {}")
            ).format(path, ', '.join(files)))

        return cls.load(config)

    @classmethod
    def load_from(cls, config):
        if isfile(config):
            return cls.load(config)
        if '.' not in basename(config):
            for prefix in cls.DEFAULT_PREFIXES:
                filename = '%s.%s' % (config, prefix)
                if isfile(filename):
                    return cls.load(filename)
        raise FileNotFoundError("Given file '{}' doesn't exist.".format(config))

    def validate(self, *args, **kwargs):
        super().validate(*args, **kwargs)
        if self.steps:
            names = Counter((s['name'].lower() for s in self.steps if 'name' in s))
            names = [name for name, amount in names.items() if amount > 1]
            if names:
                raise ProjectConfigError(("Step names should be unique.\n"
                    "Following names were used more than once:\n  - {}")
                    .format('\n  - '.join(names)))
        if 'volumes' in self:
            vols = set(self['volumes'].keys())
            step_vols = chain.from_iterable(
                [step['volumes'] for step in self.steps if 'volumes' in step])
            step_vol_names = {vol['name'] for vol in step_vols if 'name' in vol}
            invalid_vols = vols - step_vol_names
            if len(invalid_vols) > 0:
                raise ProjectConfigError("All volumes in 'volumes' should be "
                    "referenced in at least one step. These volumes weren't "
                    "referenced in any steps: {}".format(invalid_vols))

    def add_step(self, step):
        if 'name' in step:
            if step['name'].lower() in self.steps_by_name:
                raise ValueError("A step with the name '%s' already exists." % step['name'])
        self.steps.append(step)

    def del_step(self, step):
        if not isinstance(step, (dict, Mapping)):
            step = self.get_step(step)
        self.steps.remove(step)

    def get_step(self, step_ref):
        if step_ref.isdigit():
            try:
                idx = int(step_ref)
                return self.steps[idx]
            except IndexError as err:
                # IndexError doesn't include the index by default
                raise IndexError(idx) from err
        return self.steps_by_name[step_ref.lower()]

    def ref_to_index(self, step_ref):
        if step_ref.isdigit():
            return int(step_ref)
        return self.steps.index(self.get_step(step_ref))

    @property
    def steps(self):
        return self.setdefault('steps', [])

    @property
    def steps_by_name(self):
        return {s['name'].lower(): s for s in self.steps if 'name' in s}
