#!/usr/bin/env python3
# -*- encoding: utf-8 -*-
"""A setuptools based setup module.

See:
https://packaging.python.org/en/latest/distributing.html
https://github.com/pypa/sampleproject
"""

import os
import re
import sys
from codecs import open
from distutils.version import StrictVersion
from functools import lru_cache
from glob import glob
from json import dump as json_dump
from os import path
from pkg_resources import parse_version
from stat import ST_ATIME, ST_MTIME, ST_MODE, S_IMODE
from unittest import runner

try:
    from setuptools import __version__ as setuptools_version, find_packages, setup
    from setuptools.command.build_py import build_py
except ImportError:
    print('Setuptools is not installed! Have you installed requirements_build.txt?', file=sys.stderr)
    sys.exit(1)

if StrictVersion(setuptools_version) < StrictVersion('20.2'):
    print('Your setuptools version does not support PEP 508. Have you installed requirements_build.txt?', file=sys.stderr)
    sys.exit(1)

try:
    # setuptools >= 40.1.0
    from setuptools import find_namespace_packages
except ImportError:
    # setuptools >= 3.4?
    from setuptools import PEP420PackageFinder
    find_namespace_packages = PEP420PackageFinder.find

try:
    from ruamel.yaml import YAML
except ImportError:
    print("You do not have ruamel.yaml installed. Have you installed requirements_build.txt?", file=sys.stderr)
    sys.exit(1)
else:
    yaml_load = YAML(typ='safe').load


ROOT = path.abspath(path.dirname(__file__))


# ensure that the TextTestRunner buffers stdio
class BufferedTextTestRunner(runner.TextTestRunner):
    def __init__(self, *, buffer=None, **kwargs):
        if buffer is None:
            buffer = True
        super().__init__(buffer=buffer, **kwargs)
runner.BufferedTextTestRunner = BufferedTextTestRunner


class BuildPyWithYamlConvert(build_py):
    """Enhanced 'build_py' command that converts yaml schemas"""

    def run(self):
        super().run()
        self.convert_yaml_schemas()

    def __getattr__(self, attr):
        if attr == 'schema_files':
            self.data_files = self._get_data_files()
            return self.schema_files
        return super().__getattr__(attr)

    def _is_schema_file(self, package, path, filename):
        # filename has to end in .yaml or .yml
        if not any(filename.endswith(x) for x in ('.yml', '.yaml')):
            return False
        # all yaml files in .schemas packages
        if package.endswith('.schemas'):
            return True
        # if file is a valid yaml with '$schema' key in root
        src = path.join(path, filename)
        try:
            with open(src, encoding='utf-8') as in_:
                data = yaml_load(in_)
        except Exception:
            return False
        return '$schema' in data

    def _get_data_files(self):
        data_files, schema_files = [], []
        for package, src_dir, build_dir, filenames in super()._get_data_files():
            datas, schemas = [], []
            for filename in filenames:
                group = schemas if self._is_schema_file(package, src_dir, filename) else datas
                group.append(filename)
            if datas:
                data_files.append((package, src_dir, build_dir, datas))
            if schemas:
                schema_files.append((package, src_dir, build_dir, schemas))

        self.schema_files = schema_files
        return data_files

    def convert_yaml_schemas(self):
        """Convert YAML schema files to JSON under build path"""
        for package, src_dir, build_dir, filenames in self.schema_files:
            for filename in filenames:
                src = path.join(src_dir, filename)
                dst = path.join(build_dir, filename).rpartition('.')[0] + '.json'
                self.mkpath(path.dirname(dst))
                self.make_file(src, dst,
                               self.convert_a_yaml_schema_to_json,
                               (src, dst))

    def convert_a_yaml_schema_to_json(self, src, dst):
        # convert yaml to json
        with open(src, encoding='utf-8') as in_,  open(dst, 'w', encoding='utf-8') as out:
            json_dump(yaml_load(in_), out, indent='\t')

        # copy mode and times: CPython, Lib/distutils/file_util.py, copy_file()
        st = os.stat(src)
        os.utime(dst, (st[ST_ATIME], st[ST_MTIME]))
        os.chmod(dst, S_IMODE(st[ST_MODE]))


def read(*file):
    assert file, "Mising filename"
    try:
        with open(path.join(*file), encoding='utf-8') as f:
            return f.read()
    except IOError:
        return ''


def _coerce_requirements(args):
    if not args:
        args = (ROOT,)
    if not args[-1].endswith('.txt'):
        args = tuple(args) + ('requirements.txt',)
    return args


def read_requirements(*file):
    file = _coerce_requirements(file)
    reqs = (l.split('#', 1)[0].strip() for l in read(*file).splitlines())
    return [l for l in reqs if l]


def get_version(*file):
    match = re.search(r"^__version__ = ['\"]([^'\"]*)['\"]", read(*file), re.M)
    if match:
        version = parse_version(match.group(1))
        major, minor, *_ = version._version.release + (0,)
        return (str(version), major, minor)
    raise RuntimeError("Unable to find version string.")


@lru_cache(maxsize=512)
def is_core_package(name: str):
    if name == NAME:
        return True
    return path.exists(path.join(ROOT, name, 'setup.py'))


@lru_cache(maxsize=512)
def find_core_packages(name: str):
    if not is_core_package(name):
        raise ValueError("Can not find packges for {}, as it is not a core package".format(name))
    return find_packages(
        where=path.join(ROOT, name),
        exclude=('dist*', 'build*', 'tests*'),
    )


