import argparse
import logging
from collections import namedtuple
from functools import partial
from glob import glob
from itertools import chain
from os import chdir, getcwd
from os.path import abspath, expanduser, expandvars, join as path_join
from sys import exit as _exit, stderr, stdout

from apluslms_yamlidator.document import Document
from apluslms_yamlidator.utils.yaml import rt_dump as yaml_dump
from apluslms_yamlidator.validator import ValidationError, render_error

from . import __version__
from .builder import Engine
from .configuration import CourseConfigError, CourseConfig
from .settings import RomanSettings
from .utils.translation import _


LOG_LEVELS = [logging.WARNING, logging.INFO, logging.DEBUG]
logger = logging.getLogger(__name__)


def exit(status=None, message=None):
    if message:
        print(message, file=stderr)
    logging.shutdown()
    _exit(status or 0)


_ActionContext = namedtuple('ActionContext', ('parser', 'args', 'settings', 'action'))
class ActionContext(_ActionContext):
    @property
    def action_name(self):
        action = self.action
        while hasattr(action, 'func'):
            action = action.func
        return action.__name__

    def run(self):
        return self.action(self)


class CallbackSubParsersAction(argparse._SubParsersAction):
    def __init__(self, *args, callback_dest=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._callback_dest = callback_dest

    def add_parser(self, name, callback=None, **kwargs):
        parser = super().add_parser(name, **kwargs)
        if callback:
            parser.set_defaults(**{self._callback_dest: callback})
        return parser

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        pass


class CallbackArgumentParser(argparse.ArgumentParser):
    def __init__(self, *args, callback_dest=None, callback_subparser_defaults=None, **kwargs):
        super().__init__(*args, **kwargs)

        if callback_dest is None:
            callback_dest = 'action'
        self.register('action', 'parsers', partial(CallbackSubParsersAction, callback_dest=callback_dest))
        self._callback_dest = callback_dest
        self._callback_subparsers = None
        self._callback_subparser_defaults = callback_subparser_defaults.copy() if callback_subparser_defaults is not None else {}

    @property
    def help_callback(self):
        return self._callback_help_action

    def _callback_help_action(self, *args, **kwargs):
        self.print_help()
        self.exit()

    def get_callback(self, namespace):
        return getattr(namespace, self._callback_dest, None)

    def set_callback(self, callback):
        self.set_defaults(**{self._callback_dest: callback})

    def set_subparser_defaults(self, **kwargs):
        self._callback_subparser_defaults.update(kwargs)

    def use_subparsers(self, **kwargs):
        if self._callback_subparsers:
            raise RuntimeError
        for k, v in self._callback_subparser_defaults.items():
            kwargs.setdefault(k, v)
        kwargs.setdefault('parser_class',
            partial(type(self),
                    callback_dest=self._callback_dest,
                    callback_subparser_defaults=self._callback_subparser_defaults))
        self._callback_subparsers = subparsers = self.add_subparsers(**kwargs)
        if self.get_default(self._callback_dest) is None:
            self.set_callback(self.help_callback)
        return subparsers

    def add_parser(self, *args, **kwargs):
        if not self._callback_subparsers:
            raise RuntimeError
        return self._callback_subparsers.add_parser(*args, **kwargs)

    def copy_defaults_to(self, parser):
        # NOTE: uses undocumented private API
        defaults = {}

        # add any action defaults that aren't present
        for action in self._actions:
            if action.dest is not argparse.SUPPRESS and action.default is not argparse.SUPPRESS:
                defaults.setdefault(action.dest, action.default)

        # add any parser defaults that aren't present
        for dest in self._defaults:
            defaults.setdefault(dest, self._defaults[dest])

        if defaults:
            parser.set_defaults(**defaults)


## The command-line interface

# a basic parser interface

def configure_logging():
    logging.basicConfig(
        level=LOG_LEVELS[0],
        format="%(asctime)-15s %(levelname)-8s %(name)s: %(message)s",
    )


def create_parser(version=__version__,
                  **kwargs):
    # the parser
    kwargs.setdefault('description', _("A course material builder"))
    #parser = argparse.ArgumentParser(**kwargs)
    parser = CallbackArgumentParser(**kwargs)

    # basic options
    parser.add_argument('-V', '--version',
        action='version',
        version="%%(prog)s %s" % (version,),
        help=_("print a version info and exit"))
    parser.add_argument('-v', '--verbose',
        action='count',
        default=0,
        help=_("show some logged messages; repeat to show more"))
    parser.add_argument('--debug',
        action='store_true',
        help=_("show all logged messages"))
    # setting file support

    # global roman settings (user settings)
    parser.add_argument('-c', '--config',
        metavar=_('FILE'),
        help=_("use FILE as the global roman settings"))

    # project location and project settings
    parser.add_argument('-C', '--directory',
        metavar=_('DIR'),
        default=[],
        action='append',
        help=_("Change to directory DIR before reading the configuration file or doing anything else. If specified multiple times, then values are joined with `os.path.join`."))
    parser.add_argument('-f', '--file',
        dest='project_config',
        metavar=_('FILE'),
        help=_("use the FILE as the project configuration file"))

    RomanSettings.populate_parser(parser)

    return parser


def parse_actioncontext(parser):
    args = parser.parse_args()

    # set logging level
    max_level = len(LOG_LEVELS)-1
    if args.debug or args.verbose >= max_level:
        args.debug = True
        args.verbose = max_level
    logging.getLogger().setLevel(LOG_LEVELS[args.verbose])

    # set working directory
    if args.directory:
        dirs = [expanduser(expandvars(d)) for d in args.directory]
        chdir(path_join(*dirs))

    # load settings
    allow_missing = args.config is None
    config = args.config if args.config is not None else RomanSettings.get_config_path()

    logger.debug(_("Loading settings from '%s'"), config)

    try:
        settings = RomanSettings.load(config, allow_missing=allow_missing)
    except ValidationError as e:
        exit(1, '\n'.join(render_error(e)))
    except IOError as e:
        exit(1, str(e))

    settings.update_from_namespace(args)

    # post process rest of the args
    if args.steps and any(step == '?' for step in args.steps):
        args.list_steps = True

    return ActionContext(parser, args, settings, parser.get_callback(args))


# parser configuration for roman cli

def add_cli_actions(parser):
    parser.set_subparser_defaults(
        metavar=_('COMMAND'),
        help=_("a list of sub commands:"))
    parser.use_subparsers(
        title=_("Commands"),
        description=_("Top level commands. Check the per command help with `%(prog)s COMMAND --help`."))

    build = parser.add_parser('build', aliases=['b'],
        callback=build_action,
        help=_("build the course (default action)"))
    build.add_argument('-s', '--steps', nargs='+',
        help=_("select which steps to build and in which order (use either index or step name)"))
    build.add_argument('-l', '--list-steps',
        action='store_true',
        help=_("list all available steps and exit"))

    # build is the default callback. set defaults for it
    build.copy_defaults_to(parser)
    parser.set_callback(build_action)

    config = parser.add_parser('config', aliases=['c'],
        callback=config_print_action,
        help=_("manage %(prog)s configuration"))
    config.add_argument('-u', '--user', action='store_true',
        help=_("limit actions to the global %(prog)s configuration"))
    config.add_argument('-p', '--project', action='store_true',
        help=_("limit actions to the project configuration"))
    with config.use_subparsers(title=_("Config actions")):
        config.add_parser('print', aliases=['p'],
            callback=config_print_action,
            help=_("print configurations (default action)"))

    validate = parser.add_parser('validate',
        help=_("validatation actions for debuging"))
    with validate.use_subparsers(title=_("Validate actions")):
        validate_schema = validate.add_parser('schema',
            callback=validate_schema_action,
            help=_("validate an YAML against a known schema"))
        validate_schema.add_argument('-v', '--version', metavar='version',
            dest='schema_version',
            help=_("the version of a schema for validation (read from 'version' in a document by default)"))
        validate_schema.add_argument('schema_name', metavar='schema',
            help=_("the name of a schema for validation"))
        validate_schema.add_argument('data_files', metavar='file', nargs='+',
            help=_("an YAML/JSON file(s) to be validated"))

    backend = parser.add_parser('backend',
        help=_("backend actions for debuging"))
    with backend.use_subparsers(title=_("Backend actions")):
        backend.add_parser('test',
            callback=backend_test_action,
            help=_("test the backend connection"))
        backend.add_parser('info',
            callback=partial(backend_test_action, verbose=True),
            help=_("show the backend information"))

    return parser


def main():
    """
    CLI main function:
    1. parse arguments
    2. execute action with the arguments
    3. exit with the code from the action
    """
    configure_logging()
    parser = create_parser()
    add_cli_actions(parser)
    context = parse_actioncontext(parser)

    logger.debug(_("Executing action %s"), context.action_name)
    context.settings.save() # FIXME: testing
    exit(context.run())


## Actions
# action utils
def get_engine(context):
    try:
        return Engine(settings=context.settings)
    except ImportError:
        exit(1, _("ERROR: Unable to find backend '{}'.").format(context.settings.get('backend', 'docker')))


def get_config(context):
    try:
        if context.args.project_config:
            project_config = abspath(expanduser(expandvars(context.args.project_config)))
            return CourseConfig.load_from(project_config)
        return CourseConfig.find_from(getcwd())
    except CourseConfigError as e:
        exit(1, _("Invalid course configuration: {}").format(e))


def verify_engine(engine, only_when_error=False):
    error = engine.verify()
    if error:
        print(_("Container backend connection failed.\nUsing driver %s.%s.\n\n%s") % (
            engine.backend.__module__,
            engine.backend.__class__.__name__,
            error,
        ))
        if hasattr(engine.backend, 'debug_hint'):
            print("\n" + engine.backend.debug_hint)
        return False
    elif not only_when_error:
        print(_("Container backend connected successfully.\nUsing driver %s.%s.") % (
            engine.backend.__module__,
            engine.backend.__class__.__name__,
        ))
    return True


# actions

def build_action(context):
    config = get_config(context)
    engine = get_engine(context)
    builder = engine.create_builder(config)
    if context.args.list_steps:
        steps = builder.get_steps()
        num_len = max(2, len(str(len(steps)-1)))
        name_len = max(4, len(max(step.name or "" for step in steps)))
        header_fmt = "{:>%ds}  {:%ds} {}" % (num_len, name_len)
        step_fmt = "{:%dd}. {:%ds} {}" % (num_len, name_len)
        print(header_fmt.format('ID', 'NAME', 'IAMGE'))
        for step in steps:
            print(step_fmt.format(step.ref, step.name or "", step.img))
        return

    if not verify_engine(engine, only_when_error=True):
        return 1

    # build course
    steps = context.args.steps
    if steps:
        steps = chain.from_iterable(step.split(',') for step in steps)
    result = builder.build(steps)
    print(result)
    return result.code


def config_print_action(context):
    all_ = not any(getattr(context.args, k, False) for k in ('user', 'project'))
    if context.args.debug:
        print("---\n# arguments:")
        data = {k: v for k, v in vars(context.args).items() if k[0] != '_' and not callable(v)}
        yaml_dump(data, stdout)
    if all_ or context.args.user:
        print("---\n# roman settings:")
        yaml_dump(context.settings._data, stdout)
    if all_ or context.args.project:
        print("---\n# project config")
        yaml_dump(get_config(context)._data, stdout)


def validate_schema_action(context):
    Container = Document.bind(schema=context.args.schema_name).Container
    files = chain.from_iterable(glob(s) for s in context.args.data_files)
    max_version = context.args.schema_version

    def print2(msg):
        print(" ", msg)

    if max_version is None:
        get_docs = lambda x: x
    else:
        def get_docs(container):
            try:
                doc = container.get_latest(max_version, validate=False)
            except KeyError:
                print2(_("No elements with max version %s found!") % max_version)
                return ()
            if doc.version is None:
                doc.version = max_version
            return (doc,)

    errors = 0
    documents = 0
    for file_ in files:
        container = Container(file_)
        print("%s:" % (container.path,))
        if not container:
            print2(_("The data file is empty!"))
            continue

        for doc in get_docs(container):
            try:
                doc.validate(quiet=True)
            except ValidationError as e:
                print2(_("Document %d failed against %s") % (doc.index, doc.validator_id))
                print('\n'+'\n'.join('    '+s for s in render_error(e))+'\n')
                errors += 1
            else:
                print2(_("Document %d validates against %s") % (doc.index, doc.validator_id))
            documents += 1
        print()
    if errors > 0:
        print(_("Found total of %d errors in %d documents.") % (errors, documents))
        return 1
    print(_("All %d documents are valid.") % documents)
    return 0


def backend_test_action(context, verbose=False):
    engine = get_engine(context)
    if not verify_engine(engine):
        return 1
    if verbose:
        print('\n')
        print(engine.version_info())
    return 0


if __name__ == '__main__':
    main()