@lru_cache(maxsize=512)
def get_core_version(name: str):
    for module in find_core_packages(name):
        try:
            return get_version(ROOT, name, *module.split('.'), '__init__.py')
        except (IOError, RuntimeError):
            pass
    return VERSION


# this hack skips version requirement when called from pip3
if os.environ.get('_', '').endswith('pip3') and sys.argv == ['-c', 'egg_info']:
    def core_requirement(name: str):
        return name
else:
    def core_requirement(name: str):
        version, major, minor = get_core_version(name)
        fmt = "{0} >= {1}, <{2}" if major > 0 else "{0} == {1}"
        return fmt.format(name, version, major+1)


def get_requirements(*file):
    return [core_requirement(req) if ' ' not in req and is_core_package(req) else req
            for req in read_requirements(*file)]


def get_extra_requirements(*file, sep='-'):
    file = _coerce_requirements(file)
    *where, filename = file
    base, ext = path.splitext(filename)
    extras = {}
    for extras_file in glob(path.join(*where, base+sep+'*'+ext)):
        name = path.splitext(path.basename(extras_file))[0].split(sep, 1)[1]
        reqs = get_requirements(extras_file)
        if reqs:
            extras[name] = reqs
    return extras


def parse_rst_field(text: str, *fields):
    def find():
        it = iter(text.splitlines())
        for line in it:
            if line and line[0] == ':' and line.split(':', 2)[1].lower() in fields:
                yield line.split(':', 2)[2].strip()
                break
        for line in it:
            if not line or line[0] == ':':
                break
            yield line.strip()
    return ' '.join(find())


def basic_info(where):
    info = INFO.copy()
    info.pop('description', None)
    text = read(where, 'README.rst')
    if text:
        info['long_description'] = text
        for key, fields in INFO_FIELDS:
            value = parse_rst_field(text, *fields)
            if value:
                info[key] = value
    info['install_requires'] = get_requirements(where)
    return info


def setup_package(where, name=None, **kwargs):
    if not name:
        name = path.basename(where)

    info = basic_info(where)
    info.setdefault('description', "A support package for {}".format(NAME))
    info.setdefault('long_description', "This is a support package for {}".format(NAME))
    info['name'] = name
    info['version'] = get_core_version(name)[0]
    info['packages'] = find_core_packages(where)
    info.update(**kwargs)
    setup(**info)


def _find_namespace(where):
    dir_ = path.join(where, MODULE)
    files = [(fn, path.join(dir_, fn)) for fn in os.listdir(dir_)]
    dirs = [fn for fn, fp in files if path.isdir(fp)]
    if len(dirs) != 1:
        raise RuntimeError("Unable to autodetect the namespace as there are multiple dirs in {}".format(dir_))
    return dirs[0]


def setup_namespace(where, name=None, namespace=None, **kwargs):
    """A helper to create a simple namespaced setup file"""
    if not name:
        name = path.basename(where)
    if not namespace:
        namespace = _find_namespace(where)

    info = basic_info(where)
    info.setdefault('description', "{}, module .{}.{}".format(INFO['description'], namespace, name))
    info.setdefault('long_description', "This is a module to the namespace {}.{}".format(MODULE, namespace))
    info['name'] = '-'.join((NAME, namespace, name))
    info['packages'] = ['.'.join((MODULE, namespace, name))]
    info.update(kwargs)
    setup(**info)


NAME = 'apluslms-roman'
MODULE = 'apluslms_roman'
VERSION = get_version(ROOT, MODULE, '__init__.py')
README = read(ROOT, 'README.rst')
INFO_FIELDS = (
    ('description', ('abstract', 'description')),
    ('author', ('author',)),
)
INFO = dict(
    version=VERSION[0],
    keywords='apluslms material',
    url='https://github.com/apluslms/roman',
    author_email='apluslms@googlegroups.com',
    license='MIT',
    platforms=['any'],

    # See https://pypi.python.org/pypi?%3Aaction=list_classifiers
    classifiers=[
        #   3 - Alpha
        #   4 - Beta
        #   5 - Production/Stable
        'Development Status :: 4 - Beta',
        'License :: OSI Approved :: MIT License',

        'Intended Audience :: Developers',
        'Operating System :: OS Independent',
        'Topic :: Software Development :: Build Tools',
        'Topic :: Software Development :: Libraries :: Python Modules',

        'Programming Language :: Python :: 3',
        'Programming Language :: Python :: 3.5',
        'Programming Language :: Python :: 3.6',
        'Programming Language :: Python :: 3.7',
        'Programming Language :: Python :: 3 :: Only',
    ],

    include_package_data = True,
    package_data={
        '': [
            '*.json',
            '*.yaml',
            '*.yml',
        ],
    },

    test_suite='tests',
    test_runner='unittest.runner:BufferedTextTestRunner',
    cmdclass={
        'build_py': BuildPyWithYamlConvert,
    },
)
INFO.update((key, parse_rst_field(README, *fields))  for key, fields in INFO_FIELDS)


if __name__ == '__main__':
    packages = find_packages(include=[MODULE, MODULE+'.*'])
    packages.extend(find_namespace_packages(include=[MODULE+'.s.*']))
    setup(
        name=NAME,
        long_description=README,
        install_requires=get_requirements(),
        extras_require=get_extra_requirements(),
        packages=packages,
        entry_points={
            'console_scripts': [
                'roman = apluslms_roman.cli:main',
            ],
        },
        zip_safe=False,
        **INFO,
    )
